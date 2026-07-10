"""Turn fetched HTML into LLM-ready material: clean Markdown, the title, and resolved links.

The single biggest win in AI scraping (crawl4ai's ``fit_markdown``, Firecrawl's markdown output) is
to *clean before you reason* — feed the model structured Markdown, not raw HTML, so extraction is
cheaper and more accurate. We reuse the MarkItDown seam Chimera already ships (the ``documents``
extra) and fall back to a stdlib text strip when it's absent, so nothing here needs a new dependency.
"""

from __future__ import annotations

import contextlib
from html.parser import HTMLParser
from urllib.parse import urljoin


def html_to_markdown(html: str) -> str | None:
    """Rendered HTML -> clean Markdown via MarkItDown; ``None`` when the ``documents`` extra is absent
    or conversion fails (the caller then uses :func:`plain_text`)."""
    from chimera.tools.browser import _html_to_markdown  # the shared MarkItDown-via-tempfile seam

    return _html_to_markdown(html)


class _TextExtractor(HTMLParser):
    """Minimal stdlib HTML→visible-text: drops script/style, keeps text and block breaks."""

    _SKIP = {"script", "style", "noscript", "template", "svg"}
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        # collapse runs of blank lines / spaces
        lines = [ln.strip() for ln in raw.splitlines()]
        out: list[str] = []
        for ln in lines:
            if ln or (out and out[-1]):
                out.append(ln)
        return "\n".join(out).strip()


def plain_text(html: str) -> str:
    """Visible text from HTML using only the stdlib (fallback when MarkItDown isn't installed)."""
    parser = _TextExtractor()
    with contextlib.suppress(Exception):  # a malformed page is not a crash
        parser.feed(html)
    return parser.text()


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and self.title is None and data.strip():
            self.title = data.strip()


def page_title(html: str) -> str | None:
    p = _TitleParser()
    with contextlib.suppress(Exception):
        p.feed(html)
    return p.title


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)


def extract_links(html: str, base_url: str, *, max_links: int = 200) -> list[str]:
    """Absolute http(s) links found on the page (relative URLs resolved against ``base_url``)."""
    p = _LinkParser()
    with contextlib.suppress(Exception):
        p.feed(html)
    seen: dict[str, None] = {}
    for href in p.hrefs:
        if href.startswith(("mailto:", "javascript:", "#", "tel:")):
            continue
        absolute = urljoin(base_url, href.strip())
        if absolute.startswith(("http://", "https://")) and absolute not in seen:
            seen[absolute] = None
        if len(seen) >= max_links:
            break
    return list(seen)
