"""Tests for the Signal adapter's pure logic + send (no bridge, no network)."""

from __future__ import annotations

from typing import Any

import pytest

from chimera.server import SignalAdapter


def _item(*, text: str = "hi", source: str = "+15551234567", data: bool = True) -> dict[str, Any]:
    envelope: dict[str, Any] = {"source": source}
    if data:
        envelope["dataMessage"] = {"message": text}
    return {"envelope": envelope}


def _adapter(**kwargs: Any) -> SignalAdapter:
    return SignalAdapter("http://localhost:8080", "+15550000000", **kwargs)


def test_message_from_envelope_builds_inbound() -> None:
    msg = _adapter()._message_from_envelope(_item(text="  hi ", source="+15551110000"))
    assert msg is not None
    assert msg.text == "hi" and msg.chat_id == "+15551110000" and msg.platform == "signal"


def test_ignores_receipts_empty_and_malformed() -> None:
    adapter = _adapter()
    assert adapter._message_from_envelope(_item(data=False)) is None  # receipt/typing update
    assert adapter._message_from_envelope(_item(text="   ")) is None
    assert adapter._message_from_envelope({}) is None


def test_respects_allowlist() -> None:
    adapter = _adapter(allowed_users={"+15559999999"})
    assert adapter._message_from_envelope(_item(source="+15551234567")) is None
    assert adapter._message_from_envelope(_item(source="+15559999999")) is not None


def test_send_posts_to_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    seen: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **_: Any) -> None: ...

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_: Any) -> bool:
            return False

        def post(self, url: str, json: dict[str, Any]) -> None:
            seen.update(url=url, json=json)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    out = _adapter().send("+15551234567", "hello")
    assert "sent message to signal +15551234567" in out
    assert seen["url"].endswith("/v2/send")
    assert seen["json"] == {"message": "hello", "number": "+15550000000", "recipients": ["+15551234567"]}
