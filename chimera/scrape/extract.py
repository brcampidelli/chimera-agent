"""Structured extraction, done safely — schema-constrained JSON from untrusted page content.

This is Chimera's edge over Firecrawl/ScrapeGraphAI: they feed the page straight into the extraction
LLM, so a page can prompt-inject the model that fills your schema. Chimera routes extraction through
the **quarantined reader** (a tool-less model whose output is validated against a Pydantic schema), so
a hostile page can at worst produce *wrong field values* — never a new instruction or tool call. The
blast radius is the schema, not the model's obedience.

Large pages are handled map-reduce style: chunk the content, extract each chunk in quarantine, and
merge (first non-null wins per field), short-circuiting once every field is filled to cap LLM cost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chimera.governance.quarantine import QuarantinedReader, fields_schema
from chimera.providers.gateway import SupportsComplete
from chimera.server.gateway import chunk_text

_MAX_CHARS_PER_CHUNK = 12_000
_MAX_CHUNKS = 6  # cost cap: never spend more than this many quarantined calls on one extraction


@dataclass
class ExtractResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def extract_structured(
    content: str,
    fields: list[str],
    backend: SupportsComplete,
    *,
    model: str | None = None,
    max_chars: int = _MAX_CHARS_PER_CHUNK,
) -> ExtractResult:
    """Extract ``fields`` from untrusted ``content`` as validated JSON, injection-safe.

    Runs the quarantined reader over token-bounded chunks and merges the results. Returns every
    requested field (``None`` for the ones the content didn't provide).
    """
    names = [str(f).strip() for f in fields if str(f).strip()]
    if not names:
        return ExtractResult(ok=False, error="no fields requested")

    schema = fields_schema(names)
    reader = QuarantinedReader(backend, model)
    chunks = chunk_text(content, max_chars) or [""]

    merged: dict[str, Any] = {name: None for name in names}
    any_ok = False
    first_error = ""
    for chunk in chunks[:_MAX_CHUNKS]:
        result = reader.extract(chunk, schema)
        if not result.ok:
            first_error = first_error or result.error
            continue
        any_ok = True
        for name in names:
            if merged[name] is None and result.data.get(name) is not None:
                merged[name] = result.data[name]
        if all(merged[name] is not None for name in names):
            break  # every field filled — stop early, don't pay for the rest of the page

    if not any_ok:
        return ExtractResult(ok=False, data=merged, error=first_error or "no data extracted")
    return ExtractResult(ok=True, data=merged)
