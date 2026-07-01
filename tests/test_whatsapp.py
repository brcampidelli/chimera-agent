"""Tests for the WhatsApp Cloud API sender + inbound parser (no network)."""

from __future__ import annotations

from typing import Any

import pytest

from chimera.server import WhatsAppSender, WhatsAppWebhook


class _FakeSender:
    platform = "whatsapp"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, chat_id: str, text: str) -> str:
        self.sent.append((chat_id, text))
        return "ok"


def _payload(*, text: str = "hi", sender: str = "15551234567", type_: str = "text", message: bool = True) -> dict[str, Any]:
    value: dict[str, Any] = {}
    if message:
        value["messages"] = [{"from": sender, "type": type_, "text": {"body": text}}]
    return {"entry": [{"changes": [{"value": value}]}]}


def test_parse_inbound_text_message() -> None:
    msg = WhatsAppSender.parse_inbound(_payload(text="  hello ", sender="15551234567"))
    assert msg is not None
    assert msg.text == "hello" and msg.chat_id == "15551234567" and msg.platform == "whatsapp"


def test_parse_inbound_ignores_status_non_text_and_malformed() -> None:
    assert WhatsAppSender.parse_inbound(_payload(message=False)) is None  # delivery status
    assert WhatsAppSender.parse_inbound(_payload(type_="image")) is None
    assert WhatsAppSender.parse_inbound(_payload(text="   ")) is None
    assert WhatsAppSender.parse_inbound({}) is None
    assert WhatsAppSender.parse_inbound({"entry": []}) is None


def test_send_posts_to_cloud_api(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    seen: dict[str, Any] = {}

    class FakeResp:
        def json(self) -> dict[str, Any]:
            return {"messages": [{"id": "wamid.X"}]}

    class FakeClient:
        def __init__(self, **_: Any) -> None: ...

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_: Any) -> bool:
            return False

        def post(self, url: str, headers: dict[str, str], json: dict[str, Any]) -> FakeResp:
            seen.update(url=url, headers=headers, json=json)
            return FakeResp()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    out = WhatsAppSender("TOK", "PN123").send("15551234567", "hello")
    assert "sent message to whatsapp 15551234567" in out
    assert seen["json"]["to"] == "15551234567" and seen["json"]["text"]["body"] == "hello"
    assert "PN123/messages" in seen["url"] and seen["headers"]["Authorization"] == "Bearer TOK"


def test_send_surfaces_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeResp:
        def json(self) -> dict[str, Any]:
            return {"error": {"message": "invalid token"}}

    class FakeClient:
        def __init__(self, **_: Any) -> None: ...

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_: Any) -> bool:
            return False

        def post(self, *_a: Any, **_k: Any) -> FakeResp:
            return FakeResp()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    out = WhatsAppSender("bad", "PN").send("1", "hi")
    assert out.startswith("error:") and "invalid token" in out


def test_webhook_verify_matches_token() -> None:
    hook = WhatsAppWebhook(_FakeSender(), "TOKEN", lambda _m: "reply")  # type: ignore[arg-type]
    assert hook.verify({"hub.mode": "subscribe", "hub.verify_token": "TOKEN", "hub.challenge": "XYZ"}) == "XYZ"
    assert hook.verify({"hub.mode": "subscribe", "hub.verify_token": "WRONG", "hub.challenge": "XYZ"}) is None
    assert hook.verify({}) is None


def test_webhook_on_message_routes_and_replies() -> None:
    sender = _FakeSender()
    routed: list[str] = []

    def route(message: Any) -> str:
        routed.append(message.text)
        return f"echo: {message.text}"

    hook = WhatsAppWebhook(sender, "T", route)  # type: ignore[arg-type]
    assert hook.on_message(_payload(text="hi", sender="15551234567")) == 1
    assert routed == ["hi"] and sender.sent == [("15551234567", "echo: hi")]
    assert hook.on_message(_payload(message=False)) == 0  # status update -> nothing sent
