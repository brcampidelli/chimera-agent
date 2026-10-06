"""Study 30 S30-44: fake-transport reproductions of bot robustness claims."""

from __future__ import annotations

import threading
from typing import Any

import pytest


def test_telegram_route_failure_does_not_end_polling_or_replay_update(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from chimera.server import TelegramAdapter

    offsets: list[int] = []

    class Response:
        def __init__(self, result: list[dict[str, Any]]) -> None:
            self.result = result

        def json(self) -> dict[str, Any]:
            return {"result": self.result}

    class Client:
        polls = 0

        def __init__(self, **_: Any) -> None: ...
        def __enter__(self) -> Client: return self
        def __exit__(self, *_: Any) -> bool: return False

        def get(self, _url: str, *, params: dict[str, Any]) -> Response:
            offsets.append(params["offset"])
            self.polls += 1
            if self.polls == 1:
                return Response([{"update_id": 10, "message": {"text": "hi", "from": {"id": 1}, "chat": {"id": 2}}}])
            adapter.stop()
            return Response([])

        def post(self, *_: Any, **__: Any) -> None: ...

    adapter = TelegramAdapter("TOKEN", poll_timeout=0)
    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr("chimera.server.telegram_adapter.run_with_indicator", lambda route, message, **_: route(message))

    def route(_message: Any) -> str:
        raise RuntimeError("fake route failure")

    adapter.start(route)
    assert offsets == [0, 11]


def test_telegram_post_failure_does_not_end_polling_or_replay_update(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from chimera.server import TelegramAdapter

    offsets: list[int] = []

    class Response:
        def __init__(self, result: list[dict[str, Any]]) -> None:
            self.result = result

        def json(self) -> dict[str, Any]:
            return {"result": self.result}

    class Client:
        polls = 0

        def __init__(self, **_: Any) -> None: ...
        def __enter__(self) -> Client: return self
        def __exit__(self, *_: Any) -> bool: return False

        def get(self, _url: str, *, params: dict[str, Any]) -> Response:
            offsets.append(params["offset"])
            self.polls += 1
            if self.polls == 1:
                return Response([{"update_id": 10, "message": {"text": "hi", "from": {"id": 1}, "chat": {"id": 2}}}])
            adapter.stop()
            return Response([])

        def post(self, *_: Any, **__: Any) -> None:
            raise httpx.ConnectError("fake post failure")

    adapter = TelegramAdapter("TOKEN", poll_timeout=0)
    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr("chimera.server.telegram_adapter.run_with_indicator", lambda route, message, **_: route(message))
    adapter.start(lambda _message: "reply")
    assert offsets == [0, 11]


def test_whatsapp_processes_all_entries_and_deduplicates_message_ids() -> None:
    from chimera.server import WhatsAppWebhook

    class Sender:
        def __init__(self) -> None:
            self.sent: list[tuple[str, str]] = []
        def send(self, chat_id: str, text: str) -> str:
            self.sent.append((chat_id, text))
            return "ok"

    calls: list[str] = []
    sender = Sender()

    def route(message: Any) -> str:
        calls.append(message.text)
        return f"reply:{message.text}"

    hook = WhatsAppWebhook(sender, "token", route)  # type: ignore[arg-type]
    first = {"id": "wamid.1", "from": "111", "type": "text", "text": {"body": "one"}}
    second = {"id": "wamid.2", "from": "222", "type": "text", "text": {"body": "two"}}
    payload = {"entry": [
        {"changes": [{"value": {"messages": [first, second]}}]},
        {"changes": [{"value": {"messages": [first]}}]},
    ]}

    assert hook.on_message(payload) == 2
    assert hook.on_message(payload) == 0
    assert calls == ["one", "two"]
    assert sender.sent == [("111", "reply:one"), ("222", "reply:two")]


def test_gateway_serializes_same_chat_turns_and_creates_one_session() -> None:
    from chimera.server import InboundMessage, MessageGateway

    entered = threading.Event()
    release = threading.Event()
    factory_calls: list[object] = []
    send_counts: list[int] = []
    lock = threading.Lock()

    class Session:
        def send(self, message: str) -> str:
            with lock:
                send_counts.append(1)
                turn = len(send_counts)
            if message == "first":
                entered.set()
                assert release.wait(timeout=3)
            return f"{turn}:{message}"

    def factory() -> Session:
        factory_calls.append(object())
        return Session()

    gateway = MessageGateway(factory)  # type: ignore[arg-type]
    results: list[str] = []
    first = threading.Thread(target=lambda: results.append(gateway.on_message(InboundMessage("first", "chat"))))
    second = threading.Thread(target=lambda: results.append(gateway.on_message(InboundMessage("second", "chat"))))
    first.start()
    assert entered.wait(timeout=3)
    second.start()
    second.join(timeout=0.1)
    raced_while_first_blocked = second.is_alive()
    release.set()
    first.join(timeout=3)
    second.join(timeout=3)

    assert raced_while_first_blocked, "the second turn must wait for the first turn's session lock"
    assert len(factory_calls) == 1
    assert sorted(results) == ["1:first", "2:second"]


def test_gateway_concurrent_first_session_lookup_creates_one_session() -> None:
    import time

    from chimera.server import MessageGateway

    sessions: list[object] = []

    class Session:
        def send(self, _message: str) -> str:
            return "ok"

    def factory() -> Session:
        time.sleep(0.05)
        session = Session()
        sessions.append(session)
        return session

    gateway = MessageGateway(factory)  # type: ignore[arg-type]
    threads = [threading.Thread(target=gateway.session_for, args=("new",)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert len(sessions) == 1
    assert gateway.session_for("new") is sessions[0]



