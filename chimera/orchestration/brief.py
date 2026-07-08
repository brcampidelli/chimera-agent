"""Morning brief recipe (M16-B3): topics in, one synthesized digest out.

A brief is the validated sweet spot for hierarchy — parallel, read-heavy
research. The recipe file IS the decomposition (the user already knows the
split), so no top-model decompose call is spent: topics map deterministically
to :class:`~chimera.orchestration.spec.TaskSpec` contracts and go straight to
the workers; the top model only synthesizes.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from chimera.orchestration.spec import EffortBudget, TaskSpec

DEFAULT_OUTPUT_FORMAT = (
    "3-5 concise bullets, each with the concrete finding and its source (URL or paper id)."
)
DEFAULT_SYNTHESIS = (
    "A morning brief: one short headline line per topic, then a 'Worth watching' line "
    "with the single most important cross-topic development."
)


class BriefRecipe(BaseModel):
    """The user-editable brief definition (YAML)."""

    name: str = "morning-brief"
    topics: list[str] = Field(default_factory=list)
    output_format: str = DEFAULT_OUTPUT_FORMAT
    synthesis: str = DEFAULT_SYNTHESIS
    boundaries: str = "Research only; do not modify files. Prefer recent, sourced findings."


def load_brief(path: Path) -> BriefRecipe:
    """Load and validate a brief recipe from YAML (raises ValueError on a bad file)."""
    import yaml

    raw = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"brief recipe {path} must be a YAML mapping")
    recipe = BriefRecipe.model_validate(data)
    if not recipe.topics:
        raise ValueError(f"brief recipe {path} has no topics")
    return recipe


def specs_from_brief(recipe: BriefRecipe, *, max_tokens: int = 8_000) -> list[TaskSpec]:
    """Deterministic decomposition: one contract per topic (no model call)."""
    budget = EffortBudget(max_tokens=max_tokens)
    return [
        TaskSpec(
            task_id=f"topic-{i + 1}",
            objective=f"Research the topic: {topic}",
            output_format=recipe.output_format,
            boundaries=recipe.boundaries,
            effort=budget,
        )
        for i, topic in enumerate(recipe.topics)
    ]


def brief_task(recipe: BriefRecipe) -> str:
    """The synthesis-facing task description."""
    lines = ", ".join(recipe.topics)
    return f"Produce {recipe.synthesis}\nTopics researched: {lines}"
