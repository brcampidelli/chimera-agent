"""Tests for the scheduler (deterministic — time is injected)."""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.scheduler import CronJob, CronStore, Scheduler

NOW = 1_000_000.0  # fixed epoch for determinism


def _scheduler(tmp_path: Path) -> Scheduler:
    return Scheduler(CronStore(tmp_path / "jobs.json"))


def test_schedule_cron_sets_future_next_run(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path)
    job = sched.schedule_cron("daily", "0 9 * * *", "run report", now=NOW)
    assert job.trigger == "cron"
    assert job.next_run is not None and job.next_run > NOW
    assert sched.due(NOW) == []  # not due yet
    assert [j.id for j in sched.due(job.next_run)] == [job.id]


def test_invalid_cron_expression_rejected(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path)
    with pytest.raises(ValueError):
        sched.schedule_cron("bad", "not a cron", "x", now=NOW)


def test_run_due_dispatches_and_advances(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path)
    job = sched.schedule_cron("daily", "0 9 * * *", "run report", now=NOW)
    fired: list[str] = []

    scheduled = job.next_run
    assert scheduled is not None
    ran = sched.run_due(scheduled, lambda j: fired.append(j.id))
    assert [j.id for j in ran] == [job.id]
    assert fired == [job.id]

    reloaded = sched.store.get(job.id)
    assert reloaded.last_run == scheduled
    assert reloaded.next_run is not None and reloaded.next_run > scheduled


def test_event_jobs(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path)
    job = sched.schedule_event("on push", "git_push", "run ci")
    assert [j.id for j in sched.jobs_for_event("git_push")] == [job.id]
    assert sched.jobs_for_event("other") == []

    fired: list[str] = []
    ran = sched.fire_event("git_push", NOW, lambda j: fired.append(j.id))
    assert fired == [job.id]
    assert ran[0].last_run == NOW


def test_webhook_jobs(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path)
    job = sched.schedule_webhook("on gh push", "gh-push", "summarize the push")
    assert job.trigger == "webhook" and job.schedule == "gh-push"
    assert [j.id for j in sched.jobs_for_webhook("gh-push")] == [job.id]
    assert sched.jobs_for_webhook("other") == []
    # webhook jobs are not time-due and don't fire on unrelated events
    assert sched.due(NOW) == [] and sched.jobs_for_event("gh-push") == []

    fired: list[str] = []
    ran = sched.fire_webhook("gh-push", NOW, lambda j: fired.append(j.id))
    assert fired == [job.id] and ran[0].last_run == NOW


def test_store_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    store = CronStore(path)
    store.add(CronJob(id="abc", name="n", schedule="* * * * *", action="a"))

    reopened = CronStore(path)
    assert "abc" in reopened
    assert reopened.get("abc").name == "n"

    reopened.remove("abc")
    assert "abc" not in CronStore(path)


def test_enable_disable(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path)
    job = sched.schedule_cron("daily", "0 9 * * *", "x", now=NOW)

    sched.disable(job.id)
    assert sched.store.get(job.id).enabled is False

    enabled = sched.enable(job.id, now=NOW)
    assert enabled.enabled is True
    assert enabled.next_run is not None and enabled.next_run > NOW


def test_disabled_job_not_due(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path)
    job = sched.schedule_cron("daily", "0 9 * * *", "x", now=NOW)
    job.enabled = False
    sched.store.add(job)
    assert job.next_run is not None
    assert sched.due(job.next_run) == []
