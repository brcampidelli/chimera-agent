"""A self-authored, data-driven skill.

A ``LearnedSkill`` is a reusable prompt template the agent writes for itself after
succeeding at a task. It executes by filling its template with the call arguments
and asking a model — **no code execution**, so it is safe to create autonomously
before the governance kernel (M5) lands. It is serializable, so the evolution engine
can persist, refine and retire learned skills.
"""

from __future__ import annotations

from typing import Any

from chimera.providers.gateway import SupportsComplete
from chimera.skills.base import SkillResult
from chimera.skills.llm_skill import LLMSkill

_LEARNED_SYSTEM = (
    "Follow the instruction exactly and output only the requested result, with no preamble."
)


class LearnedSkill(LLMSkill):
    """A skill defined by a prompt template rather than hand-written code."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        prompt_template: str,
        version: str = "0.1.0",
        backend: SupportsComplete | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(backend, model)
        self.name = name
        self.description = description
        self.version = version
        self.prompt_template = prompt_template

    def run(self, **kwargs: Any) -> SkillResult:
        try:
            prompt = self.prompt_template.format(**kwargs)
        except (KeyError, IndexError) as exc:
            return SkillResult(ok=False, error=f"missing template variable: {exc}")
        return SkillResult(ok=True, output=self.ask(_LEARNED_SYSTEM, prompt))

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "prompt_template": self.prompt_template,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, str],
        *,
        backend: SupportsComplete | None = None,
        model: str | None = None,
    ) -> LearnedSkill:
        return cls(
            name=data["name"],
            description=data["description"],
            prompt_template=data["prompt_template"],
            version=data.get("version", "0.1.0"),
            backend=backend,
            model=model,
        )
