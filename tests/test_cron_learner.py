"""Tests for self-learned cron proposals."""

from __future__ import annotations

from pathlib import Path

from chimera.scheduler import CronLearner, CronStore, Scheduler


def test_proposes_recurring_task() -> None:
    learner = CronLearner(min_occurrences=3)
    history = [
        "run the weekly report",
        "Run the weekly report",  # normalizes the same
        "run the weekly report!",
        "do something else once",
    ]
    proposals = learner.analyze(history)
    assert len(proposals) == 1
    assert proposals[0].occurrences == 3
    assert "report" in proposals[0].name


def test_below_threshold_no_proposal() -> None:
    learner = CronLearner(min_occurrences=3)
    assert learner.analyze(["a task", "a task"]) == []


def test_build_job_enabled_with_override_schedule() -> None:
    learner = CronLearner(min_occurrences=2)
    (proposal,) = learner.analyze(["sync the files", "sync the files"])
    job = learner.build_job(proposal, enabled=True, schedule="0 6 * * 1")
    assert job.enabled is True  # interactive flow creates enabled after confirmation
    assert job.schedule == "0 6 * * 1"
    assert job.created_by == "agent"
    assert job.metadata["proposed"] is True


def test_register_proposals_are_disabled_agent_jobs(tmp_path: Path) -> None:
    learner = CronLearner(min_occurrences=2)
    sched = Scheduler(CronStore(tmp_path / "jobs.json"))
    proposals = learner.analyze(["backup the db", "backup the db"])
    jobs = learner.register_proposals(sched, proposals)

    assert len(jobs) == 1
    job = jobs[0]
    assert job.created_by == "agent"
    assert job.enabled is False
    assert job.metadata["proposed"] is True
    # persisted, but not "due" because it's disabled
    assert job.id in sched.store
    assert sched.due(9_999_999_999.0) == []
