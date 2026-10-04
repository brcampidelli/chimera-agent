"""Study 29, P5.2 review: a declared local port is judged by who answers on it, not by its number.

The first version refused the ports Chimera listens on and opened every other declared one. The
desktop's own dev server breaks that: Vite on 5173 (``npm --prefix apps/desktop run dev``) proxies
``/api`` to the app's API, so with 5173 declared — the example the Settings field itself offered —
``http://localhost:5173/api/approvals`` reached the approvals of the running app through a port
Chimera never held. Every Chimera listener now marks its responses, a proxy relays the mark, and the
browser asks the same URL with an ``OPTIONS`` (which runs no handler) before a request goes there.

The servers below are real sockets, and the browser tests drive a real Chromium (skipped where it
cannot launch). The "Chimera" behind the proxy is a stand-in that marks its answers with the real
header constant; the first tests are what make that stand-in honest — each real listener marks.
"""

from __future__ import annotations

import http.client
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from chimera.core.listeners import INSTANCE_HEADER
from chimera.tools.browser import BrowserTool
from chimera.tools.browser_playwright import PlaywrightDriver
from chimera.tools.browser_reach import CHIMERA, OTHER, SILENT, BrowserReach, who_answers

# --- every real listener marks its answers ----------------------------------------------------


def test_the_apps_api_marks_every_answer_including_the_probe(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.config import Settings

    app = build_api_app(lambda: None, settings=Settings(CHIMERA_HOME=str(tmp_path)))  # type: ignore[arg-type]
    client = TestClient(app)
    for response in (
        client.options("/api/approvals"),  # what the browser asks: no route declares OPTIONS
        client.get("/api/health"),
        client.get("/api/no-such-route"),
    ):
        assert response.headers.get(INSTANCE_HEADER) == "1", response.status_code


def test_the_guest_listener_marks_its_answers(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from chimera.api.guest_api import build_guest_app
    from chimera.api.sharing import SessionBus, ShareStore

    guest = build_guest_app(
        store=ShareStore(tmp_path / "shares.json"),
        bus=SessionBus(),
        session_view=lambda sid: None,
        session_workspace=lambda sid: str(tmp_path),
        start_turn=lambda *a, **k: None,
    )
    client = TestClient(guest)
    assert client.options("/api/session").headers.get(INSTANCE_HEADER) == "1"
    assert client.get("/api/session").headers.get(INSTANCE_HEADER) == "1"  # a 401 is marked too


def test_the_serve_gateway_marks_its_answers_even_a_method_it_does_not_have() -> None:
    from chimera.server.http import make_server

    server = make_server(gateway=None, host="127.0.0.1", port=0)  # type: ignore[arg-type]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = int(server.server_address[1])
        assert who_answers(f"http://127.0.0.1:{port}/a2a") == CHIMERA  # the stdlib's own 501
    finally:
        server.shutdown()
        server.server_close()


# --- a declared port that relays to Chimera ----------------------------------------------------


class _Chimera(BaseHTTPRequestHandler):
    """Stands for the app's API: marks every answer, records every request that ran a handler."""

    def log_message(self, *args: object) -> None:
        return

    def _answer(self) -> None:
        self.send_response(200)
        self.send_header(INSTANCE_HEADER, "1")
        self.send_header("Content-Length", "13")
        self.end_headers()
        self.wfile.write(b"APPROVED-ALL!")

    def do_OPTIONS(self) -> None:  # noqa: N802 — the stdlib's name
        self.server.asked.append(self.path)  # type: ignore[attr-defined]
        self._answer()

    def do_GET(self) -> None:  # noqa: N802
        self.server.acted.append(f"GET {self.path}")  # type: ignore[attr-defined]
        self._answer()

    def do_POST(self) -> None:  # noqa: N802
        self.server.acted.append(f"POST {self.path}")  # type: ignore[attr-defined]
        self._answer()


class _DevProxy(BaseHTTPRequestHandler):
    """Stands for Vite: serves its own pages and relays ``/api`` to the API, headers and all."""

    def log_message(self, *args: object) -> None:
        return

    def _relay(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        upstream = http.client.HTTPConnection("127.0.0.1", self.server.upstream, timeout=5)  # type: ignore[attr-defined]
        upstream.request(method, self.path, body=body)
        answer = upstream.getresponse()
        data = answer.read()
        self.send_response(answer.status)
        for key, value in answer.getheaders():
            if key.lower() not in ("content-length", "connection", "transfer-encoding"):
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        upstream.close()

    def _own(self) -> None:
        pages: dict[str, str] = self.server.pages  # type: ignore[attr-defined]
        data = pages.get(self.path.split("?")[0], "<p>dev-home</p>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _route(self, method: str) -> None:
        if self.path.startswith("/api"):
            self._relay(method)
        elif method == "OPTIONS":
            self.send_response(204)
            self.end_headers()
        else:
            self._own()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._route("OPTIONS")

    def do_GET(self) -> None:  # noqa: N802
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._route("POST")


def _start(handler: type[BaseHTTPRequestHandler], **attrs: Any) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    for key, value in attrs.items():
        setattr(server, key, value)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture()
def relay() -> Iterator[dict[str, Any]]:
    api = _start(_Chimera, asked=[], acted=[])
    dev = _start(
        _DevProxy,
        upstream=int(api.server_address[1]),
        pages={
            "/fetches": (
                "<p>dev-fetches</p><script>"
                "fetch('/api/approvals/1', {method: 'POST', body: 'decision=approve'});"
                "fetch('/api/conversations');"
                "</script><img src='/api/approvals.png'>"
            ),
        },
    )
    yield {"api": api, "dev_port": int(dev.server_address[1])}
    for server in (api, dev):
        server.shutdown()
        server.server_close()


def test_the_probe_reads_the_mark_through_the_proxy_and_only_on_the_paths_it_relays(
    relay: dict[str, Any],
) -> None:
    port = relay["dev_port"]
    assert who_answers(f"http://127.0.0.1:{port}/api/approvals") == CHIMERA
    assert who_answers(f"http://localhost:{port}/") == OTHER
    assert relay["api"].acted == [], "the probe ran a handler"


def test_a_port_where_nothing_answers_is_silent_and_refused() -> None:
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    assert who_answers(f"http://127.0.0.1:{port}/") == SILENT
    reach = BrowserReach(local_ports={port}, owned=frozenset)
    assert not reach.permits(f"http://127.0.0.1:{port}/")
    with pytest.raises(ValueError, match="nothing answered"):
        reach.check(f"http://127.0.0.1:{port}/")


def test_the_tool_refuses_the_relayed_api_and_says_why(relay: dict[str, Any]) -> None:
    port = relay["dev_port"]
    reach = BrowserReach(local_ports={port}, owned=frozenset)
    assert reach.permits(f"http://localhost:{port}/")
    assert not reach.permits(f"http://localhost:{port}/api/approvals")
    with pytest.raises(ValueError, match="answered by Chimera itself"):
        reach.check(f"http://localhost:{port}/api/approvals")

    class _Never:
        def navigate(self, url: str) -> Any:
            raise AssertionError(f"the driver was asked for {url}")

        def frame(self) -> None:
            return None

    tool = BrowserTool(driver=_Never(), reach=reach)  # type: ignore[arg-type]
    out = tool.run(action="navigate", url=f"http://localhost:{port}/api/approvals")
    assert out.startswith("error:") and "Chimera itself" in out
    assert relay["api"].acted == []


@pytest.fixture()
def relay_driver(relay: dict[str, Any]) -> Iterator[PlaywrightDriver]:
    reach = BrowserReach(
        local_ports={relay["dev_port"]},
        owned=frozenset,  # the API's port is not this process's: only the mark can refuse it
        floor=lambda url: False,
    )
    try:
        d = PlaywrightDriver(headless=True, reach=reach)
    except Exception as exc:  # noqa: BLE001 — no Chromium here (CI): these tests need a real browser
        pytest.skip(f"chromium unavailable: {exc}")
    yield d
    d.close()


def test_a_real_browser_on_a_declared_dev_server_never_reaches_the_api_behind_it(
    relay: dict[str, Any], relay_driver: PlaywrightDriver
) -> None:
    """The scenario of the review: 5173 declared so the agent can see a UI change, and a page that
    steers it at ``/api``. Zero requests may run a handler on the API; the dev server's own page
    still loads."""
    port = relay["dev_port"]
    with pytest.raises(ValueError, match="blocked navigation"):
        relay_driver.navigate(f"http://localhost:{port}/api/approvals")
    relay_driver.navigate(f"http://localhost:{port}/fetches")
    relay_driver._page.wait_for_timeout(700)
    assert "dev-fetches" in relay_driver.page_text()
    assert relay["api"].acted == []
    # What did reach it: the probes, each an OPTIONS that runs no handler.
    assert set(relay["api"].asked) <= {"/api/approvals", "/api/approvals/1", "/api/conversations", "/api/approvals.png"}
    assert relay_driver.guard.blocked_requests >= 3


def test_the_owner_port_check_still_runs_before_anyone_is_asked() -> None:
    """A port Chimera holds is refused by its number, without a probe: the sidecar is never asked
    anything, not even an OPTIONS."""
    asked: list[str] = []

    def answers(url: str) -> str:
        asked.append(url)
        return OTHER

    reach = BrowserReach(local_ports={3000, 51234}, owned=lambda: frozenset({51234}), answers=answers)
    assert not reach.permits("http://127.0.0.1:51234/api/approvals")
    assert asked == []
    assert reach.permits("http://127.0.0.1:3000/")
    assert asked == ["http://127.0.0.1:3000/"]
    assert urlparse(asked[0]).port == 3000
