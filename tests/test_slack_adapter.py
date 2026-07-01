"""Tests for the Slack adapter's pure logic + send (no slack_sdk, no network)."""

from __future__ import annotations

from typing import Any

import pytest

from chimera.server import SlackAdapter


def _event(*, text: str = "hi", user: str = "U1", channel: str = "C1", subtype: str | None = None, bot_id: str | None = None) -> dict[str, Any]:
    event: dict[str, Any] = {"type": "message", "text": text, "user": user, "channel": channel}
    if subtype is not None:
        event["subtype"] = subtype
    if bot_id is not None:
        event["bot_id"] = bot_id
    return event


def _adapter(**kwargs: Any) -> SlackAdapter:
    return SlackAdapter("xoxb-bot", "xapp-app", **kwargs)


def test_message_from_event_builds_inbound() -> None:
    msg = _adapter()._message_from_event(_event(text="  hello ", user="U7", channel="C42"))
    assert msg is not None
    assert msg.text == "hello" and msg.chat_id == "C42" and msg.user == "U7" and msg.platform == "slack"


def test_ignores_non_message_subtypes_and_bots_and_empty() -> None:
    adapter = _adapter()
    assert adapter._message_from_event({"type": "reaction_added"}) is None
    assert adapter._message_from_event(_event(subtype="message_changed")) is None
    assert adapter._message_from_event(_event(bot_id="B1")) is None
    assert adapter._message_from_event(_event(subtype="bot_message")) is None
    assert adapter._message_from_event(_event(text="   ")) is None


def test_allowlist_and_respond_to_bots() -> None:
    restricted = _adapter(allowed_users={"U9"})
    assert restricted._message_from_event(_event(user="U1")) is None
    assert restricted._message_from_event(_event(user="U9")) is not None
    assert _adapter(respond_to_bots=True)._message_from_event(_event(bot_id="B1")) is not None


def test_send_posts_via_web_api(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    posted: list[dict[str, Any]] = []

    class FakeResp:
        def json(self) -> dict[str, Any]:
            return {"ok": True}

    class FakeClient:
        def __init__(self, **_: Any) -> None: ...

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_: Any) -> bool:
            return False

        def post(self, url: str, headers: dict[str, str], json: dict[str, Any]) -> FakeResp:
            posted.append({"url": url, "headers": headers, "json": json})
            return FakeResp()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    out = _adapter().send("C55", "hello")
    assert "sent 1 message" in out
    assert posted[0]["json"] == {"channel": "C55", "text": "hello"}
    assert posted[0]["headers"]["Authorization"] == "Bearer xoxb-bot"


def test_send_surfaces_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeResp:
        def json(self) -> dict[str, Any]:
            return {"ok": False, "error": "channel_not_found"}

    class FakeClient:
        def __init__(self, **_: Any) -> None: ...

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_: Any) -> bool:
            return False

        def post(self, *_a: Any, **_k: Any) -> FakeResp:
            return FakeResp()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    out = _adapter().send("C1", "hi")
    assert out.startswith("error:") and "channel_not_found" in out
