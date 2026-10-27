"""Key-gated web search across providers, kept on the existing fetch/taint path.

Provider wire formats live in small adapters so a provider-specific API change cannot silently
alter the common result shape. The tool name remains ``web_search``: the governance ledger
classifies that name as a public fetch, and keeping it is what keeps fetched snippets tainted.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any

from chimera.config import Settings, get_settings
from chimera.tools.base import Tool

_TAVILY_URL = "https://api.tavily.com/search"
_FIRECRAWL_URL = "https://api.firecrawl.dev/v1/search"
_EXA_URL = "https://api.exa.ai/search"
_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
_PROVIDER_ORDER = ("tavily", "firecrawl", "exa", "brave")
_PROVIDER_KEYS = {
    "tavily": ("TAVILY_API_KEY", "tavily_api_key"),
    "firecrawl": ("FIRECRAWL_API_KEY", "firecrawl_api_key"),
    "exa": ("EXA_API_KEY", "exa_api_key"),
    "brave": ("BRAVE_API_KEY", "brave_api_key"),
}
_COOLDOWNS: dict[str, float] = {}
_COOLDOWN_LOCK = threading.Lock()
# 401/402/403 mean the key or the account is unusable until a human acts, so retrying within the
# hour only burns requests. The cooldown is process-wide, the same as a 429.
_REFUSED_STATUSES = frozenset({401, 402, 403})
_REFUSED_COOLDOWN_SECONDS = 3600.0
# The refused providers and when each stops being refused. Kept apart from `_COOLDOWNS` so a message
# can tell "dead until a person acts" from "rate limited for a minute".
_REFUSED_UNTIL: dict[str, float] = {}
# Appended when every configured provider is refused or missing and the browser can search Google in
# this registry: the agent switches instead of retrying a provider the cooldown will keep refusing.
_GOOGLE_HINT = " Use browser action=google_search instead."
_PROVIDER_CODE = re.compile(r"[A-Za-z0-9_]{1,40}")


class _RateLimited(Exception):
    def __init__(self, retry_after: str | None) -> None:
        self.retry_after = retry_after


class _ProviderHTTPError(Exception):
    """A non-2xx answer. Holds the status and an optional short machine code, never the body text."""

    def __init__(self, status: int, code: str | None) -> None:
        super().__init__(status)
        self.status = status
        self.code = code

    def describe(self) -> str:
        if self.status in (401, 403):
            reason = "the key was refused"
        elif self.status == 402:
            reason = "the account is out of credit (top up at the provider)"
        elif self.status >= 500:
            reason = "the provider is failing"
        else:
            reason = "the provider rejected the request"
        text = f"HTTP {self.status}, {reason}"
        return f"{text}; provider code {self.code}" if self.code else text


def _retry_delay(value: str | None, now: float) -> float:
    if value is not None:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                return max(0.0, retry_at.timestamp() - now)
            except (TypeError, ValueError, OverflowError):
                pass
    return 60.0


def _error_code(response: Any) -> str | None:
    """A short machine code such as CREDIT_EXHAUSTED, or None. The free-text detail is never read."""
    try:
        data = response.json()
    except Exception:  # a non-JSON error body has nothing safe to report beyond the status
        return None
    if not isinstance(data, dict):
        return None
    error = data.get("error")
    for candidate in (error.get("code") if isinstance(error, dict) else None, data.get("code")):
        if isinstance(candidate, str) and _PROVIDER_CODE.fullmatch(candidate):
            return candidate
    return None


def _check_response(response: Any) -> dict[str, Any]:
    if response.status_code == 429:
        raise _RateLimited(response.headers.get("Retry-After"))
    if not 200 <= response.status_code < 300:
        # Client exception text may contain a server URL or body; keep only the status and a
        # validated code so provider credentials and arbitrary response content stay out.
        raise _ProviderHTTPError(response.status_code, _error_code(response))
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("invalid search response")
    return data


def _tavily_search(key: str, query: str, limit: int) -> list[dict[str, str]]:
    import httpx

    response = httpx.post(
        _TAVILY_URL, json={"api_key": key, "query": query, "max_results": limit}, timeout=30.0,
    )
    data = _check_response(response)
    results = data.get("results", [])
    return [
        {
            "title": str(item.get("title", "")), "url": str(item.get("url", "")),
            "snippet": str(item.get("content", "")),
        }
        for item in results if isinstance(item, dict)
    ]


def _firecrawl_search(key: str, query: str, limit: int) -> list[dict[str, str]]:
    import httpx

    response = httpx.post(
        _FIRECRAWL_URL,
        headers={"Authorization": f"Bearer {key}"},
        json={"query": query, "limit": limit},
        timeout=30.0,
    )
    data = _check_response(response)
    # Firecrawl's `/v1/search` endpoint returns search hits under `data`; this mapping stays
    # isolated so any API envelope change is reviewed here rather than changing tool semantics.
    results = data.get("data", [])
    return [
        {
            "title": str(item.get("title", "")), "url": str(item.get("url", "")),
            "snippet": str(item.get("description", item.get("markdown", ""))),
        }
        for item in results if isinstance(item, dict)
    ]


def _exa_search(key: str, query: str, limit: int) -> list[dict[str, str]]:
    import httpx

    response = httpx.post(
        _EXA_URL, headers={"x-api-key": key},
        json={"query": query, "numResults": limit, "contents": {"text": True}}, timeout=30.0,
    )
    data = _check_response(response)
    return [
        {
            "title": str(item.get("title", "")), "url": str(item.get("url", "")),
            "snippet": str(item.get("text", "")),
        }
        for item in data.get("results", []) if isinstance(item, dict)
    ]


def _brave_search(key: str, query: str, limit: int) -> list[dict[str, str]]:
    import httpx

    response = httpx.get(
        _BRAVE_URL, headers={"X-Subscription-Token": key, "Accept": "application/json"},
        params={"q": query, "count": limit}, timeout=30.0,
    )
    data = _check_response(response)
    web = data.get("web", {})
    results = web.get("results", []) if isinstance(web, dict) else []
    return [
        {
            "title": str(item.get("title", "")), "url": str(item.get("url", "")),
            "snippet": str(item.get("description", "")),
        }
        for item in results if isinstance(item, dict)
    ]


_ADAPTERS: dict[str, Callable[[str, str, int], list[dict[str, str]]]] = {
    "tavily": _tavily_search,
    "firecrawl": _firecrawl_search,
    "exa": _exa_search,
    "brave": _brave_search,
}


def _configured_key(settings: Settings, provider: str) -> tuple[str, str | None]:
    env_name, attribute = _PROVIDER_KEYS[provider]
    return env_name, getattr(settings, attribute)


def _all_refused(available: list[tuple[str, str | None]]) -> bool:
    """True when every provider in `available` is inside its refused cooldown right now."""
    now = time.monotonic()
    with _COOLDOWN_LOCK:
        return bool(available) and all(_REFUSED_UNTIL.get(name, 0.0) > now for name, _ in available)


def _format_results(provider: str, query: str, results: list[dict[str, str]]) -> str:
    if not results:
        return f"No results for {query!r} (provider: {provider})."
    lines = [
        f"- {item['title']} — {item['url']}\n  {item['snippet'][:300]}" for item in results
    ]
    return f"Top {len(results)} results for {query!r} (provider: {provider}):\n" + "\n".join(lines)


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

    #: Set by the assembly that also registers the paired-Chrome browser. Returns True while that
    #: browser offers ``google_search`` in the same registry; None (the default) means no hint.
    google_fallback: Callable[[], bool] | None = None

    def _google_hint(self, available: list[tuple[str, str | None]]) -> str:
        fallback = self.google_fallback
        if fallback is None or not _all_refused(available):
            return ""
        return _GOOGLE_HINT if fallback() else ""

    def run(self, **kwargs: Any) -> str:
        settings = get_settings()
        query = str(kwargs["query"])
        limit = int(kwargs.get("max_results") or 5)
        selected = settings.websearch_provider.strip().lower()
        if selected != "auto" and selected not in _PROVIDER_ORDER:
            return "error: CHIMERA_WEBSEARCH_PROVIDER must be auto, tavily, firecrawl, exa, or brave."

        available = [
            (provider, _configured_key(settings, provider)[1])
            for provider in _PROVIDER_ORDER
            if _configured_key(settings, provider)[1]
        ]
        if not available:
            required = ", ".join(_configured_key(settings, name)[0] for name in _PROVIDER_ORDER)
            missing_hint = _GOOGLE_HINT if self.google_fallback is not None and self.google_fallback() else ""
            return f"error: web_search is unavailable; set any one of these keys: {required}.{missing_hint}"
        if selected != "auto":
            env_name, key = _configured_key(settings, selected)
            if not key:
                return f"error: provider {selected} needs {env_name}."
            available = [(selected, key)]

        now = time.monotonic()
        with _COOLDOWN_LOCK:
            ready = (
                [item for item in available if _COOLDOWNS.get(item[0], 0.0) <= now]
                if selected == "auto"
                else available
            )
            cooling = not ready
        if cooling:
            # Outside the lock: the hint reads the same lock to tell refused from rate-limited.
            hint = self._google_hint(available)
            return f"error: all configured web search providers are in cooldown; retry later.{hint}"
        # (provider, reason, was_rate_limit) for each provider `auto` moved past.
        skipped: list[tuple[str, str, bool]] = []
        for provider, key in ready:
            assert key is not None
            try:
                results = _ADAPTERS[provider](key, query, limit)
            except _RateLimited as exc:
                delay = _retry_delay(exc.retry_after, time.time())
                with _COOLDOWN_LOCK:
                    _COOLDOWNS[provider] = time.monotonic() + delay
                if selected != "auto":
                    return f"error: {provider} search rate limited; retry after {int(delay)} seconds."
                skipped.append((provider, f"rate limited for {int(delay)} seconds", True))
                continue  # `auto`: the next ready provider gets the same query
            except _ProviderHTTPError as exc:
                if exc.status in _REFUSED_STATUSES:
                    with _COOLDOWN_LOCK:
                        _COOLDOWNS[provider] = time.monotonic() + _REFUSED_COOLDOWN_SECONDS
                        _REFUSED_UNTIL[provider] = _COOLDOWNS[provider]
                    if selected != "auto":
                        return f"error: {provider} search failed: {exc.describe()}."
                    skipped.append((provider, exc.describe(), False))
                    continue
                return f"error: {provider} search failed: {exc.describe()}."
            except Exception as exc:  # provider/client failures are intentionally credential-free
                return f"error: {provider} search failed ({type(exc).__name__})."
            return _format_results(provider, query, results[:limit])
        if all(was_rate_limit for _, _, was_rate_limit in skipped):
            return "error: every ready web search provider returned HTTP 429; retry later."
        reasons = "; ".join(f"{name}: {reason}" for name, reason, _ in skipped)
        hint = self._google_hint(available)
        return f"error: no web search provider could answer ({reasons}).{hint}"
