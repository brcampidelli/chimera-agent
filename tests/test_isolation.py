"""Tests for parallel multi-agent isolation — worktree (filesystem) + process (fault)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chimera.orchestration import run_in_processes, run_isolated


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _init_repo(path: Path) -> None:
    _git(["init"], path)
    _git(["config", "user.email", "t@t.co"], path)
    _git(["config", "user.name", "t"], path)
    (path / "seed.txt").write_text("seed", encoding="utf-8")
    _git(["add", "-A"], path)
    _git(["commit", "-m", "init"], path)


def _writer(name: str, content: str):  # noqa: ANN202 — returns a unit fn
    def run(ws: Path) -> str:
        (ws / name).write_text(content, encoding="utf-8")
        return content

    return run


def test_isolated_units_merge_disjoint_edits(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    batch = run_isolated(
        tmp_path,
        [("a", _writer("a.txt", "AAA")), ("b", _writer("b.txt", "BBB"))],
    )
    assert batch.ok and not batch.conflicts
    assert batch.merged == 2
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "AAA"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "BBB"


def test_isolated_conflict_is_reported_not_clobbered(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    batch = run_isolated(
        tmp_path,
        [("a", _writer("shared.txt", "from-A")), ("b", _writer("shared.txt", "from-B"))],
    )
    assert batch.conflicts == ["shared.txt"]  # both touched it -> flagged
    assert batch.merged == 0  # neither version silently wins
    assert not (tmp_path / "shared.txt").exists()  # left for the caller to resolve


def test_isolated_faulty_unit_does_not_fail_the_batch(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    def boom(ws: Path) -> str:
        raise RuntimeError("worker crashed")

    batch = run_isolated(tmp_path, [("good", _writer("ok.txt", "ok")), ("bad", boom)])
    by_name = {r.name: r for r in batch.results}
    assert by_name["good"].ok and not by_name["bad"].ok
    assert "worker crashed" in by_name["bad"].error
    assert (tmp_path / "ok.txt").read_text(encoding="utf-8") == "ok"  # good one still merged


def test_isolated_outside_git_runs_in_place(tmp_path: Path) -> None:
    batch = run_isolated(tmp_path, [("a", _writer("plain.txt", "hi"))])  # no git repo
    assert batch.ok
    assert (tmp_path / "plain.txt").read_text(encoding="utf-8") == "hi"


# --- process isolation (module-level fns so they're picklable) ---


def _answer() -> int:
    return 42


def _explode() -> int:
    raise ValueError("boom")


def test_run_in_processes_isolates_failures() -> None:
    results = run_in_processes([("ok", _answer), ("bad", _explode)])
    by_name = {r.name: r for r in results}
    assert by_name["ok"].ok and by_name["ok"].value == 42
    assert not by_name["bad"].ok
    assert "boom" in by_name["bad"].error


def test_run_in_processes_empty_is_noop() -> None:
    assert run_in_processes([]) == []


@pytest.mark.parametrize("units", [[], None])
def test_run_isolated_empty(tmp_path: Path, units: list | None) -> None:
    if units is None:
        return
    assert run_isolated(tmp_path, units).results == []
