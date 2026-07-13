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


def test_agent_created_cron_starts_disabled(tmp_path: Path) -> None:
    # The "self-learned crons start disabled" invariant is defended at the scheduler boundary,
    # not just in the learner — an agent-created cron must not fire until a human enables it.
    sched = _scheduler(tmp_path)
    human = sched.schedule_cron("h", "0 9 * * *", "x", now=NOW, created_by="human")
    agent = sched.schedule_cron("a", "0 9 * * *", "x", now=NOW, created_by="agent")
    assert human.enabled is True
    assert agent.enabled is False


def test_malformed_job_entry_is_skipped_not_fatal(tmp_path: Path) -> None:
    # One bad entry (hand-edit / version skew) must not drop every other cron on load.
    path = tmp_path / "jobs.json"
    good = CronJob(id="ok1", name="good", trigger="cron", schedule="0 9 * * *", action="run")
    import json

    path.write_text(
        json.dumps([good.model_dump(), {"id": "bad", "not": "a valid job"}]), encoding="utf-8"
    )
    store = CronStore(path)
    assert [j.id for j in store.list()] == ["ok1"]  # the good one survives


def test_cron_store_save_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    store = CronStore(path)
    store.add(CronJob(id="j1", name="n", trigger="cron", schedule="0 9 * * *", action="run"))
    # No stray temp file left behind, and the file is valid JSON.
    assert not list(tmp_path.glob("jobs.json.*"))  # unique per-write temp is cleaned up
    import json

    assert json.loads(path.read_text(encoding="utf-8"))[0]["id"] == "j1"


def test_structurally_broken_json_keeps_jobs_and_retries(tmp_path: Path) -> None:
    # A truncated/typo'd file must NOT crash load() or wipe the in-memory crons (finding #1).
    path = tmp_path / "jobs.json"
    store = CronStore(path)
    store.add(CronJob(id="j1", name="n", trigger="cron", schedule="0 9 * * *", action="run"))
    path.write_text('[{"id": "j1", ', encoding="utf-8")  # truncated JSON, unparseable
    store.reload_if_changed()  # must not raise
    assert [j.id for j in store.list()] == ["j1"]  # kept, not wiped
    # mtime was NOT advanced past the broken read, so fixing the file triggers a fresh reload.
    import json
    import os

    path.write_text(
        json.dumps(
            [CronJob(id="j2", name="n", trigger="cron", schedule="0 9 * * *", action="run").model_dump()]
        ),
        encoding="utf-8",
    )
    # Force a distinctly newer mtime: on coarse-granularity clocks (Windows) all three writes can
    # share one tick, which would make reload_if_changed miss the fix — this asserts the *logic*
    # (a changed file reloads) without depending on wall-clock resolution.
    future = path.stat().st_mtime + 10
    os.utime(path, (future, future))
    assert store.reload_if_changed() is True
    assert [j.id for j in store.list()] == ["j2"]


def test_non_list_root_is_ignored_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    path.write_text('{"id": "j1"}', encoding="utf-8")  # object root, not a list
    store = CronStore(path)  # must not raise
    assert store.list() == []


def test_concurrent_add_from_another_process_is_not_lost(tmp_path: Path) -> None:
    # A long-lived store holding a stale snapshot must not clobber a job another process wrote
    # to the same file when it next saves (finding #2 — lost update).
    path = tmp_path / "jobs.json"
    daemon = CronStore(path)
    daemon.add(CronJob(id="A", name="a", trigger="cron", schedule="0 9 * * *", action="run"))
    # Another process adds job C directly to the file, out of band.
    other = CronStore(path)
    other.add(CronJob(id="C", name="c", trigger="cron", schedule="0 9 * * *", action="run"))
    # The daemon, still holding its A-only snapshot, saves (e.g. mark_ran on A) — C must survive.
    daemon.add(CronJob(id="B", name="b", trigger="cron", schedule="0 9 * * *", action="run"))
    reopened = CronStore(path)
    assert {j.id for j in reopened.list()} == {"A", "B", "C"}


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
