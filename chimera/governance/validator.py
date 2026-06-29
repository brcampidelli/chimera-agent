"""Static validators for the self-modification edit surface.

Self-modification is only allowed through a *structured, statically-checkable*
surface (per AutoMegaKernel): a proposed learned skill or schedule must pass a
validator before it is accepted. This rejects unsafe proposals before they ever run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SKILL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_MAX_TEMPLATE_CHARS = 4000
_FORBIDDEN_PHRASES = (
    "ignore previous",
    "ignore all previous",
    "rm -rf",
    "exfiltrate",
    "disable safety",
    "reveal the system prompt",
)


@dataclass
class ValidationResult:
    accepted: bool
    reasons: list[str] = field(default_factory=list)


class SkillValidator:
    """Validates a learned-skill proposal before it is kept."""

    def validate(self, data: dict[str, str]) -> ValidationResult:
        reasons: list[str] = []
        name = data.get("name", "")
        if not _SKILL_NAME.fullmatch(name):
            reasons.append("name must be snake_case (2-41 chars, start with a letter)")

        template = data.get("prompt_template", "")
        if not template.strip():
            reasons.append("prompt_template is empty")
        elif len(template) > _MAX_TEMPLATE_CHARS:
            reasons.append(f"prompt_template exceeds {_MAX_TEMPLATE_CHARS} chars")

        lowered = template.lower()
        for phrase in _FORBIDDEN_PHRASES:
            if phrase in lowered:
                reasons.append(f"forbidden phrase in template: {phrase!r}")

        if not data.get("description", "").strip():
            reasons.append("description is empty")

        return ValidationResult(accepted=not reasons, reasons=reasons)


class ScheduleValidator:
    """Validates a cron expression for a (possibly self-proposed) schedule."""

    def validate(self, cron_expr: str) -> ValidationResult:
        from croniter import croniter

        if not croniter.is_valid(cron_expr):
            return ValidationResult(False, [f"invalid cron expression: {cron_expr!r}"])
        return ValidationResult(True)
