"""A guest's view of a conversation is SEALED when the turn is made, not guessed from its text after.

Third round of audit P1b (second Codex review). The projection of the previous round still decided
from content and from names, which each had a way through:

* authorship was "has an author" — a turn the owner sent from Discord (``author="discord"``) counted
  as a guest's, streamed its text and entered the guest's model context whole;
* the "human" message was whatever was sent to the model, so an attached document's text, an
  expanded command or a mention's context reached the guest as what the person said;
* the final answer was the last assistant text — an older answer the server had augmented, the
  fragment before an internal retry, an error sentence with a path and an ACP agent's narration all
  qualified;
* an ACP tool's name is ``"<kind>: <title>"`` and the title is the operation, path included;
* the jobs and works notes of the owner went into a guest's turn context.

Each is sealed where it is created (``guest_history``) and the projection reads the seal only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.guest_projection import project_frame
from chimera.api.sharing import SessionBus
from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult, ToolActivity
from chimera.core.guest_history import (
    ANSWER_UNAVAILABLE,
    AUTO_PLACEHOLDER,
    IN_PROGRESS_TEXT,
    UNFINISHED_TEXT,
    project_history,
)
from chimera.interface import ChatSession

FILE_CONTENT = "CLIENTE-741 saldo=9318204"
MIDWAY = "saldo encontrado: 9318204; vou conferir"
PRIVATE_PATH = "C:/Users/dono/privado/clientes.csv"
FINAL = "All done"


class _Agent:
    histories: list[list[Any]] = []
    notes: list[str] = []
    tasks: list[str] = []
    mode = ""
    ws: Path | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        config = kwargs.get("config") or (args[2] if len(args) > 2 else None)
        type(self).notes.append(str(getattr(config, "turn_notes", "")))

    def run(self, task: str, *, on_token: Any = None, on_tool: Any = None, on_edit: Any = None,
            history: Any = None, **_: Any) -> AgentResult:
        prior = list(history or [])
        if type(self).ws is not None and task.startswith("work please"):
            # A turn that changed a file is what the verifier runs for.
            (type(self).ws / "src.py").write_text(f"x = {len(type(self).tasks)}", encoding="utf-8")
            if on_edit:
                on_edit("src.py", "--- src.py+++ src.py")
        type(self).histories.append(prior)
        type(self).tasks.append(task)
        if type(self).mode == "repair_raises" and "verification" in task.lower():
            raise RuntimeError(f"repair failed at {PRIVATE_PATH}")
        if type(self).mode in ("secret_first_answer", "repair_raises") and task.startswith("work"):
            text = f"first pass: {FILE_CONTENT}"
            return AgentResult(
                answer=text, steps=1, stopped_reason="final", model="test/model",
                transcript=[*prior, {"role": "user", "content": task},
                            {"role": "assistant", "content": text}])
        if type(self).mode == "secret_first_answer" and "verification" in task.lower():
            return AgentResult(
                answer="repaired", steps=1, stopped_reason="final", model="test/model",
                transcript=[*prior, {"role": "user", "content": task},
                            {"role": "assistant", "content": "repaired"}])
        if type(self).mode == "draft_with_private_data" and task.startswith("deadline"):
            text = f"The deadline is Friday. {FILE_CONTENT}"
            return AgentResult(
                answer=text, steps=1, stopped_reason="final", model="test/model",
                transcript=[*prior, {"role": "user", "content": task},
                            {"role": "assistant", "content": text}])
        if type(self).mode == "timeout":
            text = f"the model provider did not answer in time, so the run stopped (timeout {PRIVATE_PATH})"
            return AgentResult(
                answer=text, steps=1, stopped_reason="provider_timeout", model="test/model",
                transcript=[*prior, {"role": "user", "content": task},
                            {"role": "assistant", "content": text}])
        if task.startswith("work"):
            args = {"path": PRIVATE_PATH}
            if on_tool:
                on_tool(ToolActivity("read_file", args, True, FILE_CONTENT))
            if on_token:
                on_token(MIDWAY)
                on_token(FINAL)
            return AgentResult(
                answer=FINAL, steps=2, stopped_reason="final", model="test/model",
                transcript=[
                    *prior, {"role": "user", "content": task},
                    {"role": "assistant", "content": MIDWAY, "tool_calls": [{
                        "id": "c1", "type": "function",
                        "function": {"name": "read_file", "arguments": json.dumps(args)}}]},
                    {"role": "tool", "tool_call_id": "c1", "content": FILE_CONTENT},
                    {"role": "assistant", "content": FINAL}])
        return AgentResult(
            answer="ok", steps=1, stopped_reason="final", model="test/model",
            transcript=[*prior, {"role": "user", "content": task}, {"role": "assistant", "content": "ok"}])


@pytest.fixture(autouse=True)
def _fresh() -> Any:
    get_settings.cache_clear()
    _Agent.histories, _Agent.notes, _Agent.tasks, _Agent.mode = [], [], [], ""
    yield
    get_settings.cache_clear()


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, TestClient, Path]:
    import chimera.core
    from chimera.api import build_api_app

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    for key in ("CHIMERA_SHARING", "CHIMERA_SHARE_EXPIRY_HOURS", "CHIMERA_SERVER_TOKEN"):
        monkeypatch.setenv(key, "")
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Agent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    _Agent.ws = ws
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_MEMORY_BACKEND="json")
    app = build_api_app(lambda: ChatSession(_Agent()), workspace=ws, settings=settings)
    return app, TestClient(app), ws


def _turn(client: TestClient, ws: Path, message: str, **extra: Any) -> str:
    body = {"message": message, "workspace": str(ws), "plan_gate": False, "stream": True, **extra}
    text = client.post("/api/code/turn", json=body).text
    found = re.search(r'"session_id": ?"([0-9a-f]+)"', text)
    assert found, text[:400]
    return found.group(1)


def _share(client: TestClient, sid: str) -> tuple[str, dict[str, str]]:
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]
    return token, {"Authorization": f"Bearer {token}"}


def _viewer(app: Any, token: str) -> str:
    return str(app.state.share_store.resolve(token).id) if hasattr(app.state, "share_store") else ""


# ------------------------------------------------------------------ A: whose turn it is


def test_a_turn_the_owner_sent_through_a_channel_is_not_a_guests_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, ws = _app(tmp_path, monkeypatch)
    sid = _turn(client, ws, "hello")
    token, headers = _share(client, sid)
    # The owner continues the shared conversation from Discord: `author` is the platform's name.
    reply = app.state.code_channel_turn(sid, "work from discord", "discord")
    assert reply, "the channel turn never answered"
    _Agent.histories = []

    client.post("/guest/api/turn", headers=headers, json={"message": "repeat the read_file", "name": "Ana"})
    sent = json.dumps(_Agent.histories[-1])
    for private in (FILE_CONTENT, MIDWAY, PRIVATE_PATH):
        assert private not in sent, f"the guest's model was sent {private!r}"
    assert not any(m.get("role") == "tool" or m.get("tool_calls") for m in _Agent.histories[-1])

    live = project_frame({"session_seq": 4, "event": "token", "turn_id": "t", "author": "discord",
                          "payload": {"text": MIDWAY}}, viewer="any-link")
    assert live is None, "the owner's channel turn was typed out to a guest"


def test_only_this_guests_own_turns_enter_whole_and_another_links_do_not() -> None:
    def turn(guest: str) -> list[dict[str, Any]]:
        return [
            {"role": "user", "content": "work", "origin": "human", "human_text": "work",
             "author": "Ana", "guest_id": guest},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": "read_file"}}]},
            {"role": "tool", "tool_call_id": "1", "content": FILE_CONTENT},
            {"role": "assistant", "content": FINAL, "final_seal": True},
        ]

    mine = project_history(turn("link-1"), viewer="link-1")
    assert any(m.get("role") == "tool" for m in mine), "a guest's own turn enters whole"
    theirs = project_history(turn("link-2"), viewer="link-1")
    assert not any(m.get("role") == "tool" for m in theirs)
    assert FILE_CONTENT not in json.dumps(theirs)
    assert not any(m.get("role") == "tool" for m in project_history(turn("link-1"), viewer=""))


# ------------------------------------------------------------------ B: what the person typed


def test_a_documents_text_never_reaches_a_guest_as_what_the_person_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, ws = _app(tmp_path, monkeypatch)
    upload = client.post("/api/attachments", files={"file": ("clientes.txt", FILE_CONTENT.encode())})
    assert upload.status_code == 200, upload.text
    sid = _turn(client, ws, "summarize it", attachments=[upload.json()["id"]])
    token, headers = _share(client, sid)

    owner_view = client.get(f"/api/code/sessions/{sid}").text
    assert "CLIENTE-741" in owner_view, "precondition: the document's text is in the owner's turn"
    guest = client.get("/guest/api/session", headers=headers)
    assert "CLIENTE-741" not in guest.text and "9318204" not in guest.text
    assert guest.json()["exchanges"][0]["you"] == "summarize it"

    frames = [project_frame(f, viewer="x") for f in app.state.session_bus.replay(sid)]
    shown = json.dumps([f for f in frames if f is not None])
    assert "CLIENTE-741" not in shown
    assert '"message": "summarize it"' in shown

    _Agent.histories = []
    client.post("/guest/api/turn", headers=headers, json={"message": "what did it say?", "name": "Ana"})
    assert "CLIENTE-741" not in json.dumps(_Agent.histories[-1])
    assert any(m.get("content") == "summarize it" for m in _Agent.histories[-1])


def test_a_message_with_no_sealed_human_text_is_a_placeholder() -> None:
    history = project_history([
        {"role": "user", "content": f"summarize it\n\nAttached document:\n{FILE_CONTENT}", "origin": "human"},
        {"role": "assistant", "content": FINAL, "final_seal": True},
    ])
    assert history[0] == {"role": "user", "content": AUTO_PLACEHOLDER}
    assert FILE_CONTENT not in json.dumps(history)


# ------------------------------------------------------------------ C: the final answer


def test_an_old_augmented_answer_and_a_pre_retry_fragment_are_never_shown() -> None:
    from chimera.api.code_replay import exchanges_from_messages
    from chimera.api.guest_projection import project_exchange

    old = [
        {"role": "user", "content": "q", "origin": "human", "human_text": "q"},
        {"role": "assistant", "content": f"Done. Verification failed: {FILE_CONTENT}"},
    ]
    assert FILE_CONTENT not in json.dumps(project_history(old))
    assert project_history(old)[-1] == {"role": "assistant", "content": ANSWER_UNAVAILABLE}

    fragment = [
        {"role": "user", "content": "q", "origin": "human", "human_text": "q"},
        {"role": "assistant", "content": "saldo=9318204", "tool_calls": []},  # truncated call, no seal
        {"role": "user", "content": "your tool call was cut off, retry"},
        {"role": "assistant", "content": FINAL, "final_seal": True, "model_answer": FINAL},
    ]
    shown_history = json.dumps(project_history(fragment))
    assert "9318204" not in shown_history and FINAL in shown_history
    exchanges = [project_exchange(e) for e in exchanges_from_messages(fragment)]
    assert "9318204" not in json.dumps(exchanges)
    assert exchanges[0] is not None and exchanges[0]["answer"] == ANSWER_UNAVAILABLE
    assert exchanges[1] is not None and exchanges[1]["answer"] == FINAL


def test_a_turn_that_did_not_end_normally_is_not_sealed_and_its_error_text_does_not_reach_a_guest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, ws = _app(tmp_path, monkeypatch)
    sid = _turn(client, ws, "hello")
    token, headers = _share(client, sid)
    _Agent.mode = "timeout"
    _turn(client, ws, "go on", session_id=sid)

    assert PRIVATE_PATH in client.get(f"/api/code/sessions/{sid}").text, "the owner still reads it"
    guest = client.get("/guest/api/session", headers=headers)
    assert "privado" not in guest.text and "timeout" not in guest.text
    assert guest.json()["exchanges"][-1]["answer"] == ANSWER_UNAVAILABLE
    frames = [project_frame(f, viewer="x") for f in app.state.session_bus.replay(sid)]
    done = [f for f in frames if f is not None and f["event"] == "done"][-1]
    assert done["payload"]["answer"] == UNFINISHED_TEXT
    assert "privado" not in json.dumps([f for f in frames if f is not None])


# ------------------------------------------------------------------ D: an external (ACP) agent


def test_an_acp_turn_gives_a_guest_the_text_after_its_last_tool_and_only_the_tools_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.api.code_acp as code_acp
    from chimera.acp.turn import AcpTurnResult

    def run_external_turn(**kw: Any) -> Any:
        kw["on_token"](f"reading {FILE_CONTENT} first. ")
        kw["on_tool"](f"read: Read {PRIVATE_PATH}", {}, True, FILE_CONTENT)
        kw["on_token"](FINAL)
        return AcpTurnResult(
            answer=f"reading {FILE_CONTENT} first. {FINAL}", final_text=FINAL, stop_reason="end_turn",
            tool_names=["read"],
        )

    monkeypatch.setattr(code_acp, "run_external_turn", run_external_turn)
    app, client, ws = _app(tmp_path, monkeypatch)
    sid = _turn(client, ws, "go", provider="claude")
    token, headers = _share(client, sid)

    frames = [project_frame(f, viewer="x") for f in app.state.session_bus.replay(sid)]
    shown = json.dumps([f for f in frames if f is not None])
    assert PRIVATE_PATH not in shown and "CLIENTE-741" not in shown and "privado" not in shown
    tools = [f["payload"] for f in frames if f is not None and f["event"] == "tool"]
    assert tools == [{"name": "read", "ok": True, "status": "ok"}]
    done = [f for f in frames if f is not None and f["event"] == "done"][-1]
    assert done["payload"]["answer"] == FINAL

    guest = client.get("/guest/api/session", headers=headers)
    assert "CLIENTE-741" not in guest.text
    assert guest.json()["exchanges"][-1]["answer"] == FINAL


def test_acp_text_before_a_tool_call_is_not_the_final_text() -> None:
    from chimera.acp.turn import AcpTurn

    turn = AcpTurn.__new__(AcpTurn)
    from chimera.acp.turn import AcpTurnResult

    turn._result = AcpTurnResult()  # type: ignore[attr-defined]
    turn._on_token = None  # type: ignore[attr-defined]
    turn._calls = {}  # type: ignore[attr-defined]
    turn._on_tool = None  # type: ignore[attr-defined]
    turn._on_edit = None  # type: ignore[attr-defined]
    chunk = {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "narration "}}
    turn._notify("session/update", {"update": chunk})  # type: ignore[attr-defined]
    turn._notify("session/update", {"update": {"sessionUpdate": "tool_call", "toolCallId": "1"}})  # type: ignore[attr-defined]
    turn._notify("session/update", {"update": {  # type: ignore[attr-defined]
        "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "closing"}}})
    assert turn._result.answer == "narration closing"  # type: ignore[attr-defined]
    assert turn._result.final_text == "closing"  # type: ignore[attr-defined]


# ------------------------------------------------------------------ E: the owner's jobs and works


def test_the_owners_job_and_work_notes_are_not_put_in_a_guests_turn_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.core.jobs as jobs
    from chimera.api.works import WorkManager

    monkeypatch.setattr(jobs, "finished_note", lambda *a, **k: "JOB-NOTE: ran `cat C:/privado/x`")
    monkeypatch.setattr(WorkManager, "note", lambda self, parent: "WORK-NOTE: failed at C:/privado/y")
    app, client, ws = _app(tmp_path, monkeypatch)
    sid = _turn(client, ws, "hello")
    assert any("JOB-NOTE" in n for n in _Agent.notes), "precondition: the owner's turn gets the notes"
    token, headers = _share(client, sid)
    _Agent.notes = []

    client.post("/guest/api/turn", headers=headers, json={"message": "which jobs finished?", "name": "Ana"})
    assert _Agent.notes, "the guest's turn never built an agent"
    for note in _Agent.notes:
        assert "JOB-NOTE" not in note and "WORK-NOTE" not in note and "privado" not in note


# ------------------------------------------------------------------ the seal is written when the turn closes


def _verified_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gate: Any) -> tuple[Any, TestClient, Path]:
    """The app of the other tests, with a verifier that fails once ``gate`` is set."""
    from types import SimpleNamespace

    import chimera.core.verify

    app, client, ws = _app(tmp_path, monkeypatch)
    monkeypatch.setattr("chimera.api.app.resolve_verify", lambda request, _ws: ("pytest -q", "user"))

    class _Verifier:
        def __init__(self, command: str, workspace: Path, **kwargs: Any) -> None:
            pass

        def verify(self) -> Any:
            gate.wait(20)
            return SimpleNamespace(passed=False, abstained=False, output="verifier output")

    monkeypatch.setattr(chimera.core.verify, "CommandVerifier", _Verifier)
    return app, client, ws


def test_while_the_verifier_runs_a_guest_is_not_given_the_first_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    import time

    gate = threading.Event()
    app, client, ws = _verified_app(tmp_path, monkeypatch, gate)
    gate.set()
    sid = _turn(client, ws, "hello")
    gate.clear()
    token, headers = _share(client, sid)
    _Agent.mode = "secret_first_answer"

    worker = threading.Thread(
        target=lambda: _turn(client, ws, "work please", session_id=sid, verify_retry=True), daemon=True,
    )
    worker.start()
    try:
        for _ in range(100):  # the first pass is stored before the verifier is asked
            if FILE_CONTENT in client.get(f"/api/code/sessions/{sid}").text:
                break
            time.sleep(0.1)
        assert FILE_CONTENT in client.get(f"/api/code/sessions/{sid}").text, "precondition: pass one is stored"
        guest = client.get("/guest/api/session", headers=headers)
        assert FILE_CONTENT not in guest.text and "9318204" not in guest.text
        assert guest.json()["exchanges"][-1]["answer"] == IN_PROGRESS_TEXT
    finally:
        gate.set()
        worker.join(30)
    after = client.get("/guest/api/session", headers=headers)
    assert "9318204" not in after.text, "the first pass is never the final answer"
    assert after.json()["exchanges"][-1]["answer"] == "repaired"


def test_a_repair_that_raises_leaves_no_sealed_answer_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    gate = threading.Event()
    gate.set()
    app, client, ws = _verified_app(tmp_path, monkeypatch, gate)
    sid = _turn(client, ws, "hello")
    token, headers = _share(client, sid)
    _Agent.mode = "repair_raises"
    _turn(client, ws, "work please", session_id=sid, verify_retry=True)

    assert any("verification" in task.lower() for task in _Agent.tasks), "precondition: a repair was asked"
    (stored,) = (tmp_path / "home").rglob(f"{sid}.json")
    messages = json.loads(stored.read_text(encoding="utf-8"))["messages"]
    first_pass = [m for m in messages if m.get("role") == "assistant" and FILE_CONTENT in str(m.get("content"))]
    assert first_pass, "precondition: the first pass is in the stored conversation"
    assert not any(m.get("final_seal") and FILE_CONTENT in str(m.get("content")) for m in messages), (
        "a seal was written for a turn whose repair failed"
    )
    guest = client.get("/guest/api/session", headers=headers)
    assert "9318204" not in guest.text


# ------------------------------------------------------------------ job tools, and a card's prompt


def test_a_guests_turn_has_no_job_tool_to_list_or_read_the_owners_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.core
    from chimera.api.guest_audience import WITHHELD_TOOLS

    assert {"job_status", "job_cancel"} <= WITHHELD_TOOLS
    seen: dict[str, Any] = {}

    class _Spy(_Agent):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            tools = args[1] if len(args) > 1 else kwargs.get("tools")
            names = tools.names() if hasattr(tools, "names") else []
            seen.setdefault("calls", []).append(list(names))

    app, client, ws = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(chimera.core, "Agent", _Spy, raising=True)
    sid = _turn(client, ws, "hello")
    assert "job_status" in seen["calls"][-1], "precondition: the owner's turn has the tool"
    token, headers = _share(client, sid)
    client.post("/guest/api/turn", headers=headers, json={"message": "list the jobs", "name": "Ana"})
    guest_tools = seen["calls"][-1]
    assert guest_tools and "job_status" not in guest_tools and "job_cancel" not in guest_tools


def test_a_card_the_model_proposed_is_never_what_a_person_said_to_a_guest() -> None:
    from chimera.api.code_replay import exchanges_from_messages
    from chimera.api.guest_projection import project_exchange, project_frame
    from chimera.core.guest_history import CARD_TURN_TEXT

    generated = f"fix the parser for {FILE_CONTENT}"
    messages = [
        {"role": "user", "content": generated, "origin": "auto", "human_text": CARD_TURN_TEXT},
        {"role": "assistant", "content": FINAL, "final_seal": True, "model_answer": FINAL},
    ]
    assert FILE_CONTENT not in json.dumps(project_history(messages))
    assert project_history(messages)[0] == {"role": "user", "content": AUTO_PLACEHOLDER}
    (exchange,) = exchanges_from_messages(messages)
    shown = project_exchange(exchange)
    assert shown is not None and shown["you"] == AUTO_PLACEHOLDER and FILE_CONTENT not in json.dumps(shown)
    frame = project_frame({"session_seq": 1, "event": "turn_started", "turn_id": "t", "author": "",
                           "payload": {"message": generated, "origin": "auto"}})
    assert frame is not None and frame["payload"]["message"] == AUTO_PLACEHOLDER


def test_a_turn_started_from_a_proposed_card_is_marked_auto_where_it_is_created() -> None:
    from chimera.api.code_api import CardOrigin, CodeTurnRequest, _turn_origin
    from chimera.core.guest_history import CARD_TURN_TEXT

    typed = CodeTurnRequest(message="please summarize")
    assert _turn_origin(typed) == ("human", "please summarize")

    card = CodeTurnRequest(message=f"generated prompt with {FILE_CONTENT}")
    card._task_card = CardOrigin(tainted=False)
    assert _turn_origin(card) == ("auto", CARD_TURN_TEXT)

    loop = CodeTurnRequest(message=f"CI log {FILE_CONTENT}")
    loop.close_loop_follow = {"turn_id": "t"}
    assert _turn_origin(loop) == ("auto", "")


# ------------------------------------------------------------------ a draft the sources did not support


@pytest.mark.parametrize("outcome, corrected", [
    ("escalated", "The deadline is Monday."),
    ("declined", "the sources provided don't cover this"),
])
def test_a_draft_the_attachment_check_rejected_is_never_what_a_guest_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str, corrected: str
) -> None:
    """The draft quoted a private figure and a wrong date; the check threw it out. What replaces it is
    the turn's final answer when a stronger model wrote it (``escalated``) and nothing to seal when it
    is the server's decline sentence. In neither case is the discarded draft the sealed answer."""
    import chimera.fusion.verified as verified

    monkeypatch.setattr(verified, "check_answer", lambda *a, **k: (corrected, {"outcome": outcome}, 0.0))
    app, client, ws = _app(tmp_path, monkeypatch)
    sid = _turn(client, ws, "hello")
    token, headers = _share(client, sid)
    _Agent.mode = "draft_with_private_data"
    _turn(client, ws, "deadline for the delivery?", session_id=sid)

    owner = client.get(f"/api/code/sessions/{sid}").text
    assert corrected in owner, "precondition: the corrected answer is what the owner has"

    expected = corrected if outcome == "escalated" else UNFINISHED_TEXT
    frames = [project_frame(f, viewer="x") for f in app.state.session_bus.replay(sid)]
    shown = [f for f in frames if f is not None]
    assert "CLIENTE-741" not in json.dumps(shown) and "9318204" not in json.dumps(shown)
    assert [f for f in shown if f["event"] == "done"][-1]["payload"]["answer"] == expected

    guest = client.get("/guest/api/session", headers=headers)
    assert "CLIENTE-741" not in guest.text and "9318204" not in guest.text
    last = guest.json()["exchanges"][-1]
    assert last["answer"] == (corrected if outcome == "escalated" else ANSWER_UNAVAILABLE)

    _Agent.histories = []
    client.post("/guest/api/turn", headers=headers, json={"message": "and then?", "name": "Ana"})
    sent = json.dumps(_Agent.histories[-1])
    assert "CLIENTE-741" not in sent and "9318204" not in sent
    if outcome == "escalated":
        assert corrected in sent


def test_a_replacement_updates_or_clears_the_candidate_final_answer() -> None:
    from chimera.core.code_session import CodeSession

    def session() -> CodeSession:
        found = CodeSession(agent=None)  # type: ignore[arg-type]
        found.messages = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "draft"}]
        found.set_pending_final("draft")
        return found

    appended = session()
    appended.replace_last_answer("draft + verdict")
    assert appended.pending_final_text() == "draft", "an appended verdict leaves the model's words as they were"
    corrected = session()
    corrected.replace_last_answer("better", candidate="better")
    assert corrected.pending_final_text() == "better"
    server_text = session()
    server_text.replace_last_answer("sorry, no", candidate=None)
    assert server_text.pending_final_text() is None


# ------------------------------------------------------------------ a reconnect past the ring


def test_a_reconnecting_guest_keeps_the_authorship_of_the_frames_brought_back_from_the_run_log() -> None:
    from typing import Any as _Any

    log: dict[str, list[dict[str, _Any]]] = {}

    def backfill(turn_id: str, after: int, before: int) -> list[dict[str, _Any]]:
        return list(log.get(turn_id, []))

    bus = SessionBus(ring=5, backfill=backfill)
    bus.publish("s1", "turn_started", {"message": "m", "origin": "human"}, turn_id="t1", author="Ana",
                guest="link-1")
    for n in range(1, 13):
        frame = bus.publish("s1", "token", {"text": f"w{n} "}, turn_id="t1", author="Ana", guest="link-1")
        log.setdefault("t1", []).append({"event": "token", "text": f"w{n} ", "session_seq": frame["session_seq"]})

    frames = bus.replay("s1", since=0)
    assert len(frames) > 5, "precondition: frames were brought back from the run log"
    brought_back = [f for f in frames if f["event"] == "token"]
    assert all(f.get("guest") == "link-1" for f in brought_back), "the run log's frames lost their authorship"
    mine = [project_frame(f, viewer="link-1") for f in frames]
    text = "".join(f["payload"]["text"] for f in mine if f is not None and f["event"] == "token")
    assert text == "".join(f"w{n} " for n in range(1, 13)), "the guest's own tokens were dropped on reconnect"
    assert all(project_frame(f, viewer="link-2") is None for f in brought_back)


# ------------------------------------------------------------------ a closed link ends a stream whose frames are all filtered


def test_a_link_that_closes_ends_a_guest_stream_even_when_every_frame_is_filtered_out() -> None:
    """The projection drops the owner's tokens, so a guest following an owner's turn can go a long
    time without receiving a frame. The link's check is made on every frame BEFORE the filter and on
    every heartbeat, so a closed link ends the stream whether or not anything would have been sent."""
    import asyncio

    from chimera.api.guest_api import live_frames

    async def never() -> bool:
        return False

    async def scenario() -> None:
        bus = SessionBus()
        open_ = {"yes": True}
        stream = live_frames(
            bus, "s1", since=10**6, name="Ana", disconnected=never, heartbeat_seconds=0.05,
            still_open=lambda: open_["yes"], share_id="x",
            frame_filter=lambda frame: project_frame(frame, viewer="link-1"),
        )
        first = await asyncio.wait_for(stream.__anext__(), 2)  # the presence frame
        assert first["event"] == "presence"
        # A frame the guest is never sent while the link is open: nothing comes of it.
        bus.publish("s1", "token", {"text": MIDWAY}, turn_id="t", author="")
        item = await asyncio.wait_for(stream.__anext__(), 2)
        assert item["event"] == "heartbeat" and MIDWAY not in item["data"]
        open_["yes"] = False
        # Closed: a filtered frame ends the stream, and so does a quiet moment (the heartbeat).
        bus.publish("s1", "token", {"text": MIDWAY}, turn_id="t", author="")
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(stream.__anext__(), 2)

    asyncio.run(scenario())
