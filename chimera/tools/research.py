"""Reference research tools: arxiv_search (no key, no dependency) and youtube_transcript.

``arxiv_search`` hits the public arXiv Atom API and parses it with the stdlib — always on.
``youtube_transcript`` needs the optional ``youtube-transcript-api`` (the ``youtube``
extra); it returns a helpful message when the library or a transcript is unavailable
(YouTube frequently blocks caption scraping, so failures are expected and handled).
"""

from __future__ import annotations

import re
from typing import Any
from xml.etree import ElementTree

from chimera.tools.base import Tool

_ARXIV_URL = "https://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"
_MAX_OUTPUT_CHARS = 20_000
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
            response = httpx.get(
                _ARXIV_URL,
                params={"search_query": f"all:{query}", "start": 0, "max_results": max_results},
                timeout=30.0,
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
            summary = (entry.findtext(f"{_ATOM}summary") or "").strip().replace("\n", " ")[:300]
            lines.append(f"- {title}\n  {authors}\n  {link}\n  {summary}")
        return f"Top {len(entries)} arXiv results for {query!r}:\n" + "\n".join(lines)


class YouTubeTranscriptTool(Tool):
    name = "youtube_transcript"
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
            chunks = YouTubeTranscriptApi.get_transcript(video_id)
        except Exception as exc:  # noqa: BLE001 - the library raises many types; often unavailable
            return f"error: transcript unavailable: {exc}"
        text = " ".join(str(chunk.get("text", "")) for chunk in chunks).strip()
        if len(text) > _MAX_OUTPUT_CHARS:
            text = text[:_MAX_OUTPUT_CHARS] + f"\n... [truncated, {len(text)} chars total]"
        return text or "error: empty transcript"
