"""The Storage card's numbers (study 29, P5.3): a size, or null — never a zero that was not counted.

`chimera/core/resources.py` already lives by this rule for CPU and VRAM. Disk gets the same one: a
folder that could not be read, or a count that ran out of time, comes back as ``bytes: null`` with
a note — because a partial count shown as a size is believed as one, and the decision it feeds
("is it the worktrees filling C:?") is exactly the one a wrong number gets wrong.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from pathlib import Path

import pytest

import chimera.core.storage as storage
from chimera.config import get_settings
from chimera.core.storage import measure, rotate_logs, summary_rows, tree_size


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Temp and the worktree folder are the test's own, so nothing on this machine is counted."""
    import tempfile

    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("CHIMERA_WORKTREE_DIR", "")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "browsers"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def category(report: dict[str, object], key: str) -> dict[str, object]:
    rows = report["categories"]
    assert isinstance(rows, list)
    return next(row for row in rows if row["key"] == key)


def test_a_folder_that_does_not_exist_is_measured_as_zero(tmp_path: Path) -> None:
    """Nothing there is a measurement. It is the one zero this module may report."""
    size = tree_size(tmp_path / "never-made")
    assert (size.bytes, size.files) == (0, 0)


def test_a_folder_that_cannot_be_read_is_null_not_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    write(home / "sessions" / "a.json", 10)
    real = os.scandir
    blocked = str(home / "sessions")

    def scandir(path: str) -> object:
        if os.path.normcase(str(path)) == os.path.normcase(blocked):
            raise PermissionError(13, "denied", path)
        return real(path)

    monkeypatch.setattr(storage.os, "scandir", scandir)
    report = measure(home)

    sessions = category(report, "sessions")
    assert sessions["bytes"] is None and sessions["files"] is None
    assert "PermissionError" in str(sessions["note"])
    # The remainder is computed from the parts, so one unmeasured part makes it unmeasured too —
    # otherwise the missing sessions would reappear as "other".
    assert category(report, "other")["bytes"] is None


def test_a_count_that_runs_out_of_time_is_null_not_the_part_it_reached(tmp_path: Path) -> None:
    write(tmp_path / "big" / "a.bin", 100)
    size = tree_size(tmp_path / "big", deadline=time.monotonic() - 1)
    assert size.bytes is None and "stopped counting" in size.note


def test_an_unknown_browser_location_is_null(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    browsers = category(measure(tmp_path / "home"), "browsers")
    assert browsers["bytes"] is None and browsers["note"]


def test_every_file_is_counted_once_and_the_parts_add_up_to_the_home(tmp_path: Path) -> None:
    """The scheduler's trace is a log, not a schedule; counted in both, the card would show more
    disk than the drive lost."""
    home = tmp_path / "home"
    write(home / "sessions" / "s.json", 100)
    write(home / "history.db", 200)
    write(home / "history.db-wal", 50)
    write(home / "memory.db", 300)
    write(home / "scheduler" / "jobs.json", 40)
    write(home / "scheduler" / "cron_traces.jsonl", 70)
    write(home / "traces.jsonl", 30)
    write(home / "traces.jsonl.1", 20)
    write(home / "approvals" / "history.jsonl", 9)
    write(home / "voice" / "requests.jsonl", 8)
    write(home / "skills" / "b" / "bundle.json", 7)
    write(home / "runs.jsonl", 5)
    write(tmp_path / "browsers" / "chromium" / "chrome.exe", 1000)

    report = measure(home)
    sizes = {row["key"]: row["bytes"] for row in report["categories"]}

    assert sizes["sessions"] == 100
    assert sizes["history"] == 250, "the WAL is part of the store"
    assert sizes["memory"] == 300
    assert sizes["logs"] == 30 + 20 + 70
    assert sizes["scheduler"] == 40
    assert sizes["approvals"] == 9 and sizes["voice"] == 8 and sizes["bundles"] == 7
    assert sizes["other"] == 5
    assert sizes["browsers"] == 1000
    assert sizes["worktrees"] == 0
    home_named = [k for k in sizes if k not in ("worktrees", "browsers")]
    assert sum(int(sizes[k] or 0) for k in home_named) == tree_size(home).bytes


def test_the_terminal_says_not_measured_where_the_card_says_null(tmp_path: Path) -> None:
    report = measure(tmp_path / "home")
    report["categories"][0]["bytes"] = None
    report["categories"][0]["note"] = "sessions: PermissionError"
    rows = dict(summary_rows(report))
    assert rows["sessions"].startswith("not measured")
    assert "0 B" not in rows["sessions"]


def test_rotating_moves_only_the_traces_and_keeps_one_generation(tmp_path: Path) -> None:
    """The rename the trace writers already make at their cap, made now. The spend log is NOT one of
    them: rotating it would reset today's spend, and with it the daily cap."""
    home = tmp_path / "home"
    write(home / "traces.jsonl", 100)
    write(home / "traces.jsonl.1", 400)
    write(home / "scheduler" / "cron_traces.jsonl", 60)
    write(home / "usage.jsonl", 500)
    write(home / "audit.jsonl", 500)
    write(home / "trajectories.jsonl", 500)

    result = rotate_logs(home)

    assert result == {"rotated": 2, "bytes_freed": 400, "failed": 0}
    assert not (home / "traces.jsonl").exists()
    assert (home / "traces.jsonl.1").stat().st_size == 100, "the current trace is the kept generation"
    assert (home / "scheduler" / "cron_traces.jsonl.1").stat().st_size == 60
    for untouched in ("usage.jsonl", "audit.jsonl", "trajectories.jsonl"):
        assert (home / untouched).stat().st_size == 500, untouched
        assert not (home / f"{untouched}.1").exists(), untouched


def test_a_first_rotation_frees_nothing_and_says_so(tmp_path: Path) -> None:
    write(tmp_path / "traces.jsonl", 100)
    assert rotate_logs(tmp_path) == {"rotated": 1, "bytes_freed": 0, "failed": 0}


def test_the_whole_report_keeps_one_time_limit_and_says_what_it_did_not_reach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding: the 5 s budget was per COUNT — per category and per worktree — so one report
    could take (categories + worktrees + 2) x 5 s, and the Settings screen asked for two at once.

    Every walk here takes 0.3 s unless its deadline comes first. Twenty worktrees and the named
    categories are well past a 1 s report limit: the report must come back near that limit, with the
    rows it did not reach null and noted — never a size for a walk that was cut short."""
    import tempfile

    for _ in range(20):
        write(Path(tempfile.mkdtemp(prefix="chimera-wt-")) / "f.bin", 10)
    real = storage.tree_size

    def slow(path: Path, *, exclude: Iterable[Path] = (), deadline: float | None = None) -> storage.Size:
        assert deadline is not None, "every count in a report carries a deadline"
        time.sleep(max(0.0, min(0.3, deadline - time.monotonic())))
        return real(path, exclude=exclude, deadline=deadline)

    monkeypatch.setattr(storage, "tree_size", slow)
    monkeypatch.setattr(storage, "REPORT_BUDGET_SECONDS", 1.0)

    started = time.monotonic()
    report = measure(tmp_path / "home")
    elapsed = time.monotonic() - started

    assert elapsed < 3.0, f"the report took {elapsed:.1f} s against a 1 s limit"
    worktrees = category(report, "worktrees")
    assert worktrees["bytes"] is None and "stopped counting" in str(worktrees["note"])
    assert any(w["bytes"] is None for w in report["worktrees"])


def test_two_screens_asking_at_once_share_one_walk_of_the_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Storage and Diagnostics cards mount together and each asks for the report. One walk."""
    import threading

    calls: list[Path] = []
    real = storage.measure

    def counted(home: Path, workspace: Path | None = None) -> dict[str, object]:
        calls.append(home)
        time.sleep(0.3)
        return real(home, workspace)

    monkeypatch.setattr(storage, "measure", counted)
    storage.forget_shared_reports()
    home = tmp_path / "home"
    results: list[dict[str, object]] = []
    threads = [
        threading.Thread(target=lambda: results.append(storage.measure_shared(home))) for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert len(calls) == 1 and len(results) == 2 and results[0] == results[1]
    storage.forget_shared_reports()
    storage.measure_shared(home)
    assert len(calls) == 2, "an action that changes the disk must drop the shared report"
