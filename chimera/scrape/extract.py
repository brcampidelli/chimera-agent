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

import re
from dataclasses import dataclass, field
from typing import Any

from chimera.governance.quarantine import QuarantinedReader, fields_schema
from chimera.providers.gateway import SupportsComplete
from chimera.server.gateway import chunk_text

_MAX_CHARS_PER_CHUNK = 12_000
_MAX_CHUNKS = 6  # cost cap: never spend more than this many quarantined calls on one extraction
_ATTR = re.compile(r"^(.*?)::attr\(([^)]+)\)\s*$")  # "a.link::attr(href)" -> (selector, attribute)


def extract_by_css(html: str, selectors: dict[str, str]) -> dict[str, Any] | None:
    """Deterministic, LLM-free extraction: run each field's CSS selector over the HTML.

    The crawl4ai insight — for a known page template, a CSS selector is free, repeatable and exact, so
    try it *before* paying for an LLM. Selectors take ``field: "css"`` (text of the first match) or
    ``field: "css::attr(name)"`` (an attribute). Returns ``None`` if BeautifulSoup isn't installed (the
    caller then uses the quarantined LLM path); a field with no match maps to ``None``.
    """
    try:
        from bs4 import BeautifulSoup  # ships with the `documents` extra (markitdown)
    except ImportError:
        return None
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Any] = {}
    for name, raw in selectors.items():
        attr_match = _ATTR.match(raw.strip())
        selector = attr_match.group(1).strip() if attr_match else raw.strip()
        try:
            node = soup.select_one(selector)
        except Exception:  # noqa: BLE001 — a bad selector yields no value, not a crash
            out[name] = None
            continue
        if node is None:
            out[name] = None
        elif attr_match:
            value = node.get(attr_match.group(2))
            out[name] = str(value) if value is not None else None
        else:
            out[name] = node.get_text(strip=True) or None
    return out


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
