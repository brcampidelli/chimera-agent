"""Data model for memory items across the hierarchical layers."""

from __future__ import annotations

from pathlib import Path
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


def project_key(workspace: str | Path | None) -> str | None:
    """The one string a folder is filed under in ``project=``: absolute and normalised.

    The writer and the reader have to agree on this and they did not. ``chimera solve`` stored
    ``project=str(Path(workspace))`` — literally ``"."`` for a run in the current directory — while
    the coding turn recalled with ``str(Path(req.workspace).expanduser().resolve())``. Two strings
    for one folder means the scoped read matches nothing the scoped write produced, and the symptom
    is not an error: it is a memory that is simply never recalled, in the one place it was written
    for. Scoping recall without fixing this would have moved the defect rather than closed it.

    ``None`` in, ``None`` out — and so is a blank string, because "no workspace" reaches this from a
    default argument as often as it does from a missing one. ``None`` means a fact that belongs
    everywhere, which is what every fact written before the field existed is.
    """
    if workspace is None or not str(workspace).strip():
        return None
    return str(Path(workspace).expanduser().resolve())


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
    created_at: float | None = None
    """When this fact was written, as epoch seconds, or ``None`` for one written before the field.

    ``None`` is the migration, exactly as ``project`` above: a memory that predates this has no age
    and must not be given a plausible one — a fact stamped with the moment of the upgrade would
    read as written today, which is the opposite of what it is.

    Recency had no source before this. :func:`chimera.memory.value.rank` uses POSITION in the list
    as the proxy and says so, and nothing could tell a reader that a `file:line` citation in a
    six-month-old memory may have moved — the age was simply not recorded."""

    metadata: dict[str, Any] = Field(default_factory=dict)
