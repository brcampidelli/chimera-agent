"""Example built-in skill.

A placeholder that demonstrates the :class:`~chimera.skills.base.Skill` contract.
The real built-in library (web research, file edits, git, code-fix, browser, ...)
lands in milestone M1; this keeps the scaffold testable in the meantime.
"""

from __future__ import annotations

from typing import Any

from chimera.skills.base import Skill, SkillResult


class EchoSkill(Skill):
    """Return the given text — the minimal end-to-end skill example."""

    name = "echo"
    description = "Return the provided text unchanged."
    version = "0.1.0"

    def run(self, **kwargs: Any) -> SkillResult:
        text = kwargs.get("text")
        if not isinstance(text, str):
            return SkillResult(ok=False, error="missing required string argument 'text'")
        return SkillResult(ok=True, output=text)
