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


def test_http_bearer_token_guards_state_changing_endpoints() -> None:
    gateway = MessageGateway(EchoSession)
    chat = b'{"text": "hi"}'
    # No token configured -> open (current behavior, localhost-friendly).
    assert handle(gateway, "POST", "/chat", chat)[0] == 200
    # Token configured -> a POST without/with the wrong bearer is 401.
    assert handle(gateway, "POST", "/chat", chat, token="s3cret")[0] == 401
    assert handle(gateway, "POST", "/chat", chat, token="s3cret",
                  headers={"Authorization": "Bearer nope"})[0] == 401
    # Correct bearer passes.
    assert handle(gateway, "POST", "/chat", chat, token="s3cret",
                  headers={"Authorization": "Bearer s3cret"})[0] == 200
    # GET /health stays open even with a token.
    assert handle(gateway, "GET", "/health", b"", token="s3cret")[0] == 200


def test_http_whatsapp_rejects_a_bad_hmac_signature() -> None:
    import hashlib
    import hmac

    from chimera.server import WhatsAppSender, WhatsAppWebhook

    gateway = MessageGateway(EchoSession)
    hook = WhatsAppWebhook(
        WhatsAppSender("t", "pid"), "vt", gateway.on_message, app_secret="appsecret"
    )
    body = b'{"entry": []}'
    good = "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest()
    # No/incorrect signature -> 403; the correct HMAC passes.
    assert handle(gateway, "POST", "/whatsapp", body, whatsapp=hook)[0] == 403
    assert handle(gateway, "POST", "/whatsapp", body, whatsapp=hook,
                  headers={"X-Hub-Signature-256": "sha256=deadbeef"})[0] == 403
    assert handle(gateway, "POST", "/whatsapp", body, whatsapp=hook,
                  headers={"X-Hub-Signature-256": good})[0] == 200


def test_http_handle_webhook_fires_jobs() -> None:
    gateway = MessageGateway(EchoSession)
    seen: dict[str, object] = {}

    def webhooks(hook: str, payload: dict[str, object]) -> list[str]:
        seen["hook"] = hook
        seen["payload"] = payload
        return ["ran the job"]

    status, body = handle(gateway, "POST", "/webhook/gh-push", b'{"ref": "main"}', webhooks=webhooks)
    assert status == 200 and body["fired"] == 1 and body["results"] == ["ran the job"]
    assert seen == {"hook": "gh-push", "payload": {"ref": "main"}}


def test_http_handle_webhook_404_when_no_job() -> None:
    gateway = MessageGateway(EchoSession)
    status, _ = handle(gateway, "POST", "/webhook/none", b"{}", webhooks=lambda h, p: [])
    assert status == 404


def test_http_handle_webhook_ignored_without_handler() -> None:
    gateway = MessageGateway(EchoSession)
    status, _ = handle(gateway, "POST", "/webhook/x", b"{}")  # no webhooks handler -> not routed
    assert status == 404


class _FakeWhatsApp:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    def verify(self, params: dict[str, str]) -> str | None:
        return params.get("hub.challenge") if params.get("hub.verify_token") == "T" else None

    def on_message(self, payload: dict[str, object]) -> int:
        self.messages.append(payload)
        return 1


def test_http_handle_whatsapp_verification_returns_raw_challenge() -> None:
    gateway = MessageGateway(EchoSession)
    whatsapp = _FakeWhatsApp()
    status, body = handle(
        gateway, "GET", "/whatsapp?hub.mode=subscribe&hub.verify_token=T&hub.challenge=XYZ", b"", whatsapp=whatsapp
    )
    assert status == 200 and body == "XYZ"  # plain-text challenge, not JSON
    status, _ = handle(gateway, "GET", "/whatsapp?hub.verify_token=BAD", b"", whatsapp=whatsapp)
    assert status == 403


def test_http_handle_whatsapp_inbound_post() -> None:
    gateway = MessageGateway(EchoSession)
    whatsapp = _FakeWhatsApp()
    status, body = handle(gateway, "POST", "/whatsapp", b'{"entry": []}', whatsapp=whatsapp)
    assert status == 200 and body["received"] == 1
    assert whatsapp.messages == [{"entry": []}]


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
