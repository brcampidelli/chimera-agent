"""Memory nudges — suggest saving preferences the user states but hasn't stored.

Low-friction personalization: when a recent message expresses a first-person preference
("I prefer async", "I always use ruff"), and nothing like it is in memory yet, surface a
gentle suggestion to save it as a persona fact. It's a pure, deterministic function — the
caller decides whether/how to show the suggestion, and it never stores anything itself.

User messages are first-person, so this detects "I <verb> ..." directly rather than reusing
the graph's third-person extractor ("Bruno prefers ..."). "Already known" is a token-overlap
check, so "I use ruff" won't re-nudge when "Bruno uses ruff" is already stored.
"""

from __future__ import annotations

import re

# First-person preference verbs (optionally preceded by an adverb like "always"/"really").
_PREFERENCE = re.compile(
    r"\bi (?:always |usually |generally |really |only |never )?"
    r"(?:prefer|like|love|use|need|want|require|avoid|dislike|hate)s?\b",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {"i", "you", "the", "a", "an", "for", "and", "to", "of", "my", "is", "are", "it"}
)
# A suggestion whose significant tokens are mostly present in a stored fact is "known".
_KNOWN_OVERLAP = 0.6


def _significant(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if len(t) >= 3 and t not in _STOPWORDS}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _is_known(phrase: str, known_token_sets: list[set[str]]) -> bool:
    tokens = _significant(phrase)
    if not tokens:
        return True  # nothing meaningful to save
    return any(len(tokens & known) / len(tokens) >= _KNOWN_OVERLAP for known in known_token_sets)


def detect_nudges(
    user_texts: list[str], known_facts: list[str], *, max_suggestions: int = 3
) -> list[str]:
    """First-person preferences stated in ``user_texts`` that aren't already in memory.

    Returns each as the stated phrase (e.g. "I prefer async code"), deduped and capped at
    ``max_suggestions``. Empty when nothing new is worth saving.
    """
    known_token_sets = [_significant(fact) for fact in known_facts]
    seen: set[str] = set()
    suggestions: list[str] = []
    for text in user_texts:
        for sentence in re.split(r"[.\n;!?]+", text):
            match = _PREFERENCE.search(sentence)
            if match is None:
                continue
            phrase = sentence[match.start() :].strip().strip(",")
            norm = _normalize(phrase)
            if norm in seen or _is_known(phrase, known_token_sets):
                continue
            seen.add(norm)
            suggestions.append(phrase)
            if len(suggestions) >= max_suggestions:
                return suggestions
    return suggestions
