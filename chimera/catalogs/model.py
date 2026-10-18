"""The neutral item every catalogue is read into — the shape the desktop's catalogue screen consumes.

One shape for three repositories with three formats, so the screen has one card and one filter set.

**No usage counts, by construction.** There is no ``installs``, ``downloads`` or ``install_count``
field, and a test fails if one appears (``tests/test_pinned_catalogs.py``). Chimera is local and has
no telemetry, so any such number would be invented, and the consent screen is the worst place to
invent one. Popularity is GitHub stars **with the date they were read** — ``stars``, ``stars_at``
and ``stars_repo`` (whose stars they are: often a whole upstream repository holding many items) —
and the history fields ``added_at`` / ``updated_at`` come from the catalogue repository's git
history, recorded when the pin was bumped. A null is shown as "not known", never as zero.

``sections`` are computed on this machine from those facts and from local signals only
(:mod:`chimera.catalogs.sections`); each suggestion carries the reason in words.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr

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
    #: When ``stars`` was read (ISO date or timestamp). Never shown without it.
    stars_at: str | None = None
    #: ``owner/repo`` the stars belong to, or null when the item names no GitHub repository.
    stars_repo: str | None = None
    #: When the item's own content first appeared / last changed in the catalogue repository, at the
    #: pinned commit (UTC). Null when the pin records no history for it.
    added_at: str | None = None
    updated_at: str | None = None

    #: Whether it could be used with no secret, no account and nothing that executes — the facts the
    #: catalogue records (a connector's auth and capability, a skill's ``ships_scripts``, a plugin's
    #: components). None when the catalogue does not say. Feeds the "works without setup" section.
    _setup_free: bool | None = PrivateAttr(default=None)
    #: The item comes from the Chimera library itself rather than an upstream author.
    _own: bool = PrivateAttr(default=False)

    def mark(self, *, setup_free: bool | None, own: bool) -> CatalogItemOut:
        """Record the facts the sections read but the screen does not need as fields."""
        self._setup_free = setup_free
        self._own = own
        return self

    @property
    def setup_free(self) -> bool | None:
        return self._setup_free

    @property
    def own(self) -> bool:
        return self._own


SectionId = Literal["for_you", "no_setup", "library", "new", "updated"]


class CatalogSuggestionOut(BaseModel):
    """One item in a section, and why it is there in a sentence."""

    id: str
    reason: str


class CatalogSectionOut(BaseModel):
    """A section the screen may show. Absent when nothing qualifies — never shown empty."""

    id: SectionId
    title: str
    #: The rule, in words, so the section is never read as an opinion.
    reason: str
    items: list[CatalogSuggestionOut] = Field(default_factory=list)


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
    #: The pinned commit's date, the reference for "New" and "Updated". Null in an older pin.
    committed_at: str | None = None
    #: Sections computed on this machine (see the module docstring). Only non-empty ones appear.
    sections: list[CatalogSectionOut] = Field(default_factory=list)


__all__ = [
    "CatalogItemOut",
    "CatalogOut",
    "CatalogSectionOut",
    "CatalogSourceOut",
    "CatalogSuggestionOut",
    "SectionId",
]
