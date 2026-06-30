"""Real step executors — map a workflow step's ``uses`` to the agent stack.

* ``run``       — a single Tier-1 model call.
* ``shell``     — a command via the configured sandbox.
* ``solve``     — Tier-2 autonomous (plan + verify-or-revert), gated by ``with.verify``.
* ``crew``      — a Tier-3 role crew.
* ``lifecycle`` — the SDLC crew (plan -> build -> test -> review).

Everything is built lazily so importing this module stays cheap and never needs a key
until a step actually runs.
"""

from __future__ import annotations

from pathlib import Path

from chimera.workflow.models import WorkflowStep
from chimera.workflow.runner import StepExecutor, StepResult

_MAX_OUTPUT = 2000


def build_executors(*, workspace: Path, model: str | None = None) -> dict[str, StepExecutor]:
    from chimera.providers import LLMGateway

    gateway = LLMGateway()

    def run_step(step: WorkflowStep) -> StepResult:
        prompt = str(step.with_.get("prompt") or step.with_.get("task") or "")
        return StepResult(True, gateway.quick(prompt, model=model))

    def shell_step(step: WorkflowStep) -> StepResult:
        from chimera.sandbox import get_sandbox

        result = get_sandbox().run(str(step.with_.get("command", "")), cwd=workspace)
        return StepResult(result.exit_code == 0, result.output[:_MAX_OUTPUT])

    def solve_step(step: WorkflowStep) -> StepResult:
        from chimera.core import (
            Agent,
            AgentConfig,
            AutonomousAgent,
            AutonomousConfig,
            Planner,
            WorkspaceGuard,
        )
        from chimera.core.verify import CommandVerifier
        from chimera.tools import default_registry

        verify = step.with_.get("verify")
        worker = Agent(gateway, default_registry(workspace), AgentConfig(model=model))
        auto = AutonomousAgent(
            worker,
            planner=Planner(gateway, model),
            verifier=CommandVerifier(str(verify), workspace) if verify else None,
            guard=WorkspaceGuard(workspace),
            config=AutonomousConfig(max_attempts=int(step.with_.get("max_attempts", 2))),
        )
        outcome = auto.run(str(step.with_.get("task", "")))
        return StepResult(outcome.success, outcome.answer)

    def crew_step(step: WorkflowStep) -> StepResult:
        from chimera.orchestration import demo_crew

        return StepResult(True, demo_crew(gateway).run(str(step.with_.get("task", ""))).answer)

    def lifecycle_step(step: WorkflowStep) -> StepResult:
        from chimera.orchestration import lifecycle_crew

        crew = lifecycle_crew(
            gateway, workspace=workspace, verify=step.with_.get("verify"), model=model
        )
        outcome = crew.run(str(step.with_.get("task", "")))
        return StepResult(outcome.success, outcome.answer)

    return {
        "run": run_step,
        "shell": shell_step,
        "solve": solve_step,
        "crew": crew_step,
        "lifecycle": lifecycle_step,
    }
