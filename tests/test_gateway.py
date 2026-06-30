"""Tests for the messaging gateway, local adapter, and HTTP server."""

from __future__ import annotations

import threading

import httpx

from chimera.server import (
    InboundMessage,
    LocalAdapter,
    MessageGateway,
    handle,
    make_server,
)


class EchoSession:
    """A fake ChatSession: echoes with a per-session turn counter."""

    def __init__(self) -> None:
        self.turns: list[str] = []

    def send(self, message: str) -> str:
        self.turns.append(message)
        return f"echo[{len(self.turns)}]: {message}"

    def reset(self) -> None:
        self.turns.clear()


def test_gateway_routes_and_isolates_chats() -> None:
    gateway = MessageGateway(EchoSession)
    assert gateway.on_message(InboundMessage("hi", chat_id="A")) == "echo[1]: hi"
    assert gateway.on_message(InboundMessage("yo", chat_id="A")) == "echo[2]: yo"  # same session
    assert gateway.on_message(InboundMessage("hey", chat_id="B")) == "echo[1]: hey"  # fresh session
    assert gateway.active_chats == 2


def test_local_adapter_feeds_the_gateway() -> None:
    gateway = MessageGateway(EchoSession)
    adapter = LocalAdapter()
    adapter.start(gateway.on_message)
    assert adapter.feed("hello") == "echo[1]: hello"
    assert adapter.feed("again") == "echo[2]: again"


def test_http_handle_health_and_chat() -> None:
    gateway = MessageGateway(EchoSession)

    status, body = handle(gateway, "GET", "/health", b"")
    assert status == 200 and body["status"] == "ok"

    status, body = handle(gateway, "POST", "/chat", b'{"text": "hi", "chat_id": "x"}')
    assert status == 200 and body["reply"] == "echo[1]: hi"

    status, _ = handle(gateway, "POST", "/chat", b"{}")  # missing text
    assert status == 400
    status, _ = handle(gateway, "GET", "/nope", b"")
    assert status == 404


def test_http_server_end_to_end() -> None:
    gateway = MessageGateway(EchoSession)
    server = make_server(gateway, "127.0.0.1", 0)  # ephemeral port
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        health = httpx.get(f"http://127.0.0.1:{port}/health", timeout=5)
        assert health.status_code == 200 and health.json()["status"] == "ok"

        chat = httpx.post(
            f"http://127.0.0.1:{port}/chat", json={"text": "ping", "chat_id": "c1"}, timeout=5
        )
        assert chat.status_code == 200 and chat.json()["reply"] == "echo[1]: ping"
    finally:
        server.shutdown()
        thread.join(timeout=5)
