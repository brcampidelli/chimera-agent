"""Collective skill evolution (OpenClaw-Skill, 2606.16774).

A single model proposing a skill has single-model bias and may not transfer. This
evolver proposes a candidate skill from **each model of the fusion panel**, then keeps
the one that **transfers best** — i.e. that actually runs and passes its check across the
most panel models — gated by the same governance validator. Cross-model agreement is the
quality signal, exactly as the panel is for fusion reasoning.
"""

from __future__ import annotations

from collections.abc import Callable

from chimera.evolution.evolver import SkillEvolver
from chimera.evolution.learned_skill import LearnedSkill
from chimera.governance.validator import SkillValidator
from chimera.providers.gateway import SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("evolution.collective")


class CollectiveSkillEvolver:
    """Proposes skills across a model panel and keeps the most transferable one."""

    def __init__(
        self,
        backend: SupportsComplete,
        panel_models: list[str],
        *,
        transfer_models: list[str] | None = None,
        validator: SkillValidator | None = None,
    ) -> None:
        self.backend = backend
        self.panel_models = panel_models
        # Proposing and testing are different jobs with different budgets. Strong models write
        # better candidates; testing only asks "does it run and pass elsewhere", which cheap models
        # answer just as well — and more of them is what makes the acceptance statistic mean
        # anything (n=3 cannot support a 0.5 lower-bound gate; see chimera.eval.anytime).
        self.transfer_models = list(transfer_models) if transfer_models else list(panel_models)
        self.validator = validator

    def propose_collective(self, task: str, solution: str) -> list[LearnedSkill]:
        """Propose one candidate skill per panel model, deduped by name."""
        candidates: list[LearnedSkill] = []
        seen: set[str] = set()
        for model in self.panel_models:
            skill = SkillEvolver(self.backend, model).propose(task, solution)
            if skill is not None and skill.name not in seen:
                seen.add(skill.name)
                candidates.append(skill)
        return candidates

    def transfer_counts(
        self, skill: LearnedSkill, test_input: dict[str, str], check: Callable[[str], bool]
    ) -> tuple[int, int]:
        """(passed, n): how many transfer models the skill runs+passes on, out of that panel.

        A model that is unreachable (deprecated, rate-limited, no credit) fails its call, and
        :meth:`Skill.execute` turns every exception into ``ok=False`` — so infrastructure trouble is
        indistinguishable here from a skill that genuinely did not transfer. That is deliberate: it
        can only make the gate *stricter*, never more permissive, so the worst case is rejecting a
        good skill rather than accepting a bad one. Excluding failed calls from ``n`` would be worse
        — a skill that runs on 1 of 9 models would score a perfect 1/1.
        """
        if not self.transfer_models:
            return (0, 0)
        passed = 0
        for model in self.transfer_models:
            variant = LearnedSkill.from_dict(skill.to_dict(), backend=self.backend, model=model)
            result = variant.execute(**test_input)
            if result.ok and check(result.output):
                passed += 1
        return passed, len(self.transfer_models)

    def transferability(
        self, skill: LearnedSkill, test_input: dict[str, str], check: Callable[[str], bool]
    ) -> float:
        """Fraction of panel models on which the skill runs and passes its check."""
        passed, n = self.transfer_counts(skill, test_input, check)
        return passed / n if n else 0.0

    def evolve_collective(
        self,
        task: str,
        solution: str,
        *,
        test_input: dict[str, str],
        check: Callable[[str], bool],
        min_transfer: float = 0.5,
    ) -> tuple[LearnedSkill, float] | None:
        """Return the best-transferable validated candidate (with its score), or None."""
        best: LearnedSkill | None = None
        best_score = -1.0
        for candidate in self.propose_collective(task, solution):
            if self.validator is not None and not self.validator.validate(candidate.to_dict()).accepted:
                continue
            score = self.transferability(candidate, test_input, check)
            if score > best_score:
                best_score, best = score, candidate
        if best is None or best_score < min_transfer:
            return None
        _log.debug("kept collective skill %s (transfer=%.2f)", best.name, best_score)
        return best, best_score
