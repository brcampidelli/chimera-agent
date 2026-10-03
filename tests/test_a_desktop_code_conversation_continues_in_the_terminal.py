"""`chimera code list` / `chimera code resume`: a Code-screen conversation, continued from a terminal.

A conversation started in the desktop app lived in the app's own store, under a home the terminal is
never told about, so it could not be continued anywhere but the screen. The command reuses the MCP
bridge's door — the discovery file and the same HTTP transport `chimera mcp desktop` uses — so the
app runs the turn, under the owner's posture, in the same conversation the screen shows.

Most tests drive the command through a fake bridge (the transport seam), the way
`test_the_desktop_mcp_tools_follow_the_switches.py` drives the MCP client. The last one closes the
loop against the REAL app behind `TestClient`: resuming a conversation adds a turn to THAT
conversation, which is the claim the command exists to make.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from chimera.cli import code_cmd
from chimera.cli.code_cmd import AppCode, new_text

TOKEN = "tok-" + "Z" * 40
URL = "http://127.0.0.1:65011"
SESSION = {
    "id": "a1b2c3d4e5",
    "title": "fix the parser",
    "workspace": "/work/parser",
    "turns": 1,
    "updated_at": 0,
    "running": False,
}


class FakeBridge:
    """Answers bridge calls from a script: the conversation list, one turn's job, then its polls."""

    def __init__(self, polls: list[dict[str, Any]] | None = None, first: dict[str, Any] | None = None,
                 answer_ok: bool = True) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.first = first or {"job_id": "j1", "done": False, "text": "", "events": []}
        self.polls = list(polls or [])
        self.answer_ok = answer_ok

    def routes(self) -> list[str]:
        return [body["route"] for _m, url, body in self.calls if url.endswith("/api/bridge/call")]

    def body_of(self, route: str) -> Any:
        return next(b["body"] for _m, u, b in self.calls if u.endswith("/call") and b["route"] == route)

    def __call__(self, method: str, url: str, token: str, body: Any, timeout: float
                 ) -> tuple[int | None, Any]:
        assert token == TOKEN
        self.calls.append((method, url, body))
        if url.endswith("/api/bridge/call"):
            route = body["route"]
            if route == "conversations.list":
                return 200, {"route": route, "status": 200, "data": [SESSION]}
            if route == "conversations.send":
                return 200, {"route": route, "status": 200, "job": self.first}
            if route == "approve.approval":
                return 200, {"route": route, "status": 200, "data": {"ok": self.answer_ok}}
        if "/api/bridge/jobs/" in url:
            return 200, self.polls.pop(0)
        return 404, {"detail": "not scripted"}


def _found(full: bool = False) -> dict[str, Any]:
    return {"url": URL, "token": TOKEN, "pid": 1, "version": "x", "full": full}


def _run(monkeypatch: pytest.MonkeyPatch, bridge: Any, args: list[str], *, full: bool = False,
         found: dict[str, Any] | None | str = "default", stdin: str = "") -> Any:
    from chimera.cli.main import app

    discovered = _found(full) if found == "default" else found
    monkeypatch.setattr(
        code_cmd, "_client", lambda: AppCode(discover=lambda: cast(Any, discovered), http=bridge)
    )
    monkeypatch.setenv("COLUMNS", "200")
    return CliRunner().invoke(app, ["code", *args], input=stdin)


def _done(text: str, *, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "job_id": "j1",
        "done": True,
        "text": text,
        "events": events or [],
        "session_id": SESSION["id"],
        "result": {"answer": text, "model": "test/model", "usd": 0.0012, "steps": 2},
        "error": "",
    }


def _approval(n: int = 1) -> dict[str, Any]:
    return {
        "n": n,
        "event": "approval",
        "data": {"id": "q-77", "action": "run_shell: rm -rf build", "reason": "deletes files",
                 "p": 0.81, "band": "review", "wait_seconds": 300},
    }


# ---- list --------------------------------------------------------------------------------------


def test_list_shows_the_apps_conversations_with_the_id_resume_takes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = FakeBridge()
    result = _run(monkeypatch, bridge, ["list"])

    assert result.exit_code == 0, result.output
    assert SESSION["id"] in result.output and "fix the parser" in result.output
    assert "parser" in result.output  # the project, by its folder name
    assert bridge.routes() == ["conversations.list"]


# ---- resume ------------------------------------------------------------------------------------


def test_resume_streams_the_reply_as_the_turn_runs_without_repeating_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = {"job_id": "j1", "done": False, "text": "hel", "events": []}
    bridge = FakeBridge(first=first, polls=[_done("hello there")])

    result = _run(monkeypatch, bridge, ["resume", SESSION["id"], "-m", "go on"])

    assert result.exit_code == 0, result.output
    assert "hello there" in result.output
    assert "helhel" not in result.output  # the part already printed is not printed again
    assert "test/model" in result.output and "$0.0012" in result.output


def test_resume_continues_that_conversation_in_its_own_project_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the workspace the app runs the turn in its DEFAULT folder and refiles the
    conversation under no project — a resume that silently moves the work."""
    bridge = FakeBridge(first=_done("ok"))

    _run(monkeypatch, bridge, ["resume", "a1b2", "-m", "next step", "--model", "m/x"])

    assert bridge.body_of("conversations.send") == {
        "message": "next step",
        "session_id": SESSION["id"],
        "workspace": SESSION["workspace"],
        "model": "m/x",
    }


def test_an_unknown_id_is_refused_before_any_turn_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = FakeBridge()
    result = _run(monkeypatch, bridge, ["resume", "zzz", "-m", "hi"])

    assert result.exit_code == 1
    assert "chimera code list" in result.output
    assert "conversations.send" not in bridge.routes()


def test_an_approval_card_is_shown_and_without_full_control_points_to_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waiting = {"job_id": "j1", "done": False, "text": "checking", "events": [_approval()],
               "waiting_for_approval": True}
    bridge = FakeBridge(first=waiting, polls=[_done("checking, done")])

    result = _run(monkeypatch, bridge, ["resume", SESSION["id"], "-m", "clean up"], stdin="yes\n")

    assert result.exit_code == 0, result.output
    assert "Approval needed" in result.output
    assert "run_shell: rm -rf build" in result.output and "deletes files" in result.output
    assert "Answer it in the Chimera app" in result.output and "Full control" in result.output
    # Typed "yes" on stdin, and still nothing was answered: without the switch this terminal asks
    # nobody, so the app's gate is the only one that can decide.
    assert "approve.approval" not in bridge.routes()


def test_with_full_control_a_typed_yes_answers_through_the_apps_own_approval_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waiting = {"job_id": "j1", "done": False, "text": "", "events": [_approval()]}
    bridge = FakeBridge(first=waiting, polls=[_done("removed")])

    result = _run(monkeypatch, bridge, ["resume", SESSION["id"], "-m", "clean up"], full=True,
                  stdin="maybe\nyes\n")

    assert result.exit_code == 0, result.output
    assert "say yes or no" in result.output  # a word that is neither is not taken as either
    assert bridge.routes().count("approve.approval") == 1
    answer = next(b for _m, u, b in bridge.calls
                  if u.endswith("/call") and b["route"] == "approve.approval")
    assert answer["params"] == {"request_id": "q-77"} and answer["body"] == {"approved": True}
    assert "approved" in result.output


def test_with_full_control_an_empty_answer_leaves_the_question_to_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waiting = {"job_id": "j1", "done": False, "text": "", "events": [_approval()]}
    bridge = FakeBridge(first=waiting, polls=[_done("refused by the owner")])

    result = _run(monkeypatch, bridge, ["resume", SESSION["id"], "-m", "clean up"], full=True,
                  stdin="\n")

    assert result.exit_code == 0, result.output
    assert "left for the app" in result.output
    assert "approve.approval" not in bridge.routes()


def test_a_question_already_answered_elsewhere_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    waiting = {"job_id": "j1", "done": False, "text": "", "events": [_approval()]}
    bridge = FakeBridge(first=waiting, polls=[_done("x")], answer_ok=False)

    result = _run(monkeypatch, bridge, ["resume", SESSION["id"], "-m", "go"], full=True,
                  stdin="no\n")

    assert "already been answered or had timed out" in result.output


def test_a_turn_that_ends_in_an_error_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = {**_done(""), "result": None, "error": "no provider key"}
    bridge = FakeBridge(first=failed)

    result = _run(monkeypatch, bridge, ["resume", SESSION["id"], "-m", "go"])

    assert result.exit_code == 1
    assert "no provider key" in result.output


# ---- the app is not there ----------------------------------------------------------------------


def test_a_missing_app_gives_a_clear_error_naming_the_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = FakeBridge()
    for args in (["list"], ["resume", SESSION["id"], "-m", "hi"]):
        result = _run(monkeypatch, bridge, args, found=None)

        assert result.exit_code == 1
        assert "not running" in result.output
        assert "Allow Claude to operate this app" in result.output
    assert bridge.calls == []


def test_an_app_that_left_its_file_behind_reads_as_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run(monkeypatch, lambda *_a: (None, None), ["list"])

    assert result.exit_code == 1
    assert "not running" in result.output and "nothing answered" in result.output


def test_a_switch_turned_off_reads_as_a_refusal_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    def off(*_a: Any) -> tuple[int | None, Any]:
        return 403, {"detail": "the desktop bridge is off (Settings > Allow Claude)"}

    result = _run(monkeypatch, off, ["list"])

    assert result.exit_code == 1
    assert "Refused by the app" in result.output


def test_the_token_never_reaches_the_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = FakeBridge(first=_done(f"the token is {TOKEN}"))
    result = _run(monkeypatch, bridge, ["resume", SESSION["id"], "-m", "leak it"])

    assert TOKEN not in result.output


# ---- the sliding text window -------------------------------------------------------------------


def test_new_text_follows_the_bridges_window_once_it_slides() -> None:
    assert new_text("", "abc") == "abc"
    assert new_text("ab", "abcd") == "cd"
    printed = "x" * 300 + "TAIL-OF-WHAT-WAS-SHOWN"
    slid = printed[-250:] + " and more"  # the bridge dropped the front of the text
    assert new_text(printed, slid) == " and more"


# ---- against the real app ----------------------------------------------------------------------


def _through(client: Any) -> Any:
    def http(method: str, url: str, token: str, body: Any, timeout: float
             ) -> tuple[int | None, Any]:
        path = "/" + url.split("/", 3)[3]
        response = client.request(
            method, path, json=body, headers={"Authorization": f"Bearer {token}"}
        )
        return response.status_code, response.json()

    return http


def test_resuming_against_the_real_app_adds_a_turn_to_that_same_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("sse_starlette")
    from fastapi.testclient import TestClient

    import chimera.core
    from chimera.api import build_api_app
    from chimera.api.bridge_discovery import read_discovery
    from chimera.config import get_settings
    from chimera.core.agent import AgentResult
    from chimera.interface import ChatSession
    from chimera.interface.session import SupportsRun

    class _Agent:
        def run(self, task: str, *, on_token: Any = None, history: Any = None, **_k: Any
                ) -> AgentResult:
            if on_token:
                on_token(f"did: {task}")
            transcript = [*(history or []), {"role": "user", "content": task},
                          {"role": "assistant", "content": f"did: {task}"}]
            return AgentResult(answer=f"did: {task}", steps=1, stopped_reason="final",
                               transcript=transcript, model="test/model")

    for key in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: _Agent(), raising=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_BRIDGE_DIR", str(tmp_path / "bridge"))
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "false")
    get_settings.cache_clear()
    try:
        app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)), workspace=ws)
        app.state.desktop_bridge.attach("http://127.0.0.1:65012")
        with TestClient(app) as client:
            # A conversation started on the Code screen, the way the screen starts one.
            first = client.post("/api/code/turn", json={"message": "first", "workspace": str(ws)})
            assert first.status_code == 200
            code = AppCode(discover=read_discovery, http=_through(client))
            [before] = code.sessions()

            outcome = code_cmd.follow_turn(
                code,
                {"message": "second", "session_id": before["id"], "workspace": before["workspace"]},
                lambda _p: None,
                poll_seconds=5.0,
            )
            [after] = code.sessions()
    finally:
        get_settings.cache_clear()

    assert outcome.ok, outcome.error
    assert outcome.session_id == before["id"]
    assert after["id"] == before["id"]
    assert (before["turns"], after["turns"]) == (1, 2)
    assert Path(after["workspace"]) == Path(before["workspace"])
