"""`desktop_job` hands back a finished turn a client can read in one tool result.

Measured 2026-10-06: the app's job record carries the whole result twice (as ``result`` and as the
final ``done`` event), with every generation id and every tool call by name. A 233-step turn came
back as 288,000 characters — past what an MCP client accepts — so the answer arrived as an overflow
file. The record on the app is untouched; the bridge returns a compact copy.
"""

from __future__ import annotations

import json
from typing import Any

from chimera.server.desktop_mcp import DesktopMCP

TOKEN = "tok-" + "Q" * 40


def _finished_job(steps: int) -> dict[str, Any]:
    result = {
        "answer": "Pronto.",
        "model": "m/x",
        "usd": 0.8,
        "steps": steps,
        "tool_names": ["read_file", "run_shell", "read_file"] * (steps // 3),
        "generation_ids": [f"gen-{i:08d}-aaaaaaaaaaaaaaaaaaaa" for i in range(steps)],
    }
    return {
        "job_id": "j1", "route": "conversations.send", "done": True, "session_id": "s1",
        "turn_id": "t1", "text": "Pronto.", "result": result, "error": "",
        "events": [{"n": 1, "event": "tool", "data": {"name": "read_file"}},
                   {"n": 2, "event": "done", "data": dict(result)}],
        "next": 2,
    }


def _ask(job: dict[str, Any]) -> str:
    def http(method: str, url: str, tok: str, body: Any, timeout: float) -> tuple[int, Any]:
        return 200, job

    def found() -> dict[str, Any]:
        return {"url": "http://127.0.0.1:65009", "token": TOKEN, "pid": 1, "version": "x",
                "full": False}

    return DesktopMCP(discover=found, http=http).dispatch("desktop_job", {"job_id": "j1"})


def test_a_long_turn_comes_back_compact() -> None:
    text = _ask(_finished_job(240))
    data = json.loads(text)
    assert "generation_ids" not in data["result"]
    assert data["result"]["generations"] == 240
    assert data["result"]["tool_names"] == {"read_file": 160, "run_shell": 80}
    done = [e for e in data["events"] if e["event"] == "done"]
    assert done == [{"n": 2, "event": "done", "data": {"answer": "(see result)"}}]
    # What the client was waiting for is all still there.
    assert data["result"]["answer"] == "Pronto." and data["result"]["usd"] == 0.8
    assert len(text) < 3000


def test_a_job_still_running_passes_through() -> None:
    job = {"job_id": "j1", "done": False, "events": [{"n": 1, "event": "tool", "data": {}}],
           "next": 1}
    assert json.loads(_ask(job))["events"] == job["events"]
