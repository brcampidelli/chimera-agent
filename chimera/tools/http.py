"""HTTP fetch tool (read-only GET) for simple web access and RAG ingestion."""

from __future__ import annotations

from typing import Any

from chimera.tools.base import Tool

_MAX_BODY_CHARS = 20_000
_DEFAULT_TIMEOUT = 30.0


class HttpGetTool(Tool):
    name = "http_get"
    description = "Fetch a URL with an HTTP GET and return status + body text."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to fetch (http/https)."},
            "timeout": {"type": "number", "description": "Timeout in seconds (default 30)."},
        },
        "required": ["url"],
    }

    def run(self, **kwargs: Any) -> str:
        import httpx  # lazy import keeps tool construction cheap

        url = str(kwargs["url"])
        timeout = float(kwargs.get("timeout") or _DEFAULT_TIMEOUT)
        try:
            response = httpx.get(url, timeout=timeout, follow_redirects=True)
        except httpx.HTTPError as exc:
            return f"error: request failed: {exc}"
        body = response.text
        if len(body) > _MAX_BODY_CHARS:
            body = body[:_MAX_BODY_CHARS] + f"\n... [truncated, {len(body)} chars total]"
        return f"[{response.status_code}] {url}\n{body}"
