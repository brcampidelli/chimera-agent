"""Tests for the A2A adapter (M12b) — agent card + task lifecycle over JSON-RPC."""

from __future__ import annotations

from typing import Any

from chimera.integrations import A2AServer, chimera_agent_card
from chimera.server.gateway import MessageGateway
from chimera.server.http import handle


def _server() -> A2AServer:
    return A2AServer(solve=lambda text: f"done: {text}")


def _send(text: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": text}]}},
    }


def test_agent_card_shape() -> None:
    card = chimera_agent_card("http://x/a2a", version="0.2.0")
    assert card["name"] == "Chimera" and card["url"] == "http://x/a2a"
    assert card["version"] == "0.2.0"
    skill_ids = {s["id"] for s in card["skills"]}
    assert {"solve", "fuse"} <= skill_ids


def test_message_send_completes_task() -> None:
    resp = _server().dispatch(_send("refactor"))
    task = resp["result"]
    assert task["kind"] == "task"
    assert task["status"]["state"] == "completed"
    assert task["status"]["message"]["parts"][0]["text"] == "done: refactor"
    assert task["status"]["message"]["role"] == "agent"


def test_tasks_get_after_send() -> None:
    server = _server()
    task_id = server.dispatch(_send("hi"))["result"]["id"]
    got = server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tasks/get", "params": {"id": task_id}})
    assert got["result"]["id"] == task_id and got["result"]["status"]["state"] == "completed"


def test_tasks_get_unknown_is_error() -> None:
    resp = _server().dispatch({"jsonrpc": "2.0", "id": 3, "method": "tasks/get", "params": {"id": "nope"}})
    assert resp["error"]["code"] == -32001


def test_unknown_method_is_error() -> None:
    resp = _server().dispatch({"jsonrpc": "2.0", "id": 4, "method": "frobnicate", "params": {}})
    assert resp["error"]["code"] == -32601


def test_message_send_without_text_is_invalid_params() -> None:
    bad = {"jsonrpc": "2.0", "id": 5, "method": "message/send", "params": {"message": {"parts": []}}}
    resp = _server().dispatch(bad)
    assert resp["error"]["code"] == -32602


def test_failed_solve_becomes_failed_task() -> None:
    def boom(text: str) -> str:
        raise RuntimeError("model down")

    resp = A2AServer(solve=boom).dispatch(_send("x"))
    assert resp["result"]["status"]["state"] == "failed"
    assert "model down" in resp["result"]["status"]["message"]["parts"][0]["text"]


def test_http_serves_agent_card_and_dispatches() -> None:
    gateway = MessageGateway(lambda: None)  # not exercised for A2A routes
    pair = (_server(), chimera_agent_card("http://h/a2a", version="0.2.0"))

    status, card = handle(gateway, "GET", "/.well-known/agent.json", b"", a2a=pair)
    assert status == 200 and isinstance(card, dict) and card["name"] == "Chimera"

    import json

    body = json.dumps(_send("via http")).encode()
    status, resp = handle(gateway, "POST", "/a2a", body, a2a=pair)
    assert status == 200 and isinstance(resp, dict)
    assert resp["result"]["status"]["message"]["parts"][0]["text"] == "done: via http"


def test_http_a2a_absent_when_not_configured() -> None:
    gateway = MessageGateway(lambda: None)
    status, _ = handle(gateway, "GET", "/.well-known/agent.json", b"")
    assert status == 404  # no A2A unless the pair is passed
