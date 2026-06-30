"""web_search tool — the reference external-API integration (key-gated).

The template for wiring a third-party API as a tool: read the key from settings,
call the API, return text. :func:`~chimera.tools.builtin.default_registry` registers
this only when ``TAVILY_API_KEY`` is set, so the agent sees it the moment you add
the key. Brave/SerpAPI (or any provider) follow the same shape; arbitrary REST APIs
can also be imported with the OpenAPI->tool importer.
"""

from __future__ import annotations

from typing import Any

from chimera.config import get_settings
from chimera.tools.base import Tool

_TAVILY_URL = "https://api.tavily.com/search"


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web and return the top results (title, URL, snippet)."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."},
            "max_results": {"type": "integer", "description": "Max results (default 5)."},
        },
        "required": ["query"],
    }

    def run(self, **kwargs: Any) -> str:
        import httpx  # lazy import keeps tool construction cheap

        key = get_settings().tavily_api_key
        if not key:
            return "error: web_search needs TAVILY_API_KEY (set it in .env)."
        query = str(kwargs["query"])
        max_results = int(kwargs.get("max_results") or 5)
        try:
            response = httpx.post(
                _TAVILY_URL,
                json={"api_key": key, "query": query, "max_results": max_results},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            return f"error: search failed: {exc}"
        results = data.get("results", [])
        if not results:
            return f"no results for {query!r}"
        lines = [
            f"- {item.get('title', '')} — {item.get('url', '')}\n  {item.get('content', '')[:300]}"
            for item in results
        ]
        return f"Top {len(results)} results for {query!r}:\n" + "\n".join(lines)
