"""A guest reads the answers of the owner's turns, never what the machine looked at to give them.

Owner's decision (audit P1b, 2026-11): in a conversation shared by link the guest sees the final
answers of the owner's turns, and not the tools' arguments or results, the edit diffs, the approval
previews, the browser pictures, the verifier's output, the instruction sources, the receipt's
cost, or the host's paths. The bus carries every frame because the owner's own screen reads the
same stream, so the guest's door is where it is decided — by an ALLOWLIST: a frame type nobody
listed does not reach a guest at all.

Pinned through the guest's own routes, on a real server: the live stream while the turn runs, the
same stream replayed afterwards, and the stored conversation. And the other way round: the owner
still receives everything.
"""

from __future__ import annotations

import contextlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from chimera.api.sharing import SessionBus
from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult, ToolActivity
from chimera.interface import ChatSession
from tests.test_an_open_guest_stream_closes_when_its_link_does import _serve

PRIVATE_FILE = "clientes/cliente-reservado.csv"
PRIVATE_CONTENT = "ORION-741,saldo=9.318.204,00"
PRIVATE_DIFF = "+API_KEY = 'sk-live-DIFF-SECRET'"
PRIVATE_VERIFY = "VERIFIER-PRINTED-clientes-reservado"
PRIVATE_PREVIEW = "PREVIEW-rm -rf /dados-do-dono"
PRIVATE_PICTURE = "JPEG-OF-THE-OWNERS-BANK-PAGE"
PRIVATE_SOURCE = "C:/Users/dono/.chimera/agent.json"
ANSWER = "Done: I read the file and the totals match."

#: Every private string above, for the "nothing of this reached the guest" assertions.
PRIVATE = (
    PRIVATE_FILE, PRIVATE_CONTENT, PRIVATE_DIFF, PRIVATE_VERIFY, PRIVATE_PREVIEW, PRIVATE_PICTURE,
    PRIVATE_SOURCE, "sk-live-DIFF-SECRET", "ORION-741",
)


class _OwnerAgent:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def run(self, task: str, **kw: Any) -> AgentResult:
        if "work" in task:
            args = {"path": PRIVATE_FILE}
            if kw.get("on_tool_start"):
                kw["on_tool_start"]("read_file", args, time.time())
            if kw.get("on_tool"):
                kw["on_tool"](ToolActivity("read_file", args, True, PRIVATE_CONTENT))
            if kw.get("on_edit"):
                kw["on_edit"]("app.py", PRIVATE_DIFF)
            if kw.get("on_token"):
                kw["on_token"](ANSWER)
            return AgentResult(
                answer=ANSWER, steps=2, stopped_reason="final", model="test/model",
                tool_names=["read_file"],
                transcript=[
                    {"role": "user", "content": task},
                    {"role": "assistant", "content": "", "tool_calls": [{
                        "id": "c1", "type": "function",
                        "function": {"name": "read_file", "arguments": json.dumps(args)},
                    }]},
                    {"role": "tool", "tool_call_id": "c1", "content": PRIVATE_CONTENT},
                    {"role": "assistant", "content": ANSWER},
                ],
            )
        return AgentResult(
            answer="ok", steps=1, stopped_reason="final", model="test/model",
            transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}],
        )


@pytest.fixture(autouse=True)
def _fresh_settings() -> Any:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Path]:
    import chimera.core
    from chimera.api import build_api_app

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    for key in ("CHIMERA_SHARING", "CHIMERA_SHARE_EXPIRY_HOURS", "CHIMERA_SERVER_TOKEN"):
        monkeypatch.setenv(key, "")
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _OwnerAgent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_MEMORY_BACKEND="json")
    return build_api_app(lambda: ChatSession(_OwnerAgent()), workspace=ws, settings=settings), ws


def _read_into(lines: list[str], url: str, params: dict[str, str]) -> threading.Thread:
    def read() -> None:
        try:
            with httpx.stream("GET", url, params=params, timeout=httpx.Timeout(20, read=20)) as r:
                for line in r.iter_lines():
                    lines.append(line)
        except httpx.HTTPError:
            pass

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    return thread


def _wait_for(lines: list[str], needle: str, seconds: float = 10) -> None:
    for _ in range(int(seconds * 10)):
        if needle in " ".join(lines):
            return
        time.sleep(0.1)


def _events(lines: list[str]) -> list[dict[str, Any]]:
    out = []
    for line in lines:
        if line.startswith("data: ") and line != "data: {}":
            with contextlib.suppress(ValueError):
                out.append(json.loads(line[len("data: "):]))
    return out


def _publish_what_the_machine_saw(bus: SessionBus, sid: str, turn_id: str) -> None:
    """The frames a coding turn emits that no fake agent reaches: an approval card, a verifier
    run, a browser picture, and a frame type that did not exist when this test was written."""
    bus.publish(sid, "approval", {
        "id": "a1", "action": "run_shell", "reason": PRIVATE_PREVIEW,
        "preview": [PRIVATE_PREVIEW], "decision": None,
    }, turn_id=turn_id)
    bus.publish(sid, "verified", {
        "command": f"python check.py {PRIVATE_FILE}", "state": "passed", "output": PRIVATE_VERIFY,
    }, turn_id=turn_id)
    bus.publish(sid, "browser", {"url": PRIVATE_SOURCE, "jpeg": PRIVATE_PICTURE}, turn_id=turn_id,
                keep=False)
    bus.publish(sid, "brand_new_frame", {"text": "A-FRAME-NOBODY-LISTED"}, turn_id=turn_id)


def test_a_guest_gets_answers_and_tool_names_and_the_owner_still_gets_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, ws = _app(tmp_path, monkeypatch)
    base, server, thread = _serve(app)
    guest_lines: list[str] = []
    owner_lines: list[str] = []
    try:
        owner = httpx.Client(base_url=base, timeout=30)
        first = owner.post("/api/code/turn", json={"message": "hello", "workspace": str(ws), "stream": False})
        found = re.search(r'"session_id": ?"([0-9a-f]+)"', first.text)
        assert found, first.text[:300]
        sid = found.group(1)
        token = owner.post(f"/api/code/sessions/{sid}/share").json()["token"]
        bus: SessionBus = app.state.session_bus

        _read_into(guest_lines, f"{base}/guest/api/live", {"t": token, "name": "Ana", "since": "0"})
        _read_into(owner_lines, f"{base}/api/code/sessions/{sid}/live", {"since": "0"})
        time.sleep(0.5)

        turn = owner.post(
            "/api/code/turn",
            json={"message": "work on it", "workspace": str(ws), "session_id": sid, "stream": True},
        )
        done_turn = re.findall(r'"turn_id": ?"([0-9a-f-]+)"', " ".join(owner_lines))
        _publish_what_the_machine_saw(bus, sid, done_turn[-1] if done_turn else "t-x")
        _wait_for(guest_lines, '"event": "done"')
        _wait_for(owner_lines, "A-FRAME-NOBODY-LISTED")
        assert turn.status_code == 200

        # A guest who arrives afterwards is replayed the same turn from the bus.
        replay_lines: list[str] = []
        _read_into(replay_lines, f"{base}/guest/api/live", {"t": token, "name": "Bia", "since": "0"})
        _wait_for(replay_lines, '"event": "done"')
        time.sleep(0.3)
    finally:
        app.state.session_bus.end_guests()
        app.state.guest_server.stop()
        server.should_exit = True
        thread.join(timeout=5)

    owner_text = " ".join(owner_lines)
    # The owner's window is the control: it still sees the machine's work.
    for visible in (PRIVATE_CONTENT, PRIVATE_DIFF, PRIVATE_VERIFY, PRIVATE_PREVIEW, "A-FRAME-NOBODY-LISTED"):
        assert visible in owner_text, f"the owner lost {visible!r}"

    for name, lines in (("live", guest_lines), ("replay", replay_lines)):
        text = " ".join(lines)
        assert '"event": "done"' in text, f"{name}: the guest never got the answer"
        for secret in PRIVATE:
            assert secret not in text, f"{name}: the guest received {secret!r}"
        assert "A-FRAME-NOBODY-LISTED" not in text, f"{name}: an unlisted frame type reached the guest"
        events = _events(lines)
        kinds = {e["event"] for e in events}
        assert kinds <= {"turn_started", "token", "tool_start", "tool", "approval", "done", "error",
                         "presence"}, f"{name}: {kinds}"
        answer = next(e for e in events if e["event"] == "done" and ANSWER in json.dumps(e))
        assert answer["payload"]["answer"] == ANSWER
        tools = [e["payload"] for e in events if e["event"] == "tool"]
        assert tools == [{"name": "read_file", "ok": True, "status": "ok"}], f"{name}: {tools}"
        approvals = [e["payload"] for e in events if e["event"] == "approval"]
        assert approvals and all(a == {"status": "waiting"} for a in approvals)
        done = [e["payload"] for e in events if e["event"] == "done"][-1]
        assert set(done) <= {"answer", "author", "memory_facts_used"}, f"{name}: {sorted(done)}"


def test_the_stored_conversation_a_guest_reads_has_the_answer_and_no_tool_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    app, ws = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    first = client.post("/api/code/turn", json={"message": "work on it", "workspace": str(ws), "stream": False})
    found = re.search(r'"session_id": ?"([0-9a-f]+)"', first.text)
    assert found, first.text[:300]
    sid = found.group(1)
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]

    owner_view = client.get(f"/api/code/sessions/{sid}").text
    assert PRIVATE_CONTENT in owner_view, "the owner's own replay keeps the tool's observation"

    guest = client.get("/guest/api/session", headers={"Authorization": f"Bearer {token}"})
    assert guest.status_code == 200
    for secret in PRIVATE:
        assert secret not in guest.text, f"the stored conversation gave the guest {secret!r}"
    exchange = guest.json()["exchanges"][-1]
    assert exchange["answer"] == ANSWER
    assert exchange["tools"] == [{"name": "read_file", "ok": True, "status": "ok"}]
    assert exchange["edits"] == [] and exchange["verified"] is None
    assert set(exchange["done"]) <= {"answer", "author", "memory_facts_used"}


def test_a_frame_type_nobody_listed_is_dropped_and_a_listed_one_loses_its_other_fields() -> None:
    """The fail-closed rule, at its seam: this is the function the guest's door calls."""
    from chimera.api.guest_projection import project_exchange, project_frame

    unknown = {"session_seq": 3, "event": "brand_new_frame", "turn_id": "t", "author": "",
               "payload": {"text": "anything"}}
    assert project_frame(unknown) is None
    assert project_frame({"event": "edit", "payload": {"patch": PRIVATE_DIFF}}) is None
    assert project_frame("not a frame") is None
    assert project_frame({"payload": {}}) is None

    shown = project_frame({
        "session_seq": 4, "event": "tool", "turn_id": "t", "author": "",
        "payload": {"name": "read_file", "ok": False, "arguments": {"path": PRIVATE_FILE},
                    "observation": PRIVATE_CONTENT, "extra_field_added_later": "x"},
    })
    assert shown is not None
    assert shown["payload"] == {"name": "read_file", "ok": False, "status": "error"}

    error = project_frame({"session_seq": 5, "event": "error", "turn_id": "t", "author": "",
                           "payload": {"message": f"failed, see {PRIVATE_SOURCE}"}})
    assert error is not None and PRIVATE_SOURCE not in json.dumps(error)

    stored = project_exchange({
        "you": "q", "origin": "human", "human_text": "q", "answer": "a", "final_answer": "a", "tools": [{"name": "t", "arguments": {"p": PRIVATE_FILE},
                                              "observation": PRIVATE_CONTENT, "ok": True}],
        "edits": [{"path": "a.py", "patch": PRIVATE_DIFF}], "verified": {"output": PRIVATE_VERIFY},
        "done": {"answer": "a", "usd": 1.2, "instruction_sources": [PRIVATE_SOURCE]},
        "batches": [{"note": PRIVATE_CONTENT}], "guidance": [PRIVATE_CONTENT],
    })
    assert stored is not None
    assert PRIVATE_CONTENT not in json.dumps(stored) and PRIVATE_DIFF not in json.dumps(stored)
    assert stored["done"] == {"answer": "a"}
