"""Minimal skill-context retrieval (the seed of Tier-1 RAG).

A keyword scorer over skill name + description, used to surface the few most
relevant skills for a task and inject them as context. Vector retrieval and a
richer knowledge store arrive with the memory layers (M3/M4); this keeps the
dependency surface zero for now.
"""

from __future__ import annotations

import re

from chimera.skills.base import Skill
from chimera.skills.registry import SkillRegistry

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def retrieve_relevant_skills(
    registry: SkillRegistry, query: str, *, k: int = 3
) -> list[Skill]:
    """Return up to ``k`` skills whose name/description best match ``query``."""
    terms = _tokenize(query)
    if not terms:
        return []
    scored: list[tuple[int, str, Skill]] = []
    for skill in registry.skills():
        haystack = _tokenize(f"{skill.name} {skill.description}")
        score = len(terms & haystack)
        if score:
            scored.append((score, skill.name, skill))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [skill for _, _, skill in scored[:k]]


def skills_context_block(skills: list[Skill]) -> str:
    """Format skills as a context block to inject into a prompt."""
    if not skills:
        return ""
    lines = ["Relevant skills you can use:"]
    lines += [f"- {skill.name}: {skill.description}" for skill in skills]
    return "\n".join(lines)
