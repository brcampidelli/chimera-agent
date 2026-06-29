"""Explicit planning: decompose a task into concrete steps before executing.

A small, model-backed planner. The plan is injected into the agent's context so
execution is directed rather than improvised — the seed of Tier-2 autonomy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from chimera.providers.gateway import Message, SupportsComplete

_PLANNER_SYSTEM = (
    "You are a planning assistant. Given a task, produce a short numbered plan of "
    "3-7 concrete, actionable steps. Output ONLY the numbered steps, one per line."
)
_STEP = re.compile(r"^\s*\d+[.)]\s*(.+)$")


@dataclass
class Plan:
    """An ordered list of steps for a task."""

    steps: list[str] = field(default_factory=list)
    raw: str = ""

    def as_text(self) -> str:
        return "\n".join(f"{i}. {step}" for i, step in enumerate(self.steps, 1))


class Planner:
    """Produces a :class:`Plan` for a task using a model backend."""

    def __init__(self, backend: SupportsComplete, model: str | None = None) -> None:
        self.backend = backend
        self.model = model

    def plan(self, task: str, *, context: str = "") -> Plan:
        user = f"{context}\n\nTask: {task}" if context else task
        raw = self.backend.complete(
            [Message(role="system", content=_PLANNER_SYSTEM), Message(role="user", content=user)],
            model=self.model,
            temperature=0.2,
        ).content
        return Plan(steps=self._parse(raw), raw=raw)

    @staticmethod
    def _parse(text: str) -> list[str]:
        steps = [m.group(1).strip() for line in text.splitlines() if (m := _STEP.match(line))]
        if not steps:
            steps = [line.strip() for line in text.splitlines() if line.strip()]
        return steps
