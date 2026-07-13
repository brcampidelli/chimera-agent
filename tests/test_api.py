"""Tests for the desktop API (FastAPI + SSE), no network — a fake agent drives the real ChatSession.

Skipped entirely when the optional 'desktop' extra (fastapi/sse-starlette) isn't installed.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.core.agent import AgentResult, ToolActivity  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402


class _FakeAgent:
    """Agent stub: streams two token deltas + one tool activity, returns a rich AgentResult."""

    def run(
        self,
        task: str,
        *,
        on_token: Callable[[str], None] | None = None,
        on_tool: Callable[[ToolActivity], None] | None = None,
    ) -> AgentResult:
        if on_tool is not None:
            on_tool(ToolActivity(name="read_file", arguments={}, ok=True, observation="ok"))
        if on_token is not None:
            on_token("Hel")
            on_token("lo")
        return AgentResult(
            answer="Hello",
            steps=1,
            stopped_reason="final",
            prompt_tokens=10,
            completion_tokens=2,
            usd=0.001,
            tool_names=["read_file"],
        )


def _client(tmp_path: Any, *, token: str | None = None) -> TestClient:
    from chimera.api import build_api_app

    # Construct via validation aliases (the fields only populate by alias, not python name), so home
    # actually points at tmp_path and doesn't pollute the repo's .chimera dir.
    kwargs: dict[str, Any] = {"CHIMERA_HOME": str(tmp_path / "home")}
    if token is not None:
        kwargs["CHIMERA_SERVER_TOKEN"] = token
    settings = Settings(**kwargs)

    def factory() -> ChatSession:
        return ChatSession(_FakeAgent())

    return TestClient(build_api_app(factory, settings=settings))


def _read_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse a raw SSE stream body into (event, data-dict) pairs."""
    events: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            events.append((event, json.loads(line[len("data:"):].strip())))
    return events


def test_chat_stream_emits_session_token_tool_done(tmp_path: Any) -> None:
    client = _client(tmp_path)
    resp = client.post("/api/chat/stream", json={"message": "hi", "stream": True})
    assert resp.status_code == 200
    events = _read_sse(resp.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "session"  # client learns its session id first
    assert "token" in kinds and "tool" in kinds and kinds[-1] == "done"
    tokens = [d["text"] for e, d in events if e == "token"]
    assert tokens == ["Hel", "lo"]  # deltas in order
    tool = next(d for e, d in events if e == "tool")
    assert tool == {"name": "read_file", "ok": True}
    done = next(d for e, d in events if e == "done")
    assert done["answer"] == "Hello"
    assert done["prompt_tokens"] == 10 and done["completion_tokens"] == 2
    assert done["usd"] == 0.001 and done["tool_names"] == ["read_file"]


def test_chat_stream_without_streaming_still_answers(tmp_path: Any) -> None:
    client = _client(tmp_path)
    resp = client.post("/api/chat/stream", json={"message": "hi", "stream": False})
    events = _read_sse(resp.text)
    assert "token" not in [e for e, _ in events]  # no token events when streaming is off
    done = next(d for e, d in events if e == "done")
    assert done["answer"] == "Hello"


def test_session_is_persisted_and_listed_and_deletable(tmp_path: Any) -> None:
    client = _client(tmp_path)
    resp = client.post("/api/chat/stream", json={"message": "remember me", "stream": True})
    sid = next(d for e, d in _read_sse(resp.text) if e == "session")["session_id"]

    listed = client.get("/api/sessions").json()
    assert any(s["id"] == sid and s["turns"] == 1 for s in listed)
    assert listed[0]["title"] == "remember me"  # title = first user message

    got = client.get(f"/api/sessions/{sid}").json()
    assert got["turns"] == [{"user": "remember me", "assistant": "Hello"}]

    assert client.delete(f"/api/sessions/{sid}").json() == {"deleted": True}
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_bearer_token_guards_chat_when_configured(tmp_path: Any) -> None:
    client = _client(tmp_path, token="s3cret")
    assert client.post("/api/chat/stream", json={"message": "hi"}).status_code == 401
    ok = client.post(
        "/api/chat/stream", json={"message": "hi"}, headers={"Authorization": "Bearer s3cret"}
    )
    assert ok.status_code == 200


def test_health_ok(tmp_path: Any) -> None:
    assert _client(tmp_path).get("/api/health").json()["status"] == "ok"
