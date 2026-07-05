"""Data model for memory items across the hierarchical layers."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MemoryKind = Literal["working", "episodic", "semantic", "persona"]


class MemoryItem(BaseModel):
    """A single unit of memory.

    ``key`` is an optional dedup/identity key; ``source`` records the origin app
    (e.g. ``"hermes"`` after a migration merge). ``provenance`` records trust: a fact
    written during a run that consumed untrusted content is ``"tainted"`` — recall
    surfaces that origin so a poisoned memory can't masquerade as a verified one.
    """

    id: str
    kind: MemoryKind = "semantic"
    content: str
    key: str | None = None
    source: str = "chimera"
    provenance: str = "clean"
    metadata: dict[str, Any] = Field(default_factory=dict)
