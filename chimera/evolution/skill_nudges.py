"""Skill nudges — suggest encapsulating a recurring procedure as a reusable skill.

The skill analogue of memory nudges: when the same kind of request recurs across a session
and no existing skill covers it, surface a gentle suggestion to save it as a skill. It's a
pure, deterministic function (reuses the token-Jaccard clustering) — the caller decides
whether to act. It never creates a skill itself; that's the autonomous
:class:`~chimera.evolution.auto_evolve.AutoSkillEvolver`'s job. This is the low-friction,
opt-in surface for the human-in-the-loop path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from chimera.memory.consolidate import group_similar

_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {"i", "you", "the", "a", "an", "for", "and", "to", "of", "my", "is", "are", "it", "me", "please"}
)
# A task whose significant tokens are mostly covered by a known skill isn't worth nudging.
_KNOWN_OVERLAP = 0.6


def _significant(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if len(t) >= 3 and t not in _STOPWORDS}


@dataclass(frozen=True)
class SkillNudge:
    task: str  # a representative phrasing of the recurring task
    count: int  # how many similar requests were seen


def detect_skill_nudges(
    tasks: list[str],
    known_skills: list[str],
    *,
    min_recurrences: int = 2,
    threshold: float = 0.5,
    max_suggestions: int = 2,
) -> list[SkillNudge]:
    """Recurring task clusters (>= ``min_recurrences``) not already covered by a skill.

    ``known_skills`` are name/description strings of existing skills; a cluster whose tokens
    are mostly covered by one is skipped. Returns at most ``max_suggestions`` nudges.
    """
    known_sets = [_significant(skill) for skill in known_skills]
    suggestions: list[SkillNudge] = []
    for group in group_similar(tasks, threshold=threshold):
        if len(group) < min_recurrences:
            continue
        representative = tasks[group[0]]
        tokens = _significant(representative)
        if not tokens:
            continue
        if any(len(tokens & known) / len(tokens) >= _KNOWN_OVERLAP for known in known_sets):
            continue  # an existing skill already covers this
        suggestions.append(SkillNudge(task=representative, count=len(group)))
        if len(suggestions) >= max_suggestions:
            break
    return suggestions
