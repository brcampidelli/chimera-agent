"""Optional Firecrawl passthrough — used ONLY for pages the built-in engine can't fetch.

Chimera scrapes the great majority of the web itself (http + the built-in browser + MarkItDown), with
zero external service. But heavy anti-bot / Cloudflare pages need a proxy farm we deliberately don't
run. When ``FIRECRAWL_API_KEY`` is set, the scrape/extract tools fall back to Firecrawl's hosted
scrape for exactly those cases — an honest "use their infra only when needed", not a dependency.
Unset the key and this module is never touched.
"""

from __future__ import annotations

from typing import Any

_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"


def firecrawl_scrape(url: str, api_key: str, *, timeout: float = 45.0) -> tuple[str, dict[str, Any]]:
    """Return ``(markdown, metadata)`` for ``url`` via Firecrawl. Raises on any failure."""
    import httpx

    resp = httpx.post(
        _ENDPOINT,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"url": url, "formats": ["markdown"]},
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ValueError("firecrawl: unexpected response shape")
    markdown = str(data.get("markdown") or "")
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    return markdown, metadata
