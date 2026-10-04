"""Write the stored memory out as a file the owner keeps: JSON for a machine, Markdown for a person.

Local only. Nothing here sends anything anywhere; the desktop turns the text into a download and the
terminal prints it or writes it to a path the owner names.

Two things are deliberately NOT in an export, and both for the same reason — an export leaves the
place its contents were protected in:

* **Secrets.** Every fact is passed through :func:`chimera.core.redact.redact` again on the way out.
  ``remember`` masks at write time, but facts written before that masking existed, edited by hand in
  the store, or imported by an older version never went through it, and an export is exactly where
  such a fact would be read by something other than this program.
* **``metadata``.** It is a free-form dict any writer may fill (a migration puts the source file path
  there, a run its own bookkeeping). Nothing in it is a fact about the owner, and nothing promises it
  holds no credential, so the export carries the fields that ARE the fact and its labels.

``provenance`` IS exported, and that matters more than it looks: a file without it would turn every
"learned from untrusted content" fact into an unlabelled sentence the moment someone pasted it back.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from chimera.core.redact import redact
from chimera.memory.models import MemoryItem

#: The order kinds are listed in the Markdown file: the owner's profile first, then what they know.
_KIND_ORDER = ("persona", "semantic", "episodic", "working")

EXPORT_FORMATS = ("json", "markdown")


def _record(item: MemoryItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "kind": item.kind,
        "content": redact(item.content),
        "source": item.source,
        "provenance": item.provenance,
        "project": item.project,
        "created_at": item.created_at,
    }


def export_json(items: Iterable[MemoryItem], *, exported_at: str) -> str:
    """Every fact as one JSON document. ``exported_at`` is passed in, so the output is reproducible."""
    records = [_record(item) for item in items]
    return json.dumps(
        {"format": "chimera-memory", "version": 1, "exported_at": exported_at, "facts": records},
        indent=2,
        ensure_ascii=False,
    )


def export_markdown(items: Iterable[MemoryItem], *, exported_at: str) -> str:
    """Every fact as a Markdown list, grouped by kind, with its labels beside it in words."""
    records = [_record(item) for item in items]
    lines = ["# Chimera memory", "", f"Exported {exported_at} · {len(records)} fact(s).", ""]
    kinds = list(_KIND_ORDER) + sorted({r["kind"] for r in records} - set(_KIND_ORDER))
    for kind in kinds:
        group = [r for r in records if r["kind"] == kind]
        if not group:
            continue
        lines += [f"## {kind}", ""]
        for r in group:
            labels = [f"source: {r['source']}"]
            if r["provenance"] == "tainted":
                labels.append("unverified: learned from untrusted content")
            if r["project"]:
                labels.append(f"project: {r['project']}")
            # One line per fact, so a newline inside a fact is folded rather than allowed to start
            # what would read as a second, unlabelled fact.
            text = " ".join(str(r["content"]).split())
            lines.append(f"- {text} _({'; '.join(labels)})_")
        lines.append("")
    return "\n".join(lines)


def export_memory(items: Iterable[MemoryItem], fmt: str, *, exported_at: str) -> str:
    """Dispatch on ``fmt`` (one of :data:`EXPORT_FORMATS`); anything else is a ``ValueError``."""
    if fmt == "json":
        return export_json(items, exported_at=exported_at)
    if fmt == "markdown":
        return export_markdown(items, exported_at=exported_at)
    raise ValueError(f"format must be one of {list(EXPORT_FORMATS)}")
