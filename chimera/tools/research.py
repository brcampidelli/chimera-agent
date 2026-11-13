"""Reference research tools: arxiv_search (no key, no dependency) and youtube_transcript.

``arxiv_search`` hits the public arXiv Atom API and parses it with the stdlib — always on.
``youtube_transcript`` needs the optional ``youtube-transcript-api`` (the ``youtube``
extra); it returns a helpful message when the library or a transcript is unavailable
(YouTube frequently blocks caption scraping, so failures are expected and handled).
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any
from xml.etree import ElementTree

from chimera.tools.base import Tool

_ARXIV_URL = "https://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"
_MAX_OUTPUT_CHARS = 20_000
#: arXiv asks API clients for no more than one request every three seconds. Five conversations
#: searching at once sent five at once, and every one came back HTTP 429: 13 research areas, zero
#: papers (2026-10-09, plan M16). One pace for the whole process, so conversations share it.
_ARXIV_INTERVAL = 3.0
_ARXIV_TRIES = 3
#: The longest wait a Retry-After may impose; past it the search fails and says so.
_ARXIV_MAX_RETRY_AFTER = 30.0
#: Per abstract. It was 300 characters, which cut most abstracts before their result, and reports
#: written from them said "no number in the abstract" for numbers that were there (plan M16).
_ABSTRACT_CHARS = 2_000
_arxiv_lock = threading.Lock()
_arxiv_last = 0.0


def _arxiv_get(params: dict[str, Any]) -> Any:
    """GET the arXiv API at arXiv's pace, retrying a 429 or 503 a bounded number of times."""
    import httpx  # lazy

    global _arxiv_last
    response: Any = None
    for attempt in range(_ARXIV_TRIES):
        with _arxiv_lock:
            wait = _arxiv_last + _ARXIV_INTERVAL - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            response = httpx.get(_ARXIV_URL, params=params, timeout=30.0)
            _arxiv_last = time.monotonic()
        status = getattr(response, "status_code", 200)
        if status not in (429, 503) or attempt == _ARXIV_TRIES - 1:
            return response
        delay = _ARXIV_INTERVAL * (attempt + 1)
        header = str(getattr(response, "headers", {}).get("retry-after", "") or "")
        if header.isdigit():
            delay = min(float(header), _ARXIV_MAX_RETRY_AFTER)
        time.sleep(delay)
    return response
_YOUTUBE_ID = re.compile(r"(?:v=|/shorts/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})")


def _youtube_id(value: str) -> str | None:
    value = value.strip()
    match = _YOUTUBE_ID.search(value)
    if match:
        return match.group(1)
    return value if re.fullmatch(r"[A-Za-z0-9_-]{11}", value) else None


class ArxivSearchTool(Tool):
    name = "arxiv_search"
    description = "Search arXiv and return each paper's title, authors, link and summary."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search terms (title/abstract/author)."},
            "max_results": {"type": "integer", "description": "Max papers (default 5)."},
        },
        "required": ["query"],
    }

    def run(self, **kwargs: Any) -> str:
        import httpx  # lazy

        query = str(kwargs["query"])
        max_results = int(kwargs.get("max_results") or 5)
        try:
            response = _arxiv_get(
                {"search_query": f"all:{query}", "start": 0, "max_results": max_results}
            )
            if getattr(response, "status_code", 200) in (429, 503):
                return (
                    f"error: arxiv search failed: HTTP {response.status_code} after "
                    f"{_ARXIV_TRIES} tries (arXiv allows one request every "
                    f"{_ARXIV_INTERVAL:g} seconds); try again in a minute"
                )
            response.raise_for_status()
            root = ElementTree.fromstring(response.text)
        except (httpx.HTTPError, ElementTree.ParseError) as exc:
            return f"error: arxiv search failed: {exc}"
        entries = root.findall(f"{_ATOM}entry")
        if not entries:
            return f"no arXiv results for {query!r}"
        lines = []
        for entry in entries:
            title = (entry.findtext(f"{_ATOM}title") or "").strip().replace("\n", " ")
            link = (entry.findtext(f"{_ATOM}id") or "").strip()
            authors = ", ".join(
                (a.findtext(f"{_ATOM}name") or "").strip() for a in entry.findall(f"{_ATOM}author")
            )
            summary = " ".join((entry.findtext(f"{_ATOM}summary") or "").split())
            if len(summary) > _ABSTRACT_CHARS:
                summary = f"{summary[:_ABSTRACT_CHARS]}... [abstract cut at {_ABSTRACT_CHARS} of {len(summary)} chars]"
            year = (entry.findtext(f"{_ATOM}published") or "")[:4]
            dated = f" ({year})" if year.isdigit() else ""
            lines.append(f"- {title}{dated}\n  {authors}\n  {link}\n  {summary}")
        return f"Top {len(entries)} arXiv results for {query!r}:\n" + "\n".join(lines)


class YouTubeTranscriptTool(Tool):
    name = "youtube_transcript"
    #: `_MAX_OUTPUT_CHARS` on the transcript, with its own marker.
    clips_own_output = True
    description = "Fetch the transcript (captions) of a YouTube video by id or URL."
    parameters = {
        "type": "object",
        "properties": {
            "video": {"type": "string", "description": "YouTube video id or URL."},
        },
        "required": ["video"],
    }

    def run(self, **kwargs: Any) -> str:
        video_id = _youtube_id(str(kwargs.get("video", "")))
        if not video_id:
            return "error: provide a valid YouTube video id or URL"
        try:
            from youtube_transcript_api import YouTubeTranscriptApi  # optional `youtube` extra
        except ImportError:
            return "error: youtube_transcript needs the extra: uv sync --extra youtube"
        try:
            # youtube-transcript-api 1.x: `fetch` is an INSTANCE method returning a FetchedTranscript
            # of snippets. The 0.x classmethod `get_transcript` was removed in 1.0, so the old call
            # raised AttributeError on every invocation — which the `except` below swallowed into
            # "transcript unavailable", making a dead tool look like a flaky one.
            chunks = YouTubeTranscriptApi().fetch(video_id)
        except Exception as exc:  # noqa: BLE001 - the library raises many types; often unavailable
            return f"error: transcript unavailable: {exc}"
        text = " ".join(str(chunk.text) for chunk in chunks).strip()
        if len(text) > _MAX_OUTPUT_CHARS:
            text = text[:_MAX_OUTPUT_CHARS] + f"\n... [truncated, {len(text)} chars total]"
        return text or "error: empty transcript"
