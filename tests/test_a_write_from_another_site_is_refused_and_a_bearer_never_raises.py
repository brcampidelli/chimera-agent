"""CSRF on the token-less owner API, uploads behind the same check, and one bearer comparison.

Without ``CHIMERA_SERVER_TOKEN`` (the desktop default) nothing but the network stops a page open in
the owner's browser from POSTing to 127.0.0.1: a ``text/plain`` or multipart body is a CORS simple
request, so there is no preflight and the browser only hides the answer. A browser always says where
a write came from; a client that is not a browser (the CLI, the MCP bridge) sends neither
``Origin`` nor ``Sec-Fetch-Site`` and must keep working.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.config import Settings, get_settings
from chimera.core.bearer import bearer_matches
from chimera.interface import ChatSession
from chimera.server.http import authorized

TOKEN = "owner-bearer-for-the-csrf-tests"
ROUTE = "/api/security/overview"  # a POST that changes nothing, so the status is the whole answer
OWN = "http://127.0.0.1:8765"


class _Idle:
    def run(self, task: str, **_: Any) -> Any:
        raise AssertionError("no agent in these tests")


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> TestClient:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    # The guards read the process settings on every request (so `PATCH /api/config` is live), not the
    # object handed to the builder: the environment has to say the same thing.
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    fields: dict[str, Any] = {"CHIMERA_HOME": str(tmp_path / "home"), "CHIMERA_MEMORY_BACKEND": "json", **env}
    app = build_api_app(lambda: ChatSession(_Idle()), workspace=tmp_path, settings=Settings(**fields))
    return TestClient(app, base_url=OWN)


# --- the token-less write ------------------------------------------------------------------------


def test_a_text_plain_post_from_another_origin_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    refused = client.post(
        ROUTE, content=b"x", headers={"origin": "https://evil.example", "content-type": "text/plain"}
    )
    assert refused.status_code == 403


def test_a_cross_site_post_with_no_origin_is_refused_by_what_the_browser_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.post(ROUTE, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    # Another port on localhost is another page, and "same-site" says so.
    assert client.post(ROUTE, headers={"sec-fetch-site": "same-site"}).status_code == 403


def test_the_apps_own_origin_and_a_client_that_is_not_a_browser_still_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.post(ROUTE, headers={"origin": OWN}).status_code == 200
    assert client.post(ROUTE, headers={"sec-fetch-site": "same-origin"}).status_code == 200
    # No Origin, no Sec-Fetch-Site: the CLI and the bridge. A CSRF cannot come from here, because a
    # browser sends one of the two on every cross-site POST.
    assert client.post(ROUTE).status_code == 200


def test_an_origin_the_operator_named_may_still_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch, CHIMERA_ALLOWED_ORIGINS="https://desk.example")
    assert client.post(ROUTE, headers={"origin": "https://desk.example"}).status_code == 200


def test_a_read_is_never_turned_away_by_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/health", headers={"origin": "https://evil.example"}).status_code != 403


# --- the two uploads that skipped the origin check -----------------------------------------------


@pytest.mark.parametrize("route", ["/api/attachments", "/api/transcribe"])
def test_an_upload_from_another_site_is_refused_before_it_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    client = _client(tmp_path, monkeypatch)
    sent = client.post(
        route, files={"file": ("n.txt", b"hello", "text/plain")}, headers={"origin": "https://evil.example"}
    )
    assert sent.status_code == 403
    assert not (tmp_path / "home" / "attachments").exists()


def test_an_attachment_from_the_apps_own_origin_is_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    sent = client.post("/api/attachments", files={"file": ("n.txt", b"hello", "text/plain")}, headers={"origin": OWN})
    assert sent.status_code == 200, sent.text


# --- one bearer comparison -----------------------------------------------------------------------


def test_bearer_matches_is_exact_and_never_raises() -> None:
    assert bearer_matches(f"Bearer {TOKEN}", TOKEN)
    assert not bearer_matches(f"Bearer {TOKEN}x", TOKEN)
    assert not bearer_matches("Bearer é", TOKEN)  # a str compare_digest raised TypeError here
    assert not bearer_matches(f"Bearer {TOKEN}", "é")
    assert not bearer_matches("", TOKEN)
    assert not bearer_matches(None, TOKEN)
    assert not bearer_matches("Bearer ", "")
    assert bearer_matches("Bearer é", "é")


def test_a_non_ascii_bearer_is_a_401_on_the_owner_api_not_a_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch, CHIMERA_SERVER_TOKEN=TOKEN)
    # Starlette reads header bytes as latin-1, so this is what a client sending `é` produces.
    bad = {"authorization": "Bearer é".encode("latin-1")}
    assert client.post(ROUTE, headers=bad).status_code == 401  # the middleware
    assert client.post(ROUTE, headers={"authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_the_stdlib_gateway_says_no_to_a_non_ascii_bearer() -> None:
    assert authorized("POST", "/chat", {"Authorization": "Bearer é"}, TOKEN) is False
    assert authorized("POST", "/chat", {"Authorization": f"Bearer {TOKEN}"}, TOKEN) is True


# --- a GET that does something, and the doors that read a body before knowing who is asking ------


@pytest.mark.parametrize("route", ["/api/orchestration/runs", "/api/code/sessions"])
def test_a_get_that_prunes_or_archives_refuses_another_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    client = _client(tmp_path, monkeypatch)
    # An `<img src>` on another site: no Origin, and the browser says cross-site.
    assert client.get(route, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.get(route, headers={"origin": "https://evil.example"}).status_code == 403
    assert client.get(route).status_code == 200
    assert client.get(route, headers={"sec-fetch-site": "same-origin"}).status_code == 200


def test_the_token_less_gateway_wants_json_and_no_other_origin() -> None:
    from chimera.server.http import browser_refusal

    host = {"Host": "127.0.0.1:8765"}
    # `fetch(url, {method: "POST", mode: "no-cors", body: JSON.stringify(...)})`: no Content-Type.
    assert browser_refusal("POST", "/chat", host, None) == (415, "send Content-Type: application/json")
    text = {**host, "Content-Type": "text/plain"}
    assert browser_refusal("POST", "/chat", text, None) is not None
    json_h = {**host, "Content-Type": "application/json; charset=utf-8"}
    assert browser_refusal("POST", "/chat", json_h, None) is None
    other = {**json_h, "Origin": "https://evil.example"}
    assert browser_refusal("POST", "/chat", other, None) == (403, "refusing a write sent from another site")
    assert browser_refusal("POST", "/chat", {**json_h, "Sec-Fetch-Site": "cross-site"}, None) is not None
    # Its own origin, a read, and a token-protected gateway are untouched.
    assert browser_refusal("POST", "/chat", {**json_h, "Origin": "http://127.0.0.1:8765"}, None) is None
    assert browser_refusal("GET", "/health", host, None) is None
    assert browser_refusal("POST", "/chat", host, TOKEN) is None


def test_the_running_gateway_refuses_a_no_cors_post_before_touching_the_agent() -> None:
    import threading
    import urllib.error
    import urllib.request

    from chimera.server.http import make_server

    class _Never:
        active_chats = 0

        def on_message(self, message: Any) -> str:
            raise AssertionError("a refused write must not reach the agent")

    server = make_server(_Never(), port=0)  # type: ignore[arg-type]  # stub gateway
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/chat"
        # What `fetch(url, {method: "POST", mode: "no-cors", body: ...})` sends: no Content-Type.
        request = urllib.request.Request(url, data=b'{"text": "hi"}', method="POST")
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request, timeout=5)  # loopback test server
        assert refused.value.code == 415
    finally:
        server.shutdown()
        server.server_close()


def test_the_bridge_says_no_before_it_reads_a_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    # Not JSON at all: were the body read first, FastAPI answers 422 before `authorize` ever runs.
    refused = client.post("/api/bridge/call", content=b"not json " * 1000, headers={"content-type": "application/json"})
    assert refused.status_code == 403
    assert "bridge is off" in refused.text


def test_the_openai_door_checks_the_bearer_before_it_reads_a_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch, CHIMERA_SERVER_TOKEN=TOKEN)
    refused = client.post("/v1/chat/completions", content=b"not json", headers={"content-type": "application/json"})
    assert refused.status_code == 401


def test_the_gateway_answers_a_missing_bearer_and_a_huge_body_without_reading_it() -> None:
    import socket
    import threading

    from chimera.server.http import MAX_BODY_BYTES, make_server

    class _Never:
        active_chats = 0

        def on_message(self, message: Any) -> str:
            raise AssertionError("never reaches the agent")

    def post(server: Any, headers: str) -> bytes:
        with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5) as conn:
            # Only the headers are sent. A server that read the body first would block here until
            # the timeout; one that answers from the headers replies at once.
            conn.sendall(f"POST /chat HTTP/1.1\r\nHost: 127.0.0.1\r\n{headers}\r\n".encode())
            return conn.recv(4096)

    for token, headers, expected in [
        (TOKEN, f"Content-Type: application/json\r\nContent-Length: {MAX_BODY_BYTES * 5}\r\n", b" 401 "),
        (None, f"Content-Type: application/json\r\nContent-Length: {MAX_BODY_BYTES + 1}\r\n", b" 413 "),
    ]:
        server = make_server(_Never(), port=0, token=token)  # type: ignore[arg-type]  # stub gateway
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            assert expected in post(server, headers).split(b"\r\n", 1)[0] + b" "
        finally:
            server.shutdown()
            server.server_close()


@pytest.mark.parametrize("route", ["/api/memory?q=anything"])
def test_the_memory_search_refuses_another_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.get(route, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.get(route, headers={"origin": "https://evil.example"}).status_code == 403


def test_a_plugin_update_check_refuses_another_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    # The guard runs before the route looks the plugin up, so an unknown name is enough to tell it
    # apart: 403 from the guard, 404 from the route.
    assert client.get("/api/plugins/nope/update", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.get("/api/plugins/nope/update").status_code == 404
