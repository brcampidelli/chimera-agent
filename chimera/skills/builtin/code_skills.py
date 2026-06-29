"""Tier-1 code skills: autocomplete, point-fix, and script generation.

These are LLM-backed procedures — the "Augmented Tools" tier. They are deliberately
small and single-purpose; the agent (and, later, the evolution engine) composes and
refines them over time.
"""

from __future__ import annotations

from typing import Any

from chimera.skills.base import SkillResult
from chimera.skills.llm_skill import LLMSkill


def _require_str(kwargs: dict[str, Any], key: str) -> str | None:
    value = kwargs.get(key)
    return value if isinstance(value, str) and value.strip() else None


class CompleteCodeSkill(LLMSkill):
    """Continue a snippet of code (autocomplete)."""

    name = "complete_code"
    description = "Continue/complete a snippet of code from a prefix."
    version = "0.1.0"

    def run(self, **kwargs: Any) -> SkillResult:
        code = _require_str(kwargs, "code") or _require_str(kwargs, "prefix")
        if code is None:
            return SkillResult(ok=False, error="missing required string 'code'")
        language = _require_str(kwargs, "language") or "the same language"
        system = (
            "You are a code-completion engine. Continue the given code in "
            f"{language}. Output ONLY the continuation, no prose, no code fences."
        )
        return SkillResult(ok=True, output=self.ask(system, code))


class FixCodeSkill(LLMSkill):
    """Apply a targeted fix to code given a described problem or error."""

    name = "fix_code"
    description = "Fix a bug in code given the code and a problem/error description."
    version = "0.1.0"

    def run(self, **kwargs: Any) -> SkillResult:
        code = _require_str(kwargs, "code")
        issue = _require_str(kwargs, "issue") or _require_str(kwargs, "error")
        if code is None or issue is None:
            return SkillResult(ok=False, error="missing required strings 'code' and 'issue'")
        system = (
            "You are a precise bug-fixer. Return the corrected, complete code only — "
            "no prose, no code fences. Make the smallest change that fixes the problem."
        )
        user = f"Problem:\n{issue}\n\nCode:\n{code}"
        return SkillResult(ok=True, output=self.ask(system, user))


class GenerateScriptSkill(LLMSkill):
    """Generate a single runnable script from a description."""

    name = "generate_script"
    description = "Generate a single runnable script from a natural-language description."
    version = "0.1.0"

    def run(self, **kwargs: Any) -> SkillResult:
        description = _require_str(kwargs, "description") or _require_str(kwargs, "task")
        if description is None:
            return SkillResult(ok=False, error="missing required string 'description'")
        language = _require_str(kwargs, "language") or "python"
        system = (
            f"You generate a single, self-contained, runnable {language} script. "
            "Output ONLY the script — no prose, no code fences."
        )
        return SkillResult(ok=True, output=self.ask(system, description))
