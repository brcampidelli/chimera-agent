"""Tests for IsolatedCrew — tool-using workers editing one task in parallel worktrees."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from chimera.orchestration import IsolatedCrew, IsolatedWorker, Role, RoleAgent
from chimera.providers.gateway import CompletionResult, ToolCall
from chimera.tools import ToolRegistry, WriteFileTool


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _init_repo(path: Path) -> None:
    _git(["init"], path)
    _git(["config", "user.email", "t@t.co"], path)
    _git(["config", "user.name", "t"], path)
    (path / "seed.txt").write_text("seed", encoding="utf-8")
    _git(["add", "-A"], path)
    _git(["commit", "-m", "init"], path)


class WritingBackend:
    """Issues one write_file tool call (into the worker's worktree), then finishes."""

    def __init__(self, path: str, content: str) -> None:
        self.path = path
        self.content = content
        self.n = 0

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.n += 1
        if self.n == 1 and kwargs.get("tools"):
            return CompletionResult(
                content="", model="fake",
                tool_calls=[ToolCall(id="1", name="write_file",
                                     arguments={"path": self.path, "content": self.content})],
            )
        return CompletionResult(content=f"done {self.path}", model="fake")


class BoomBackend:
    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        raise RuntimeError("worker crashed")


def _writer_tools(ws: Path) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(WriteFileTool(ws))
    return reg


def _worker(name: str, path: str, content: str) -> IsolatedWorker:
    return IsolatedWorker(Role(name, f"SYS-{name}"), _writer_tools, backend=WritingBackend(path, content))


def test_disjoint_workers_merge(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    crew = IsolatedCrew(WritingBackend("_", "_"), [_worker("a", "a.txt", "AAA"), _worker("b", "b.txt", "BBB")])
    res = crew.run("do your part", tmp_path)
    assert res.ok and res.merged == 2
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "AAA"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "BBB"
    assert {m.sender for m in res.transcript} == {"a", "b"}


def test_conflicting_workers_are_reported(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    crew = IsolatedCrew(
        WritingBackend("_", "_"),
        [_worker("a", "shared.txt", "from-A"), _worker("b", "shared.txt", "from-B")],
    )
    res = crew.run("edit shared", tmp_path)
    assert res.conflicts == ["shared.txt"]
    assert res.merged == 0 and not res.ok
    assert not (tmp_path / "shared.txt").exists()  # neither version silently wins


def test_failing_worker_does_not_sink_the_crew(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    good = _worker("good", "ok.txt", "ok")
    bad = IsolatedWorker(Role("bad", "SYS"), _writer_tools, backend=BoomBackend())
    res = IsolatedCrew(WritingBackend("_", "_"), [good, bad]).run("go", tmp_path)
    assert "bad" in res.failures and "worker crashed" in res.failures["bad"]
    assert (tmp_path / "ok.txt").read_text(encoding="utf-8") == "ok"  # the good worker still merged
    assert {m.sender for m in res.transcript} == {"good"}


class EchoUserBackend:
    """Returns the last user message content, so we can inspect what the supervisor saw."""

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        content = ""
        for message in messages:
            data = message.as_dict() if hasattr(message, "as_dict") else message
            if data.get("role") == "user":
                content = str(data.get("content", ""))
        return CompletionResult(content=content, model="fake")


def test_supervisor_synthesizes_merged_results(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    supervisor = RoleAgent(Role("supervisor", "SUP"), EchoUserBackend())
    crew = IsolatedCrew(
        WritingBackend("_", "_"),
        [_worker("a", "a.txt", "AAA"), _worker("b", "b.txt", "BBB")],
        supervisor=supervisor,
    )
    res = crew.run("build the thing", tmp_path)
    assert res.summary  # the supervisor produced a report
    assert "done a.txt" in res.summary and "done b.txt" in res.summary  # it saw both merged outputs
    assert "build the thing" in res.summary  # ...for the given task


def test_no_supervisor_leaves_summary_empty(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    res = IsolatedCrew(WritingBackend("_", "_"), [_worker("a", "a.txt", "AAA")]).run("t", tmp_path)
    assert res.summary == ""


def test_verify_gate_rejects_a_worker_whose_check_fails(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    # Each worker writes a marker file; verify passes only when the file says "good".
    passer = _worker("passer", "out/passer.txt", "good")
    failer = _worker("failer", "out/failer.txt", "bad")
    # verify: exit 0 iff the running worktree contains no file whose content is "bad".
    verify = "python -c \"import pathlib,sys; sys.exit(any(p.read_text()=='bad' for p in pathlib.Path('out').glob('*.txt')))\""
    res = IsolatedCrew(WritingBackend("_", "_"), [passer, failer]).run("go", tmp_path, verify=verify)
    assert "failer" in res.rejected  # ran, but its change failed verification
    assert {m.sender for m in res.transcript} == {"passer"}  # only the verified one merged
    assert (tmp_path / "out" / "passer.txt").exists()
    assert not (tmp_path / "out" / "failer.txt").exists()  # rejected change was discarded
    assert not res.ok  # a rejected worker means the run isn't fully clean


def test_a_worker_that_passed_but_lost_its_file_is_not_reported_as_landing_it(tmp_path: Path) -> None:
    """Passing the check and landing are different things, and the frame has to keep them apart.

    Two workers who both succeed on one file both lose it — the one-file-one-owner rule. Reporting
    `landed` from the verifier's verdict put "the files it wrote, and that landed" on a card
    directly above a panel saying nothing landed, which is precisely the reading this screen was
    built to prevent.
    """
    _init_repo(tmp_path)
    seen: list[Any] = []
    crew = IsolatedCrew(
        WritingBackend("_", "_"),
        [_worker("a", "shared.txt", "from-A"), _worker("b", "shared.txt", "from-B")],
        on_event=seen.append,
    )

    res = crew.run("edit shared", tmp_path)

    produced = {e.task_id: e.data for e in seen if e.kind == "worker_produced"}
    assert set(produced) == {"a", "b"}
    for name, data in produced.items():
        assert data["landed"] is False, name
        assert data["files"] == [], name
        # And the file is still named, because it is the only account of what that worker tried.
        assert data["lost"] == ["shared.txt"], name
    assert res.merged == 0


def test_a_worker_that_actually_landed_says_so(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    seen: list[Any] = []
    crew = IsolatedCrew(
        WritingBackend("_", "_"),
        [_worker("a", "a.txt", "AAA"), _worker("b", "b.txt", "BBB")],
        on_event=seen.append,
    )

    crew.run("do your part", tmp_path)

    produced = {e.task_id: e.data for e in seen if e.kind == "worker_produced"}
    assert produced["a"]["landed"] is True and produced["a"]["files"] == ["a.txt"]
    assert produced["a"]["lost"] == []
