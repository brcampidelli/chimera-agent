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

from chimera.evolution.evolver import SkillEvolver
from chimera.evolution.learned_skill import LearnedSkill
from chimera.evolution.skill_store import SkillStore
from chimera.governance.validator import SkillValidator
from chimera.telemetry import get_logger

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
    ) -> None:
        self.evolver = evolver
        self.store = store
        self.validator = validator
        self.min_recurrences = min_recurrences

    def maybe_evolve(self, task: str, solution: str, prior_successes: int) -> LearnedSkill | None:
        """Return the kept skill, or None if not recurring / rejected / untested."""
        if prior_successes < self.min_recurrences:
            return None  # not recurring enough yet

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

        self.store.add(candidate)
        _log.debug("kept auto-skill %s", candidate.name)
        return candidate
