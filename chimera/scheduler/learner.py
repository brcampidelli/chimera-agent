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

from chimera.scheduler.engine import Scheduler
from chimera.scheduler.models import CronJob

_TOKEN = re.compile(r"[a-z0-9]+")


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

    def register_proposals(
        self, scheduler: Scheduler, proposals: list[CronProposal]
    ) -> list[CronJob]:
        """Add proposals as **disabled** agent-created jobs awaiting approval."""
        jobs: list[CronJob] = []
        for proposal in proposals:
            job = CronJob(
                id=uuid.uuid4().hex[:8],
                name=proposal.name,
                trigger="cron",
                schedule=proposal.suggested_schedule,
                action=proposal.action,
                created_by="agent",
                enabled=False,
                metadata={"occurrences": proposal.occurrences, "proposed": True},
            )
            scheduler.store.add(job)
            jobs.append(job)
        return jobs
