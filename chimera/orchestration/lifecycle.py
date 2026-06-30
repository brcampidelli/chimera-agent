"""SDLC lifecycle crew: plan -> build -> test -> review, with verify-or-revert.

A pre-assembled pipeline over the existing primitives:

- **plan**   — a planner decomposes the task into steps.
- **build**  — the worker implements it in the workspace.
- **test**   — an executable verifier is the gate; on failure the build is reverted and
  retried (verify-or-revert), up to a budget.
- **review** — a reviewer role critiques the verified result (advisory).

``build`` + ``test`` are run by the Tier-2 :class:`AutonomousAgent`, which owns
verify-or-revert, so the per-stage gate is the same executable ground truth used
everywhere else — not a second, weaker check.

Imports of :mod:`chimera.core` are lazy (inside methods) because
``chimera.orchestration`` is reachable from ``chimera.core``'s own import graph; a
top-level import here would be circular.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from chimera.orchestration.roles import Role, RoleAgent
from chimera.providers.gateway import SupportsComplete
from chimera.telemetry import get_logger

if TYPE_CHECKING:
    from chimera.core.autonomous import Worker
    from chimera.core.checkpoint import WorkspaceGuard
    from chimera.core.verify import Verifier

_log = get_logger("orchestration.lifecycle")

_REVIEWER = Role(
    "reviewer",
    "You are a senior reviewer. Given a task and the work that already passed its "
    "tests, review it for correctness, edge cases and clarity. Be concise: note what "
    "is good and any follow-ups. Do NOT rewrite the work.",
)


@dataclass
class StageResult:
    name: str
    output: str
    passed: bool


@dataclass
class LifecycleResult:
    success: bool
    answer: str
    stages: list[StageResult] = field(default_factory=list)


class LifecycleCrew:
    """Runs a task through plan -> build -> test -> review."""

    def __init__(
        self,
        worker: Worker,
        backend: SupportsComplete,
        *,
        verifier: Verifier | None = None,
        guard: WorkspaceGuard | None = None,
        model: str | None = None,
        max_build_attempts: int = 2,
    ) -> None:
        self.worker = worker
        self.backend = backend
        self.verifier = verifier
        self.guard = guard
        self.model = model
        self.max_build_attempts = max_build_attempts

    def run(self, task: str) -> LifecycleResult:
        from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
        from chimera.core.planner import Planner

        stages: list[StageResult] = []

        # 1. plan
        plan = Planner(self.backend, self.model).plan(task)
        plan_text = plan.as_text() or "(no steps)"
        stages.append(StageResult("plan", plan_text, bool(plan.steps)))

        # 2 + 3. build + test — the AutonomousAgent owns the verify-or-revert gate
        auto = AutonomousAgent(
            self.worker,
            verifier=self.verifier,
            guard=self.guard,
            config=AutonomousConfig(
                max_attempts=self.max_build_attempts, use_planner=False, use_manager=False
            ),
        )
        outcome = auto.run(f"{task}\n\nPlan:\n{plan_text}")
        stages.append(StageResult("build", outcome.answer, bool(outcome.attempts)))
        test_note = "verified" if outcome.success else "verification failed"
        stages.append(StageResult("test", test_note, outcome.success))

        # 4. review — advisory; the executable test already gated success
        review = RoleAgent(_REVIEWER, self.backend).act(
            task, context=f"Work:\n{outcome.answer}\n\nTest: {test_note}"
        )
        stages.append(StageResult("review", review, True))

        _log.debug("lifecycle finished: success=%s", outcome.success)
        return LifecycleResult(success=outcome.success, answer=outcome.answer, stages=stages)


def lifecycle_crew(
    backend: SupportsComplete,
    *,
    workspace: Path,
    verify: str | None = None,
    model: str | None = None,
    max_steps: int = 8,
    max_build_attempts: int = 2,
) -> LifecycleCrew:
    """Assemble a lifecycle crew wired to the real agent loop, tools and verifier."""
    from chimera.core import Agent, AgentConfig
    from chimera.core.checkpoint import WorkspaceGuard
    from chimera.core.verify import CommandVerifier
    from chimera.tools import default_registry

    worker = Agent(
        backend, default_registry(workspace), AgentConfig(model=model, max_steps=max_steps)
    )
    return LifecycleCrew(
        worker,
        backend,
        verifier=CommandVerifier(verify, workspace) if verify else None,
        guard=WorkspaceGuard(workspace),
        model=model,
        max_build_attempts=max_build_attempts,
    )
