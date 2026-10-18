"""The neutral item every catalogue is read into — the shape the desktop's catalogue screen consumes.

One shape for three repositories with three formats, so the screen has one card and one filter set.

**No usage counts, by construction.** There is no ``installs``, ``downloads`` or ``install_count``
field, and a test fails if one appears (``tests/test_pinned_catalogs.py``). Chimera is local and has
no telemetry, so any such number would be invented, and the consent screen is the worst place to
invent one. Popularity, when it comes (plan M4), is GitHub stars **with the date they were read** —
``stars`` and ``stars_at`` — and the history fields ``added_at`` / ``updated_at`` come from the
catalogue repository's git history. All four are null until M4 fills them; a null is shown as
"not known", never as zero.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from chimera.catalogs.pins import CatalogKind


class CatalogSourceOut(BaseModel):
    """Where an item was read: the pinned repository, commit and file."""

    owner: str
    repo: str
    sha: str
    path: str


class CatalogItemOut(BaseModel):
    id: str
    kind: CatalogKind
    name: str
    description: str = ""
    author: str = ""
    #: The SPDX expression the catalogue records, or null when it records none.
    license: str | None = None
    #: One of the 26 shared ids, or ``other``.
    category: str
    categories_secondary: list[str] = Field(default_factory=list)
    source: CatalogSourceOut
    #: Whether today's Chimera could install it at all. Reading the catalogue installs nothing.
    installable: bool
    #: Why not, in words, when ``installable`` is false.
    refusal: str | None = None
    stars: int | None = None
    stars_at: str | None = None
    added_at: str | None = None
    updated_at: str | None = None


class CatalogOut(BaseModel):
    """One catalogue as read now. Never an error: an unreadable catalogue is empty with a reason."""

    kind: CatalogKind
    #: ``owner/repo`` and the pinned commit the items come from.
    repo: str
    sha: str
    items: list[CatalogItemOut] = Field(default_factory=list)
    #: False when nothing could be read (offline with nothing cached, or every file refused).
    available: bool
    #: Why the catalogue is empty or partial, in a sentence, or null when it is whole.
    reason: str | None = None
    #: One line per file or entry that was refused or skipped, so a partial catalogue says what it lacks.
    problems: list[str] = Field(default_factory=list)


__all__ = ["CatalogItemOut", "CatalogOut", "CatalogSourceOut"]
