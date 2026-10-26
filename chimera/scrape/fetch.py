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
from chimera.tools.final_url import clear_final_url, final_url, set_final_url

_UA = "Mozilla/5.0 (compatible; ChimeraAgent/1.0; +https://github.com/brcampidelli/chimera-agent)"
_THIN_CHARS = 200  # below this, an HTTP result is probably a JS shell or a soft block -> escalate
_MAX_BODY_BYTES = 10 * 1024 * 1024  # cap the download so a multi-GB body can't OOM the host
_MAX_REDIRECTS = 10


@dataclass
class FetchResult:
    """A fetched page as LLM-ready material."""

    url: str
    markdown: str
    title: str | None = None
    links: list[str] = field(default_factory=list)
    source: str = "http"  # which backend produced it: http | browser | firecrawl
    status: int | None = None
    html: str = ""  # raw HTML (http/browser paths only) — for deterministic CSS-selector extraction
    final_url: str | None = None


def _http_fetch(url: str, timeout: float = 20.0) -> tuple[int, str]:
    import httpx

    from chimera.scrape.ssrf import check_url

    # Follow redirects MANUALLY so every hop is SSRF-checked (a public URL can 302 to an internal
    # address / the cloud-metadata endpoint), and stream the body with a size cap so a huge response
    # can't OOM the host.
    with httpx.Client(timeout=timeout, follow_redirects=False, headers={"User-Agent": _UA}) as client:
        for _ in range(_MAX_REDIRECTS):
            check_url(url)
            with client.stream("GET", url) as resp:
                if resp.is_redirect and resp.headers.get("location"):
                    url = str(httpx.URL(url).join(resp.headers["location"]))
                    continue
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > _MAX_BODY_BYTES:
                        break
                set_final_url(url)  # the hop that supplied this body
                return resp.status_code, bytes(body).decode(resp.encoding or "utf-8", errors="replace")
    raise ValueError(f"too many redirects fetching {url!r}")


def _browser_fetch(url: str) -> str:
    """Render ``url`` in the built-in browser and return its post-JS HTML (auto-installs Chromium).

    SSRF-checked here as well as in the driver (study 24, item 1): ``render="browser"`` reached
    this function without any check — only ``_http_fetch`` had one — so ``scrape`` could render
    ``http://169.254.169.254/…`` or the sidecar. The explicit check gives a clean refusal before a
    browser is launched; the driver's route re-checks every redirect and navigation after it.
    """
    from chimera.scrape.ssrf import check_url
    from chimera.tools import browser as b

    check_url(url)
    try:
        driver = b._new_playwright_driver(headless=True)
    except Exception as exc:  # noqa: BLE001 — most likely the Chromium binary isn't downloaded yet
        if not (b._missing_browser_binary(exc) and b._auto_install_enabled()):
            raise
        b._install_chromium()
        driver = b._new_playwright_driver(headless=True)
    try:
        driver.navigate(url)
        set_final_url(str(getattr(driver, "url", "") or ""))
        return driver.page_html()
    finally:
        driver.close()


def _firecrawl_fetch(url: str, api_key: str) -> tuple[str, dict[str, Any]]:
    from chimera.scrape.firecrawl import firecrawl_scrape

    return firecrawl_scrape(url, api_key)


def _from_html(
    url: str, html: str, source: str, status: int | None = None, *, final_url: str | None = None
) -> FetchResult:
    markdown = html_to_markdown(html) or plain_text(html)
    return FetchResult(
        url=url,
        markdown=markdown or "",
        title=page_title(html),
        links=extract_links(html, url),
        source=source,
        status=status,
        html=html,
        final_url=final_url or None,  # unknown stays unknown: the caller never gets the requested URL
    )


def _thin(md: str) -> bool:
    return len(md.strip()) < _THIN_CHARS


def fetch_page(url: str, *, render: str = "auto", settings: Settings | None = None) -> FetchResult:
    """Fetch ``url`` and return clean Markdown + metadata.

    ``render``: ``"auto"`` (HTTP, escalate to browser/Firecrawl if thin — the default),
    ``"http"`` (HTTP only), ``"browser"`` (force the JS-rendering browser), or ``"firecrawl"``
    (force the Firecrawl fallback; needs the key).

    The URL of the returned page is recorded for the ledger (``chimera.tools.final_url``): the
    final URL of the result that is returned, never of an earlier attempt, and empty when the
    provider did not say which URL it read. Empty is untrusted.
    """
    clear_final_url()
    result = _fetch_page(url, render=render, settings=settings)
    set_final_url(result.final_url)
    return result


def _fetch_page(url: str, *, render: str, settings: Settings | None) -> FetchResult:
    settings = settings or get_settings()
    fc_key = settings.firecrawl_api_key

    if render == "firecrawl":
        if not fc_key:
            return FetchResult(url, "error: render='firecrawl' needs FIRECRAWL_API_KEY", source="firecrawl")
        md, meta = _firecrawl_fetch(url, fc_key)
        return _from_firecrawl(url, md, meta)

    if render == "browser":
        html = _browser_fetch(url)
        return _from_html(url, html, "browser", final_url=final_url())

    # http (and the auto cascade)
    status, html = _http_fetch(url)
    result = _from_html(url, html, "http", status, final_url=final_url())
    if render != "auto" or not _thin(result.markdown):
        return result

    # escalate: the built-in browser (JS render)
    try:
        rendered_html = _browser_fetch(url)
        rendered = _from_html(url, rendered_html, "browser", final_url=final_url())
        if len(rendered.markdown.strip()) > len(result.markdown.strip()):
            result = rendered
    except Exception:  # noqa: BLE001 — browser unavailable -> keep the HTTP result
        pass

    # last resort: Firecrawl, only if still thin and a key is set
    if _thin(result.markdown) and fc_key:
        try:
            md, meta = _firecrawl_fetch(url, fc_key)
            if len(md.strip()) > len(result.markdown.strip()):
                result = _from_firecrawl(url, md, meta)
        except Exception:  # noqa: BLE001 — Firecrawl failed -> keep what we have
            pass
    return result


def _from_firecrawl(url: str, md: str, meta: dict[str, Any]) -> FetchResult:
    """Firecrawl's own account of the page. No final URL in its metadata means unknown provenance."""
    provider_url = str(meta.get("url") or meta.get("sourceURL") or "")
    return FetchResult(
        url,
        md,
        title=meta.get("title"),
        source="firecrawl",
        status=meta.get("statusCode"),
        final_url=provider_url or None,
    )
