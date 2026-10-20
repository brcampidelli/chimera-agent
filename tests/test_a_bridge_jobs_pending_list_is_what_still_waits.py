"""A bridge job's ``pending_approvals`` lists the questions still waiting, not every one it asked.

Observed on 2026-10-10: ``desktop_job`` kept listing ~20 questions the owner had already answered,
while ``desktop_approvals list`` (the queue on disk) showed the truth. `BridgeJob.take` appended
every ``approval`` frame to ``job.approvals`` and nothing ever took one out; `_job_out` printed the
whole list whenever the latest frame was a question. The stream says when a question is ASKED and
never when it is answered or refused by silence, so the job's own memory cannot know — the question
file can: `pending.ask_durably` writes it before the frame goes out and deletes it once the question
resolves, which is what the global list reads.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sse_starlette.sse import EventSourceResponse

from chimera.api import build_api_app
from chimera.api.bridge_routes import ROUTES, BridgeRoute
from chimera.config import get_settings
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun

URL = "http://127.0.0.1:65012"


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "false")
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    app.state.desktop_bridge.attach(URL)
    return app


def _ask(home: Path, request_id: str, action: str, *, kind: str = "approval") -> Path:
    directory = home / "approvals"
    directory.mkdir(parents=True, exist_ok=True)
    question = directory / f"{request_id}.ask.json"
    question.write_text(
        json.dumps(
            {
                "id": request_id,
                "action": action,
                "reason": "review",
                "asked_at": time.time(),
                "run_id": "test-turn",
                "kind": kind,
                "wait_seconds": 300.0,
            }
        ),
        encoding="utf-8",
    )
    return question


def test_answered_and_expired_questions_leave_the_jobs_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    home = tmp_path / "home"
    for request_id in ("aaaaaaaaaaaa", "bbbbbbbbbbbb"):
        question = _ask(home, request_id, "run_shell: " + request_id)
        value = json.loads(question.read_text(encoding="utf-8"))
        value["run_id"] = "bridge-turn"
        question.write_text(json.dumps(value), encoding="utf-8")
    release = threading.Event()

    @app.post("/api/test/asks")
    async def _stream() -> EventSourceResponse:
        async def frames() -> Any:
            for qid, action in (
                ("aaaaaaaaaaaa", "run_shell: one"),
                ("bbbbbbbbbbbb", "run_shell: two"),
            ):
                yield {
                    "event": "approval",
                    "data": json.dumps(
                        {
                            "id": qid,
                            "action": action,
                            "reason": "review",
                            "asked_at": time.time(),
                            "wait_seconds": 300.0,
                            "kind": "approval",
                        }
                    ),
                }
            while not release.is_set():
                await asyncio.sleep(0.02)
            yield {"event": "done", "data": "{}"}

        return EventSourceResponse(frames())

    monkeypatch.setitem(ROUTES, "test.asks", BridgeRoute("POST", "/api/test/asks", stream=True))
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    with TestClient(app) as client:
        job = client.post(
            "/api/bridge/call",
            json={"route": "test.asks", "body": {}, "wait_seconds": 10},
            headers=headers,
        ).json()["job"]
        from chimera.api.desktop_bridge import _job_out
        from chimera.governance.pending import is_waiting, queued_for_turn

        job_state = app.state.bridge_jobs.get(job["job_id"])
        for request_id in ("aaaaaaaaaaaa", "bbbbbbbbbbbb"):
            question = home / "approvals" / f"{request_id}.ask.json"
            value = json.loads(question.read_text(encoding="utf-8"))
            value["run_id"] = job["turn_id"]
            question.write_text(json.dumps(value), encoding="utf-8")
        job_state.turn_id = "bridge-turn"
        job_state.done = False
        job = _job_out(
            job_state,
            None,
            [],
            lambda request_id: is_waiting(home, request_id),
            queued=lambda turn_id: queued_for_turn(home, turn_id, 300.0),
        )
        assert job["waiting_for_approval"] is True
        assert [a["id"] for a in job["pending_approvals"]] == ["aaaaaaaaaaaa", "bbbbbbbbbbbb"]
        # How long silence leaves each one, so a client can tell "waiting" from "about to refuse".
        assert all(0 < a["seconds_left"] <= 300 for a in job["pending_approvals"])

        (home / "approvals" / "aaaaaaaaaaaa.answer.json").write_text(
            json.dumps({"approved": True, "answered_at": time.time()}), encoding="utf-8"
        )
        later = client.get(f"/api/bridge/jobs/{job['job_id']}", headers=headers).json()
        assert [a["id"] for a in later["pending_approvals"]] == ["bbbbbbbbbbbb"]
        assert later["waiting_for_approval"] is True

        # An expired unanswered request is no longer live, even before its worker removes the file.
        question_file = home / "approvals" / "bbbbbbbbbbbb.ask.json"
        expired = json.loads(question_file.read_text(encoding="utf-8"))
        expired["asked_at"] = time.time() - 301
        question_file.write_text(json.dumps(expired), encoding="utf-8")
        job_state = app.state.bridge_jobs.get(job["job_id"])
        for question in job_state.approvals:
            if question["id"] == "bbbbbbbbbbbb":
                question["asked_at"] = expired["asked_at"]
        after = client.get(f"/api/bridge/jobs/{job['job_id']}", headers=headers).json()
        assert after["pending_approvals"] == []
        assert after["waiting_for_approval"] is False
        release.set()


def test_completed_job_lists_only_live_approvals_and_open_questions_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app(tmp_path, monkeypatch)
    _ask(tmp_path / "home", "cccccccccccc", "choose a destination", kind="user_question")
    # Exercise the same output path with a completed BridgeJob while the durable question remains
    # queued; ask_user associates the durable request with the coding turn that asked it.
    from chimera.api.desktop_bridge import BridgeJob, _job_out
    from chimera.governance.pending import is_waiting, queued_for_turn

    job = BridgeJob(id="completed-question", route="test.completed", done=True, turn_id="turn-q")
    question_file = tmp_path / "home" / "approvals" / "cccccccccccc.ask.json"
    value = json.loads(question_file.read_text(encoding="utf-8"))
    value["run_id"] = "turn-q"
    value["wait_seconds"] = 300.0
    question_file.write_text(json.dumps(value), encoding="utf-8")
    result = _job_out(
        job,
        None,
        [],
        lambda request_id: is_waiting(tmp_path / "home", request_id),
        queued=lambda turn_id: queued_for_turn(tmp_path / "home", turn_id, 300.0),
    )
    assert result["done"] is True
    assert result["waiting_for_approval"] is False
    assert result["pending_approvals"] == []
    assert [item["id"] for item in result["still_open_questions"]] == ["cccccccccccc"]

    # An answer makes the durable question non-pending before its waiting worker cleans up.
    (tmp_path / "home" / "approvals" / "cccccccccccc.user-answer.json").write_text(
        json.dumps({"approved": True, "answered_at": time.time()}), encoding="utf-8"
    )
    result = _job_out(
        job,
        None,
        [],
        lambda request_id: is_waiting(tmp_path / "home", request_id),
        queued=lambda turn_id: queued_for_turn(tmp_path / "home", turn_id, 300.0),
    )
    assert result["pending_approvals"] == []
    assert result["still_open_questions"] == []
    assert result["waiting_for_approval"] is False


def test_the_mcp_summary_says_when_silence_refuses_each_question() -> None:
    from chimera.server.desktop_mcp import DesktopMCP

    job = {
        "job_id": "j3",
        "done": False,
        "waiting_for_approval": True,
        "pending_approvals": [
            {"id": "q1", "action": "run_shell: ls", "reason": "review", "seconds_left": 42}
        ],
        "events": [],
        "next": 1,
        "text": "",
    }
    text = DesktopMCP(discover=lambda: None)._render_job(job, "tok")
    assert "run_shell: ls (review)" in text and "refused by silence in 42s" in text
