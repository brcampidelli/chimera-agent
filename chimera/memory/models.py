"""Data model for memory items across the hierarchical layers."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MemoryKind = Literal["working", "episodic", "semantic", "persona"]


#: Passed as ``project=`` to mean "do not filter by project at all".
#:
#: Distinct from ``None``, which means "only facts that belong everywhere", and that distinction is
#: the whole design. Of the four callers of ``search``, three want no filter — the Memory screen's
#: own search box, ``chimera memory search``, and the MCP tool — because they are browsing what is
#: stored rather than answering inside a folder. One wants the filter: the recall that feeds a turn.
#: Making no-filter the default is what keeps every existing caller behaving as it did.
EVERY_PROJECT = "*"


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
    #: Which project this fact belongs to, or ``None`` for one that belongs everywhere.
    #:
    #: First-class rather than a key inside ``metadata``, for the reason ``provenance`` is: it
    #: decides WHAT A RUN CAN SEE, so it has to round-trip identically through every backend or the
    #: guarantee breaks purely by which store the owner picked.
    #:
    #: ``None`` is the migration. Every fact written before this field existed has no project and is
    #: therefore global, which is what it always effectively was — nobody loses a memory by updating.
    project: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
