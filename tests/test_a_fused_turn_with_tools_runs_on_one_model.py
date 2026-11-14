"""'Fuse this turn' on an agent that carries tools answers on ONE model, and says so (P0-09).

`FusionEngine.complete` drops tool schemas and ignores the ContextVar fence, so installing it as the
agent's backend fused a turn of a tool run and reported `fused: true` with the tools silently gone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api.runs import append_run, build_receipt
from chimera.config import Settings
from chimera.core.agent import AgentResult
from chimera.core.autonomous import Attempt, AutonomousResult
from chimera.interface import ChatSession
from chimera.tools import ToolRegistry
from chimera.tools.builtin import EchoTool


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry


def _sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            out.append((event, json.loads(line[len("data: "):])))
    return out


def test_a_chat_turn_with_tools_is_not_fused_and_the_done_frame_says_so(tmp_path: Path) -> None:
    fuse_backend = object()
    seen: dict[str, Any] = {}

    class _Agent:
        def __init__(self) -> None:
            self.backend: Any = object()
            self.tools = _registry()

        def run(self, task: str, *, on_token: Any = None, on_tool: Any = None) -> AgentResult:
            seen["backend"] = self.backend
            return AgentResult(answer="A", steps=1, stopped_reason="final")

    agent = _Agent()
    own = agent.backend
    client = TestClient(
        build_api_app(
            lambda: ChatSession(agent), settings=Settings(CHIMERA_HOME=str(tmp_path / "h")),
            fuse_backend=fuse_backend,
        )
    )

    done = next(
        d for e, d in _sse(client.post("/api/chat/stream", json={"message": "m", "fuse": True}).text)
        if e == "done"
    )

    assert seen["backend"] is own  # the fusion engine was never installed
    assert done["fused"] is False
    assert done["fuse_downgraded"] is True


def test_a_coding_turn_with_tools_is_not_fused_and_the_done_frame_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_code_api import _frames, _ScriptedAgent

    class _ToolAgent(_ScriptedAgent):
        def __init__(self) -> None:
            super().__init__()
            self.tools = _registry()
            self.backend: Any = object()

    agent = _ToolAgent()
    import chimera.core

    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: agent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    client = TestClient(
        build_api_app(
            lambda: ChatSession(_ScriptedAgent()), workspace=ws,
            settings=Settings(CHIMERA_HOME=str(tmp_path / "home")), fuse_backend=object(),
        )
    )
    own = agent.backend

    done = next(
        d for e, d in _frames(client.post("/api/code/turn", json={"message": "fix it", "fuse": True}))
        if e == "done"
    )

    assert agent.backend is own
    assert done["fused"] is False
    assert done["fuse_downgraded"] is True


def test_runs_route_returns_the_requested_model_and_effort_of_each_attempt(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = AutonomousResult(
        answer="ok", success=True,
        attempts=[
            Attempt(1, "a", False, False, False, requested_model="vendor/mid", effort=""),
            Attempt(2, "b", True, True, False, requested_model="vendor/mid#high", effort="high"),
        ],
    )
    append_run(home / "runs.jsonl", build_receipt(result, "t", None, "2026-11-14T00:00:00+00:00"))
    client = TestClient(
        build_api_app(lambda: ChatSession(object()), settings=Settings(CHIMERA_HOME=str(home)))  # type: ignore[arg-type]
    )

    attempts = client.get("/api/runs").json()[0]["attempts"]

    assert [(a["requested_model"], a["effort"]) for a in attempts] == [
        ("vendor/mid", ""),
        ("vendor/mid#high", "high"),
    ]
