"""`POST /api/code/turn` — a coding turn that continues a conversation instead of starting one.

The run endpoint is a closed transaction: plan, execute, verify, revert, receipt. That is right for
"make the tests pass" and wrong for "what does this module do?", "ok, rename it", "no, the other
one". Those are turns, and a turn is only worth anything if it remembers what the previous one did.

The tests that matter here are the second one — the session id survives a turn and the next turn
resumes it — and the ones asserting the seams are shared with the run endpoint rather than
reimplemented, because a second copy of that registry order is one waiting to drift silently.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.core.agent import AgentResult, ToolActivity  # noqa: E402
from chimera.core.context_budget import RunState  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402


class _ScriptedAgent:
    """An agent that calls one tool, edits one file, and echoes the history it received."""

    def __init__(self) -> None:
        self.tasks: list[str] = []
        self.histories: list[list[Any]] = []
        self.run_state = RunState()

    def run(
        self,
        task: str,
        *,
        on_token: Any = None,
        on_tool: Any = None,
        on_edit: Any = None,
        history: list[Any] | None = None,
    ) -> AgentResult:
        self.tasks.append(task)
        self.histories.append(list(history or []))
        if on_token:
            on_token("hel")
            on_token("lo")
        if on_tool:
            on_tool(ToolActivity("read_file", {"path": "a.py"}, True, "x = 1\n"))
        if on_edit:
            on_edit("a.py", "--- a.py\n+++ a.py\n@@\n+x = 2\n")
        transcript: list[Any] = [
            {"role": "system", "content": "SYS"},
            *(history or []),
            {"role": "user", "content": task},
            {"role": "assistant", "content": "hello"},
        ]
        return AgentResult(
            answer="hello", steps=1, stopped_reason="final", transcript=transcript,
            tool_names=["read_file"], model="test/model",
        )


def _client(tmp_path: Path, agent: Any) -> TestClient:
    """The real app, with only the agent construction replaced — everything else is production."""
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    del agent  # patched via the fixture; the app itself is untouched production wiring
    # The chat factory is required and irrelevant here — this file exercises /api/code/*, which
    # builds its own agent per turn and never touches a ChatSession.
    return TestClient(
        build_api_app(lambda: ChatSession(_ScriptedAgent()), workspace=ws, settings=settings)
    )


def _frames(response: Any) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            out.append((event, json.loads(line[len("data: ") :])))
    return out


@pytest.fixture()
def patched(monkeypatch: Any) -> _ScriptedAgent:
    """Replace the Agent the endpoint constructs, leaving the rest of the path real."""
    agent = _ScriptedAgent()
    import chimera.core

    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: agent, raising=True)
    return agent


def test_a_turn_streams_tokens_tools_edits_and_a_verdict(tmp_path: Path, patched: Any) -> None:
    client = _client(tmp_path, patched)
    response = client.post("/api/code/turn", json={"message": "read a.py"})
    assert response.status_code == 200

    kinds = [event for event, _ in _frames(response)]
    assert kinds[0] == "session"  # the id arrives before anything it could be needed for
    assert "token" in kinds and "tool" in kinds and "edit" in kinds and kinds[-1] == "done"


def test_the_second_turn_resumes_the_first(tmp_path: Path, patched: Any) -> None:
    """The whole point. Without this, 'now fix the other one' reaches an agent that has no idea
    which files it read five seconds ago, and it reads them all again."""
    client = _client(tmp_path, patched)
    first = client.post("/api/code/turn", json={"message": "read a.py"})
    session_id = dict(_frames(first))["session"]["session_id"]

    client.post("/api/code/turn", json={"message": "now rename it", "session_id": session_id})

    assert patched.histories[0] == []
    assert any(m.get("content") == "read a.py" for m in patched.histories[1])


def test_an_unknown_session_id_starts_that_conversation_rather_than_failing(
    tmp_path: Path, patched: Any
) -> None:
    """A client holding an id whose file was deleted should get a working conversation under that
    id, not a 404 it has no way to recover from."""
    client = _client(tmp_path, patched)
    response = client.post("/api/code/turn", json={"message": "hi", "session_id": "never-seen"})
    assert dict(_frames(response))["session"]["session_id"] == "never-seen"


def test_a_tool_frame_is_clipped_the_same_way_the_run_endpoint_clips_it(
    tmp_path: Path, patched: Any
) -> None:
    """Same builder, so a tool call looks the same whichever endpoint produced it."""
    client = _client(tmp_path, patched)
    frames = dict(_frames(client.post("/api/code/turn", json={"message": "go"})))
    tool = frames["tool"]
    assert tool["name"] == "read_file" and tool["ok"] is True
    assert tool["arguments"] == {"path": "a.py"}


def test_deleting_a_session_is_idempotent(tmp_path: Path, patched: Any) -> None:
    client = _client(tmp_path, patched)
    response = client.post("/api/code/turn", json={"message": "hi"})
    session_id = dict(_frames(response))["session"]["session_id"]

    assert client.delete(f"/api/code/sessions/{session_id}").json() == {"ok": True}
    # A second click on Clear is not an error.
    assert client.delete(f"/api/code/sessions/{session_id}").json() == {"ok": False}


def test_the_openapi_schema_actually_generates(tmp_path: Path, patched: Any) -> None:
    """Generating the schema is a separate failure surface from serving the endpoint, and this file
    found that out the hard way.

    With `from __future__ import annotations`, a `-> EventSourceResponse` return annotation is a
    string FastAPI resolves against the defining module's globals at schema-build time. Import that
    name inside a function and every request still works, every test here still passes, and
    `python -m chimera.api.schema_dump` dies with an undefined ForwardRef — which then writes an
    EMPTY openapi.json over the committed one. The whole suite was green when that happened.
    """
    schema = _client(tmp_path, patched).app.openapi()
    assert "/api/code/turn" in schema["paths"]
    assert "/api/runs" in schema["paths"]


def test_the_run_endpoint_and_the_turn_endpoint_share_one_set_of_seams() -> None:
    """Declared once so the two cannot drift into meaning different things by the same field name."""
    from chimera.api.app import RunRequest
    from chimera.api.code_api import CodeSeams, CodeTurnRequest

    seams = set(CodeSeams.model_fields)
    assert seams <= set(RunRequest.model_fields)
    assert seams <= set(CodeTurnRequest.model_fields)
    assert issubclass(RunRequest, CodeSeams) and issubclass(CodeTurnRequest, CodeSeams)


# --- What the turn WROTE gets a verdict, and a failing one offers an undo ---------------------
#
# This is the pair that made one button honest. Send used to edit your workspace and keep whatever
# it wrote, while a second button beside it ran the same text through plan → verify → revert. The
# screen never said which was which, so pressing Enter was silently the weaker option. These tests
# hold the two halves that make the collapse safe: the turn is judged, and the judgement is
# reversible on request rather than on its own.


class _EditingAgent(_ScriptedAgent):
    """Like the scripted agent, but its edit actually lands on disk — the snapshot needs a file."""

    def __init__(self, ws: Path) -> None:
        super().__init__()
        self._ws = ws

    def run(self, task: str, **kw: Any) -> AgentResult:
        (self._ws / "a.py").write_text("x = 2\n", encoding="utf-8")
        return super().run(task, **kw)


def _editing_client(tmp_path: Path, monkeypatch: Any) -> tuple[TestClient, Path]:
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    import chimera.core

    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: _EditingAgent(ws), raising=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    app = build_api_app(lambda: ChatSession(_ScriptedAgent()), workspace=ws, settings=settings)
    return TestClient(app), ws


def _verdict(response: Any) -> dict[str, Any]:
    return next(payload for event, payload in _frames(response) if event == "verified")


def test_a_project_with_no_verify_command_says_so_rather_than_staying_quiet(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # The case a user would otherwise assume was checked, because the run on the other screen does
    # check. Silence here reads as approval.
    client, _ = _editing_client(tmp_path, monkeypatch)
    verdict = _verdict(client.post("/api/code/turn", json={"message": "edit a.py"}))
    assert verdict == {"command": None, "source": "none", "state": "none"}


def test_a_failing_verification_offers_an_undo_and_does_not_take_it(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, ws = _editing_client(tmp_path, monkeypatch)
    (ws / "tests").mkdir()
    (ws / "tests" / "test_it.py").write_text("def test_it():\n    assert False\n", encoding="utf-8")

    verdict = _verdict(client.post("/api/code/turn", json={"message": "edit a.py"}))
    assert verdict["state"] == "failed"
    assert verdict["source"] == "inferred:tests/"
    assert verdict["revert_token"]
    # Offered, NOT applied: the edit the user watched being typed is still there.
    assert (ws / "a.py").read_text(encoding="utf-8") == "x = 2\n"

    assert client.post(f"/api/code/revert/{verdict['revert_token']}").json()["ok"] is True
    assert not (ws / "a.py").exists()  # the snapshot predates the file


def test_a_revert_token_is_single_use(tmp_path: Path, monkeypatch: Any) -> None:
    # A second press must not restore a snapshot the user has since typed on top of.
    client, ws = _editing_client(tmp_path, monkeypatch)
    (ws / "tests").mkdir()
    (ws / "tests" / "test_it.py").write_text("def test_it():\n    assert False\n", encoding="utf-8")

    token = _verdict(client.post("/api/code/turn", json={"message": "edit a.py"}))["revert_token"]
    assert client.post(f"/api/code/revert/{token}").json()["ok"] is True
    assert client.post(f"/api/code/revert/{token}").json() == {"ok": False, "restored": 0}


def test_an_unknown_revert_token_is_refused_rather_than_erroring(
    tmp_path: Path, patched: Any
) -> None:
    client = _client(tmp_path, patched)
    assert client.post("/api/code/revert/nope").json() == {"ok": False, "restored": 0}


def test_a_workspace_that_does_not_exist_is_refused_rather_than_created(
    tmp_path: Path, patched: Any
) -> None:
    # The one axis on which this endpoint was more permissive than the chat it replaces: any path was
    # accepted, and the turn ran against a directory the agent would then create files in. The
    # read-only fs endpoints and POST /api/runs both validate; this one did not.
    client = _client(tmp_path, patched)
    response = client.post(
        "/api/code/turn", json={"message": "hi", "workspace": str(tmp_path / "nope")}
    )
    assert response.status_code == 400
    assert not (tmp_path / "nope").exists()
