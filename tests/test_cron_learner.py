"""Tests for self-learned cron proposals."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from chimera.scheduler import (
    CronLearner,
    CronStore,
    Scheduler,
    describe_schedule,
    upcoming_firings,
)


@pytest.mark.parametrize(
    ("expression", "description"),
    [
        ("* * * * *", "every minute"),
        ("*/15 * * * *", "every 15 minutes"),
        ("0 7 * * 1-5", "every weekday at 07:00"),
        ("30 8 * * *", "every day at 08:30"),
        ("5 * * * *", "every hour at minute 05"),
        ("0 9 1 * *", "custom schedule: 0 9 1 * *"),
        ("not a cron", "custom schedule: not a cron"),
    ],
)
def test_schedule_description_fixtures(expression: str, description: str) -> None:
    assert describe_schedule(expression) == description


def test_upcoming_firings_reuse_first_run_and_cross_dst() -> None:
    zone = ZoneInfo("America/New_York")
    now = datetime(2026, 3, 7, 8, 0, tzinfo=zone).timestamp()
    persisted_next = datetime(2026, 3, 7, 9, 0, tzinfo=zone).timestamp()

    firings = upcoming_firings(
        "0 9 * * *", now, timezone=zone, first_run=persisted_next
    )

    assert firings == [
        datetime(2026, 3, 7, 9, 0, tzinfo=zone),
        datetime(2026, 3, 8, 9, 0, tzinfo=zone),
        datetime(2026, 3, 9, 9, 0, tzinfo=zone),
    ]
    assert [item.utcoffset() for item in firings] == [
        datetime(2026, 3, 7, 9, 0, tzinfo=zone).utcoffset(),
        datetime(2026, 3, 8, 9, 0, tzinfo=zone).utcoffset(),
        datetime(2026, 3, 9, 9, 0, tzinfo=zone).utcoffset(),
    ]
    assert [item.strftime("%H:%M %Z") for item in firings] == [
        "09:00 EST",
        "09:00 EDT",
        "09:00 EDT",
    ]


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
