"""Assertions shared by tests for the sub-agent's wrapped response contract."""

from __future__ import annotations

from chimera.governance.sanitize import FENCE_CLOSE, FENCE_OPEN


def _unfenced_answer(text: str) -> str:
    """Return the entire fenced payload, requiring exactly one well-formed fence."""
    assert text.count(FENCE_OPEN) == 1
    assert text.count(FENCE_CLOSE) == 1
    header, opening, fenced = text.partition(f"{FENCE_OPEN}\n")
    assert header.startswith("Subagent status: ")
    assert opening
    answer, closing, trailer = fenced.partition(f"\n{FENCE_CLOSE}")
    assert closing and not trailer
    return answer


__all__ = ["_unfenced_answer"]
