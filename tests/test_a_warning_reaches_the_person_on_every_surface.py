"""A warning that does not stop the turn has to reach the person, on the surface they are using.

`tests/test_a_run_can_warn_without_stopping.py` holds the half that lives in the loop. This holds
the other half, where a warning would quietly get lost: the stream the desktop reads, and the
terminal REPL's turn. Both hand the callback only to an agent whose `run` declares it, so an agent
written before the channel existed must keep working, unwarned, rather than fail one frame deeper.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.core.agent import AgentResult
from chimera.interface import render
from chimera.interface.session import ChatSession


def _result() -> AgentResult:
    return AgentResult(
        answer="done", steps=1, stopped_reason="final", transcript=[], tool_names=[], model="m",
    )


class _WarningAgent:
    """Declares `on_notice` and uses it."""

    def __init__(self) -> None:
        from chimera.core.context_budget import RunState

        self.run_state = RunState()

    def run(self, task: str, *, on_notice: Any = None, on_tool: Any = None, **kw: Any) -> AgentResult:
        if on_notice:
            on_notice("steps_low", "2 steps left before this turn stops", {"steps_left": 2})
        return _result()


class _OldAgent:
    """Written before the channel: no `on_notice` in its signature, and a `**kw` catch-all that
    would raise one frame deeper if it were handed the keyword anyway."""

    def __init__(self) -> None:
        from chimera.core.context_budget import RunState

        self.run_state = RunState()

    def run(
        self,
        task: str,
        *,
        on_tool: Any = None,
        on_token: Any = None,
        on_edit: Any = None,
        history: Any = None,
        turn_notes: Any = None,
    ) -> AgentResult:
        return _result()


def _frames(response: Any) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            out.append((event, json.loads(line[len("data: "):])))
    return out


def _client(tmp_path: Path, monkeypatch: Any, agent: Any) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: agent, raising=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return TestClient(
        build_api_app(lambda: ChatSession(_OldAgent()), workspace=ws, settings=settings)
    )


def test_the_desktop_stream_carries_a_warning_as_its_own_frame(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client = _client(tmp_path, monkeypatch, _WarningAgent())
    response = client.post("/api/code/turn", json={"message": "go"})

    notices = [p for event, p in _frames(response) if event == "notice"]
    assert [n["code"] for n in notices] == ["steps_low"]
    assert notices[0]["text"] == "2 steps left before this turn stops"
    assert notices[0]["steps_left"] == 2
    assert _frames(response)[-1][0] == "done"  # the turn still finished the way it would have


def test_an_agent_from_before_the_channel_still_runs_on_the_desktop_path(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client = _client(tmp_path, monkeypatch, _OldAgent())
    response = client.post("/api/code/turn", json={"message": "go"})

    assert response.status_code == 200
    events = [event for event, _ in _frames(response)]
    assert "notice" not in events and "error" not in events and events[-1] == "done"


@pytest.mark.parametrize("agent_cls", [_WarningAgent, _OldAgent])
def test_the_terminal_turn_hands_the_callback_only_to_an_agent_that_declares_it(
    agent_cls: Any,
) -> None:
    heard: list[tuple[str, str]] = []
    session = ChatSession(agent_cls())

    report = session.send_verbose("go", on_notice=lambda c, t, d: heard.append((c, t)))

    assert report.answer == "done"
    if agent_cls is _WarningAgent:
        assert heard == [("steps_low", "2 steps left before this turn stops")]
    else:
        assert heard == []


def test_the_terminal_says_a_known_warning_and_a_code_it_was_not_taught() -> None:
    assert "2 steps left" in render.notice_line("steps_low", "server words")
    # An unknown code is still shown, with the words the agent sent: silence would hide a warning
    # a newer build learned to send.
    assert "something new" in render.notice_line("from_the_future", "something new")
    # And what the agent sends is text, not markup.
    assert "[bold]" not in render.notice_line("from_the_future", "[bold]x[/bold]").replace(
        "\\[bold]", ""
    )
