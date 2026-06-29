"""Data model for memory items across the hierarchical layers."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MemoryKind = Literal["working", "episodic", "semantic", "persona"]


class MemoryItem(BaseModel):
    """A single unit of memory.

    ``key`` is an optional dedup/identity key; ``source`` records provenance
    (e.g. ``"hermes"`` after a migration merge).
    """

    id: str
    kind: MemoryKind = "semantic"
    content: str
    key: str | None = None
    source: str = "chimera"
    metadata: dict[str, Any] = Field(default_factory=dict)
