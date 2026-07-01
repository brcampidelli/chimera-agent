"""Tests for the Telegram adapter's pure logic + send (no real network)."""

from __future__ import annotations

from typing import Any

import pytest

from chimera.server import TelegramAdapter


def _update(*, text: str = "hi", user_id: int = 1, is_bot: bool = False, chat_id: int = 100, kind: str = "message") -> dict[str, Any]:
    return {
        "update_id": 5,
        kind: {"text": text, "from": {"id": user_id, "is_bot": is_bot}, "chat": {"id": chat_id}},
    }


def test_message_from_update_builds_inbound() -> None:
    msg = TelegramAdapter("token")._message_from_update(_update(text="  hello ", user_id=7, chat_id=42))
    assert msg is not None
    assert msg.text == "hello" and msg.chat_id == "42" and msg.user == "7" and msg.platform == "telegram"


def test_ignores_bots_empty_and_non_message() -> None:
    adapter = TelegramAdapter("token")
    assert adapter._message_from_update(_update(is_bot=True)) is None
    assert adapter._message_from_update(_update(text="   ")) is None
    assert adapter._message_from_update({"update_id": 1}) is None  # no message payload


def test_respects_allowlist_and_edited_and_bots_option() -> None:
    restricted = TelegramAdapter("token", allowed_users={"42"})
    assert restricted._message_from_update(_update(user_id=7)) is None
    assert restricted._message_from_update(_update(user_id=42)) is not None

    assert TelegramAdapter("token")._message_from_update(_update(kind="edited_message")) is not None
    assert TelegramAdapter("token", respond_to_bots=True)._message_from_update(_update(is_bot=True)) is not None


def test_send_posts_via_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    posted: list[tuple[str, dict[str, Any]]] = []

    class FakeClient:
        def __init__(self, **_: Any) -> None: ...

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_: Any) -> bool:
            return False

        def post(self, url: str, json: dict[str, Any]) -> None:
            posted.append((url, json))

    monkeypatch.setattr(httpx, "Client", FakeClient)
    out = TelegramAdapter("TOK").send("55", "hello")
    assert "sent 1 message" in out
    assert posted[0][1] == {"chat_id": "55", "text": "hello"}
    assert "botTOK/sendMessage" in posted[0][0]
