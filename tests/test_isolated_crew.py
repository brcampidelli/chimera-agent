"""Tests for IsolatedCrew — tool-using workers editing one task in parallel worktrees."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from chimera.orchestration import IsolatedCrew, IsolatedWorker, Role
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
