"""Answering a paused run over HTTP.

The whole human-in-the-loop envelope has been in the core since M13 and was reachable only from the
CLI. The desktop could therefore arm a pause it had no way to answer: the run stopped, parked itself
under a thread, and the app had no route to accept, edit, redirect or reject it. `app.py` said as
much in a comment — "routing the approval to the desktop's HITL UI is the follow-up".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api.app import RunRequest
from chimera.config import Settings
from chimera.core.agent import AgentResult
from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
from chimera.core.checkpoint import WorkspaceGuard
from chimera.core.events import EventSink
from chimera.core.runstate import RunCheckpointer
from chimera.interface import ChatSession


class _FakeChatAgent:
    def run(self, message: str) -> AgentResult:
        return AgentResult(answer="hi", steps=1, stopped_reason="final")


class _Tainted:
    """Reports that the run consumed untrusted content — the condition a pause exists for."""

    def run_tainted(self) -> bool:
        return True


class _Worker:
    """Edits a file, so the run reaches success on its own merits and the pause is what stops it.

    An edit-nothing worker no longer passes the success gate, and a run that never succeeds never
    reaches the approval interrupt — the pause is a gate on ACCEPTING good work done under untrusted
    influence, not a consolation prize for work that failed.
    """

    def __init__(self) -> None:
        self.runs = 0
        self.workspace: Path | None = None

    def run(self, task: str) -> AgentResult:
        self.runs += 1
        if self.workspace is not None:
            (self.workspace / f"edit_{self.runs}.py").write_text("# edited\n", encoding="utf-8")
        return AgentResult(answer="the untrusted answer", steps=1, stopped_reason="final")


def _client(tmp_path: Path, worker: _Worker) -> tuple[TestClient, Path]:
    """A client plus the sandbox its runs write into.

    The workspace is handed back and passed on EVERY request on purpose: with no ``workspace`` the
    API falls back to the directory the app was launched from, which under pytest is the repository
    itself. A test whose worker edits files must never be allowed to default to that.
    """
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    ws_root = tmp_path / "ws"
    ws_root.mkdir()

    def factory(
        req: RunRequest,
        ws: Any,
        on_event: EventSink,
        _settings: Any,
        should_stop: Any = None,
    ) -> AutonomousAgent:
        worker.workspace = Path(ws)
        return AutonomousAgent(
            worker,  # type: ignore[arg-type]
            taint=_Tainted(),  # type: ignore[arg-type]
            checkpointer=RunCheckpointer(settings.home / "runs.db"),
            pause_on_taint=req.pause_on_taint,
            guard=WorkspaceGuard(ws),
            workspace=ws,
            on_event=on_event,
            run_log=settings.home / "runs.jsonl",
            config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
        )

    client = TestClient(
        build_api_app(
            lambda: ChatSession(_FakeChatAgent()), settings=settings, solve_agent_factory=factory
        )
    )
    return client, ws_root


def _frames(text: str) -> list[tuple[str, dict[str, Any]]]:
    import json

    out: list[tuple[str, dict[str, Any]]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        event, data = "message", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if data:
            out.append((event, json.loads(data)))
    return out


def test_a_tainted_run_pauses_instead_of_reporting_a_verdict(tmp_path: Path) -> None:
    client, ws = _client(tmp_path, _Worker())
    resp = client.post(
        "/api/runs",
        json={"task": "read the web and act", "thread_id": "job-1", "pause_on_taint": True, "workspace": str(ws)},
    )
    frames = _frames(resp.text)
    kinds = [e for e, _ in frames]

    # A pause is not a `done`. `done` carries a verdict and this run has not reached one — a client
    # that read it as an ordinary failure would silently discard work sitting there to be released.
    assert "paused" in kinds and "done" not in kinds
    paused = next(d for e, d in frames if e == "paused")
    assert paused["thread_id"] == "job-1"
    assert paused["answer"] == "the untrusted answer"


def test_the_paused_list_survives_the_window_that_witnessed_it(tmp_path: Path) -> None:
    client, ws = _client(tmp_path, _Worker())
    client.post("/api/runs", json={"task": "t", "thread_id": "job-1", "pause_on_taint": True, "workspace": str(ws)})

    # Nothing here is holding the original stream. Close the app mid-run and the run is still
    # parked; without this route it would be invisible and unanswerable forever.
    listed = client.get("/api/runs/paused").json()
    assert [r["thread_id"] for r in listed] == ["job-1"]
    assert listed[0]["tainted"] is True
    assert listed[0]["answer"] == "the untrusted answer"


def test_accept_then_resume_finalizes_the_reviewed_answer_without_re_running(tmp_path: Path) -> None:
    worker = _Worker()
    client, ws = _client(tmp_path, worker)
    client.post("/api/runs", json={"task": "t", "thread_id": "job-1", "pause_on_taint": True, "workspace": str(ws)})
    assert worker.runs == 1

    verdict = client.post("/api/runs/job-1/respond", json={"action": "accept"}).json()
    assert verdict == {"ok": True, "resume_required": True, "retries": False}

    # Recording the verdict does NOT conclude the run — the resume is where it finalizes.
    resp = client.post("/api/runs", json={"task": "t", "thread_id": "job-1", "workspace": str(ws)})
    done = next(d for e, d in _frames(resp.text) if e == "done")
    assert done["success"] is True and done["answer"] == "the untrusted answer"
    assert worker.runs == 1  # approval is of the EXACT reviewed output, not of a fresh attempt
    assert client.get("/api/runs/paused").json() == []


def test_edit_finalizes_the_humans_correction_not_the_models(tmp_path: Path) -> None:
    client, ws = _client(tmp_path, _Worker())
    client.post("/api/runs", json={"task": "t", "thread_id": "job-1", "pause_on_taint": True, "workspace": str(ws)})

    client.post("/api/runs/job-1/respond", json={"action": "edit", "answer": "the corrected answer"})
    resp = client.post("/api/runs", json={"task": "t", "thread_id": "job-1", "workspace": str(ws)})
    done = next(d for e, d in _frames(resp.text) if e == "done")
    assert done["answer"] == "the corrected answer"


def test_respond_says_it_will_cost_another_attempt(tmp_path: Path) -> None:
    worker = _Worker()
    client, ws = _client(tmp_path, worker)
    client.post("/api/runs", json={"task": "t", "thread_id": "job-1", "pause_on_taint": True, "workspace": str(ws)})

    verdict = client.post(
        "/api/runs/job-1/respond", json={"action": "respond", "feedback": "cite your source"}
    ).json()
    # The one action that re-runs the worker. Saying so is what lets the UI warn before spending it.
    assert verdict == {"ok": True, "resume_required": True, "retries": True}


def test_ignore_ends_the_run_denied(tmp_path: Path) -> None:
    client, ws = _client(tmp_path, _Worker())
    client.post("/api/runs", json={"task": "t", "thread_id": "job-1", "pause_on_taint": True, "workspace": str(ws)})

    assert client.post("/api/runs/job-1/respond", json={"action": "ignore"}).json()["ok"] is True
    resp = client.post("/api/runs", json={"task": "t", "thread_id": "job-1", "workspace": str(ws)})
    done = next(d for e, d in _frames(resp.text) if e == "done")
    assert done["success"] is False  # a rejected tainted result is not sanctioned


def test_a_stale_or_unknown_verdict_is_a_no_op_not_an_error(tmp_path: Path) -> None:
    client, ws = _client(tmp_path, _Worker())
    # Answering a run that already resolved is exactly what a stale click sends. A 404 would turn a
    # harmless double-click into an error dialog.
    for body in ({"action": "accept"}, {"action": "not-an-action"}):
        resp = client.post("/api/runs/never-existed/respond", json=body)
        assert resp.status_code == 200 and resp.json()["ok"] is False


def test_no_thread_means_no_pause_at_all(tmp_path: Path) -> None:
    # Asserted on the REAL builder, not the test factory: this is a rule the production wiring has
    # to hold. pause_on_taint without a thread has nowhere to park the run, so it must not half-arm
    # into a state nobody could ever come back to.
    from chimera.api.app import _build_solve_agent

    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))

    armed = _build_solve_agent(
        RunRequest(task="t", thread_id="job-1", pause_on_taint=True), ws, lambda e: None, settings
    )
    assert armed.pause_on_taint is True
    assert armed.checkpointer is not None and armed.taint is not None

    loose = _build_solve_agent(
        RunRequest(task="t", pause_on_taint=True), ws, lambda e: None, settings
    )
    assert loose.pause_on_taint is False
    assert loose.checkpointer is None
