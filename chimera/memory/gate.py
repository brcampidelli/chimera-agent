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

_WORD = re.compile(r"[a-z0-9]+")

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


def _tokens(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if len(word) >= 2}


@dataclass
class MemoryGate:
    """Decides whether a recalled memory may enter the prompt."""

    min_overlap: int = 1
    block_injection: bool = True

    def admit(self, item: MemoryItem, query: str) -> tuple[bool, str]:
        if self.block_injection and _INJECTION.search(item.content):
            return False, "blocked: injection/override pattern"
        if len(_tokens(query) & _tokens(item.content)) < self.min_overlap:
            return False, "blocked: below relevance floor"
        return True, ""

    def filter(self, items: list[MemoryItem], query: str) -> list[MemoryItem]:
        return [item for item in items if self.admit(item, query)[0]]

    def is_clean(self, text: str) -> bool:
        """Injection-only admission (no relevance floor), for facts recalled by a path OTHER than
        keyword similarity — e.g. entity-graph links, which intentionally may share no token with the
        query. Those still must not smuggle override/injection text into the prompt."""
        return not (self.block_injection and bool(_INJECTION.search(text)))
