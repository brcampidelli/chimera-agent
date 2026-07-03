"""A self-authored, data-driven skill.

A ``LearnedSkill`` is a reusable procedure the agent writes for itself after solving
(or failing) a task. It carries an optional TRS-style *reasoning card* — the five
fields Trigger / Do / Avoid / Check / Risk plus retrieval ``triggers`` — distilled
from experience, and optionally an executable ``prompt_template``. A card with a
template runs by filling the template and asking a model (**no code execution**); a
card without one is *advisory* (an anti-pattern lesson) that is retrieved and injected
into reasoning but never executed. It is serializable, so the evolution engine can
persist, refine and retire learned skills.
"""

from __future__ import annotations

from typing import Any, Literal

from chimera.providers.gateway import SupportsComplete
from chimera.skills.base import SkillResult
from chimera.skills.llm_skill import LLMSkill

SkillKind = Literal["pattern", "anti_pattern"]

_LEARNED_SYSTEM = (
    "Follow the instruction exactly and output only the requested result, with no preamble."
)
_CARD_FIELDS = ("Trigger", "Do", "Avoid", "Check", "Risk")


class LearnedSkill(LLMSkill):
    """A skill defined by a reasoning card and/or a prompt template, not hand-written code."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        prompt_template: str = "",
        version: str = "0.1.0",
        trigger: str = "",
        do: str = "",
        avoid: str = "",
        check: str = "",
        risk: str = "",
        triggers: list[str] | None = None,
        kind: SkillKind = "pattern",
        backend: SupportsComplete | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(backend, model)
        self.name = name
        self.description = description
        self.version = version
        self.prompt_template = prompt_template
        self.trigger = trigger
        self.do = do
        self.avoid = avoid
        self.check = check
        self.risk = risk
        self.triggers = list(triggers or [])
        self.kind = kind

    def run(self, **kwargs: Any) -> SkillResult:
        if not self.prompt_template:
            return SkillResult(ok=False, error="advisory card: no executable template")
        try:
            prompt = self.prompt_template.format(**kwargs)
        except (KeyError, IndexError) as exc:
            return SkillResult(ok=False, error=f"missing template variable: {exc}")
        return SkillResult(ok=True, output=self.ask(_LEARNED_SYSTEM, prompt))

    def card_text(self, max_lines: int = 6) -> str:
        """Render the five card fields as a few-bullet block, budget-capped for injection."""
        values = (self.trigger, self.do, self.avoid, self.check, self.risk)
        lines = [
            f"- {label}: {value.strip()}"
            for label, value in zip(_CARD_FIELDS, values, strict=True)
            if value.strip()
        ]
        if not lines:  # a template-only skill with no card: fall back to its description
            return f"- {self.description.strip()}"
        return "\n".join(lines[:max_lines])

    def has_card(self) -> bool:
        return bool(self.do.strip() or self.check.strip())

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "prompt_template": self.prompt_template,
            "kind": self.kind,
            "trigger": self.trigger,
            "do": self.do,
            "avoid": self.avoid,
            "check": self.check,
            "risk": self.risk,
            "triggers": self.triggers,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, object],
        *,
        backend: SupportsComplete | None = None,
        model: str | None = None,
    ) -> LearnedSkill:
        raw_triggers = data.get("triggers", [])
        triggers = [str(t) for t in raw_triggers] if isinstance(raw_triggers, list) else []
        kind: SkillKind = "anti_pattern" if data.get("kind") == "anti_pattern" else "pattern"
        return cls(
            name=str(data["name"]),
            description=str(data["description"]),
            prompt_template=str(data.get("prompt_template", "")),
            version=str(data.get("version", "0.1.0")),
            trigger=str(data.get("trigger", "")),
            do=str(data.get("do", "")),
            avoid=str(data.get("avoid", "")),
            check=str(data.get("check", "")),
            risk=str(data.get("risk", "")),
            triggers=triggers,
            kind=kind,
            backend=backend,
            model=model,
        )
