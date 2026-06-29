"""Multi-agent communication with redundancy consolidation (MOC).

In a team, the same point often surfaces from several agents. Concatenating every
message inflates context. Following Multi-Order Communication, ``consolidate`` merges
near-duplicate messages (keeping the most recent), so downstream agents get the
evidence without the bloat — keeping context lean at scale.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN = re.compile(r"[a-z0-9]+")


@dataclass
class AgentMessage:
    """A message produced by a named agent."""

    sender: str
    content: str


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def _similarity(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def consolidate(messages: list[AgentMessage], *, threshold: float = 0.8) -> list[AgentMessage]:
    """Merge near-duplicate messages (Jaccard >= threshold), keeping the latest."""
    kept: list[AgentMessage] = []
    kept_tokens: list[set[str]] = []
    for message in messages:
        tokens = _tokens(message.content)
        merged_into: int | None = None
        for index, existing in enumerate(kept_tokens):
            if _similarity(tokens, existing) >= threshold:
                merged_into = index
                break
        if merged_into is None:
            kept.append(message)
            kept_tokens.append(tokens)
        else:
            kept[merged_into] = message  # keep the more recent phrasing
            kept_tokens[merged_into] = tokens
    return kept


def render(messages: list[AgentMessage]) -> str:
    return "\n\n".join(f"[{m.sender}] {m.content}" for m in messages)
