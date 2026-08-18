"""Static validators for the self-modification edit surface.

Self-modification is only allowed through a *structured, statically-checkable*
surface (per AutoMegaKernel): a proposed learned skill or schedule must pass a
validator before it is accepted. This rejects unsafe proposals before they ever run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Hyphens and 64 chars, not underscores and 41. The rule was written for names the agent MINTS,
# which are snake_case — and the curated library that shipped later is kebab-case, so the validator
# refused all 23 of the project's own official cards. `chimera skills-import skills/verify-before-
# claiming`, the one line the README gives for using them, printed "Refused" and exited 0. 41 was
# also short: the longest shipped card is 57 characters.
#
# What the rule is actually for is unchanged, which is why widening it is safe: the name is used as
# a store key and as a directory name by `skills-export`, so it must stay a single path segment. A
# leading lowercase letter plus `[a-z0-9_-]` still admits no dot, slash, space or control character,
# so `..`, absolute paths and shell metacharacters remain impossible.
_SKILL_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
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

    def validate(self, data: dict[str, object]) -> ValidationResult:
        reasons: list[str] = []
        name = str(data.get("name") or "")
        if not _SKILL_NAME.fullmatch(name):
            reasons.append(
                "name must be lowercase a-z0-9 with _ or - (2-64 chars, start with a letter)"
            )

        template = str(data.get("prompt_template") or "")
        do = str(data.get("do") or "")
        check = str(data.get("check") or "")
        # An advisory card (an anti-pattern lesson, or any card with no executable
        # template) is retrieval-only: it needs Do + Check instead of a template.
        is_advisory = data.get("kind") == "anti_pattern" or (not template.strip() and do.strip())
        if is_advisory:
            if not do.strip():
                reasons.append("advisory card missing Do")
            if not check.strip():
                reasons.append("advisory card missing Check")
        elif not template.strip():
            reasons.append("prompt_template is empty")
        if len(template) > _MAX_TEMPLATE_CHARS:
            reasons.append(f"prompt_template exceeds {_MAX_TEMPLATE_CHARS} chars")

        # Scan the template AND every card field for forbidden phrases.
        scan = " ".join(
            str(data.get(field) or "")
            for field in ("prompt_template", "trigger", "do", "avoid", "check", "risk")
        ).lower()
        for phrase in _FORBIDDEN_PHRASES:
            if phrase in scan:
                reasons.append(f"forbidden phrase: {phrase!r}")

        if not str(data.get("description") or "").strip():
            reasons.append("description is empty")

        return ValidationResult(accepted=not reasons, reasons=reasons)


class ScheduleValidator:
    """Validates a cron expression for a (possibly self-proposed) schedule."""

    def validate(self, cron_expr: str) -> ValidationResult:
        from croniter import croniter

        if not croniter.is_valid(cron_expr):
            return ValidationResult(False, [f"invalid cron expression: {cron_expr!r}"])
        return ValidationResult(True)
