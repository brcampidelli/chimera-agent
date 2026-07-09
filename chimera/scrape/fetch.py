"""Unified page fetch — cheapest tool first, escalate only when needed.

Fetching a page has a cost gradient: a plain HTTP GET is nearly free, a real browser (JS render) is
heavier, and a hosted anti-bot service (Firecrawl) is heaviest. ``fetch_page`` walks that gradient:
try HTTP; if the result looks empty (a JS-rendered SPA, or a soft block) escalate to the built-in
browser; and only if that is still thin AND a Firecrawl key is set, fall back to Firecrawl. This is
the same cost-aware-cascade instinct as Chimera's fusion router, applied to fetching.

Every network step is a module-level seam (``_http_fetch`` / ``_browser_fetch`` / ``_firecrawl_fetch``)
so the whole cascade is unit-tested with fakes — no network, no browser, no key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chimera.config import Settings, get_settings
from chimera.scrape.clean import extract_links, html_to_markdown, page_title, plain_text

_UA = "Mozilla/5.0 (compatible; ChimeraAgent/1.0; +https://github.com/brcampidelli/chimera-agent)"
_THIN_CHARS = 200  # below this, an HTTP result is probably a JS shell or a soft block -> escalate


@dataclass
class FetchResult:
    """A fetched page as LLM-ready material."""

    url: str
    markdown: str
    title: str | None = None
    links: list[str] = field(default_factory=list)
    source: str = "http"  # which backend produced it: http | browser | firecrawl
    status: int | None = None


def _http_fetch(url: str, timeout: float = 20.0) -> tuple[int, str]:
    import httpx

    resp = httpx.get(url, timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA})
    return resp.status_code, resp.text


def _browser_fetch(url: str) -> str:
    """Render ``url`` in the built-in browser and return its post-JS HTML (auto-installs Chromium)."""
    from chimera.tools import browser as b

    try:
        driver = b._new_playwright_driver(headless=True)
    except Exception as exc:  # noqa: BLE001 — most likely the Chromium binary isn't downloaded yet
        if not (b._missing_browser_binary(exc) and b._auto_install_enabled()):
            raise
        b._install_chromium()
        driver = b._new_playwright_driver(headless=True)
    try:
        driver.navigate(url)
        return driver.page_html()
    finally:
        driver.close()


def _firecrawl_fetch(url: str, api_key: str) -> tuple[str, dict[str, Any]]:
    from chimera.scrape.firecrawl import firecrawl_scrape

    return firecrawl_scrape(url, api_key)


def _from_html(url: str, html: str, source: str, status: int | None = None) -> FetchResult:
    markdown = html_to_markdown(html) or plain_text(html)
    return FetchResult(
        url=url,
        markdown=markdown or "",
        title=page_title(html),
        links=extract_links(html, url),
        source=source,
        status=status,
    )


def _thin(md: str) -> bool:
    return len(md.strip()) < _THIN_CHARS


def fetch_page(url: str, *, render: str = "auto", settings: Settings | None = None) -> FetchResult:
    """Fetch ``url`` and return clean Markdown + metadata.

    ``render``: ``"auto"`` (HTTP, escalate to browser/Firecrawl if thin — the default),
    ``"http"`` (HTTP only), ``"browser"`` (force the JS-rendering browser), or ``"firecrawl"``
    (force the Firecrawl fallback; needs the key).
    """
    settings = settings or get_settings()
    fc_key = settings.firecrawl_api_key

    if render == "firecrawl":
        if not fc_key:
            return FetchResult(url, "error: render='firecrawl' needs FIRECRAWL_API_KEY", source="firecrawl")
        md, meta = _firecrawl_fetch(url, fc_key)
        return FetchResult(url, md, title=meta.get("title"), source="firecrawl", status=meta.get("statusCode"))

    if render == "browser":
        return _from_html(url, _browser_fetch(url), "browser")

    # http (and the auto cascade)
    status, html = _http_fetch(url)
    result = _from_html(url, html, "http", status)
    if render != "auto" or not _thin(result.markdown):
        return result

    # escalate: the built-in browser (JS render)
    try:
        rendered = _from_html(url, _browser_fetch(url), "browser")
        if len(rendered.markdown.strip()) > len(result.markdown.strip()):
            result = rendered
    except Exception:  # noqa: BLE001 — browser unavailable -> keep the HTTP result
        pass

    # last resort: Firecrawl, only if still thin and a key is set
    if _thin(result.markdown) and fc_key:
        try:
            md, meta = _firecrawl_fetch(url, fc_key)
            if len(md.strip()) > len(result.markdown.strip()):
                result = FetchResult(url, md, title=meta.get("title"), source="firecrawl",
                                     status=meta.get("statusCode"))
        except Exception:  # noqa: BLE001 — Firecrawl failed -> keep what we have
            pass
    return result
