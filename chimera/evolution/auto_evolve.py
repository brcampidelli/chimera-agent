"""Auto-evolution hook: turn a recurring success into a validated, tested skill.

Fired by the autonomous loop after a verified success. It only acts once a task
pattern has **recurred** (so one-off tasks don't spawn skills), and every candidate
clears two gates before it is kept:

1. **Governance** — the :class:`SkillValidator`'s constrained edit surface rejects an
   unsafe proposal before it is ever run.
2. **Executable smoke test** — the skill must run end-to-end and produce non-empty
   output. This is the verify-or-revert discipline applied to the agent's own skills:
   a proposal that doesn't actually work is discarded, never stored.

Learned skills are prompt templates with no code execution, so creating one is
non-destructive; the gates above keep it honest rather than guarding against harm.
"""

from __future__ import annotations

import string
from typing import TYPE_CHECKING

from chimera.eval.anytime import wilson_lower
from chimera.evolution.evolver import SkillEvolver
from chimera.evolution.learned_skill import LearnedSkill
from chimera.evolution.skill_store import SkillStore
from chimera.governance.validator import SkillValidator
from chimera.telemetry import get_logger

if TYPE_CHECKING:
    from chimera.evolution.collective import CollectiveSkillEvolver
    from chimera.governance.audit import AuditLog

_log = get_logger("evolution.auto")


def _placeholders(template: str) -> list[str]:
    return [field for _, field, _, _ in string.Formatter().parse(template) if field]


class AutoSkillEvolver:
    """Proposes, gates and stores a learned skill when a task recurs."""

    def __init__(
        self,
        evolver: SkillEvolver,
        store: SkillStore,
        *,
        validator: SkillValidator | None = None,
        min_recurrences: int = 2,
        collective: CollectiveSkillEvolver | None = None,
        min_transfer: float = 0.5,
        accept_mode: str = "point",
        provisional: bool = False,
        audit: AuditLog | None = None,
    ) -> None:
        self.evolver = evolver
        self.store = store
        self.validator = validator
        self.audit = audit
        # M18-4: when set, a clean-run skill is born 'provisional' (on measured probation) instead of
        # active — the lifecycle policy promotes it once it proves itself, or demotes it if it doesn't.
        self.provisional = provisional
        self.min_recurrences = min_recurrences
        # When a fusion panel is available, prefer a candidate proposed across the
        # panel and kept by cross-model transferability (OpenClaw-Skill) over a
        # single-model proposal. Falls back to single-model when unset.
        self.collective = collective
        self.min_transfer = min_transfer
        # "point" (raw pass fraction) or "wilson" (lower confidence bound on the
        # fraction) — the honesty upgrade that stops a lucky small-sample pass counting.
        self.accept_mode = accept_mode

    def _mark_and_store(self, skill: LearnedSkill, *, tainted: bool) -> LearnedSkill:
        """Store a skill with anti-poisoning provenance (Zombie Agents defense).

        A skill distilled during a run that consumed untrusted content is marked
        ``tainted`` and held ``pending`` — it never enters retrieval until a human
        approves it (`chimera skills-approve`). Clean runs store active as before.
        """
        if tainted:
            skill.provenance = "tainted"
            skill.status = "pending"
            _log.debug("skill %s held PENDING (tainted-run provenance)", skill.name)
            if self.audit is not None:
                self.audit.record(
                    "taint_provenance",
                    {"artifact": "skill", "name": skill.name, "action": "held_pending"},
                )
        elif self.provisional:
            skill.status = "provisional"
            _log.debug("skill %s born PROVISIONAL (on measured probation)", skill.name)
        self.store.add(skill)
        return skill

    def maybe_evolve(
        self, task: str, solution: str, prior_successes: int, *, tainted: bool = False
    ) -> LearnedSkill | None:
        """Return the kept skill, or None if not recurring / rejected / untested."""
        if prior_successes < self.min_recurrences:
            return None  # not recurring enough yet
        if self.collective is not None:
            return self._evolve_collective(task, solution, tainted=tainted)
        return self._evolve_single(task, solution, tainted=tainted)

    def maybe_evolve_failure(
        self, task: str, detail: str, prior_failures: int, *, tainted: bool = False
    ) -> LearnedSkill | None:
        """Distill a RECURRING failure into an advisory anti-pattern card.

        Gated on recurrence (so a one-off failure doesn't spawn a card) and on the
        governance validator. There is no executable smoke test — an anti-pattern card is
        advisory (injected into reasoning, never run), so a bad card can only mislead, not
        act, and the verify-or-revert loop still decides success.
        """
        if prior_failures < self.min_recurrences:
            return None
        card = self.evolver.propose_failure_card(task, detail)
        if card is None:
            return None
        if card.name in self.store:
            _log.debug("anti-pattern card %s already exists; skipping", card.name)
            return None
        if self.validator is not None and not self.validator.validate(card.to_dict()).accepted:
            _log.debug("rejected anti-pattern card %s (failed validation)", card.name)
            return None
        self._mark_and_store(card, tainted=tainted)
        _log.debug("kept anti-pattern card %s", card.name)
        return card

    def maybe_distill_correction(
        self, task: str, failed: str, passed: str, *, tainted: bool = False
    ) -> LearnedSkill | None:
        """Distill a VERIFIED failed→passed correction into an anti-pattern card (M15-B4).

        Unlike ``maybe_evolve_failure`` (heuristic, recurrence-gated), this fires on a single
        transition because it is grounded in a *verified* fix — the eval turned fail into pass, so
        the diff between the two attempts is a real correction, not a guess. Gated on the governance
        validator; a card distilled during a tainted run is held pending like any other artifact.
        """
        card = self.evolver.distill_correction(task, failed, passed)
        if card is None:
            return None
        if card.name in self.store:
            _log.debug("correction card %s already exists; skipping", card.name)
            return None
        if self.validator is not None and not self.validator.validate(card.to_dict()).accepted:
            _log.debug("rejected correction card %s (failed validation)", card.name)
            return None
        self._mark_and_store(card, tainted=tainted)
        _log.debug("kept correction card %s", card.name)
        return card

    def _evolve_single(
        self, task: str, solution: str, *, tainted: bool = False
    ) -> LearnedSkill | None:
        candidate = self.evolver.propose(task, solution)
        if candidate is None:
            return None
        if candidate.name in self.store:
            _log.debug("auto-skill %s already exists; skipping", candidate.name)
            return None

        # Gate 1 — governance: reject an unsafe proposal before it ever runs.
        if self.validator is not None and not self.validator.validate(candidate.to_dict()).accepted:
            _log.debug("rejected auto-skill %s (failed validation)", candidate.name)
            return None

        # Gate 2 — executable smoke test: the skill must run and produce output.
        test_input = {field: task for field in _placeholders(candidate.prompt_template)}
        if not self.evolver.test_skill(candidate, test_input, lambda out: bool(out.strip())):
            _log.debug("discarded auto-skill %s (failed smoke test)", candidate.name)
            return None

        self._mark_and_store(candidate, tainted=tainted)
        _log.debug("kept auto-skill %s", candidate.name)
        return candidate

    def _evolve_collective(
        self, task: str, solution: str, *, tainted: bool = False
    ) -> LearnedSkill | None:
        """Propose across the fusion panel; keep the most transferable validated skill.

        Cross-model transferability is the executable gate here — it subsumes the
        single-model smoke test, since the skill must run and produce output on the
        panel models rather than on just one.
        """
        assert self.collective is not None
        best: LearnedSkill | None = None
        best_score = -1.0
        best_frac = 0.0
        for candidate in self.collective.propose_collective(task, solution):
            if candidate.name in self.store:
                continue
            if self.validator is not None and not self.validator.validate(candidate.to_dict()).accepted:
                continue
            test_input = {field: task for field in _placeholders(candidate.prompt_template)}
            passed, n = self.collective.transfer_counts(
                candidate, test_input, lambda out: bool(out.strip())
            )
            frac = passed / n if n else 0.0
            # "wilson" gates on the lower confidence bound, so a 2/3 fluke (frac 0.67 but
            # bound ~0.21) no longer clears a 0.5 threshold; "point" is the raw fraction.
            score = wilson_lower(passed, n) if self.accept_mode == "wilson" else frac
            _log.debug(
                "collective candidate %s: %d/%d (frac=%.2f gate=%.2f mode=%s)",
                candidate.name, passed, n, frac, score, self.accept_mode,
            )
            if score > best_score:
                best, best_score, best_frac = candidate, score, frac
        if best is None or best_score < self.min_transfer:
            _log.debug("no transferable auto-skill kept (best gate=%.2f)", best_score)
            return None
        self._mark_and_store(best, tainted=tainted)
        _log.debug("kept collective auto-skill %s (frac=%.2f gate=%.2f)", best.name, best_frac, best_score)
        return best
