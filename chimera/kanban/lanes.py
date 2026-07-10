"""Concrete worker lanes that run a card through the agent stack.

* :class:`SolveLane` routes a card to the Tier-2 autonomous loop (plan + verify-or-
  revert) — the card's optional ``verify`` command is the executable gate.
* :class:`CrewLane` routes a card to a Tier-3 crew (a role pipeline).

Both build the model-backed stack lazily in ``run`` so importing the module (and the
CLI) stays cheap and never needs a key until a lane actually runs.
"""

from __future__ import annotations

from pathlib import Path

from chimera.kanban.dispatch import LaneResult
from chimera.kanban.models import KanbanCard


class SolveLane:
    """Works a card with the Tier-2 autonomous loop (verify-or-revert)."""

    def __init__(self, *, workspace: Path, model: str | None = None, max_attempts: int = 2) -> None:
        self.workspace = workspace
        self.model = model
        self.max_attempts = max_attempts

    def run(self, card: KanbanCard) -> LaneResult:
        from chimera.config import get_settings
        from chimera.core import (
            Agent,
            AgentConfig,
            AutonomousAgent,
            AutonomousConfig,
            Manager,
            Planner,
            WorkspaceGuard,
        )
        from chimera.core.verify import CommandVerifier
        from chimera.evolution import build_evolution_context
        from chimera.providers import LLMGateway
        from chimera.tools import default_registry

        gateway = LLMGateway()
        settings = get_settings()
        worker = Agent(gateway, default_registry(self.workspace), AgentConfig(model=self.model))
        # M19-A4: a lane is an autonomous path too — turn the flywheel on (experience, skills,
        # memory, playbook) so working a card learns exactly as `chimera solve` does.
        evo = build_evolution_context(
            settings, gateway, self.model, home=settings.home,
            include_memory=True, include_playbook=True,
        )
        auto = AutonomousAgent(
            worker,
            planner=Planner(gateway, self.model),
            manager=Manager(gateway, self.model),
            verifier=CommandVerifier(card.verify, self.workspace) if card.verify else None,
            guard=WorkspaceGuard(self.workspace),
            spine_workspace=self.workspace,
            **evo.apply_to(),
            config=AutonomousConfig(max_attempts=self.max_attempts),
        )
        result = auto.run(card.action)
        return LaneResult(success=result.success, answer=result.answer)


class CrewLane:
    """Works a card with a Tier-3 role crew (researcher -> critic -> writer)."""

    def __init__(self, *, model: str | None = None) -> None:
        self.model = model

    def run(self, card: KanbanCard) -> LaneResult:
        from chimera.orchestration.crew import demo_crew
        from chimera.providers import LLMGateway

        result = demo_crew(LLMGateway()).run(card.action)
        return LaneResult(success=True, answer=result.answer)
