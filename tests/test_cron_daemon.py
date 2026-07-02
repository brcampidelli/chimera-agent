"""Tests for the cron daemon — the runner that gives stored crons a real clock."""

from __future__ import annotations

import threading
from pathlib import Path

from chimera.scheduler import CronDaemon, CronStore, Scheduler, make_agent_dispatch


def _scheduler(tmp_path: Path) -> Scheduler:
    return Scheduler(CronStore(tmp_path / "cron.json"))


def test_tick_fires_due_job_and_advances(tmp_path: Path) -> None:
    sch = _scheduler(tmp_path)
    sch.schedule_cron("report", "* * * * *", "write the daily report", now=0)  # next_run = 60
    fired: list[str] = []
    daemon = CronDaemon(sch, lambda job: fired.append(job.name))
    assert [j.name for j in daemon.tick(now=60)] == ["report"]  # due at t=60
    assert fired == ["report"]
    assert daemon.tick(now=60) == []  # already advanced to t=120; not due again at 60


def test_future_job_is_not_fired(tmp_path: Path) -> None:
    sch = _scheduler(tmp_path)
    sch.schedule_cron("weekly", "0 0 * * 0", "weekly audit", now=0)  # far in the future
    daemon = CronDaemon(sch, lambda job: fired.append(job.name))  # noqa: F821 — must not be called
    fired: list[str] = []
    assert daemon.tick(now=60) == []
    assert fired == []


def test_agent_dispatch_runs_task_and_reports(tmp_path: Path) -> None:
    sch = _scheduler(tmp_path)
    job = sch.schedule_cron("j", "* * * * *", "do X", now=0)
    seen: dict[str, str] = {}
    dispatch = make_agent_dispatch(
        lambda task: f"ran:{task}", on_result=lambda j, ans: seen.__setitem__(j.name, ans)
    )
    dispatch(job)
    assert seen["j"] == "ran:do X"


def test_run_forever_loops_and_survives_job_failures(tmp_path: Path) -> None:
    sch = _scheduler(tmp_path)
    sch.schedule_cron("boom", "* * * * *", "x", now=0)

    def bad_dispatch(job: object) -> None:
        raise RuntimeError("job blew up")

    stop = threading.Event()
    ticks = {"n": 0}
    clock_values = iter([60.0, 120.0, 180.0, 999.0])

    def clock() -> float:
        return next(clock_values, 999.0)

    def fake_sleep(_: float) -> None:
        ticks["n"] += 1
        if ticks["n"] >= 3:
            stop.set()  # stop after three ticks

    daemon = CronDaemon(sch, bad_dispatch, tick_seconds=0, clock=clock, sleep=fake_sleep)
    daemon.run_forever(stop=stop)  # must return despite the job raising every tick
    assert ticks["n"] >= 3  # it kept ticking through the failures


def test_start_thread_runs_then_stops(tmp_path: Path) -> None:
    sch = _scheduler(tmp_path)  # no jobs -> ticks are instant no-ops
    daemon = CronDaemon(sch, lambda job: None, tick_seconds=0.01)
    thread, stop = daemon.start()
    assert thread.is_alive()
    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_tick_reloads_jobs_added_out_of_process(tmp_path: Path) -> None:
    path = tmp_path / "cron.json"
    fired: list[str] = []
    daemon = CronDaemon(Scheduler(CronStore(path)), lambda job: fired.append(job.name))
    assert daemon.tick(now=60) == []  # daemon starts with no jobs

    # A *separate* process (its own store on the same file) schedules a cron.
    Scheduler(CronStore(path)).schedule_cron("report", "* * * * *", "daily report", now=0)

    # Without a restart, the daemon picks it up on the next tick and fires it.
    assert [j.name for j in daemon.tick(now=60)] == ["report"]
    assert fired == ["report"]


def test_dispatch_retries_delivery_then_confirms(tmp_path: Path) -> None:
    job = _scheduler(tmp_path).schedule_cron("j", "* * * * *", "do X", now=0)
    attempts = {"n": 0}

    def flaky(_job: object, _ans: str) -> None:
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise RuntimeError("transient delivery failure")

    make_agent_dispatch(lambda task: "ok", flaky, delivery_retries=2)(job)
    assert attempts["n"] == 2  # failed once, then succeeded


def test_dispatch_survives_permanent_delivery_failure(tmp_path: Path) -> None:
    job = _scheduler(tmp_path).schedule_cron("j", "* * * * *", "do X", now=0)

    def always_fails(_job: object, _ans: str) -> None:
        raise RuntimeError("sink down")

    # Must not raise — a broken sink cannot take the daemon down.
    make_agent_dispatch(lambda task: "ok", always_fails, delivery_retries=1)(job)
