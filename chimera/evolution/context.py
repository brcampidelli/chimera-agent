"""Shared evolution context (M19-A0) — make learning a property of the agent stack, not one command.

The self-evolution machinery (experience buffer, trajectory collection, long-term memory, learned-skill
distillation + retrieval, ACE playbook) was assembled inline only inside `chimera solve`. Every other
autonomous path — the kanban lanes, workflow steps, the SDLC lifecycle crew, the project orchestrator —
built a bare agent that neither learned nor read what was learned. ``EvolutionContext`` bundles the six
learning seams behind one factory so any caller turns the flywheel on identically; ``apply_to`` feeds
them straight into ``AutonomousAgent(**...)``, and ``record_external`` lets a non-Autonomous path (the
hierarchy's RoleAgent fan-out) still record an experience row + credit the retrieved skill cards.

Reuse spine: the factory reproduces the exact seam construction previously inline at the solve command,
so wiring it in is behaviour-preserving.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Import the seams from their submodules (not the package) so `chimera.evolution.__init__` can re-export
# this module without a circular import at load time.
from chimera.evolution.auto_evolve import AutoSkillEvolver
from chimera.evolution.card_retrieval import CardRetriever
from chimera.evolution.collective import CollectiveSkillEvolver
from chimera.evolution.evolver import SkillEvolver
from chimera.evolution.experience import ExperienceBuffer
from chimera.evolution.playbook import Playbook
from chimera.evolution.skill_store import SkillStore
from chimera.governance.validator import SkillValidator

if TYPE_CHECKING:
    from chimera.config import Settings
    from chimera.ecosystem.trajectory import TrajectoryCollector
    from chimera.governance.audit import AuditLog
    from chimera.providers import LLMGateway


@dataclass
class EvolutionContext:
    """The six learning seams an autonomous run reads from and writes to."""

    experience: ExperienceBuffer | None = None
    trajectories: TrajectoryCollector | None = None
    memory: Any = None  # a SupportsRemember (MemoryManager); kept Any so the seam stays duck-typed
    auto_evolver: AutoSkillEvolver | None = None
    cards: CardRetriever | None = None
    playbook: Playbook | None = None

    def apply_to(self) -> dict[str, Any]:
        """The six evolution kwargs to splat into ``AutonomousAgent(**...)``."""
        return {
            "experience": self.experience,
            "trajectories": self.trajectories,
            "memory": self.memory,
            "auto_evolver": self.auto_evolver,
            "cards": self.cards,
            "playbook": self.playbook,
        }

    def record_external(self, task: str, answer: str, *, success: bool) -> None:
        """Record an outcome from a NON-AutonomousAgent path (e.g. the hierarchy fan-out).

        Writes an experience lesson and credits the run to any injected skill cards — the read/write
        halves of the flywheel a bare RoleAgent run would otherwise skip. Skill DISTILLATION is
        intentionally NOT done here: it needs the verify-or-revert signal that only the solve/lifecycle
        path carries, so a fan-out run accrues lessons + card telemetry but never mints a skill.
        """
        if self.experience is not None:
            self.experience.record(task, "success" if success else "failure", detail=answer[:500])
        recorder = getattr(self.cards, "record_outcome", None)
        if callable(recorder):
            recorder(bool(success))


def build_evolution_context(
    settings: Settings,
    gateway: LLMGateway,
    model: str | None,
    *,
    home: Path,
    collect: bool = False,
    evolve_skills: bool = True,
    panel_evolution: bool = False,
    audit: AuditLog | None = None,
    memory: Any = None,
    playbook: Playbook | None = None,
    skill_cards: bool | None = None,
) -> EvolutionContext:
    """Assemble the six learning seams from settings — the single source of the flywheel wiring.

    Mirrors the inline block previously in ``chimera solve`` (A0, behaviour-preserving). ``memory`` and
    ``playbook`` are injected by the caller (their construction pulls CLI-specific helpers); the other
    four are built here. ``skill_cards=None`` uses ``settings.skill_cards`` (today's default); pass an
    explicit bool to override (A1 couples reading to ``evolve_skills``).
    """
    from chimera.ecosystem.trajectory import TrajectoryCollector

    use_cards = settings.skill_cards if skill_cards is None else skill_cards
    auto_evolver: AutoSkillEvolver | None = None
    if evolve_skills:
        auto_evolver = AutoSkillEvolver(
            SkillEvolver(gateway, model),
            SkillStore(home / "skills.json"),
            validator=SkillValidator(),
            audit=audit,
            provisional=settings.provisional_skills,
            collective=(
                CollectiveSkillEvolver(gateway, settings.fusion_panel, validator=SkillValidator())
                if panel_evolution
                else None
            ),
            accept_mode=settings.skill_accept_mode,
        )
    return EvolutionContext(
        experience=ExperienceBuffer(home / "experience.json"),
        trajectories=TrajectoryCollector(home / "trajectories.jsonl") if collect else None,
        memory=memory,
        auto_evolver=auto_evolver,
        cards=(
            CardRetriever(SkillStore(home / "skills.json"), k=settings.skill_cards_k)
            if use_cards
            else None
        ),
        playbook=playbook,
    )
