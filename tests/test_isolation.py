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


def _hang(_path: Path) -> int:
    """A unit that never returns — the case the timeout exists for."""
    import time

    time.sleep(600)
    return 0


def test_run_isolated_timeout_bounds_a_hung_unit(tmp_path: Path) -> None:
    """REGRESSION (audit 2026-07-20): `timeout` was dead code — a hung unit hung the batch forever.

    The deadline was passed only to ``future.result()``, which as_completed only ever hands
    already-finished futures, so the TimeoutError branch was unreachable and ``as_completed`` itself
    waited with no bound. With the fix the batch returns: the fast unit succeeds and the hung one is
    reported as a timeout. Without it this test does not fail — it never finishes.
    """
    batch = run_isolated(
        tmp_path,
        [("fast", lambda _p: 1), ("hung", _hang)],
        timeout=2.0,
    )
    by_name = {r.name: r for r in batch.results}
    assert by_name["fast"].ok and by_name["fast"].value == 1
    assert not by_name["hung"].ok
    assert "timed out" in (by_name["hung"].error or "")


def _sleep_forever() -> int:
    import time

    time.sleep(600)
    return 0


def test_run_in_processes_timeout_bounds_a_hung_unit() -> None:
    # Same dead-timeout bug in the process path, where the docstring explicitly promised that a unit
    # which "hangs past timeout" becomes a failed result. It did not; now it does.
    results = run_in_processes([("ok", _answer), ("hung", _sleep_forever)], timeout=2.0)
    by_name = {r.name: r for r in results}
    assert by_name["ok"].ok and by_name["ok"].value == 42
    assert not by_name["hung"].ok
    assert "timed out" in (by_name["hung"].error or "")
