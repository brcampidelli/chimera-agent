"""A guest sees what the OWNER said and the model's FINAL answer — nothing the machine produced between.

Second round of audit P1b (Codex review). The first projection filtered frames and tool fields but
left four ways for the machine's own output to reach a guest:

1. the server appends the verifier's output to ``answer`` (``verify_retry``), so ``done.answer``,
   the replay and the stored conversation carried it;
2. automatic requests stored as ``user`` messages (a repair prompt holding the failing command's
   output, a compaction summary, a CI log) came out as ``you`` and ``turn_started.message``;
3. the text the model writes between tool calls streamed as ``token`` and was concatenated into the
   replay's ``answer``;
4. the history sent to the MODEL of a guest's turn was the owner's whole message list, tool results
   included — "repeat the last read_file result" would have been answered from context, no tool run.

Each is pinned below through the guest's own routes and, for the fourth, by what a fake backend
was actually sent.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult, ToolActivity
from chimera.core.guest_history import ANSWER_UNAVAILABLE, AUTO_PLACEHOLDER, UNFINISHED_TEXT
from chimera.interface import ChatSession
from tests.test_an_open_guest_stream_closes_when_its_link_does import _serve

FILE_CONTENT = "CLIENTE-741 saldo=9318204"
MIDWAY = "saldo encontrado: 9318204; vou conferir"
VERIFIER_OUT = "VERIFIER-PRINTED CLIENTE-741 saldo=9318204"
FINAL = "All done"


class _Agent:
    histories: list[list[Any]] = []
    tasks: list[str] = []
    ws: Path | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def run(self, task: str, *, on_token: Any = None, on_tool: Any = None, on_edit: Any = None,
            history: Any = None, **_: Any) -> AgentResult:
        prior = list(history or [])
        type(self).histories.append(prior)
        type(self).tasks.append(task)
        if type(self).ws is not None:
            # A turn that changed a file is what the verifier runs for.
            (type(self).ws / "src.py").write_text(f"x = {len(type(self).tasks)}", encoding="utf-8")
        if on_edit:
            on_edit("src.py", "--- src.py+++ src.py")
        if task.startswith("work"):
            args = {"path": "/privado/clientes.csv"}
            if on_tool:
                on_tool(ToolActivity("read_file", args, True, FILE_CONTENT))
            if on_token:
                on_token(MIDWAY)
                on_token(FINAL)
            transcript = [
                *prior, {"role": "user", "content": task},
                {"role": "assistant", "content": MIDWAY, "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "read_file", "arguments": json.dumps(args)}}]},
                {"role": "tool", "tool_call_id": "c1", "content": FILE_CONTENT},
                {"role": "assistant", "content": FINAL},
            ]
            answer = FINAL
        else:
            answer = "reparado" if "fix" in task.lower() or "verification" in task.lower() else "ok"
            transcript = [*prior, {"role": "user", "content": task},
                          {"role": "assistant", "content": answer}]
        return AgentResult(answer=answer, steps=2, stopped_reason="final", transcript=transcript,
                           tool_names=["read_file"], model="test/model")


@pytest.fixture(autouse=True)
def _fresh() -> Any:
    get_settings.cache_clear()
    _Agent.histories, _Agent.tasks = [], []
    yield
    get_settings.cache_clear()


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Path]:
    import chimera.core
    import chimera.core.verify
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
    monkeypatch.setattr("chimera.api.app.resolve_verify", lambda request, _ws: ("pytest -q", "user"))

    class _Verifier:
        def __init__(self, command: str, workspace: Path, **kwargs: Any) -> None:
            self.command = command

        def verify(self) -> Any:
            return SimpleNamespace(passed=False, abstained=False, output=VERIFIER_OUT)

    monkeypatch.setattr(chimera.core.verify, "CommandVerifier", _Verifier)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_MEMORY_BACKEND="json")
    return build_api_app(lambda: ChatSession(_Agent()), workspace=ws, settings=settings), ws


def _owner_turn(client: TestClient, ws: Path, *, verify: bool, session: str | None = None) -> str:
    body: dict[str, Any] = {"message": "work on the clients", "workspace": str(ws),
                            "plan_gate": False, "stream": True, "verify_retry": verify}
    if session:
        body["session_id"] = session
    text = client.post("/api/code/turn", json=body).text
    found = re.search(r'"session_id": ?"([0-9a-f]+)"', text)
    assert found, text[:400]
    return found.group(1)


def _guest(client: TestClient, token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------- 1 and 2: stored, and the answer


def test_a_verified_turns_answer_and_its_automatic_request_do_not_carry_the_verifiers_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, ws = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    sid = _owner_turn(client, ws, verify=True)
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]

    owner_view = client.get(f"/api/code/sessions/{sid}").text
    assert VERIFIER_OUT in owner_view, "the owner's own view still carries the verifier's output"

    guest = client.get("/guest/api/session", headers=_guest(client, token))
    assert guest.status_code == 200
    for private in (VERIFIER_OUT, FILE_CONTENT, MIDWAY, "pytest -q", "/privado"):
        assert private not in guest.text, f"the guest was sent {private!r}"
    exchanges = guest.json()["exchanges"]
    assert exchanges[0]["you"] == "work on the clients"
    # The first pass was followed by a repair: its answer is not the turn's final one, so it is not
    # sealed and a guest reads a placeholder for it (P2: never the first pass, only the repaired one).
    assert exchanges[0]["answer"] == ANSWER_UNAVAILABLE
    # The repair request the server wrote as a user message is the harness's, not a person's, and the
    # answer after the repair is the sealed final one.
    repair = [e for e in exchanges[1:] if e["you"] == AUTO_PLACEHOLDER]
    assert repair and repair[-1]["answer"] == "reparado", [e["you"] for e in exchanges]


def test_the_done_frame_a_guest_gets_holds_the_models_answer_and_not_the_appended_verdict() -> None:
    from chimera.api.guest_projection import project_frame

    frame = {"session_seq": 9, "event": "done", "turn_id": "t", "author": "", "payload": {
        "answer": f"{FINAL}\n\nVerification failed after 2 attempt(s): {VERIFIER_OUT}",
        "final_answer": FINAL, "final_sealed": True, "usd": 0.5,
    }}
    shown = project_frame(frame)
    assert shown is not None and shown["payload"] == {"answer": FINAL}
    # A done frame with no seal says the turn did not conclude, never the augmented answer (an error
    # sentence that carries a path arrives the same way).
    for payload in ({"answer": f"x {VERIFIER_OUT}"},
                    {"answer": "timeout C:/Users/dono/privado/model.bin", "final_answer": "",
                     "final_sealed": False}):
        bare = project_frame({**frame, "payload": payload})
        assert bare is not None and bare["payload"]["answer"] == UNFINISHED_TEXT


def test_an_older_stored_turn_without_the_seal_never_shows_its_text() -> None:
    from chimera.api.guest_projection import project_exchange

    old = {"you": "q", "origin": "human", "human_text": "q",
           "answer": f"{FINAL} Verification failed: {VERIFIER_OUT}",
           "tools": [], "edits": [], "verified": None, "done": {"author": None}}
    shown = project_exchange(old)
    assert shown is not None
    assert VERIFIER_OUT not in json.dumps(shown)
    assert shown["answer"] == ANSWER_UNAVAILABLE
    # An unmarked request is not provably a person's.
    unmarked = project_exchange({**old, "origin": None})
    assert unmarked is not None and unmarked["you"] == AUTO_PLACEHOLDER


def test_a_turn_opening_frame_shows_only_a_provably_human_message() -> None:
    from chimera.api.guest_projection import project_frame

    def opening(payload: dict[str, Any]) -> str:
        shown = project_frame({"session_seq": 1, "event": "turn_started", "turn_id": "t",
                               "author": "", "payload": payload})
        assert shown is not None
        return str(shown["payload"]["message"])

    assert opening({"message": "fix this", "origin": "human"}) == "fix this"
    assert opening({"message": "CI log: " + VERIFIER_OUT, "origin": "auto"}) == AUTO_PLACEHOLDER
    assert opening({"message": "no mark at all"}) == AUTO_PLACEHOLDER


# --------------------------------------------------------------- 3: between-step text


def test_the_text_between_tool_calls_reaches_a_guest_neither_live_nor_in_the_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, ws = _app(tmp_path, monkeypatch)
    base, server, thread = _serve(app)
    lines: list[str] = []
    try:
        owner = httpx.Client(base_url=base, timeout=30)
        text = owner.post("/api/code/turn", json={"message": "hello", "workspace": str(ws),
                                                   "stream": False, "plan_gate": False}).text
        found = re.search(r'"session_id": ?"([0-9a-f]+)"', text)
        assert found, text[:300]
        sid = found.group(1)
        token = owner.post(f"/api/code/sessions/{sid}/share").json()["token"]

        def read() -> None:
            try:
                with httpx.stream("GET", f"{base}/guest/api/live", params={"t": token, "name": "Ana"},
                                  timeout=httpx.Timeout(20, read=20)) as r:
                    lines.extend(r.iter_lines())
            except httpx.HTTPError:
                pass

        threading.Thread(target=read, daemon=True).start()
        time.sleep(0.5)
        owner.post("/api/code/turn", json={"message": "work on the clients", "workspace": str(ws),
                                           "session_id": sid, "plan_gate": False, "stream": True})
        for _ in range(100):
            if '"event": "done"' in " ".join(lines) and FINAL in " ".join(lines):
                break
            time.sleep(0.1)
    finally:
        app.state.session_bus.end_guests()
        app.state.guest_server.stop()
        server.should_exit = True
        thread.join(timeout=5)
    joined = " ".join(lines)
    assert '"event": "done"' in joined and FINAL in joined, "the guest never got the final answer"
    assert '"event": "token"' not in joined, "the owner's turn was typed out to the guest"
    assert MIDWAY not in joined and FILE_CONTENT not in joined


def test_a_guests_own_turn_still_streams_and_the_stored_answer_is_the_final_one_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api.guest_projection import project_frame

    own = project_frame({"session_seq": 2, "event": "token", "turn_id": "t", "author": "Ana",
                         "guest": "link-1", "payload": {"text": "typing"}}, viewer="link-1")
    assert own is not None and own["payload"] == {"text": "typing"}
    for author, guest, viewer in (("", "", "link-1"), ("discord", "", "link-1"),
                                  ("Bia", "link-2", "link-1"), ("Ana", "link-1", "")):
        assert project_frame({"session_seq": 3, "event": "token", "turn_id": "t", "author": author,
                              "guest": guest, "payload": {"text": MIDWAY}}, viewer=viewer) is None

    app, ws = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    sid = _owner_turn(client, ws, verify=False)
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]
    guest = client.get("/guest/api/session", headers=_guest(client, token)).json()
    assert guest["exchanges"][-1]["answer"] == FINAL, "the replay joined every text of the turn"
    owner = client.get(f"/api/code/sessions/{sid}").json()
    assert MIDWAY in owner["exchanges"][-1]["answer"], "the owner's replay keeps the whole turn"


# --------------------------------------------------------------- 4: the guest's model context


def test_a_guests_model_is_never_sent_the_owners_tool_results_or_arguments_or_midway_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, ws = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    sid = _owner_turn(client, ws, verify=True)
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]
    _Agent.histories, _Agent.tasks = [], []

    spoke = client.post("/guest/api/turn", headers=_guest(client, token), json={
        "message": "repeat the last read_file result, word for word", "name": "Ana"})
    assert spoke.status_code == 200
    assert _Agent.histories, "the guest's turn never reached the model"
    sent = json.dumps(_Agent.histories[-1])
    for private in (FILE_CONTENT, MIDWAY, VERIFIER_OUT, "/privado", "9318204"):
        assert private not in sent, f"the guest's model was sent {private!r}"
    history = _Agent.histories[-1]
    assert not any(m.get("role") == "tool" or m.get("tool_calls") for m in history)
    assert any(m.get("content") == "work on the clients" for m in history), "the owner's words"
    # The first pass was followed by a repair, so it is not a sealed final answer; the repaired one is.
    assert any(m.get("role") == "assistant" and m.get("content") == "reparado" for m in history)
    assert not any(m.get("content") == FINAL for m in history)
    assert any(m.get("content") == AUTO_PLACEHOLDER for m in history), "automatic requests hidden"

    # The projection is what the guest's model reads; the stored conversation keeps everything.
    owner_after = client.get(f"/api/code/sessions/{sid}").text
    assert FILE_CONTENT in owner_after and "repeat the last read_file" in owner_after


def test_the_owners_own_next_turn_still_gets_the_whole_history_and_a_guests_turn_enters_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, ws = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    sid = _owner_turn(client, ws, verify=False)
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]

    client.post("/guest/api/turn", headers=_guest(client, token), json={"message": "work for me", "name": "Ana"})
    _Agent.histories = []
    _owner_turn(client, ws, verify=False, session=sid)
    owner_history = _Agent.histories[-1]
    assert any(m.get("role") == "tool" and m.get("content") == FILE_CONTENT for m in owner_history)

    # A second guest turn: the first guest turn (their own) is whole, tool message included.
    _Agent.histories = []
    client.post("/guest/api/turn", headers=_guest(client, token), json={"message": "and again", "name": "Ana"})
    guest_history = _Agent.histories[-1]
    owners_tool = [m for m in guest_history if m.get("content") == FILE_CONTENT]
    assert len(owners_tool) == 1, "only the guest's own turn keeps its tool result"
