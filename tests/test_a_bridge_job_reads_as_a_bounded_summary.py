"""A bridge job answers with a bounded summary by default, and never hides a question or a warning.

Three defects seen on 2026-10-18 while operating the app through the bridge (study 34):

* item 3 — ``desktop_job`` without ``since`` returned every event of the turn: 195,000 characters
  for six minutes of work, each tool frame carrying its whole observation;
* item 8 — two approvals of a coding turn expired after 300 s while every poll read
  ``pending_approvals: []``: the job knew only the questions announced on its stream;
* item 10 — "context drift" reached the log and nowhere a person or a client could see it.
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
from chimera.api.desktop_bridge import BridgeJob, _job_out
from chimera.config import get_settings
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun

URL = "http://127.0.0.1:65013"
TOOL_FRAMES = 300
OBSERVATION = "x" * 50_000


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "false")
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    app.state.desktop_bridge.attach(URL)
    return app


def _long_turn(app: Any, monkeypatch: pytest.MonkeyPatch, release: threading.Event) -> None:
    """A turn with a session frame, a drift notice, many large tool frames, and no approval frame."""

    @app.post("/api/test/long")
    async def _stream() -> EventSourceResponse:
        async def frames() -> Any:
            yield {"event": "session", "data": json.dumps({"session_id": "s1", "turn_id": "t1"})}
            yield {
                "event": "approval",
                "data": json.dumps({"id": "approval-1", "action": "send", "preview": [{"label": "body", "value": "secret"}]}),
            }
            yield {
                "event": "notice",
                "data": json.dumps(
                    {
                        "code": "context_drift",
                        "text": "context drift at step 10: 40% of late tool calls failed",
                        "step": 10,
                    }
                ),
            }
            for i in range(TOOL_FRAMES):
                name = "read_file" if i % 3 else "grep"
                yield {
                    "event": "tool",
                    "data": json.dumps({"name": name, "ok": True, "observation": OBSERVATION}),
                }
            # The turn's final frame is available before the stream closes for bookkeeping.
            yield {
                "event": "done",
                "data": json.dumps({"answer": "A" * 3_000, "usd": 0.5, "steps": 7}),
            }
            while not release.is_set():
                await asyncio.sleep(0.02)

        return EventSourceResponse(frames())

    monkeypatch.setitem(ROUTES, "test.long", BridgeRoute("POST", "/api/test/long", stream=True))


def _start(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    job: dict[str, Any] = client.post(
        "/api/bridge/call",
        json={"route": "test.long", "body": {}, "wait_seconds": 2},
        headers=headers,
    ).json()["job"]
    deadline = time.monotonic() + 20
    while job["events_total"] < TOOL_FRAMES + 2 and time.monotonic() < deadline:
        job = client.get(f"/api/bridge/jobs/{job['job_id']}?wait_seconds=1", headers=headers).json()
    assert job["events_total"] >= TOOL_FRAMES + 2
    return job


def test_the_default_answer_is_a_bounded_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    release = threading.Event()
    _long_turn(app, monkeypatch, release)
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    with TestClient(app) as client:
        job = _start(client, headers)
        raw = client.get(f"/api/bridge/jobs/{job['job_id']}", headers=headers)
        summary = raw.json()
        assert summary["summary"] is True
        # 300 frames of 50,000 characters each: the default answer stays within A8's 5 KB budget.
        assert len(raw.content) <= 5_000
        assert len(summary["events"]) <= 4 + 4
        # The warning is kept whatever its age, and counted per tool over the whole turn.
        assert any(e["event"] == "notice" for e in summary["events"])
        approval_event = next(e for e in summary["events"] if e["event"] == "approval")
        assert "preview" not in approval_event["data"]
        assert summary["tool_counts"] == {"grep": 100, "read_file": 200}
        assert summary["notices"][0]["code"] == "context_drift"
        assert summary["result"]["answer"] == "A" * 3_000
        assert summary["text"] == ""
        # Summary event fields are tightly clipped; paging still returns the larger event detail.
        tool = next(e for e in summary["events"] if e["event"] == "tool")
        assert len(tool["data"]["observation"]) <= 80
        page = client.get(
            f"/api/bridge/jobs/{job['job_id']}?since={job['events_total'] - 2}", headers=headers
        ).json()
        assert page["summary"] is False
        paged_tool = next(e for e in page["events"] if e["event"] == "tool")
        assert paged_tool["data"]["observation"].startswith("x" * 2_000)
        assert paged_tool["data"]["observation"].endswith("(+48000 chars)")
        release.set()
        done = client.get(f"/api/bridge/jobs/{job['job_id']}?wait_seconds=10", headers=headers)
        assert done.json()["receipt"] == {"usd": 0.5, "steps": 7}

def test_summary_returns_a_2500_character_final_answer_in_full() -> None:
    job = BridgeJob(id="j-final", route="conversations.send")
    answer = "F" * 2_500
    job.text = answer
    job.final = {"answer": answer, "usd": 0.5}

    summary = _job_out(job, None, [], lambda _qid: False)

    assert summary["text"] == answer
    assert summary["result"]["answer"] == answer


def test_summary_keeps_every_pending_question_and_clips_a_large_error() -> None:
    job = BridgeJob(id="j1", route="conversations.send")
    job.turn_id = "t1"
    for i in range(10):
        job.take(
            "approval",
            json.dumps({"id": f"q{i}", "action": "run a command", "reason": "needs approval"}),
        )
    for i in range(40):
        job.take("tool", json.dumps({"name": "read_file", "observation": f"event {i}"}))
    job.error = "E" * 50_000
    questions = [
        {
            "id": f"q{i}", "action": "run a command", "reason": "needs approval",
            "tool": "run_shell", "asked_at": time.time(), "wait_seconds": 300,
        }
        for i in range(10)
    ]

    summary = _job_out(job, None, [], lambda _qid: True, queued=lambda _tid: questions)
    encoded = json.dumps(summary)

    assert len(encoded.encode()) <= 5_000
    assert [question["id"] for question in summary["pending_approvals"]] == [f"q{i}" for i in range(10)]
    assert [
        event["data"]["id"] for event in summary["events"] if event["event"] == "approval"
    ] == [f"q{i}" for i in range(10)]
    assert summary["error"].startswith("E" * 64)
    assert summary["error"].endswith("(+49488 chars)")


def test_since_pages_through_every_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(tmp_path, monkeypatch)
    release = threading.Event()
    _long_turn(app, monkeypatch, release)
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    with TestClient(app) as client:
        job = _start(client, headers)
        seen: list[int] = []
        since = 0
        for _ in range(10):
            page = client.get(
                f"/api/bridge/jobs/{job['job_id']}?since={since}", headers=headers
            ).json()
            assert page["summary"] is False
            assert len(page["events"]) <= 100
            seen += [e["n"] for e in page["events"]]
            since = page["next"]
            if not page["more"]:
                break
        # The bridge keeps the last 400 events in memory; every one of those, once and in order.
        assert seen == list(range(1, job["events_total"] + 1))
        release.set()


def test_a_question_of_the_turn_shows_at_any_since_even_unannounced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    release = threading.Event()
    _long_turn(app, monkeypatch, release)
    directory = tmp_path / "home" / "approvals"
    directory.mkdir(parents=True, exist_ok=True)
    question = directory / "q7.ask.json"
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    with TestClient(app) as client:
        job = _start(client, headers)
        # Written by `pending.ask_durably` with the turn's id; the stream never carried a frame.
        question.write_text(
            json.dumps(
                {
                    "id": "q7", "action": "run_shell: git status", "reason": "no command grant",
                    "asked_at": time.time(), "run_id": "t1", "tool": "run_shell",
                }
            ),
            encoding="utf-8",
        )
        # Another turn's question is not this job's.
        (directory / "q8.ask.json").write_text(
            json.dumps({"id": "q8", "action": "x", "reason": "y", "asked_at": time.time(),
                        "run_id": "other"}),
            encoding="utf-8",
        )
        for query in ("", f"?since={job['events_total']}"):
            out = client.get(f"/api/bridge/jobs/{job['job_id']}{query}", headers=headers).json()
            assert out["waiting_for_approval"] is True
            [item] = out["pending_approvals"]
            assert item["id"] == "q7" and item["tool"] == "run_shell"
            assert item["reason"] == "no command grant"
            assert 0 < item["seconds_left"] <= 300
        question.unlink()
        out = client.get(f"/api/bridge/jobs/{job['job_id']}", headers=headers).json()
        assert out["pending_approvals"] == [] and out["waiting_for_approval"] is False
        release.set()


def test_the_mcp_tool_asks_for_the_summary_unless_given_since() -> None:
    from chimera.server.desktop_mcp import DesktopMCP

    urls: list[str] = []

    def http(method: str, url: str, tok: str, body: Any, timeout: float) -> tuple[int, Any]:
        urls.append(url)
        return 200, {"job_id": "j1", "done": False, "events": [], "next": 0,
                     "notices": [{"code": "context_drift", "text": "context drift at step 10"}]}

    mcp = DesktopMCP(
        discover=lambda: {"url": "http://x", "token": "tok-" + "Q" * 40, "pid": 1, "version": "v",
                          "full": False},
        http=http,
    )
    text = mcp.dispatch("desktop_job", {"job_id": "j1"})
    assert "since=" not in urls[-1]
    # The warning is in the first lines a client reads.
    assert "Notice: context drift at step 10" in text
    mcp.dispatch("desktop_job", {"job_id": "j1", "since": 5})
    assert "since=5" in urls[-1]
