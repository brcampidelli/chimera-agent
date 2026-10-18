"""The 26 category ids shared by the three public catalogues, plus ``other``.

Copied from the canonical file every catalogue repository carries (``categories.yaml``, same list in
``chimera-plugins``, ``chimera-skills`` and ``chimera-connectors``). The id is stable — files and the
app key on it — and the label is the app's to translate, so only the ids live here. Order is the
order of the category grid.

A catalogue that names an id outside this list does not get its own tile: the item lands in
``other``. That is the safe direction — a typo or a newer catalogue never invents a category the
app has no picture or label for, and it never drops the item either.
"""

from __future__ import annotations

CATEGORY_IDS: tuple[str, ...] = (
    "engineering",
    "code",
    "pull-requests",
    "testing-quality",
    "files-documents",
    "reference-docs",
    "design-media",
    "sites-frontend",
    "pipelines-jobs",
    "servers-cloud",
    "databases",
    "apis-integrations",
    "logs-errors",
    "security",
    "tickets-tasks",
    "notes-memory",
    "email-messaging",
    "contacts-leads",
    "sales",
    "marketing",
    "finance-payments",
    "product",
    "research",
    "operations",
    "education",
    "ai-models-agents",
)

OTHER = "other"

_KNOWN = frozenset(CATEGORY_IDS)


def normalize_category(value: object) -> str:
    """A known category id, or ``other`` for anything else (unknown, empty, not a string)."""
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _KNOWN:
            return text
    return OTHER


def normalize_secondary(values: object, *, primary: str) -> list[str]:
    """The secondary ids that are known, without the primary or repeats; unknown ones are dropped.

    Dropped rather than mapped to ``other``: a secondary category only adds a tile the item also
    appears under, and ``other`` as a second home says nothing.
    """
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        cat = normalize_category(value)
        if cat != OTHER and cat != primary and cat not in out:
            out.append(cat)
    return out[:3]


__all__ = ["CATEGORY_IDS", "OTHER", "normalize_category", "normalize_secondary"]
