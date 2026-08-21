"""Concrete worker lanes that run a card through the agent stack.

* :class:`SolveLane` routes a card to the Tier-2 autonomous loop (plan + verify-or-
  revert) — the card's optional ``verify`` command is the executable gate.
* :class:`CrewLane` routes a card to a Tier-3 crew (a role pipeline).
* :class:`AgentLane` routes a card to one of the user's own registered agents.

All build the model-backed stack lazily in ``run`` so importing the module (and the
CLI) stays cheap and never needs a key until a lane actually runs.

``AgentLane`` is the one that makes ``lane`` mean something a person chose. ``KanbanCard.lane`` has
always been a free string and :func:`~chimera.kanban.dispatch.dispatch` has always taken its runners
as an injected mapping, so an agent id IS a lane name — the dispatcher needs no change, and never
learns what lanes exist.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.kanban.dispatch import LaneResult
from chimera.kanban.models import KanbanCard

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.core.registry import AgentDef
    from chimera.kanban.dispatch import LaneRunner


def _refusals(approvals: Any) -> str:
    """A line naming what governance refused, or "" when it refused nothing.

    Read defensively: this runs on an unattended path, and a lane that crashed while reporting what
    it had been denied would be worse than one that reported nothing at all.
    """
    try:
        denied = [e for e in approvals.entries() if getattr(e, "decision", "") == "deny"]
    except Exception:  # noqa: BLE001 -- see the docstring
        return ""
    if not denied:
        return ""
    names = sorted({str(getattr(e, "tool", "") or "?") for e in denied})
    return f"[governance refused {len(denied)} call(s): {', '.join(names)}]"


class SolveLane:
    """Works a card with the Tier-2 autonomous loop (verify-or-revert)."""

    def __init__(self, *, workspace: Path, model: str | None = None, max_attempts: int = 2) -> None:
        self.workspace = workspace
        self.model = model
        self.max_attempts = max_attempts

    def for_workspace(self, workspace: Path) -> SolveLane:
        """The same lane bound to another directory — how a card gets run in isolation.

        Parallel dispatch gives each card its own git worktree, and a lane that writes files has to
        be told about it or every card would edit the one real workspace at once: two autonomous
        loops running plan / execute / verify-or-revert over the same files, each reverting work the
        other had just done. Rebinding is a new instance, not a mutation, because the caller keeps
        using the original for the next batch.
        """
        return SolveLane(workspace=workspace, model=self.model, max_attempts=self.max_attempts)

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
        from chimera.governance.profile import governed_profile
        from chimera.providers import LLMGateway
        from chimera.tools import default_registry

        gateway = LLMGateway()
        settings = get_settings()
        # Through the deployment's governance, like every other autonomous surface. It was not, and
        # the build gate that exists to make this structural reported green anyway: three classes in
        # this file have a method called `run`, the gate's key carried only function names, and so
        # the single exemption written for `AgentLane.run` — which really does restrict its registry
        # — silently covered this line too. A card in the `solve` lane therefore ran a full
        # autonomous loop with shell, file writes, code execution and network, outside any
        # allowlist, denylist, trust kernel or taint ledger, reachable from the Tasks screen. An
        # owner's `CHIMERA_TOOL_DENYLIST` did not reach it.
        #
        # Its own surface name, not the CLI's: the audit log has to be able to say which entrance a
        # run came through, and a board dispatch is not a person typing `chimera solve`.
        registry, approvals = governed_profile(
            default_registry(self.workspace),
            settings=settings,
            home=settings.home,
            surface="kanban-solve",
        )
        worker = Agent(
            gateway,
            registry,
            # The card's workspace, in both arguments: it roots the tools and it carries the
            # project's conventions. A lane is an autonomous path, so nobody is there to
            # restate them.
            AgentConfig(model=self.model, project_root=self.workspace),
        )
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
        # The refusals travel with the answer. `governed_profile` says in its own docstring
        # that a caller who throws the approvals away cannot tell "the job did its work" from
        # "the job was not allowed to" — and on a board nobody is watching, that difference is
        # the whole report.
        refused = _refusals(approvals)
        answer = result.answer + ("\n\n" + refused if refused else "")
        return LaneResult(success=result.success, answer=answer)


class CrewLane:
    """Works a card with a Tier-3 role crew (researcher -> critic -> writer)."""

    def __init__(self, *, model: str | None = None) -> None:
        self.model = model

    def run(self, card: KanbanCard) -> LaneResult:
        from chimera.orchestration.crew import demo_crew
        from chimera.providers import LLMGateway

        result = demo_crew(LLMGateway()).run(card.action)
        return LaneResult(success=True, answer=result.answer)


class AgentLane:
    """Works a card with one of YOUR agents — its instructions, its model, its tools.

    The autonomous loop underneath is exactly ``SolveLane``'s: plan, execute, verify-or-revert, and
    the card's ``verify`` command is still the executable gate. What the agent adds is who is doing
    it, and that is the whole difference between "run this" and "have the reviewer run this".

    Its allowlist is enforced by filtering the registry BEFORE the loop, not by asking the prompt to
    behave — a tool that is absent from the schema cannot be called, and a tool the model was merely
    told not to use can. That enforcement already existed in ``Role``; this reuses it rather than
    writing a second one that agrees until the day it does not.
    """

    def __init__(
        self, agent: AgentDef, *, workspace: Path, model: str | None = None, max_attempts: int = 2
    ) -> None:
        self.agent = agent
        self.workspace = workspace
        # The lane's model is the fallback, and the agent's pin wins over it: someone who pinned a
        # model on an agent said something more specific than the flag that dispatched the board.
        self.model = agent.model or model
        self.max_attempts = max_attempts

    def for_workspace(self, workspace: Path) -> AgentLane:
        """The same agent bound to another directory. See ``SolveLane.for_workspace``."""
        return AgentLane(
            self.agent, workspace=workspace, model=self.model, max_attempts=self.max_attempts
        )

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
        from chimera.core.registry import as_role
        from chimera.core.verify import CommandVerifier
        from chimera.evolution import build_evolution_context
        from chimera.governance import restrict_registry
        from chimera.providers import LLMGateway
        from chimera.tools import default_registry

        gateway = LLMGateway()
        settings = get_settings()
        role = as_role(self.agent)
        # The SAME restriction the coding turn and the runs path use, not a second one that
        # agrees with it until the day it does not. `Role.allowed_tools` and `restrict_registry`
        # already share the convention: None keeps every tool, a list keeps only those.
        registry = restrict_registry(default_registry(self.workspace), allow=role.allowed_tools)
        worker = Agent(
            gateway,
            registry,
            AgentConfig(
                model=self.model,
                # The agent's own instructions, appended the same way the conversational agent's
                # are: after the default prompt, never replacing it. A worker that could delete the
                # untrusted-data fence by being given a personality is not a worker anyone should
                # be able to define from a text field.
                instructions=role.system_prompt,
                project_root=self.workspace,
            ),
        )
        # Same flywheel as every other autonomous path: working a card learns exactly as
        # `chimera solve` does, whichever agent worked it.
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


def runners_for(
    agents: Iterable[AgentDef], *, workspace: Path, model: str | None = None
) -> dict[str, LaneRunner]:
    """One runner per registered agent, keyed by its id — which is what a card's ``lane`` names.

    Built here rather than in the CLI so the API can dispatch the same board with the same lanes; a
    second construction site is a second answer to "which agents exist", and they would diverge on
    the first change.
    """
    return {
        agent.id: AgentLane(agent, workspace=workspace, model=model) for agent in agents
    }
