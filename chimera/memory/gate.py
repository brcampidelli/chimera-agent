"""Memory admission gate — a trust boundary on recall (MemGate, 2606.06054).

Similarity recall can surface memories that are truthful but contextually wrong: a fact
that smuggles in override/injection text (a memory-based jailbreak), or one with no real
overlap with the query (spurious). The gate admits a recalled memory only if it is both
*relevant* to the query and *clean* of injection markers — turning raw similarity
retrieval into task-conditioned admission. Deterministic; no network needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from chimera.memory.models import MemoryItem
from chimera.memory.tokens import informative, tokens

# Override / prompt-injection markers a stored memory must never inject into the prompt.
_INJECTION = re.compile(
    r"ignore\s+(the\s+|all\s+|previous\s+|above\s+)*(instructions|prompts?|rules)"
    r"|disregard\s+(the\s+|all\s+|previous\s+|above\s+)*(instructions|context|rules)"
    r"|you\s+are\s+now\b"
    r"|new\s+instructions\s*:"
    r"|system\s+prompt\s*:"
    r"|</?system>"
    r"|do\s+anything\s+now\b"
    r"|reveal\s+(your|the)\s+(system\s+)?prompt",
    re.IGNORECASE,
)


def _terms(text: str) -> set[str]:
    """The pieces the recall path itself matches on (:mod:`chimera.memory.tokens`).

    The gate used its own ``[a-z0-9]+`` tokenizer and, behind the recall that found a Russian or
    Chinese fact, silently discarded it: query and fact shared zero ASCII tokens, so the floor
    read "irrelevant" for every sentence in three of the ten languages the app ships in. Recall and
    gate must agree on what a word is, or the gate overrules recall on grounds recall never used.
    """
    return {t for t in informative(tokens(text)) if len(t) >= 2 or _single_char_is_a_word(t)}


def _single_char_is_a_word(token: str) -> bool:
    """One character carries meaning only in the scripts where it is a word: Han, kana, hangul.

    The ASCII gate had ``len >= 2`` and that was right for Latin: the ``s`` of "What's" and a lone
    ``1`` are fragments, and each of them made an unrelated English fact "relevant". Dropping the
    filter wholesale for the sake of Chinese reintroduced that, so it is kept for everything else.
    """
    code = ord(token)
    return (
        0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF  # Han
        or 0x3040 <= code <= 0x30FF  # hiragana, katakana
        or 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF  # hangul
    )


@dataclass
class MemoryGate:
    """Decides whether a recalled memory may enter the prompt."""

    min_overlap: int = 1
    block_injection: bool = True

    def admit(self, item: MemoryItem, query: str) -> tuple[bool, str]:
        if self.block_injection and _INJECTION.search(item.content):
            return False, "blocked: injection/override pattern"
        if len(_terms(query) & _terms(item.content)) < self.min_overlap:
            return False, "blocked: below relevance floor"
        return True, ""

    def filter(
        self, items: list[MemoryItem], query: str, *, layer: str | None = None
    ) -> list[MemoryItem]:
        """Admitted items. ``layer`` is the retrieval layer that produced them.

        A ``"semantic"`` hit was ranked by embedding similarity, which exists to find the fact that
        shares NO word with the question; a lexical floor on top of it discards exactly what the
        layer was asked to find. There only the injection check applies.
        """
        if layer == "semantic":
            return [item for item in items if self.is_clean(item.content)]
        return [item for item in items if self.admit(item, query)[0]]

    def is_clean(self, text: str) -> bool:
        """Injection-only admission (no relevance floor), for facts recalled by a path OTHER than
        keyword similarity — e.g. entity-graph links, which intentionally may share no token with the
        query. Those still must not smuggle override/injection text into the prompt."""
        return not (self.block_injection and bool(_INJECTION.search(text)))
