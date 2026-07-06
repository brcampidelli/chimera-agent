"""Tests for A2A message/stream (M13 C2) — SSE streaming task lifecycle."""

from __future__ import annotations

from typing import Any

from chimera.integrations import A2AServer, chimera_agent_card


def _server() -> A2AServer:
    return A2AServer(solve=lambda text: f"done: {text}")


def _stream_request(text: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "message/stream",
        "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": text}]}},
    }


def test_stream_yields_working_then_completed() -> None:
    events = list(_server().stream(_stream_request("refactor")))
    assert len(events) == 2
    assert events[0]["result"]["status"]["state"] == "working"  # initial, before solve returns
    assert events[1]["result"]["status"]["state"] == "completed"
    assert events[1]["result"]["status"]["message"]["parts"][0]["text"] == "done: refactor"
    # Same task id + request id across the stream, so a client tracks one task.
    assert events[0]["result"]["id"] == events[1]["result"]["id"]
    assert all(e["id"] == 7 for e in events)


def test_stream_context_id_preserved() -> None:
    req = _stream_request("hi")
    req["params"]["contextId"] = "ctx-123"
    events = list(_server().stream(req))
    assert all(e["result"]["contextId"] == "ctx-123" for e in events)


def test_stream_task_is_fetchable_after() -> None:
    server = _server()
    events = list(server.stream(_stream_request("x")))
    task_id = events[-1]["result"]["id"]
    got = server.tasks_get({"id": task_id})
    assert got["status"]["state"] == "completed"


def test_stream_failed_solve() -> None:
    def boom(text: str) -> str:
        raise RuntimeError("model down")

    events = list(A2AServer(solve=boom).stream(_stream_request("x")))
    assert events[0]["result"]["status"]["state"] == "working"
    assert events[1]["result"]["status"]["state"] == "failed"
    assert "model down" in events[1]["result"]["status"]["message"]["parts"][0]["text"]


def test_stream_empty_message_is_error() -> None:
    bad = {"jsonrpc": "2.0", "id": 8, "method": "message/stream", "params": {"message": {"parts": []}}}
    events = list(_server().stream(bad))
    assert len(events) == 1 and events[0]["error"]["code"] == -32602


def test_agent_card_advertises_streaming() -> None:
    card = chimera_agent_card("http://x/a2a", version="0.2.0")
    assert card["capabilities"]["streaming"] is True
