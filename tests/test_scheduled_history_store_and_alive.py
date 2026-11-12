"""Three things the first version of the history and the catch-up got wrong.

* A job that is rarely written must not vanish from the history because a job that writes every
  minute filled the part of the file a read looked at, and the file must not grow without bound.
* A job behind a slow neighbour is late, not missed: ``skip`` must not discard it. Only a gap in the
  scheduler's own ticks (it was off) makes a time missed.
* The permission mode is resolved against the settings of the DISPATCH, so tightening the posture
  applies to the next run without a restart.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chimera.config import Settings
from chimera.scheduler import Scheduler
from chimera.scheduler.engine import CATCH_UP_ALIVE_GAP_S
from chimera.scheduler.history import CronHistory, HistoryRow
from chimera.scheduler.job_runner import make_run_job
from chimera.scheduler.models import CronJob
from chimera.scheduler.store import CronStore

T0 = float(datetime(2026, 3, 10, 12, 0, tzinfo=UTC).timestamp())
MIN = 60.0


def _row(job: str, at: float, reason: str = "on_time") -> HistoryRow:
    return HistoryRow(at=at, job_id=job, name=job, outcome="ran", reason=reason, status="ok",
                      detail="x" * 150)


# --------------------------------------------------------------------------- the history file


def test_a_weekly_job_is_found_behind_thousands_of_minute_lines(tmp_path: Path) -> None:
    history = CronHistory(tmp_path / "h.jsonl", max_bytes=50_000_000)
    history.append(_row("weekly", 1.0, "catch_up"))
    for i in range(6000):  # well past the 512 KB a plain tail would read
        history.append(_row("minute", 100.0 + i))

    rows = history.load(job_id="weekly")

    assert [r.reason for r in rows] == ["catch_up"]
    assert len(history.load(job_id="minute", limit=50)) == 50


def test_the_file_is_rotated_per_job_and_the_rare_job_survives(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    history = CronHistory(path, max_bytes=40_000, keep_per_job=20)
    history.append(_row("weekly", 1.0, "catch_up"))
    for i in range(600):
        history.append(_row("minute", 100.0 + i))

    assert path.stat().st_size < 40_000 + 1_000, "the file kept growing"
    assert [r.reason for r in history.load(job_id="weekly")] == ["catch_up"]
    minute = history.load(job_id="minute", limit=500)
    assert 0 < len(minute) <= 40_000 // 100
    assert minute[0].at == 100.0 + 599, "the newest line was the one thrown away"
    assert not list(tmp_path.glob("*.tmp")), "a rotation left its temp file behind"


# --------------------------------------------------------------------------- late is not missed


def _sched(tmp_path: Path) -> Scheduler:
    return Scheduler(CronStore(tmp_path / "scheduler" / "jobs.json"), jitter=False)


def _reasons(sched: Scheduler, job_id: str) -> list[tuple[str, str]]:
    rows = CronHistory(sched.store.path.parent / "cron_history.jsonl").load(job_id=job_id, limit=200)
    return [(r.outcome, r.reason) for r in reversed(rows)]


class _Calls:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self, job: CronJob) -> None:
        self.n += 1


def test_a_job_behind_a_slow_neighbour_is_late_not_missed(tmp_path: Path) -> None:
    sched = _sched(tmp_path)
    job = sched.schedule_cron("five", "*/5 * * * *", "r", now=T0, catch_up="skip")
    due = job.next_run or 0.0
    calls = _Calls()
    sched.run_due(due - 30, calls)  # the scheduler is alive: this tick is the proof

    # The next tick starts 12 minutes after the job was due because a neighbour held the loop.
    sched.run_due(due + 12 * MIN, calls)

    assert calls.n == 1, "skip discarded a job that was only behind a slow neighbour"
    assert _reasons(sched, job.id) == [("ran", "on_time")]


def test_a_gap_in_the_ticks_is_an_outage_and_skip_applies(tmp_path: Path) -> None:
    sched = _sched(tmp_path)
    job = sched.schedule_cron("five", "*/5 * * * *", "r", now=T0, catch_up="skip")
    due = job.next_run or 0.0
    calls = _Calls()
    sched.run_due(due - 30, calls)

    sched.run_due(due - 30 + CATCH_UP_ALIVE_GAP_S + 5 * MIN, calls)

    assert calls.n == 0
    assert {r for _, r in _reasons(sched, job.id)} == {"catch_up_skip"}


def test_the_daemons_heartbeat_stands_in_for_the_tick_a_new_process_never_had(
    tmp_path: Path,
) -> None:
    fresh = _sched(tmp_path / "a")
    job = fresh.schedule_cron("five", "*/5 * * * *", "r", now=T0, catch_up="skip")
    due = job.next_run or 0.0
    beat = tmp_path / "a" / "scheduler" / "heartbeat.json"
    beat.write_text(json.dumps({"at": due + 10 * MIN, "tick_seconds": 30, "pid": 1}), encoding="utf-8")
    calls = _Calls()
    fresh.run_due(due + 12 * MIN, calls)
    assert calls.n == 1, "the daemon had ticked two minutes ago: not an outage"

    stale = _sched(tmp_path / "b")
    job = stale.schedule_cron("five", "*/5 * * * *", "r", now=T0, catch_up="skip")
    due = job.next_run or 0.0
    beat = tmp_path / "b" / "scheduler" / "heartbeat.json"
    beat.write_text(json.dumps({"at": due - 3 * 3600, "tick_seconds": 30, "pid": 1}), encoding="utf-8")
    calls = _Calls()
    stale.run_due(due + 12 * MIN, calls)
    assert calls.n == 0, "no tick for three hours: the scheduler was off"


# --------------------------------------------------------------------------- live settings


class _Result:
    def __init__(self, content: str) -> None:
        self.content = content
        self.model = "fake/model"
        self.prompt_tokens = 1
        self.completion_tokens = 1
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.tool_calls: list[Any] = []
        self.finish_reason = "stop"
        self.route_meta: dict[str, Any] | None = None


class _Backend:
    def __init__(self) -> None:
        self.offered: list[str] = []

    def complete(self, messages: list[Any], **kwargs: Any) -> _Result:
        self.offered.append(json.dumps(kwargs.get("tools") or []))
        return _Result("ok")


def test_tightening_the_posture_narrows_the_next_run_without_a_restart(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    state = {"settings": Settings(**{"CHIMERA_HOME": str(tmp_path / "home"),
                                     "CHIMERA_APPROVAL": "never", "CHIMERA_REACH": "workspace_shell"})}
    backend = _Backend()
    run_job = make_run_job(
        settings=state["settings"], backend=backend, workspace=project, model="fake/model",
        max_steps=3, usage_path=tmp_path / "home" / "usage.jsonl",
        live_settings=lambda: state["settings"],
    )
    job = CronJob(id="j", name="n", schedule="* * * * *", action="a", workspace=str(project),
                  permission_mode="dont_ask")

    run_job(job)
    state["settings"] = Settings(**{"CHIMERA_HOME": str(tmp_path / "home"),
                                    "CHIMERA_APPROVAL": "always", "CHIMERA_REACH": "read_only"})
    run_job(job)

    assert '"write_file"' in backend.offered[0], "the control: dont_ask under permissive Settings"
    assert '"write_file"' not in backend.offered[1], "the tightened posture did not reach this run"
