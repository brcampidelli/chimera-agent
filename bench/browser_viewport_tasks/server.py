"""The local server the task suite browses: the authored pages, plus one generated route.

* A file under ``pages/`` is served as it is (a query string is ignored, so ``search.html?q=brass``
  is ``search.html``, and the page's own JavaScript reads the query).
* ``/go/<site>/<slug>`` answers with a small page showing that slug's code — every link on every page
  leads somewhere, and a wrong click shows a plausible page with the wrong code.

Nothing else: no redirects, no outbound request, no state. Bound to 127.0.0.1 on a free port, one
server per solve, so two solves never share a request log.
"""

from __future__ import annotations

import html
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from bench.browser_viewport_tasks.common import code_for, go_key, title_from_slug

SITE = Path(__file__).resolve().parent / "pages"
HOMES = {
    "wiki": ("/wiki/tidal-power.html", "Openpedia"),
    "docs": ("/docs/widgets-core.html", "Acme Widgets documentation"),
    "news": ("/news/index.html", "The Harbor Ledger"),
    "pkg": ("/pkg/acme-widgets.html", "PackageIndex"),
    "forum": ("/forum/thread-4412.html", "DevAnswers"),
    "shop": ("/shop/lamps.html", "Lumen & Co."),
    "repo": ("/repo/acme-widgets.html", "CodeHub"),
    "gov": ("/gov/services.html", "gov.example"),
}
TYPES = {".html": "text/html; charset=utf-8", ".json": "application/json"}


def go_page(site: str, slug: str) -> str:
    home, name = HOMES.get(site, ("/", site))
    title = title_from_slug(slug)
    code = code_for(go_key(site, slug))
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)} — {html.escape(name)}</title></head><body>"
        f'<header><a href="{home}">{html.escape(name)}</a></header>'
        f"<main><h1>{html.escape(title)}</h1><p>You are reading the page for {html.escape(title)}.</p>"
        f"<p>Page code: <strong>{code}</strong></p></main></body></html>"
    )


class _Handler(BaseHTTPRequestHandler):
    requested: list[str]

    def do_GET(self) -> None:  # noqa: N802 — the stdlib's name
        path = unquote(urlparse(self.path).path)
        self.server.requested.append(path)  # type: ignore[attr-defined]
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "go" and parts[2]:
            self._send(200, go_page(parts[1], parts[2]).encode("utf-8"), TYPES[".html"])
            return
        target = (SITE / path.lstrip("/")).resolve()
        if SITE.resolve() not in target.parents or not target.is_file():
            self._send(404, b"<!doctype html><title>Not found</title><h1>Not found</h1>", TYPES[".html"])
            return
        self._send(200, target.read_bytes(), TYPES.get(target.suffix, "application/octet-stream"))

    def _send(self, status: int, body: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


class SiteServer:
    """``with SiteServer() as origin:`` — the origin is ``http://127.0.0.1:<port>``."""

    def __init__(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.requested = []  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def requested(self) -> list[str]:
        return list(self._server.requested)  # type: ignore[attr-defined]

    def __enter__(self) -> str:
        self._thread.start()
        return self.origin

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
