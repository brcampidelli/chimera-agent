"""Self-learned crons — the agent proposing its own automations.

Scans a history of tasks the agent has performed, finds the ones that recur, and
proposes scheduled jobs for them. Proposals are registered **disabled** and tagged
``created_by='agent'`` — the human still approves (or edits the schedule) before they
run, which keeps automation creation under human control (per the governance rules).
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from croniter import croniter

from chimera.scheduler.engine import Scheduler, next_firings
from chimera.scheduler.models import CronJob

_TOKEN = re.compile(r"[a-z0-9]+")


def describe_schedule(expression: str) -> str:
    """Describe supported cron shapes exactly; leave unfamiliar valid syntax explicit."""
    fields = expression.split()
    if len(fields) != 5 or not croniter.is_valid(expression):
        return f"custom schedule: {expression}"
    minute, hour, day, month, weekday = fields
    if fields == ["*", "*", "*", "*", "*"]:
        return "every minute"
    if minute.isdigit() and hour.isdigit() and day == month == weekday == "*":
        time = f"{int(hour):02d}:{int(minute):02d}"
        return f"every day at {time}"
    if minute.isdigit() and hour.isdigit() and day == month == "*" and weekday in {
        "1-5",
        "mon-fri",
        "MON-FRI",
    }:
        time = f"{int(hour):02d}:{int(minute):02d}"
        return f"every weekday at {time}"
    if minute.startswith("*/") and hour == day == month == weekday == "*":
        interval = minute[2:]
        if interval.isdigit() and int(interval) > 0:
            return f"every {int(interval)} minutes"

    if minute.isdigit() and hour == "*" and day == month == weekday == "*":
        return f"every hour at minute {int(minute):02d}"
    return f"custom schedule: {expression}"


def upcoming_firings(
    expression: str,
    now: float,
    *,
    timezone: str | ZoneInfo | None = None,
    first_run: float | None = None,
) -> list[datetime]:
    """Return three upcoming local datetimes deterministically, including across DST changes."""
    zone: ZoneInfo | None = (
        ZoneInfo(timezone)
        if isinstance(timezone, str)
        else timezone
        if isinstance(timezone, ZoneInfo)
        else None
    )
    if zone is None:
        # The machine's own zone, exactly as the engine reads it. `astimezone()` yields a fixed
        # offset, never a ZoneInfo, so coercing it to one silently fell back to UTC.
        return [
            datetime.fromtimestamp(at, tz=UTC).astimezone()
            for at in next_firings(expression, now, first_run=first_run, count=3)
        ]
    return [
        datetime.fromtimestamp(at, tz=UTC).astimezone(zone)
        for at in next_firings(
            expression, now, first_run=first_run, count=3, timezone=zone
        )
    ]


def _normalize(task: str) -> str:
    return " ".join(_TOKEN.findall(task.lower()))


def _short_name(task: str) -> str:
    words = _TOKEN.findall(task.lower())[:4]
    return "-".join(words) or "task"


@dataclass
class CronProposal:
    """A proposed automation for a recurring task."""

    name: str
    action: str
    occurrences: int
    suggested_schedule: str


class CronLearner:
    """Detects recurring tasks and proposes crons for them."""

    def __init__(self, *, min_occurrences: int = 3, default_schedule: str = "0 9 * * *") -> None:
        self.min_occurrences = min_occurrences
        self.default_schedule = default_schedule

    def analyze(self, history: list[str]) -> list[CronProposal]:
        counts: Counter[str] = Counter()
        first_seen: dict[str, str] = {}
        for task in history:
            norm = _normalize(task)
            if not norm:
                continue
            counts[norm] += 1
            first_seen.setdefault(norm, task)

        proposals: list[CronProposal] = []
        for norm, count in counts.most_common():
            if count >= self.min_occurrences:
                original = first_seen[norm]
                proposals.append(
                    CronProposal(
                        name=_short_name(original),
                        action=original,
                        occurrences=count,
                        suggested_schedule=self.default_schedule,
                    )
                )
        return proposals

    def build_job(
        self, proposal: CronProposal, *, enabled: bool, schedule: str | None = None
    ) -> CronJob:
        """Build an agent-created :class:`CronJob` from a proposal (not yet stored).

        ``enabled`` is the caller's call: the unattended ``register_proposals`` path
        keeps it ``False`` (human enables later), while an interactive flow can pass
        ``True`` once the human has confirmed it.
        """
        return CronJob(
            id=uuid.uuid4().hex[:8],
            name=proposal.name,
            trigger="cron",
            schedule=schedule or proposal.suggested_schedule,
            action=proposal.action,
            created_by="agent",
            enabled=enabled,
            metadata={"occurrences": proposal.occurrences, "proposed": True},
        )

    def register_proposals(
        self, scheduler: Scheduler, proposals: list[CronProposal]
    ) -> list[CronJob]:
        """Add proposals as **disabled** agent-created jobs awaiting approval."""
        jobs: list[CronJob] = []
        for proposal in proposals:
            job = self.build_job(proposal, enabled=False)
            scheduler.store.add(job)
            jobs.append(job)
        return jobs
