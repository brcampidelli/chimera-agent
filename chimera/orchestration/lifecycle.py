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

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    #: True when a cooperative stop ended the run before it reached a verdict. Distinct from
    #: ``success=False``, which means the work was judged and did not pass — reporting a stop as a
    #: failure would tell somebody their code is broken when nobody ever tested it.
    cancelled: bool = False


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
        on_stage: Callable[[StageResult], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        self.worker = worker
        self.backend = backend
        self.verifier = verifier
        self.guard = guard
        self.model = model
        self.max_build_attempts = max_build_attempts
        self.on_stage = on_stage
        self.should_stop = should_stop

    def _emit(self, stage: StageResult) -> StageResult:
        """Report a stage the moment it lands, then return it for the transcript.

        Swallows anything the sink raises. A screen that fell over while rendering a stage must not
        kill a build whose tokens are already paid for — the run is the valuable thing here and the
        display is not.
        """
        if self.on_stage is not None:
            try:
                self.on_stage(stage)
            except Exception:  # noqa: BLE001 — a broken sink is not a broken build
                _log.debug("lifecycle stage sink raised", exc_info=True)
        return stage

    def _stopped(self) -> bool:
        return bool(self.should_stop and self.should_stop())

    def run(self, task: str) -> LifecycleResult:
        from chimera.config import get_settings
        from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
        from chimera.core.planner import Planner
        from chimera.evolution import build_evolution_context

        stages: list[StageResult] = []

        # 1. plan
        plan = Planner(self.backend, self.model).plan(task)
        plan_text = plan.as_text() or "(no steps)"
        stages.append(self._emit(StageResult("plan", plan_text, bool(plan.steps))))

        # Between stages, never inside one: an in-flight model call cannot be interrupted, and a
        # build halted halfway through writing files is worse than one that finished.
        if self._stopped():
            return LifecycleResult(False, plan_text, stages, cancelled=True)

        # 2 + 3. build + test — the AutonomousAgent owns the verify-or-revert gate.
        # M19-A4: the lifecycle build is an autonomous, verified path — turn the flywheel on so
        # the SDLC crew learns (skills + memory) from what it ships, like `chimera solve` does.
        settings = get_settings()
        evo = build_evolution_context(
            settings, self.backend, self.model, home=settings.home,
            include_memory=True, include_playbook=True,
        )
        auto = AutonomousAgent(
            self.worker,
            verifier=self.verifier,
            guard=self.guard,
            **evo.apply_to(),
            config=AutonomousConfig(
                max_attempts=self.max_build_attempts, use_planner=False, use_manager=False
            ),
        )
        outcome = auto.run(f"{task}\n\nPlan:\n{plan_text}")
        stages.append(self._emit(StageResult("build", outcome.answer, bool(outcome.attempts))))
        test_note = "verified" if outcome.success else "verification failed"
        stages.append(self._emit(StageResult("test", test_note, outcome.success)))

        if self._stopped():
            return LifecycleResult(outcome.success, outcome.answer, stages, cancelled=True)

        # 4. review — advisory; the executable test already gated success
        review = RoleAgent(_REVIEWER, self.backend).act(
            task, context=f"Work:\n{outcome.answer}\n\nTest: {test_note}"
        )
        stages.append(self._emit(StageResult("review", review, True)))

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
    registry: Any | None = None,
    on_stage: Callable[[StageResult], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> LifecycleCrew:
    """Assemble a lifecycle crew wired to the real agent loop, tools and verifier.

    ``registry`` exists because this build writes files and can run shell, and a caller reachable
    from a browser has to be able to hand in a governed one. The default below is the bare
    workspace registry, which is right for a terminal — someone running ``chimera lifecycle`` in
    their own shell already has every capability the agent is being given — and wrong for anything
    that answers a request. The HTTP route passes ``assemble_registry``'s output.
    """
    from chimera.core import Agent, AgentConfig
    from chimera.core.checkpoint import WorkspaceGuard
    from chimera.core.verify import CommandVerifier
    from chimera.tools import default_registry

    tools = registry if registry is not None else default_registry(workspace)
    worker = Agent(
        backend, tools, AgentConfig(model=model, max_steps=max_steps, project_root=workspace)
    )
    return LifecycleCrew(
        worker,
        backend,
        verifier=CommandVerifier(verify, workspace) if verify else None,
        guard=WorkspaceGuard(workspace),
        model=model,
        max_build_attempts=max_build_attempts,
        on_stage=on_stage,
        should_stop=should_stop,
    )
