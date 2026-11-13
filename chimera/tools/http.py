"""HTTP fetch tool (read-only GET) for simple web access and RAG ingestion."""

from __future__ import annotations

from typing import Any

from chimera.tools.base import Tool
from chimera.tools.clip import OFFSET_PARAMETER, clip_page, parse_offset
from chimera.tools.final_url import set_final_url

_MAX_BODY_CHARS = 20_000
_MAX_BODY_BYTES = 10 * 1024 * 1024  # cap the DOWNLOAD (not just the returned text) so a huge body can't OOM
_MAX_REDIRECTS = 10
_DEFAULT_TIMEOUT = 30.0


class HttpGetTool(Tool):
    name = "http_get"
    #: Bounds its own output: _MAX_BODY_CHARS on the body. The central ceiling in `ToolRegistry.run` leaves it alone.
    clips_own_output = True
    description = "Fetch a URL with an HTTP GET and return status + body text."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to fetch (http/https)."},
            "timeout": {"type": "number", "description": "Timeout in seconds (default 30)."},
            "offset": OFFSET_PARAMETER,
        },
        "required": ["url"],
    }

    def run(self, **kwargs: Any) -> str:
        import httpx  # lazy import keeps tool construction cheap

        from chimera.scrape.ssrf import check_url

        url = str(kwargs["url"])
        timeout = float(kwargs.get("timeout") or _DEFAULT_TIMEOUT)
        offset = parse_offset(kwargs.get("offset"))
        if isinstance(offset, str):
            return offset
        try:
            # SSRF guard + manual redirects (re-checked per hop) + a streamed download-size cap — the
            # same protections the scrape tools got; http_get is directly model-callable.
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                for _ in range(_MAX_REDIRECTS):
                    check_url(url)
                    with client.stream("GET", url) as response:
                        if response.is_redirect and response.headers.get("location"):
                            url = str(httpx.URL(url).join(response.headers["location"]))
                            continue
                        raw = bytearray()
                        for chunk in response.iter_bytes():
                            raw += chunk
                            if len(raw) > _MAX_BODY_BYTES:
                                break
                        status = response.status_code
                        encoding = response.encoding or "utf-8"
                    body = bytes(raw).decode(encoding, errors="replace")
                    body = clip_page(body, _MAX_BODY_CHARS, offset)
                    if body.startswith("error: offset"):
                        return body
                    set_final_url(url)  # the hop that supplied this body, read by the ledger
                    return f"[{status}] {url}\n{body}"
                return f"error: too many redirects fetching {url!r}"
        except ValueError as exc:  # SSRF-blocked host
            return f"error: {exc}"
        except httpx.HTTPError as exc:
            return f"error: request failed: {exc}"
