"""The A2A SSE stream must not be a way around the bearer token.

These tests go through the real HTTP server rather than calling :func:`handle`, because the bug they
lock down lived *outside* ``handle``: ``message/stream`` cannot use it (SSE emits many bodies, handle
returns one), so the transport branched earlier — and the branch came before the only auth check.
Every existing A2A test called ``handle`` or the ``A2AServer`` directly, which is precisely why none
of them saw it. A test that cannot reach the bug cannot guard it.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from chimera.integrations import A2AServer, chimera_agent_card
from chimera.server.gateway import MessageGateway
from chimera.server.http import authorized, make_server

TOKEN = "s3cret-token"


def _stream_body(text: str = "hello") -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/stream",
            "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": text}]}},
        }
    ).encode()


def _plain_body(text: str = "hello") -> bytes:
    body = json.loads(_stream_body(text))
    body["method"] = "message/send"
    return json.dumps(body).encode()


@pytest.fixture
def server_url() -> Iterator[str]:
    """A real token-protected server on an ephemeral port, torn down after the test."""
    gateway = MessageGateway(lambda: None)  # not exercised: these tests only hit /a2a
    card = chimera_agent_card("http://x/a2a", version="0.2.0")
    a2a = (A2AServer(solve=lambda text: f"done: {text}"), card)
    server = make_server(gateway, "127.0.0.1", 0, token=TOKEN, a2a=a2a)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


STREAM_OPENED = -1
"""Sentinel: the server began an event stream instead of answering. See :func:`_post`."""


def _post(url: str, body: bytes, *, token: str | None = None) -> tuple[int, str]:
    """POST and return ``(status, body)``.

    A rejected request answers immediately. An *accepted* ``message/stream`` opens an SSE response
    that never closes, so reading it blocks — which is what the regression looks like from outside.
    We map that timeout to :data:`STREAM_OPENED` rather than letting it surface as a raw
    ``TimeoutError``: a security test whose failure reads like flaky networking is a security test
    somebody eventually marks flaky and skips.
    """
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()
    except TimeoutError:
        return STREAM_OPENED, "<server started streaming — the request was NOT rejected>"


def test_stream_without_a_token_is_rejected(server_url: str) -> None:
    """The regression: naming the streaming method used to skip auth entirely."""
    status, body = _post(f"{server_url}/a2a", _stream_body())
    assert status == 401, f"message/stream reached the agent unauthenticated: {body}"
    assert "done:" not in body  # the solve callable must never have run


def test_stream_with_a_wrong_token_is_rejected(server_url: str) -> None:
    status, body = _post(f"{server_url}/a2a", _stream_body(), token="not-the-token")
    assert status == 401, f"a wrong bearer still reached the agent: {body}"


def _post_sse(url: str, body: bytes, *, token: str, until: str, max_lines: int = 40) -> tuple[int, str]:
    """POST and read the event stream incrementally, stopping at ``until``.

    An SSE response sets no Content-Length and keeps the connection open, so reading to EOF blocks
    forever; the client is expected to consume events as they arrive and hang up when satisfied.
    """
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    seen: list[str] = []
    with urllib.request.urlopen(request, timeout=10) as response:
        for _ in range(max_lines):
            line = response.fp.readline()
            if not line:
                break
            seen.append(line.decode("utf-8", "replace"))
            if until in seen[-1]:
                break
        return response.status, "".join(seen)


def test_stream_with_the_right_token_still_streams(server_url: str) -> None:
    """Closing the hole must not break the feature it was hiding in."""
    status, body = _post_sse(f"{server_url}/a2a", _stream_body("refactor"), token=TOKEN, until="completed")
    assert status == 200
    assert "done: refactor" in body
    assert "working" in body and "completed" in body  # both SSE lifecycle events


def test_non_streaming_a2a_is_still_guarded(server_url: str) -> None:
    unauth, _ = _post(f"{server_url}/a2a", _plain_body())
    ok, body = _post(f"{server_url}/a2a", _plain_body("x"), token=TOKEN)
    assert unauth == 401
    assert ok == 200 and "done: x" in body


class TestAuthorizedIsTheOneDecision:
    """`authorized` is the single seam every transport must consult — pin its contract."""

    def test_no_token_configured_allows_everything(self) -> None:
        assert authorized("POST", "/a2a", {}, None) is True

    @pytest.mark.parametrize("route", ["/a2a", "/chat", "/webhook/deploy"])
    def test_guarded_routes_need_the_bearer(self, route: str) -> None:
        assert authorized("POST", route, {}, TOKEN) is False
        assert authorized("POST", route, {"Authorization": f"Bearer {TOKEN}"}, TOKEN) is True

    def test_header_name_is_case_insensitive(self) -> None:
        assert authorized("POST", "/a2a", {"authorization": f"Bearer {TOKEN}"}, TOKEN) is True

    def test_query_string_does_not_smuggle_past_the_route_match(self) -> None:
        assert authorized("POST", "/a2a?stream=1", {}, TOKEN) is False

    def test_whatsapp_is_exempt_because_meta_cannot_send_our_bearer(self) -> None:
        assert authorized("POST", "/whatsapp", {}, TOKEN) is True

    def test_reads_are_not_gated(self) -> None:
        assert authorized("GET", "/health", {}, TOKEN) is True
