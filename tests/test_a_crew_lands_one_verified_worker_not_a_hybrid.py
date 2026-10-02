"""A crew lands ONE approved worker's tree, whole — never a hybrid of several (study 28, MA1).

Every crew worker attacks the same task. The merge used to copy each approved worker's files that
no other approved worker had also changed, which does two things nobody asked for:

* two solutions that each passed the check were FUSED: worker A's ``util.py`` with worker B's
  ``helper.py``, a program neither of them wrote and no check ever ran on;
* the file both of them had to change — usually the one the task was about — counted as a
  conflict and landed from NEITHER, so the more workers succeeded, the less of their work
  survived.

The rule now: one approved worker's tree lands whole (a check that ran beats one that abstained,
then the fewest changed lines, then the fewest files, then the order the workers were given), the
check runs again on the workspace the merge wrote, and the other workers' diffs are still reported.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from chimera.orchestration import IsolatedCrew, IsolatedWorker, Role
from chimera.orchestration.crew import select_worker
from chimera.orchestration.isolation import IsolatedResult
from chimera.providers.gateway import CompletionResult, ToolCall
from chimera.tools import ToolRegistry, WriteFileTool


def _git(args: list[str], cwd: Path) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout


def _init_repo(path: Path) -> None:
    _git(["init"], path)
    _git(["config", "user.email", "t@t.co"], path)
    _git(["config", "user.name", "t"], path)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(["add", "-A"], path)
    _git(["commit", "-m", "init"], path)


class FilesBackend:
    """Writes every file in ``files`` in one turn (one tool call each), then finishes."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self.n = 0

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.n += 1
        if self.n == 1 and kwargs.get("tools") and self.files:
            return CompletionResult(
                content="", model="fake",
                tool_calls=[
                    ToolCall(id=str(i), name="write_file", arguments={"path": p, "content": c})
                    for i, (p, c) in enumerate(self.files.items())
                ],
            )
        return CompletionResult(content=f"wrote {sorted(self.files)}", model="fake")


def _tools(ws: Path) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(WriteFileTool(ws))
    return reg


def _worker(name: str, files: dict[str, str]) -> IsolatedWorker:
    return IsolatedWorker(Role(name, f"SYS-{name}"), _tools, backend=FilesBackend(files))


def _crew(*workers: IsolatedWorker, **kwargs: Any) -> IsolatedCrew:
    return IsolatedCrew(FilesBackend({}), list(workers), **kwargs)


def _changed_in(repo: Path) -> set[str]:
    """Every path in the workspace that differs from HEAD, untracked included."""
    out = _git(["status", "--porcelain", "--untracked-files=all"], repo)
    return {line[3:].strip() for line in out.splitlines() if line.strip()}


def _py(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


def test_two_approved_workers_with_disjoint_edits_land_the_selected_tree_exactly(
    tmp_path: Path,
) -> None:
    """The defect itself. Before the fix, ``big.txt`` AND ``y.txt``/``z.txt`` all landed."""
    _init_repo(tmp_path)
    big = _worker("big", {"big.txt": "1\n2\n3\n4\n"})  # 4 changed lines, 1 file
    small = _worker("small", {"y.txt": "y\n", "z.txt": "z\n"})  # 2 changed lines, 2 files

    res = _crew(big, small).run("the task", tmp_path)

    assert res.selected == "small"  # fewest changed lines wins
    assert _changed_in(tmp_path) == {"y.txt", "z.txt"}, "the selected tree, and nothing else"
    assert not (tmp_path / "big.txt").exists()
    assert res.merged == 2 and res.conflicts == [] and res.ok


def test_the_file_both_workers_changed_lands_from_the_selected_one(tmp_path: Path) -> None:
    """The other half: one-file-one-owner dropped exactly the file the task was about."""
    _init_repo(tmp_path)
    a = _worker("a", {"util.py": "x = 1\n"})
    b = _worker("b", {"util.py": "x = 2\n", "helper.py": "y = 1\n"})

    res = _crew(a, b).run("fix util", tmp_path)

    assert res.selected == "a"
    assert (tmp_path / "util.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (tmp_path / "helper.py").exists(), "b's helper must not ride in on a's util"
    assert res.conflicts == []


def test_a_tie_goes_to_the_worker_listed_first(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    res = _crew(_worker("first", {"a.txt": "a\n"}), _worker("second", {"b.txt": "b\n"})).run(
        "t", tmp_path
    )

    assert res.selected == "first"
    assert _changed_in(tmp_path) == {"a.txt"}


def test_a_check_that_ran_beats_one_that_abstained_even_with_a_bigger_diff(
    tmp_path: Path,
) -> None:
    """Exit 127 is the check reaching no verdict. It merges as if no check existed — and loses a
    selection to a worker the check actually approved."""
    _init_repo(tmp_path)
    verify = _py(
        "import pathlib,sys; sys.exit(127 if pathlib.Path('abstain.txt').exists() else 0)"
    )
    abstainer = _worker("abstainer", {"abstain.txt": "a\n"})
    checked = _worker("checked", {"big.txt": "1\n2\n3\n"})

    res = _crew(abstainer, checked).run("t", tmp_path, verify=verify)

    assert res.selected == "checked"
    assert _changed_in(tmp_path) == {"big.txt"}


def test_a_worker_that_changed_nothing_is_never_selected(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    res = _crew(_worker("idle", {}), _worker("worker", {"w.txt": "w\n"})).run("t", tmp_path)

    assert res.selected == "worker"
    assert _changed_in(tmp_path) == {"w.txt"}


def test_the_check_runs_again_on_the_merged_workspace(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    log = tmp_path.parent / f"{tmp_path.name}-cwd.log"
    verify = _py(f"import os; open(r'{log}', 'a').write(os.getcwd() + chr(10))")

    res = _crew(_worker("a", {"a.txt": "a\n"})).run("t", tmp_path, verify=verify)

    cwds = [Path(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(cwds) == 2, "once in the worktree, once on the merged workspace"
    assert cwds[-1].resolve() == tmp_path.resolve()
    assert res.reverify == "passed" and res.ok


def test_a_merged_workspace_that_fails_the_check_is_reported(tmp_path: Path) -> None:
    """The realistic way it happens: the person's folder is not HEAD.

    ``poison.txt`` is uncommitted, so no worktree has it and every worker passes; the workspace
    the merge lands in does have it, and there the check fails. Reported — not reverted, and not
    hidden behind a worker that passed somewhere else.
    """
    _init_repo(tmp_path)
    (tmp_path / "poison.txt").write_text("p\n", encoding="utf-8")
    verify = _py(
        "import pathlib,sys; p=pathlib.Path('poison.txt'); print('poisoned') if p.exists() "
        "else None; sys.exit(1 if p.exists() else 0)"
    )
    seen: list[Any] = []

    res = _crew(_worker("a", {"a.txt": "a\n"}), on_event=seen.append).run(
        "t", tmp_path, verify=verify
    )

    assert res.selected == "a" and not res.rejected, "the worker passed in its own checkout"
    assert res.reverify == "failed"
    assert "poisoned" in res.reverify_output
    assert not res.ok
    done = next(e for e in seen if e.kind == "done")
    assert done.data["reverify"] == "failed" and done.data["selected"] == "a"
    assert "poisoned" in done.data["reverify_detail"]


def test_no_check_configured_means_no_second_run(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    res = _crew(_worker("a", {"a.txt": "a\n"})).run("t", tmp_path)
    assert res.reverify == "" and res.ok


def test_the_runner_up_still_reports_its_diff(tmp_path: Path) -> None:
    """It passed the same check, and its worktree is gone when the run ends."""
    _init_repo(tmp_path)
    seen: list[Any] = []
    res = _crew(
        _worker("winner", {"a.txt": "a\n"}),
        _worker("runner_up", {"b.txt": "b\nb\n"}),
        on_event=seen.append,
    ).run("t", tmp_path)

    produced = {e.task_id: e.data for e in seen if e.kind == "worker_produced"}
    assert produced["winner"]["selected"] is True and produced["winner"]["landed"] is True
    assert produced["runner_up"]["selected"] is False
    assert produced["runner_up"]["landed"] is False
    assert produced["runner_up"]["lost"] == ["b.txt"]
    assert "+b" in produced["runner_up"]["diff"]
    assert "+b" in res.diffs["runner_up"] and "+a" in res.diffs["winner"]


def test_union_mode_still_reports_a_contested_file_and_lands_neither(tmp_path: Path) -> None:
    """``merge="union"`` is for workers given DIFFERENT parts; the conflict rule lives on there."""
    _init_repo(tmp_path)
    seen: list[Any] = []
    res = _crew(
        _worker("a", {"shared.txt": "from-A\n", "a.txt": "a\n"}),
        _worker("b", {"shared.txt": "from-B\n"}),
        on_event=seen.append,
    ).run("t", tmp_path, merge="union")

    assert res.conflicts == ["shared.txt"] and not res.ok
    assert not (tmp_path / "shared.txt").exists()
    assert (tmp_path / "a.txt").exists()
    assert res.selected == ""
    assert [e.data["path"] for e in seen if e.kind == "conflict"] == ["shared.txt"]


def test_union_mode_is_re_verified_too(tmp_path: Path) -> None:
    """Under union the merged set never existed in any checkout, so it needs the second run most."""
    _init_repo(tmp_path)
    # Fails only when BOTH halves are present — a combination no single worktree ever held.
    verify = _py(
        "import pathlib,sys; sys.exit(1 if pathlib.Path('a.txt').exists() and "
        "pathlib.Path('b.txt').exists() else 0)"
    )
    res = _crew(_worker("a", {"a.txt": "a\n"}), _worker("b", {"b.txt": "b\n"})).run(
        "t", tmp_path, verify=verify, merge="union"
    )

    assert not res.rejected, "each half passed on its own"
    assert res.reverify == "failed" and not res.ok


def _result(name: str, paths: list[str], lines: int) -> IsolatedResult[Any]:
    return IsolatedResult(name, ok=True, value=None, changed_paths=paths, changed_lines=lines)


def test_selection_is_deterministic_on_lines_then_files_then_order() -> None:
    assert select_worker([_result("a", ["x"], 5), _result("b", ["y"], 2)], checked=False) == "b"
    assert select_worker(
        [_result("a", ["x", "y"], 2), _result("b", ["z"], 2)], checked=False
    ) == "b"
    assert select_worker([_result("a", ["x"], 2), _result("b", ["z"], 2)], checked=False) == "a"
    assert select_worker([_result("a", [], 0)], checked=False) is None
    assert select_worker([], checked=True) is None


def _unit(files: dict[str, str]):  # noqa: ANN202 -- returns a unit fn
    def run(ws: Path) -> bool:
        for rel, content in files.items():
            (ws / rel).parent.mkdir(parents=True, exist_ok=True)
            (ws / rel).write_text(content, encoding="utf-8")
        return "fail" not in files

    return run


def test_the_selector_is_handed_only_the_units_that_succeeded(tmp_path: Path) -> None:
    from chimera.orchestration import run_isolated

    _init_repo(tmp_path)
    offered: list[str] = []

    def pick(candidates: list[IsolatedResult[bool]]) -> str | None:
        offered.extend(c.name for c in candidates)
        return candidates[-1].name

    batch = run_isolated(
        tmp_path,
        [("ok1", _unit({"a.txt": "a"})), ("bad", _unit({"fail": "x"})), ("ok2", _unit({"b.txt": "b"}))],
        succeeded=bool,
        select=pick,
    )

    assert offered == ["ok1", "ok2"]
    assert batch.selected == "ok2" and batch.conflicts == []
    assert _changed_in(tmp_path) == {"b.txt"}


def test_a_selector_that_names_something_it_was_not_offered_lands_nothing(tmp_path: Path) -> None:
    from chimera.orchestration import run_isolated

    _init_repo(tmp_path)
    batch = run_isolated(
        tmp_path,
        [("ok", _unit({"a.txt": "a"})), ("bad", _unit({"fail": "x"}))],
        succeeded=bool,
        select=lambda _candidates: "bad",
    )

    assert batch.selected == "" and batch.merged == 0
    assert _changed_in(tmp_path) == set()


def test_bytecode_is_neither_shown_nor_counted_as_part_of_a_solution(tmp_path: Path) -> None:
    """Running the check writes ``__pycache__``; it must not make a worker look bigger."""
    from chimera.orchestration import run_isolated

    _init_repo(tmp_path)
    batch = run_isolated(
        tmp_path,
        [("w", _unit({"m.py": "x = 1\n", "__pycache__/m.cpython-312.pyc": "junk\njunk\njunk\n"}))],
    )

    (result,) = batch.results
    assert result.changed_lines == 1
    assert "__pycache__" not in result.diff and "+x = 1" in result.diff
