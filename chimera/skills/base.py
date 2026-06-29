"""The ``Skill`` abstraction.

A Skill is a reusable, parameterizable *procedure* (planning, tool sequencing,
checks, error recovery) — the unit Chimera both ships built-in and **learns to
write for itself**. Every skill carries :class:`SkillMetrics`; those metrics drive
continuous refinement (which skills to evolve, which to retire) in the evolution
engine. Skills differ from tools: a tool is one primitive call, a skill orchestrates.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from chimera.telemetry import get_logger

_log = get_logger("skills.base")


class SkillMetrics(BaseModel):
    """Running performance record for a skill, used to guide refinement."""

    runs: int = 0
    successes: int = 0
    failures: int = 0
    total_latency_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.successes / self.runs if self.runs else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.runs if self.runs else 0.0

    def record(self, *, success: bool, latency_ms: float) -> None:
        self.runs += 1
        self.total_latency_ms += latency_ms
        if success:
            self.successes += 1
        else:
            self.failures += 1


class SkillResult(BaseModel):
    """Outcome of running a skill."""

    ok: bool
    output: str = ""
    error: str | None = None


class Skill(ABC):
    """Base class for a reusable procedure.

    Subclasses set :attr:`name`, :attr:`description`, :attr:`version` and implement
    :meth:`run`. Callers should use :meth:`execute`, which records metrics and never
    raises (failures become a ``SkillResult`` with ``ok=False``).
    """

    name: str
    description: str
    version: str = "0.1.0"

    def __init__(self) -> None:
        self.metrics = SkillMetrics()

    @abstractmethod
    def run(self, **kwargs: Any) -> SkillResult:
        """Perform the skill. May raise; :meth:`execute` wraps it safely."""
        raise NotImplementedError

    def execute(self, **kwargs: Any) -> SkillResult:
        """Run the skill, capturing success/latency into :attr:`metrics`."""
        start = time.perf_counter()
        try:
            result = self.run(**kwargs)
        except Exception as exc:  # skills must never crash the agent loop
            _log.warning("skill %s failed: %s", self.name, exc)
            result = SkillResult(ok=False, error=str(exc))
        latency_ms = (time.perf_counter() - start) * 1000.0
        self.metrics.record(success=result.ok, latency_ms=latency_ms)
        return result
