"""Whole-site discovery — ``map`` (list a site's URLs) and ``crawl`` (follow links across it).

``map`` is the cheap reconnaissance step (Firecrawl's insight: list before you fetch): read the
sitemap when there is one, else scan the page's links. ``crawl`` is the BFS link-frontier that
actually fetches pages — bounded (depth + page limits), deduped, same-domain by default, and
**robots-aware** (we respect robots.txt and its crawl-delay by default — an ethical posture, per the
IBM/robots analysis, not a technical control). Every fetch reuses the Phase-1 cost-aware cascade.

Network is behind two module seams (``fetch_page`` from fetch.py, and ``_fetch_text`` here), so the
whole crawl is unit-tested with fakes — no network.
"""

from __future__ import annotations

import contextlib
import fnmatch
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from chimera.config import Settings, get_settings
from chimera.scrape.fetch import _UA, fetch_page

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)


def _fetch_text(url: str, timeout: float = 15.0) -> str:
    """Fetch a URL as raw text (sitemaps, robots.txt); '' on any failure."""
    import httpx

    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA})
        return resp.text if resp.status_code < 400 else ""
    except Exception:  # noqa: BLE001 — a missing sitemap/robots is normal, not a crash
        return ""


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _same_domain(url: str, seed: str) -> bool:
    return urlparse(url).netloc == urlparse(seed).netloc


def _matches(url: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(url, pat) for pat in patterns)


def map_site(url: str, *, search: str | None = None, limit: int = 100,
             settings: Settings | None = None) -> list[str]:
    """List a site's URLs — sitemap first (one index level deep), else the page's same-domain links."""
    settings = settings or get_settings()
    origin = _origin(url)
    found: dict[str, None] = {}

    # 1) sitemaps: robots.txt `Sitemap:` lines + the conventional /sitemap.xml
    sitemap_urls = [ln.split(":", 1)[1].strip() for ln in _fetch_text(f"{origin}/robots.txt").splitlines()
                    if ln.lower().startswith("sitemap:")]
    sitemap_urls.append(f"{origin}/sitemap.xml")
    for sm in sitemap_urls[:5]:
        locs = _LOC.findall(_fetch_text(sm))
        for loc in locs:
            if loc.endswith(".xml") and len(found) < limit:  # a sitemap index -> one level deeper
                found.update({u: None for u in _LOC.findall(_fetch_text(loc))})
            else:
                found[loc] = None
            if len(found) >= limit:
                break

    # 2) fallback: scan the page's links
    if not found:
        page = fetch_page(url, render="http", settings=settings)
        found = {link: None for link in page.links if _same_domain(link, url)}

    urls = list(found)
    if search:
        needle = search.lower()
        urls = [u for u in urls if needle in u.lower()]
    return urls[:limit]


@dataclass
class CrawledPage:
    url: str
    title: str | None
    markdown: str
    depth: int


@dataclass
class CrawlResult:
    pages: list[CrawledPage] = field(default_factory=list)  # pages fetched THIS run
    stopped_reason: str = "done"
    skipped_robots: int = 0
    resumed_from: int = 0  # pages already collected by a previous run (state resume)

    @property
    def total(self) -> int:
        return self.resumed_from + len(self.pages)


def _load_state(state_path: Path) -> tuple[set[str], list[tuple[str, int]], int]:
    """Return (visited, frontier, prior_page_count) from a saved crawl, or empty on any problem."""
    try:
        st = json.loads(state_path.read_text(encoding="utf-8"))
        visited = {str(u) for u in st.get("visited", [])}
        frontier = [(str(u), int(d)) for u, d in st.get("frontier", [])]
        pages_path = state_path.with_suffix(".jsonl")
        prior = sum(1 for _ in pages_path.open(encoding="utf-8")) if pages_path.exists() else 0
        return visited, frontier, prior
    except Exception:  # noqa: BLE001 — a corrupt/absent state file just means "start fresh"
        return set(), [], 0


def _save_state(state_path: Path, visited: set[str], frontier: list[tuple[str, int]]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"visited": sorted(visited), "frontier": frontier}), encoding="utf-8")
    tmp.replace(state_path)  # atomic — a crash mid-write never corrupts the resume point


def crawl_site(
    seed: str,
    *,
    limit: int = 20,
    max_depth: int = 2,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    same_domain: bool = True,
    respect_robots: bool = True,
    delay: float | None = None,
    state_path: Path | None = None,
    resume: bool = True,
    settings: Settings | None = None,
) -> CrawlResult:
    """BFS-crawl from ``seed``, fetching each page's clean Markdown, within the given bounds.

    If ``state_path`` is given, the frontier + visited set are checkpointed to disk after every page
    and pages are appended to a ``.jsonl`` sidecar, so a crawl interrupted at page N resumes from N+1
    (``resume=True``) instead of re-fetching. ``limit`` is the *total* page target across resumes.
    """
    settings = settings or get_settings()
    include = include or []
    exclude = exclude or []
    pages_path = state_path.with_suffix(".jsonl") if state_path else None

    result = CrawlResult()
    visited: set[str] = set()
    frontier: list[tuple[str, int]] = [(seed, 0)]
    if state_path and resume and state_path.exists():
        visited, frontier, result.resumed_from = _load_state(state_path)
        if not frontier:  # a completed crawl — nothing left to do
            result.stopped_reason = "already complete"
            return result
    elif state_path and not resume:
        for path in (state_path, pages_path):
            if path is not None:
                path.unlink(missing_ok=True)  # fresh start

    robots: RobotFileParser | None = None
    if respect_robots:
        robots = RobotFileParser()
        robots.parse(_fetch_text(f"{_origin(seed)}/robots.txt").splitlines())
        if delay is None:
            cd = robots.crawl_delay(_UA)
            delay = float(cd) if cd is not None else 0.0
    delay = delay or 0.0

    while frontier:
        if result.total >= limit:
            result.stopped_reason = f"reached page limit ({limit})"
            break
        current, depth = frontier.pop(0)
        current = current.split("#", 1)[0]  # drop fragment
        if current in visited:
            continue
        visited.add(current)
        if same_domain and not _same_domain(current, seed):
            continue
        if exclude and _matches(current, exclude):
            continue
        if include and not _matches(current, include):
            continue
        if robots is not None and not robots.can_fetch(_UA, current):
            result.skipped_robots += 1
            continue

        page = fetch_page(current, render="http", settings=settings)
        crawled = CrawledPage(current, page.title, page.markdown, depth)
        result.pages.append(crawled)
        if pages_path is not None:
            pages_path.parent.mkdir(parents=True, exist_ok=True)
            with pages_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"url": crawled.url, "title": crawled.title,
                                     "depth": crawled.depth, "markdown": crawled.markdown}) + "\n")

        if depth < max_depth:
            for link in page.links:
                target = urljoin(current, link).split("#", 1)[0]
                if target not in visited:
                    frontier.append((target, depth + 1))
        if state_path is not None:
            _save_state(state_path, visited, frontier)
        if delay:
            time.sleep(delay)

    if state_path is not None and not frontier:
        with contextlib.suppress(Exception):  # crawl finished — clear the resume checkpoint
            state_path.unlink(missing_ok=True)
    return result
