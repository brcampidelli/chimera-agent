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

# Grammatical glue that would otherwise match every skill's description and surface irrelevant skills
# for any task (e.g. "the"/"of" in a geography question). Filtered from both sides before scoring so
# a match means a shared *content* word. Short tokens (<3 chars) are dropped too — same purpose.
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "these", "those", "you", "your", "our", "she",
        "him", "her", "his", "its", "are", "was", "were", "been", "being", "have", "has", "had",
        "can", "could", "would", "should", "will", "shall", "may", "might", "must", "does", "did",
        "what", "which", "who", "whom", "how", "when", "where", "why", "into", "from", "then",
        "than", "them", "they", "their", "there", "here", "some", "any", "all", "each", "please",
        "give", "get", "make", "want", "need", "use", "using", "about", "back", "now", "not",
    }
)


def _tokenize(text: str) -> set[str]:
    return {tok for tok in _TOKEN.findall(text.lower()) if len(tok) >= 3 and tok not in _STOPWORDS}


def retrieve_relevant_skills(
    registry: SkillRegistry, query: str, *, k: int = 3
) -> list[Skill]:
    """Return up to ``k`` skills whose name/description best match ``query`` on shared content words."""
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
