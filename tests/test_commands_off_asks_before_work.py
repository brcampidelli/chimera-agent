"""A request that needs commands, in a folder where they are off, is put to the owner before any tool.

Found on 30/10: a new folder had commands off. The agent spent 1h20 editing and only at the end said
"commands are off for this folder", so nothing it changed had been verified. Now the question comes at
the start of the turn, and only when an owner's screen can answer it. A grant is the screen's alone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.api import (
    build_api_app,  # noqa: E402
    code_api,  # noqa: E402
)
from chimera.api import screen_credential as sc  # noqa: E402
from chimera.api.code_api import (  # noqa: E402
    COMMANDS_OFF_OPTIONS,
    COMMANDS_UNVERIFIED_LINE,
    CodeTurnRequest,
    gate_commands_before_work,
)
from chimera.api.posture import Posture  # noqa: E402
from chimera.config import Settings, get_settings  # noqa: E402
from chimera.core.agent import AgentResult  # noqa: E402
from chimera.core.code_projects import CodeProjectRegistry  # noqa: E402
from chimera.core.context_budget import RunState  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402

TOKEN = "owner-bearer-for-the-commands-gate-tests"
NEEDS = "rode os testes e corrija o que falhar"
PLAIN = "o que faz a função parse_date?"
REACH = {"reach": "workspace_shell"}


class _Agent:
    """Records each run, so a test can see whether the agent started before or after the question."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.tasks: list[str] = []
        self.run_state = RunState()

    def run(self, task: str, **_kw: Any) -> AgentResult:
        self.tasks.append(task)
        self.events.append("ran")
        return AgentResult(
            answer="hello", steps=1, stopped_reason="final",
            transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "hello"}],
            tool_names=[], model="test/model",
        )


def _frames(response: Any) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            out.append((event, json.loads(line[len("data: "):])))
    return out


@pytest.fixture(autouse=True)
def _host_sandbox(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHIMERA_SANDBOX", "local")
    monkeypatch.delenv("CHIMERA_HOST_EXEC", raising=False)
    monkeypatch.delenv("CHIMERA_REACH", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(code_api, "_declined_here", set())
    yield
    get_settings.cache_clear()


@pytest.fixture()
def events() -> list[str]:
    return []


@pytest.fixture()
def agent(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> _Agent:
    import chimera.core

    instance = _Agent(events)
    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: instance, raising=True)
    return instance


def _settings(home: Path) -> Settings:
    return Settings(CHIMERA_HOME=str(home))  # type: ignore[call-arg]


def _fake_ask(
    monkeypatch: pytest.MonkeyPatch, events: list[str], answer: str | None, via: str = "screen"
) -> list[dict[str, Any]]:
    """Stand in for the typed question: record it, announce it, and answer as the test says."""
    asked: list[dict[str, Any]] = []

    def ask(home: Path, question: str, options: list[str], allow_free_text: bool, **kw: Any) -> str | None:
        events.append("asked")
        asked.append({"options": list(options), "tool": kw.get("tool"), "wait": kw.get("wait_seconds")})
        kw["announce"]("q1", {"id": "q1", "kind": "user_question", "question": question})
        if kw.get("answer_meta") is not None and answer is not None:
            kw["answer_meta"]["via"] = via
        return answer

    import chimera.governance.pending as pending

    monkeypatch.setattr(pending, "ask_user_durably", ask, raising=True)
    return asked


def _app(tmp_path: Path, home: Path) -> Any:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return build_api_app(
        lambda: ChatSession(_Agent([])), workspace=ws, settings=_settings(home),
        screen=sc.for_mode("app", server_token=TOKEN),
    )


def _screen_headers(app: Any) -> dict[str, str]:
    from chimera.api.screen_credential import HEADER_NAME

    return {"authorization": f"Bearer {TOKEN}", HEADER_NAME: str(app.state.screen_credential.value)}


def _turn(app: Any, message: str, *, session_id: str | None = None, screen: bool = True) -> Any:
    body: dict[str, Any] = {"message": message, "posture": REACH}
    if session_id:
        body["session_id"] = session_id
    headers = _screen_headers(app) if screen else None
    return TestClient(app).post("/api/code/turn", json=body, headers=headers)


def _req() -> CodeTurnRequest:
    return CodeTurnRequest(message=NEEDS, posture=Posture(reach="workspace_shell"))


def _gate(req: CodeTurnRequest, home: Path, folder: Path, **kw: Any) -> tuple[str, str, list[tuple[str, str]]]:
    notices: list[tuple[str, str]] = []
    params: dict[str, Any] = {
        "message": NEEDS, "attended": True, "screen_here": True, "session_id": "s1",
        "emit": lambda *_: None, "notice": lambda code, text, _data: notices.append((code, text)),
        "run_id": "r1",
    }
    params.update(kw)
    outcome, reason = gate_commands_before_work(req, _settings(home), folder, **params)
    return outcome, reason, notices


def test_a_folder_without_commands_is_asked_before_any_tool_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: _Agent, events: list[str]
) -> None:
    asked = _fake_ask(monkeypatch, events, answer="Continuar sem comandos")
    response = _turn(_app(tmp_path, tmp_path / "home"), NEEDS)
    assert response.status_code == 200
    assert len(asked) == 1 and asked[0]["options"] == list(COMMANDS_OFF_OPTIONS)
    assert events == ["asked", "ran"], "the question has to come before the agent starts"
    kinds = [event for event, _ in _frames(response)]
    assert "user_question" in kinds and kinds.index("user_question") < kinds.index("done")


def test_a_folder_with_commands_on_is_not_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: _Agent, events: list[str]
) -> None:
    home = tmp_path / "home"
    app = _app(tmp_path, home)
    CodeProjectRegistry(home / "code_projects.json").set_grant(str(tmp_path / "ws"), True)
    asked = _fake_ask(monkeypatch, events, answer="Cancelar")
    _turn(app, NEEDS)
    assert asked == []
    assert events == ["ran"]


def test_a_plain_question_is_not_asked_even_with_commands_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: _Agent, events: list[str]
) -> None:
    asked = _fake_ask(monkeypatch, events, answer="Cancelar")
    _turn(_app(tmp_path, tmp_path / "home"), PLAIN)
    assert asked == []
    assert events == ["ran"]


def test_cancelar_ends_the_turn_before_the_agent_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: _Agent, events: list[str]
) -> None:
    _fake_ask(monkeypatch, events, answer="Cancelar")
    response = _turn(_app(tmp_path, tmp_path / "home"), NEEDS)
    assert agent.tasks == []
    assert "ran" not in events
    assert _frames(response)[-1][0] == "done"


def test_liberar_from_the_screen_grants_the_folder_and_ends_the_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: _Agent, events: list[str]
) -> None:
    home = tmp_path / "home"
    _fake_ask(monkeypatch, events, answer="Liberar", via="screen")
    response = _turn(_app(tmp_path, home), NEEDS)
    assert agent.tasks == []
    assert CodeProjectRegistry(home / "code_projects.json").shell_granted_for(tmp_path / "ws")
    assert "Comandos liberados" in str(_frames(response)[-1][1].get("answer", ""))


def test_liberar_that_cannot_be_applied_ends_the_turn_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: _Agent, events: list[str]
) -> None:
    """Silence about a refused grant was the bug: the owner and the agent are both told, and it stops."""
    home = tmp_path / "home"
    _fake_ask(monkeypatch, events, answer="Liberar", via="cli")
    response = _turn(_app(tmp_path, home), NEEDS)
    assert agent.tasks == [], "the agent does not start when the grant was not applied"
    assert not CodeProjectRegistry(home / "code_projects.json").shell_granted_for(tmp_path / "ws")
    answer = str(_frames(response)[-1][1].get("answer", ""))
    assert "Não foi possível liberar comandos" in answer
    assert "resposta não veio da tela do dono" in answer


def test_continuar_runs_without_commands_and_says_first_that_nothing_was_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: _Agent, events: list[str]
) -> None:
    _fake_ask(monkeypatch, events, answer="Continuar sem comandos")
    response = _turn(_app(tmp_path, tmp_path / "home"), NEEDS)
    assert agent.tasks == [NEEDS]
    done = _frames(response)[-1][1]
    assert str(done.get("answer", "")).startswith(COMMANDS_UNVERIFIED_LINE)
    assert "commands_off" in response.text, "the notice says commands are off"


def test_silence_proceeds_without_commands_and_is_not_remembered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: _Agent, events: list[str]
) -> None:
    asked = _fake_ask(monkeypatch, events, answer=None)
    app = _app(tmp_path, tmp_path / "home")
    response = _turn(app, NEEDS, session_id="conv-silent")
    assert agent.tasks == [NEEDS]
    assert str(_frames(response)[-1][1].get("answer", "")).startswith(COMMANDS_UNVERIFIED_LINE)
    assert len(asked) == 1
    _turn(app, NEEDS, session_id="conv-silent")
    assert len(asked) == 2, "silence is not a choice: the next turn asks again"


def test_continuar_is_remembered_for_the_conversation_until_the_folder_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: _Agent, events: list[str]
) -> None:
    home = tmp_path / "home"
    app = _app(tmp_path, home)
    ws = tmp_path / "ws"
    asked = _fake_ask(monkeypatch, events, answer="Continuar sem comandos")
    _turn(app, NEEDS, session_id="conv-1")
    assert len(asked) == 1
    _turn(app, NEEDS, session_id="conv-1")
    assert len(asked) == 1, "the same conversation is not asked again"
    # Granting the folder changes its state: the memory is dropped, and a later revoke asks again.
    CodeProjectRegistry(home / "code_projects.json").set_grant(str(ws), True)
    _turn(app, NEEDS, session_id="conv-1")
    assert len(asked) == 1, "with commands on there is nothing to ask"
    CodeProjectRegistry(home / "code_projects.json").set_grant(str(ws), False)
    _turn(app, NEEDS, session_id="conv-1")
    assert len(asked) == 2, "a folder that lost its commands is asked again"


def test_a_bridge_turn_with_no_screen_waits_for_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bridge turn with no screen open cannot be answered: it must not sit on the 300 s wait."""
    import chimera.governance.pending as pending

    waited: list[float] = []

    def must_not_wait(*_a: Any, **kw: Any) -> None:
        waited.append(float(kw.get("wait_seconds", 0.0)))
        raise AssertionError("a turn no screen can answer must not ask")

    monkeypatch.setattr(pending, "ask_user_durably", must_not_wait, raising=True)
    req = _req()
    req._via_bridge = True
    outcome, _reason, notices = _gate(req, tmp_path / "home", tmp_path / "ws", screen_here=False)
    assert outcome == "proceed_unverified"
    assert waited == []
    assert [code for code, _ in notices] == ["commands_off"]


def test_a_bridge_turn_is_never_asked_so_it_can_never_be_granted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[str]
) -> None:
    req = _req()
    req._via_bridge = True
    asked = _fake_ask(monkeypatch, events, answer="Liberar", via="screen")
    outcome, _reason, notices = _gate(req, tmp_path / "home", tmp_path / "ws")
    assert asked == [], "a bridge turn is not asked: a grant from it could not be applied"
    assert outcome == "proceed_unverified"
    assert [code for code, _ in notices] == ["commands_off"]
    assert not CodeProjectRegistry(tmp_path / "home" / "code_projects.json").shell_granted_for(tmp_path / "ws")


def test_the_grant_question_waits_at_most_a_minute_for_the_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[str]
) -> None:
    asked = _fake_ask(monkeypatch, events, answer="Cancelar")
    _gate(_req(), tmp_path / "home", tmp_path / "ws")
    assert asked[0]["wait"] == 60.0


def test_a_screen_counts_as_present_only_while_it_talks(tmp_path: Path) -> None:
    """Holding the value is not enough: a browser tab keeps it after it closes."""
    cred = sc.for_mode("app", server_token=TOKEN)
    assert not cred.seen_within(60.0)
    assert cred.matches(str(cred.value))
    assert cred.seen_within(60.0)
    assert not cred.seen_within(60.0, now=(cred.seen_at or 0.0) + 61.0)


def test_a_folder_that_gets_commands_forgets_the_declined_choice_on_any_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[str]
) -> None:
    """A turn that is not the owner's (a channel, a bridge) still clears the memory once commands are on."""
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    asked = _fake_ask(monkeypatch, events, answer="Continuar sem comandos")
    _gate(_req(), home, ws, session_id="s1")
    assert len(asked) == 1
    registry = CodeProjectRegistry(home / "code_projects.json")
    registry.set_grant(str(ws), True)
    outcome, _, _ = _gate(_req(), home, ws, attended=False, session_id="other")
    assert outcome == "proceed"
    registry.set_grant(str(ws), False)
    _gate(_req(), home, ws, session_id="s1")
    assert len(asked) == 2, "after the folder changed state, the choice is no longer remembered"


def test_a_grant_that_leaves_commands_off_says_why_and_is_not_asked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[str]
) -> None:
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    asked = _fake_ask(monkeypatch, events, answer="Liberar", via="screen")
    read_only = Posture(reach="read_only")
    req = CodeTurnRequest(message=NEEDS, posture=read_only)
    outcome, reason, _ = _gate(req, home, ws, session_id="s2")
    assert outcome == "not_granted"
    assert "postura desta conversa" in reason
    assert CodeProjectRegistry(home / "code_projects.json").shell_granted_for(ws), "the record is kept"
    _gate(req, home, ws, session_id="s2")
    assert len(asked) == 1, "the same conversation is not asked again"


def test_an_unattended_turn_is_never_asked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    asked = _fake_ask(monkeypatch, events, answer="Liberar", via="screen")
    outcome, _reason, _ = _gate(_req(), tmp_path / "home", tmp_path / "ws", attended=False)
    assert outcome == "proceed"
    assert asked == []
