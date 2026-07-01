"""Tests for the platform-agnostic messaging layer (no network)."""

from __future__ import annotations

from chimera.integrations import SenderRegistry, SendMessageTool


class FakeSender:
    platform = "discord"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, chat_id: str, text: str) -> str:
        self.sent.append((chat_id, text))
        return f"ok:{chat_id}"


def test_registry_routes_to_registered_sender() -> None:
    registry = SenderRegistry()
    sender = FakeSender()
    registry.register(sender)
    assert registry.platforms() == ["discord"]
    assert registry.send("discord", "123", "hi") == "ok:123"
    assert sender.sent == [("123", "hi")]


def test_registry_unknown_platform_returns_error() -> None:
    out = SenderRegistry().send("telegram", "1", "hi")
    assert out.startswith("error:") and "telegram" in out


def test_registry_contains_send_exceptions() -> None:
    class Boom:
        platform = "x"

        def send(self, chat_id: str, text: str) -> str:
            raise RuntimeError("nope")

    registry = SenderRegistry()
    registry.register(Boom())
    out = registry.send("x", "1", "hi")
    assert out.startswith("error:") and "nope" in out


def test_send_message_tool_validates_and_sends() -> None:
    registry = SenderRegistry()
    registry.register(FakeSender())
    tool = SendMessageTool(registry)
    assert tool.name == "send_message"
    assert tool.to_openai_schema()["function"]["name"] == "send_message"
    assert tool.run(platform="discord", chat_id="c1", text="hello") == "ok:c1"
    assert tool.run(platform="discord", chat_id="", text="x").startswith("error:")
