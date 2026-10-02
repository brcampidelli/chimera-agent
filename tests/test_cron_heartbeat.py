"""The daemon's heartbeat: the sign of life a crashed process cannot write, read by someone else.

Issue #26's gap, precisely: `cron doctor` reads *jobs*, and a dead daemon with a daily job looks
healthy for ~23 hours — the job is not yet late. The heartbeat closes that window: the daemon
leaves a timestamp on disk every tick, and any *other* process (the host cron `docs/deploy.md`
already recommends, or the app's next start) reads it and notices the silence.

The writer half lives in `chimera/scheduler/daemon.py` (the beat is written by the tick); the
reader half lives in `chimera/scheduler/watchdog.py` (the verdict). These tests drive both, plus
the two surfaces that expose the verdict: `cron doctor` (CLI) and `/api/cron/silence` (API).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.scheduler.daemon import (
    CronDaemon,
    Scheduler,
    heartbeat_age,
    read_heartbeat,
    write_heartbeat,
)
from chimera.scheduler.store import CronStore
from chimera.scheduler.watchdog import (
    default_heartbeat_path,
    infer_max_gap,
    watch_daemon,
    watch_tick_seconds,
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from chimera.api.app import build_api_app
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    return TestClient(
        build_api_app(  # type: ignore[arg-type]
            lambda: None, settings=Settings(CHIMERA_HOME=str(tmp_path / "home"))
        )
    )


def _store(tmp_path: Path) -> Any:
    return CronStore(tmp_path / "home" / "scheduler" / "jobs.json")


def _beat(tmp_path: Path) -> Path:
    return default_heartbeat_path(tmp_path / "home")


# -- the writer half: the daemon leaves a beat every tick -----------------------


def test_a_tick_writes_a_heartbeat(tmp_path: Path) -> None:
    """The beat is the record of a COMPLETED tick — written after the work, not before."""
    sch = Scheduler(_store(tmp_path))
    daemon = CronDaemon(sch, lambda job: None, tick_seconds=30, heartbeat_path=_beat(tmp_path))
    daemon.tick(now=1000.0)

    beat = read_heartbeat(_beat(tmp_path))
    assert beat is not None
    assert beat["at"] == 1000.0
    assert beat["tick_seconds"] == 30.0
    assert beat["pid"] > 0


def test_a_heartbeat_is_written_even_when_the_tick_fails(tmp_path: Path) -> None:
    """A tick whose dispatch raises still completed — the daemon is alive, the job is not. The
    beat must survive the job's failure, or a broken job would read as a dead daemon."""
    sch = Scheduler(_store(tmp_path))

    def bad_dispatch(job: Any) -> None:
        raise RuntimeError("the job broke")

    daemon = CronDaemon(sch, bad_dispatch, tick_seconds=30, heartbeat_path=_beat(tmp_path))
    daemon.tick(now=1000.0)

    assert read_heartbeat(_beat(tmp_path)) is not None


def test_a_failed_heartbeat_write_does_not_kill_the_tick(tmp_path: Path) -> None:
    """The beat is a courtesy, not the work: an unwritable path must not stop jobs dispatching."""
    sch = Scheduler(_store(tmp_path))
    fired: list[str] = []
    daemon = CronDaemon(
        sch, lambda job: fired.append(job.name), tick_seconds=30,
        heartbeat_path=tmp_path / "no-such-dir" / "deep" / "heartbeat.json",
    )
    # A directory that cannot be created (a file where the parent should be) is the honest way
    # to make the write fail on every OS.
    blocker = tmp_path / "no-such-dir"
    blocker.write_text("a file, not a directory", encoding="utf-8")

    daemon.tick(now=1000.0)

    assert fired == []  # nothing was due — but the tick itself did not raise


def test_the_default_heartbeat_path_sits_beside_the_job_store(tmp_path: Path) -> None:
    """One folder is the whole scheduler's state: a backup of one is a backup of both."""
    sch = Scheduler(_store(tmp_path))
    daemon = CronDaemon(sch, lambda job: None, tick_seconds=30)
    daemon.tick(now=1000.0)

    assert daemon.heartbeat_path == tmp_path / "home" / "scheduler" / "heartbeat.json"
    assert read_heartbeat(daemon.heartbeat_path) is not None


# -- the reader half: the verdict ------------------------------------------------


def test_no_heartbeat_is_none_not_dead(tmp_path: Path) -> None:
    """Absence of evidence is not evidence of death: a daemon that never ran left no beat, and
    the reader says so rather than guessing."""
    watch = watch_daemon(_beat(tmp_path), now=1000.0)
    assert watch.verdict == "none"
    assert watch.age_seconds is None


def test_a_fresh_beat_against_a_ceiling_is_alive(tmp_path: Path) -> None:
    write_heartbeat(_beat(tmp_path), now=1000.0, tick_seconds=30, pid=1)
    watch = watch_daemon(_beat(tmp_path), now=1005.0, max_gap_seconds=90.0)
    assert watch.verdict == "alive"
    assert watch.age_seconds == 5.0
    assert watch.max_gap_seconds == 90.0


def test_a_beat_past_the_ceiling_is_stale(tmp_path: Path) -> None:
    write_heartbeat(_beat(tmp_path), now=1000.0, tick_seconds=30, pid=1)
    watch = watch_daemon(_beat(tmp_path), now=1300.0, max_gap_seconds=90.0)
    assert watch.verdict == "stale"


def test_a_beat_without_a_ceiling_is_unknown(tmp_path: Path) -> None:
    """The honest default. A 30-min daemon and a 30-s daemon are both correct; a fixed threshold
    would call one of them dead. Without a ceiling the reader refuses to invent one."""
    write_heartbeat(_beat(tmp_path), now=1000.0, tick_seconds=30, pid=1)
    watch = watch_daemon(_beat(tmp_path), now=999_999.0)
    assert watch.verdict == "unknown"
    assert watch.max_gap_seconds is None


def test_a_corrupt_heartbeat_is_none(tmp_path: Path) -> None:
    """A torn file is absence of a beat, not a verdict — the same rule the job store follows."""
    _beat(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _beat(tmp_path).write_text('{"at": 1000, "tick', encoding="utf-8")
    assert watch_daemon(_beat(tmp_path), now=1001.0).verdict == "none"


def test_a_future_beat_reads_as_zero_not_negative(tmp_path: Path) -> None:
    """A clock that moved backwards (or a restored backup) must not produce a negative age,
    which would silently pass every staleness comparison."""
    write_heartbeat(_beat(tmp_path), now=2000.0, tick_seconds=30, pid=1)
    assert heartbeat_age(_beat(tmp_path), now=1000.0) == 0.0


def test_the_ceiling_is_three_ticks_of_the_beats_own_interval(tmp_path: Path) -> None:
    """Derived from the beat's own field, so `cron doctor` needs no configuration and stays
    right when the deployment changes its tick."""
    assert infer_max_gap(30.0) == 90.0
    assert infer_max_gap(1800.0) == 5400.0
    assert infer_max_gap(0.0) == 0.0  # a daemon that claims to tick instantly: loud, not quiet


def test_watch_tick_seconds_reads_the_beats_own_field(tmp_path: Path) -> None:
    write_heartbeat(_beat(tmp_path), now=1000.0, tick_seconds=45, pid=1)
    assert watch_tick_seconds(_beat(tmp_path)) == 45.0
    assert watch_tick_seconds(tmp_path / "nope.json") == 0.0


# -- the surfaces: the CLI and the API -------------------------------------------


def test_the_api_reports_a_dead_daemon_while_both_lists_are_empty(
    client: TestClient, tmp_path: Path
) -> None:
    """The window the heartbeat exists to close: the daemon died an hour ago, the daily job is
    not due until tomorrow, and every field the API had before this said "healthy"."""
    write_heartbeat(_beat(tmp_path), now=time.time() - 3600, tick_seconds=30, pid=1)

    body = client.get("/api/cron/silence").json()

    assert body["daemon"]["verdict"] == "stale"
    assert body["daemon"]["age_seconds"] > 3500
    assert body["daemon"]["max_gap_seconds"] == 90.0
    assert body["overdue"] == [] and body["failing"] == []


def test_the_api_reports_a_live_daemon(client: TestClient, tmp_path: Path) -> None:
    write_heartbeat(_beat(tmp_path), now=time.time(), tick_seconds=30, pid=1)

    body = client.get("/api/cron/silence").json()

    assert body["daemon"]["verdict"] == "alive"


def test_the_api_says_unknown_when_the_beat_carries_no_interval(
    client: TestClient, tmp_path: Path
) -> None:
    """A beat written by an older version (or a caller that passed no tick) has no ceiling to
    judge against — `unknown`, with the ceiling reported as None rather than invented."""
    _beat(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _beat(tmp_path).write_text(json.dumps({"at": time.time(), "pid": 1}), encoding="utf-8")

    body = client.get("/api/cron/silence").json()

    assert body["daemon"]["verdict"] == "unknown"
    assert body["daemon"]["max_gap_seconds"] is None


def test_the_api_says_none_when_there_is_no_beat(client: TestClient) -> None:
    body = client.get("/api/cron/silence").json()
    assert body["daemon"]["verdict"] == "none"
    assert body["daemon"]["age_seconds"] is None
