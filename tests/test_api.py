"""Tests for the desktop API (FastAPI + SSE), no network — a fake agent drives the real ChatSession.

Skipped entirely when the optional 'desktop' extra (fastapi/sse-starlette) isn't installed.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.core.agent import AgentResult, ToolActivity  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402


class _FakeAgent:
    """Agent stub: streams two token deltas + one tool activity, returns a rich AgentResult."""

    def run(
        self,
        task: str,
        *,
        on_token: Callable[[str], None] | None = None,
        on_tool: Callable[[ToolActivity], None] | None = None,
    ) -> AgentResult:
        if on_tool is not None:
            on_tool(ToolActivity(name="read_file", arguments={}, ok=True, observation="ok"))
        if on_token is not None:
            on_token("Hel")
            on_token("lo")
        return AgentResult(
            answer="Hello",
            steps=1,
            stopped_reason="final",
            prompt_tokens=10,
            completion_tokens=2,
            usd=0.001,
            tool_names=["read_file"],
            model="openrouter/test-model",
        )


def _client(tmp_path: Any, *, token: str | None = None) -> TestClient:
    from chimera.api import build_api_app

    # Construct via validation aliases (the fields only populate by alias, not python name), so home
    # actually points at tmp_path and doesn't pollute the repo's .chimera dir.
    kwargs: dict[str, Any] = {"CHIMERA_HOME": str(tmp_path / "home")}
    if token is not None:
        kwargs["CHIMERA_SERVER_TOKEN"] = token
    settings = Settings(**kwargs)

    def factory() -> ChatSession:
        return ChatSession(_FakeAgent())

    return TestClient(build_api_app(factory, settings=settings))


def _read_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse a raw SSE stream body into (event, data-dict) pairs."""
    events: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            events.append((event, json.loads(line[len("data:"):].strip())))
    return events


def test_chat_stream_emits_session_token_tool_done(tmp_path: Any) -> None:
    client = _client(tmp_path)
    resp = client.post("/api/chat/stream", json={"message": "hi", "stream": True})
    assert resp.status_code == 200
    events = _read_sse(resp.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "session"  # client learns its session id first
    assert "token" in kinds and "tool" in kinds and kinds[-1] == "done"
    tokens = [d["text"] for e, d in events if e == "token"]
    assert tokens == ["Hel", "lo"]  # deltas in order
    tool = next(d for e, d in events if e == "tool")
    assert tool == {"name": "read_file", "ok": True}
    done = next(d for e, d in events if e == "done")
    assert done["answer"] == "Hello"
    assert done["prompt_tokens"] == 10 and done["completion_tokens"] == 2
    assert done["usd"] == 0.001 and done["tool_names"] == ["read_file"]
    assert "route_meta" in done and done["route_meta"] is None  # single-model turn -> honest null


def test_chat_stream_without_streaming_still_answers(tmp_path: Any) -> None:
    client = _client(tmp_path)
    resp = client.post("/api/chat/stream", json={"message": "hi", "stream": False})
    events = _read_sse(resp.text)
    assert "token" not in [e for e, _ in events]  # no token events when streaming is off
    done = next(d for e, d in events if e == "done")
    assert done["answer"] == "Hello"


def test_fuse_flag_swaps_agent_backend_for_the_turn_then_restores(tmp_path: Any) -> None:
    """`fuse=true` routes THIS turn through the provided fusion backend (so its trace surfaces),
    and the session agent's original backend is restored afterwards."""
    from chimera.api import build_api_app

    fuse_backend = object()  # stands in for the FusionEngine
    seen: dict[str, Any] = {}

    class _SwappableAgent:
        def __init__(self) -> None:
            self.backend: Any = object()  # the session's normal backend

        def run(self, task: str, *, on_token: Any = None, on_tool: Any = None) -> AgentResult:
            seen["backend_during_run"] = self.backend
            fused = self.backend is fuse_backend
            return AgentResult(
                answer="F",
                steps=1,
                stopped_reason="final",
                route_meta={"kind": "fusion", "panel": []} if fused else None,
            )

    agent = _SwappableAgent()
    default_backend = agent.backend
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(
        build_api_app(lambda: ChatSession(agent), settings=settings, fuse_backend=fuse_backend)
    )

    resp = client.post("/api/chat/stream", json={"message": "hard one", "fuse": True, "stream": True})
    done = next(d for e, d in _read_sse(resp.text) if e == "done")
    assert seen["backend_during_run"] is fuse_backend  # swapped in for the fused turn
    assert done["route_meta"] == {"kind": "fusion", "panel": []}  # fusion trace surfaced
    assert agent.backend is default_backend  # restored after the turn


def test_chat_turn_appends_usage_line_and_usage_endpoint_summarizes(tmp_path: Any) -> None:
    from chimera.api.usage import load_usage

    client = _client(tmp_path)
    client.post("/api/chat/stream", json={"message": "hi", "stream": True})

    # A usage record was appended for the turn, carrying the turn's real signals.
    records = load_usage(tmp_path / "home" / "usage.jsonl")
    assert len(records) == 1
    rec = records[0]
    assert rec.model == "openrouter/test-model"
    assert rec.prompt_tokens == 10 and rec.completion_tokens == 2
    assert rec.usd == 0.001 and rec.tools == 1 and rec.route_kind is None

    # And GET /api/usage returns the aggregated summary shape over that record.
    summary = client.get("/api/usage").json()
    assert {"totals", "by_day", "by_model", "by_session", "cache_hit_pct", "route_mix"} <= set(summary)
    assert summary["totals"]["turns"] == 1
    assert summary["totals"]["usd"] == 0.001 and summary["totals"]["unpriced_turns"] == 0
    assert summary["by_model"][0]["model"] == "openrouter/test-model"
    assert summary["route_mix"] == {"single": 1, "fusion": 0, "cascade": 0}


def test_runs_endpoint_returns_receipts_newest_first(tmp_path: Any) -> None:
    from chimera.api.runs import RunReceipt, append_run

    # Seed two run receipts under the client's home (append order = chronological).
    run_log = tmp_path / "home" / "runs.jsonl"
    append_run(run_log, RunReceipt(ts="2026-07-13T00:00:00+00:00", task="older", success=True))
    append_run(
        run_log,
        RunReceipt(
            ts="2026-07-13T01:00:00+00:00",
            task="newer",
            success=False,
            verify_command="pytest -q",
        ),
    )

    client = _client(tmp_path)
    runs = client.get("/api/runs").json()
    assert [r["task"] for r in runs] == ["newer", "older"]  # most recent first
    assert runs[0]["success"] is False and runs[0]["verify_command"] == "pytest -q"
    assert runs[1]["success"] is True and runs[1]["verify_command"] is None


def test_post_runs_streams_events_done_and_persists_receipt(tmp_path: Any) -> None:
    """The run trigger streams `event` frames + a terminal `done`, and (via the agent's run_log) a
    receipt lands in runs.jsonl. Uses an injected factory that builds a REAL AutonomousAgent over a
    fake worker — no LLM — so the endpoint wiring (SSE marshalling + receipt persistence) is exercised.
    """
    from chimera.api import build_api_app
    from chimera.api.app import RunRequest
    from chimera.api.runs import load_runs
    from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
    from chimera.core.checkpoint import WorkspaceGuard
    from chimera.core.events import EventSink

    class _FakeWorker:
        def run(self, task: str) -> AgentResult:
            return AgentResult(answer="did it", steps=1, stopped_reason="final")

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))

    def solve_factory(
        req: RunRequest,
        ws: Any,
        on_event: EventSink,
        settings: Any,
        should_stop: Callable[[], bool] | None = None,
    ) -> AutonomousAgent:
        # No verifier / planner / manager: verify abstains → manager approves → success on attempt 1.
        return AutonomousAgent(
            _FakeWorker(),
            should_stop=should_stop,
            guard=WorkspaceGuard(ws),
            workspace=ws,
            on_event=on_event,
            run_log=settings.home / "runs.jsonl",
            config=AutonomousConfig(max_attempts=req.max_attempts, use_planner=False, use_manager=False),
        )

    client = TestClient(
        build_api_app(
            lambda: ChatSession(_FakeAgent()), settings=settings, solve_agent_factory=solve_factory
        )
    )
    resp = client.post("/api/runs", json={"task": "make it so", "max_attempts": 2})
    assert resp.status_code == 200
    events = _read_sse(resp.text)
    kinds = [e for e, _ in events]
    # Cancel is wired but never triggered here (default path): the stream still emits its normal frames
    # — a leading `run` id frame, the `event` progress frames, and a terminal `done`.
    assert kinds[0] == "run" and "event" in kinds and kinds[-1] == "done"
    run_frame = next(d for e, d in events if e == "run")
    assert isinstance(run_frame["run_id"], str) and run_frame["run_id"]
    done = next(d for e, d in events if e == "done")
    assert done["success"] is True and done["answer"] == "did it" and done["attempts"] == 1
    assert done["stopped_reason"] == ""  # not cancelled — an ordinary completed run
    # Each streamed `event` frame carries the AgentEvent kind + text (compact, no huge answer field).
    ev = next(d for e, d in events if e == "event")
    assert "kind" in ev and "text" in ev and "answer" not in ev

    # The receipt was persisted by the agent's run_log — the read-only GET now lists it.
    receipts = load_runs(tmp_path / "home" / "runs.jsonl")
    assert len(receipts) == 1 and receipts[0].task == "make it so" and receipts[0].success is True
    listed = client.get("/api/runs").json()
    assert listed[0]["task"] == "make it so"


def test_cancel_run_sets_known_event_and_noops_unknown(tmp_path: Any) -> None:
    """POST /api/runs/{id}/cancel sets a KNOWN in-flight run's stop Event ({ok:true}); an unknown or
    already-finished id is an honest no-op ({ok:false}, 200 — a stale Stop click is not a 404)."""
    import threading as _threading

    from chimera.api import app as app_module

    client = _client(tmp_path)  # the endpoint reads the module-level run registry; no run needed
    # Unknown id → {ok: false}, 200 (NOT 404) — a finished/unknown run is a no-op, never an error.
    resp = client.post("/api/runs/nope/cancel")
    assert resp.status_code == 200 and resp.json() == {"ok": False}
    # Known in-flight id → its stop Event is set, {ok: true}. Register a stand-in event as an in-flight
    # run would, then assert the endpoint flips it.
    event = _threading.Event()
    app_module._run_cancels["run-xyz"] = event
    try:
        resp = client.post("/api/runs/run-xyz/cancel")
        assert resp.status_code == 200 and resp.json() == {"ok": True}
        assert event.is_set()  # the run's cooperative-stop flag was actually raised
    finally:
        app_module._run_cancels.pop("run-xyz", None)


# --- Agent Manager (POST /api/agents: parallel isolated multi-task batch) --------------------------


def _init_repo(path: Any) -> None:
    """Init a throwaway git repo with one committed seed file (worktree isolation needs a repo)."""
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init"],
        ["config", "user.email", "t@t.co"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True)
    (path / "seed.txt").write_text("seed", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True, text=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True, text=True, check=True)


class _WritingAgent:
    """A stub 'agent' the injected factory returns: emits one tagged event, writes ONE file into its
    (isolated) workspace, and reports a successful single-attempt AutonomousResult. Writing a real file
    is what makes the worktree record a changed path — so conflict detection is exercised for real."""

    def __init__(self, ws: Any, on_event: Any, rel: str, content: str) -> None:
        self.ws, self.on_event, self.rel, self.content = ws, on_event, rel, content

    def run(self, task: str) -> Any:
        from chimera.core.autonomous import Attempt, AutonomousResult
        from chimera.core.events import AgentEvent

        self.on_event(AgentEvent(kind="status", text="working"))
        # An `attempt` event carries its OWN `index` (the attempt number) in `data`. The endpoint must
        # still tag the frame with the TASK index — this proves the task tag isn't clobbered by it.
        self.on_event(AgentEvent(kind="attempt", text="attempt 1/1", data={"index": 1, "max_attempts": 1}))
        (self.ws / self.rel).write_text(self.content, encoding="utf-8")
        return AutonomousResult(
            answer="done",
            success=True,
            attempts=[
                Attempt(
                    index=0, answer="done", approved=True, verified=True, reverted=False, success=True
                )
            ],
        )


def _agents_client(tmp_path: Any, rel_for: Callable[[str], str]) -> TestClient:
    """A TestClient whose injected solve factory returns a _WritingAgent writing `rel_for(task)`."""
    from chimera.api import build_api_app
    from chimera.api.app import RunRequest
    from chimera.core.events import EventSink

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))

    def factory(
        req: RunRequest,
        ws: Any,
        on_event: EventSink,
        _settings: Any,
        _should_stop: Callable[[], bool] | None = None,
    ) -> Any:
        return _WritingAgent(ws, on_event, rel_for(req.task), req.task)

    return TestClient(
        build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings, solve_agent_factory=factory)
    )


def test_post_agents_streams_tagged_events_and_batch_done(tmp_path: Any) -> None:
    """Two tasks run concurrently, each in its own worktree, writing DISJOINT files. Every live `event`
    frame is tagged with its task index, and the terminal `batch_done` reports both merged, no conflict."""
    ws = tmp_path / "ws"
    _init_repo(ws)
    client = _agents_client(tmp_path, lambda task: "a.txt" if "alpha" in task else "b.txt")

    resp = client.post(
        "/api/agents",
        json={"tasks": [{"task": "alpha work"}, {"task": "beta work"}], "workspace": str(ws)},
    )
    assert resp.status_code == 200
    events = _read_sse(resp.text)
    kinds = [e for e, _ in events]
    # Per-task cancel is wired but never triggered here (default path): the stream still emits its
    # normal frames — a leading `batch` id frame, then `start`, the tagged events, and `batch_done`.
    assert kinds[:2] == ["batch", "start"] and kinds[-1] == "batch_done"
    start = next(d for e, d in events if e == "start")
    assert start["tasks"] == ["alpha work", "beta work"] and start["workspace"] == str(ws)

    # Every streamed `event` frame carries a task index + the AgentEvent kind/text (compact).
    tagged = [d for e, d in events if e == "event"]
    assert tagged and all("index" in d and "kind" in d and "text" in d for d in tagged)
    assert {d["index"] for d in tagged} == {0, 1}  # both tasks streamed progress, correctly tagged
    # The `attempt` frames' own data-index (1) never clobbers the task tag: every tagged frame's
    # index is a valid TASK index (0 or 1), and both tasks' attempt frames say "attempt 1/1".
    attempts = [d for d in tagged if d["kind"] == "attempt"]
    assert len(attempts) == 2 and all(d["index"] in (0, 1) and d["text"] == "attempt 1/1" for d in attempts)

    bd = next(d for e, d in events if e == "batch_done")
    assert bd["is_repo"] is True  # a git repo → isolation was REAL
    assert bd["merged"] == 2 and bd["conflicts"] == []  # disjoint files both merged, no conflict
    assert {r["index"] for r in bd["results"]} == {0, 1}
    assert all(r["success"] for r in bd["results"])
    assert sorted(p for r in bd["results"] for p in r["changed_paths"]) == ["a.txt", "b.txt"]
    # The disjoint edits landed back in the real workspace.
    assert (ws / "a.txt").exists() and (ws / "b.txt").exists()


def test_post_agents_reports_same_file_conflict(tmp_path: Any) -> None:
    """Two successful tasks that BOTH change the same file: the collision is reported in `conflicts`
    and left UNMERGED (neither version silently wins) — the honest cross-task conflict signal."""
    ws = tmp_path / "ws"
    _init_repo(ws)
    client = _agents_client(tmp_path, lambda _task: "shared.txt")  # both tasks target the same path

    resp = client.post(
        "/api/agents",
        json={"tasks": [{"task": "one"}, {"task": "two"}], "workspace": str(ws)},
    )
    bd = next(d for e, d in _read_sse(resp.text) if e == "batch_done")
    assert bd["conflicts"] == ["shared.txt"]  # both touched it → flagged
    assert bd["merged"] == 0  # neither version merged
    assert not (ws / "shared.txt").exists()  # left for the user to resolve, not clobbered


def test_post_agents_non_git_repo_sets_is_repo_false(tmp_path: Any) -> None:
    """Outside a git repo, tasks run in-place with NO isolation — the response says so honestly via
    `is_repo: false` (and no changed_paths are tracked, since there's no worktree to diff)."""
    ws = tmp_path / "plain"
    ws.mkdir()
    client = _agents_client(tmp_path, lambda _task: "x.txt")

    resp = client.post("/api/agents", json={"tasks": [{"task": "t"}], "workspace": str(ws)})
    bd = next(d for e, d in _read_sse(resp.text) if e == "batch_done")
    assert bd["is_repo"] is False  # not a repo → ran in-place, no isolation
    assert bd["results"][0]["changed_paths"] == []  # no worktree, nothing to diff
    assert (ws / "x.txt").read_text(encoding="utf-8") == "t"  # the edit happened in-place


def test_post_agents_emits_batch_id_frame_and_leaves_no_cancel_path_untouched(tmp_path: Any) -> None:
    """The batch stream leads with a `batch` frame carrying its id (the cancel handle), and the DEFAULT
    path — no cancel requested — still streams exactly as before: start → tagged events → batch_done,
    every task succeeding. The registry is popped when the batch ends, so it never leaks."""
    from chimera.api import app as app_module

    ws = tmp_path / "ws"
    _init_repo(ws)
    client = _agents_client(tmp_path, lambda task: "a.txt" if "alpha" in task else "b.txt")

    resp = client.post(
        "/api/agents",
        json={"tasks": [{"task": "alpha work"}, {"task": "beta work"}], "workspace": str(ws)},
    )
    events = _read_sse(resp.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "batch"  # the id lands FIRST, before any task runs
    batch_frame = events[0][1]
    assert isinstance(batch_frame["batch_id"], str) and batch_frame["batch_id"]
    # Unchanged default path: the ordinary frames still follow, and every task still passed.
    assert kinds[1] == "start" and "event" in kinds and kinds[-1] == "batch_done"
    bd = next(d for e, d in events if e == "batch_done")
    assert all(r["success"] for r in bd["results"]) and bd["merged"] == 2
    # The finished batch is no longer cancellable — its registry entry was popped.
    assert batch_frame["batch_id"] not in app_module._agents_cancels


def test_cancel_agents_all_tasks_sets_every_event(tmp_path: Any) -> None:
    """POST /api/agents/{id}/cancel with index=null raises EVERY task's stop flag, and reports how many
    it actually raised."""
    import threading as _threading

    from chimera.api import app as app_module

    client = _client(tmp_path)  # the endpoint reads the module-level batch registry; no batch needed
    events = {0: _threading.Event(), 1: _threading.Event(), 2: _threading.Event()}
    app_module._agents_cancels["batch-all"] = events
    try:
        resp = client.post("/api/agents/batch-all/cancel", json={"index": None})
        assert resp.status_code == 200 and resp.json() == {"ok": True, "cancelled": 3}
        assert all(e.is_set() for e in events.values())  # every task's cooperative-stop flag raised
        # Re-cancelling is idempotent: still ok, but nothing NEW was raised.
        assert client.post("/api/agents/batch-all/cancel", json={"index": None}).json() == {
            "ok": True,
            "cancelled": 0,
        }
    finally:
        app_module._agents_cancels.pop("batch-all", None)


def test_cancel_agents_one_index_sets_only_that_task(tmp_path: Any) -> None:
    """An int index cancels JUST that task — the batch's other workers keep running (their flags stay
    down). An out-of-range index targets nothing: an honest no-op, not a crash."""
    import threading as _threading

    from chimera.api import app as app_module

    client = _client(tmp_path)
    events = {0: _threading.Event(), 1: _threading.Event(), 2: _threading.Event()}
    app_module._agents_cancels["batch-one"] = events
    try:
        resp = client.post("/api/agents/batch-one/cancel", json={"index": 1})
        assert resp.status_code == 200 and resp.json() == {"ok": True, "cancelled": 1}
        assert events[1].is_set()  # only the named task
        assert not events[0].is_set() and not events[2].is_set()  # the others run on
        # An index outside the batch matches no task — {ok:false, cancelled:0}, still a 200.
        assert client.post("/api/agents/batch-one/cancel", json={"index": 9}).json() == {
            "ok": False,
            "cancelled": 0,
        }
    finally:
        app_module._agents_cancels.pop("batch-one", None)


def test_cancel_agents_noops_unknown_batch(tmp_path: Any) -> None:
    """An unknown or already-finished batch id is an honest no-op ({ok:false, cancelled:0}, 200 — a
    stale Stop click is not a 404), for both a whole-batch and a single-index cancel."""
    client = _client(tmp_path)

    resp = client.post("/api/agents/nope/cancel", json={"index": None})
    assert resp.status_code == 200 and resp.json() == {"ok": False, "cancelled": 0}
    resp = client.post("/api/agents/nope/cancel", json={"index": 0})
    assert resp.status_code == 200 and resp.json() == {"ok": False, "cancelled": 0}


def test_agents_batch_wires_each_task_stop_flag_to_its_registry_event(tmp_path: Any) -> None:
    """THE wiring test — the half the other cancel tests can't see.

    They plant Events into `_agents_cancels` by hand and prove the endpoint raises them: that is
    bookkeeping. It stays green even if the batch hands its agents NO stop flag at all — i.e. every
    card's Stop silently does nothing. This closes that gap by running a REAL batch and asserting the
    flag each task's agent actually polls IS one of the live registry Events the endpoint raises, and
    that the two tasks get DISTINCT flags (so stopping one can't halt the other).
    """
    from chimera.api import app as app_module
    from chimera.api import build_api_app
    from chimera.api.app import RunRequest
    from chimera.core.events import EventSink

    ws = tmp_path / "ws"
    _init_repo(ws)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    seen: list[dict[str, Any]] = []

    def factory(
        req: RunRequest,
        ws_i: Any,
        on_event: EventSink,
        _settings: Any,
        should_stop: Callable[[], bool] | None = None,
    ) -> Any:
        # Runs mid-batch, so the batch's registry entry is still alive here. A bound `Event.is_set`
        # compares equal only to the same Event's, so this is real identity, not shape-matching.
        wired = should_stop is not None and any(
            should_stop == ev.is_set
            for events in app_module._agents_cancels.values()
            for ev in events.values()
        )
        seen.append({"task": req.task, "stop": should_stop, "wired": wired})
        return _WritingAgent(ws_i, on_event, "a.txt" if "alpha" in req.task else "b.txt", req.task)

    client = TestClient(
        build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings, solve_agent_factory=factory)
    )
    resp = client.post(
        "/api/agents",
        json={"tasks": [{"task": "alpha work"}, {"task": "beta work"}], "workspace": str(ws)},
    )
    assert resp.status_code == 200

    assert len(seen) == 2
    # Each task got a REAL flag (None here = Stop is a dead button), and it is a registry Event's.
    assert all(s["stop"] is not None for s in seen)
    assert all(s["wired"] for s in seen)
    # Distinct flags: cancelling one index must not stop the other task.
    assert seen[0]["stop"] != seen[1]["stop"]
    # Nothing was cancelled, so no flag was ever raised.
    assert all(s["stop"]() is False for s in seen)


def test_post_agents_rejects_empty_and_oversized_task_lists(tmp_path: Any) -> None:
    """Guardrails: an empty task list is a 400; more than the cap (8) is a 400."""
    client = _agents_client(tmp_path, lambda _task: "x.txt")
    assert client.post("/api/agents", json={"tasks": []}).status_code == 400
    too_many = {"tasks": [{"task": f"t{i}"} for i in range(9)]}
    assert client.post("/api/agents", json=too_many).status_code == 400


def test_plan_endpoint_returns_steps_and_makes_no_edits(tmp_path: Any, monkeypatch: Any) -> None:
    """POST /api/plan runs ONLY the planner (a single model call): it returns the concrete steps and
    touches nothing on disk. The planner is stubbed (no network) so the endpoint wiring is exercised."""
    from chimera.api import build_api_app
    from chimera.core.planner import Plan

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "sentinel.txt").write_text("untouched\n", encoding="utf-8")
    before = sorted(p.name for p in ws.iterdir())

    monkeypatch.setattr(
        "chimera.core.planner.Planner.plan",
        lambda self, task, *, context="": Plan(
            steps=["Read the file", "Fix the bug"], raw="1. Read the file\n2. Fix the bug"
        ),
    )
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(
        build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings, workspace=ws)
    )

    resp = client.post("/api/plan", json={"task": "fix the bug"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["steps"] == ["Read the file", "Fix the bug"]
    assert "1. Read the file" in body["text"] and body["note"] == ""
    # No edits: the workspace is unchanged (the planner only makes a model call, never touches files).
    assert sorted(p.name for p in ws.iterdir()) == before
    assert (ws / "sentinel.txt").read_text(encoding="utf-8") == "untouched\n"


def test_plan_endpoint_degrades_to_empty_steps_on_model_error(tmp_path: Any, monkeypatch: Any) -> None:
    """A planner/model hiccup returns empty steps + an honest note — never a 500."""
    from chimera.api import build_api_app

    def _boom(self: Any, task: str, *, context: str = "") -> Any:
        raise RuntimeError("model unavailable")

    monkeypatch.setattr("chimera.core.planner.Planner.plan", _boom)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings))

    resp = client.post("/api/plan", json={"task": "do a thing"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["steps"] == [] and body["text"] == "" and body["note"]


def test_screenshot_endpoint_captures_and_serves(tmp_path: Any, monkeypatch: Any) -> None:
    """POST /api/verify/screenshot captures a PNG (the driver mocked offline — no Chromium) and returns
    an id; GET /api/artifacts/{id} then serves the stored bytes as image/png."""
    from pathlib import Path

    import chimera.api.app as app_mod
    from chimera.api import build_api_app

    def fake_capture(url: str, path: Path) -> None:
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\nfake")  # a real (stub) PNG on disk, no browser
        return None

    monkeypatch.setattr(app_mod, "_capture_screenshot", fake_capture)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings))

    resp = client.post("/api/verify/screenshot", json={"url": "http://localhost:5173"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["error"] is None
    artifact_id = body["id"]
    assert artifact_id

    got = client.get(f"/api/artifacts/{artifact_id}")
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/png"
    assert got.content.startswith(b"\x89PNG")


def test_screenshot_endpoint_missing_browser_degrades(tmp_path: Any, monkeypatch: Any) -> None:
    """No browser runtime → a clean 200 {ok:false, id:null} carrying the honest install hint — no 500,
    no fake image."""
    import chimera.api.app as app_mod
    from chimera.api import build_api_app

    monkeypatch.setattr(app_mod, "_capture_screenshot", lambda url, path: app_mod._BROWSER_MISSING_HINT)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings))

    resp = client.post("/api/verify/screenshot", json={"url": "http://localhost:5173"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False and body["id"] is None
    assert "playwright install chromium" in body["error"]


def test_artifact_id_validation_rejects_traversal(tmp_path: Any) -> None:
    """GET /api/artifacts/{id} is hex-only: a traversal / dotted / non-hex id is a 404 — it is NOT an
    arbitrary-file read. A secret planted outside the artifacts dir is never served."""
    from chimera.api import build_api_app

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    secret = tmp_path / "home" / "secret.txt"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("TOP-SECRET-CONTENT", encoding="utf-8")
    client = TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings))

    # Ids that reach the route param and must fail the hex allowlist (dots, non-hex, uppercase), plus
    # a well-formed-but-nonexistent id. None may 200 or leak the secret.
    for bad in ["..", "abc.txt", "nothexvalue", "ABCDEF01", "0123456789abcdef"]:
        r = client.get(f"/api/artifacts/{bad}")
        assert r.status_code == 404, bad
        assert "TOP-SECRET-CONTENT" not in r.text


def test_run_request_plan_injection_skips_the_planner(tmp_path: Any) -> None:
    """When a plan is provided, the AutonomousAgent uses it verbatim and NEVER calls the planner —
    the seam the desktop 'plan mode' relies on (the human-approved plan drives the run)."""
    from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
    from chimera.core.checkpoint import WorkspaceGuard
    from chimera.core.planner import Plan

    class _SpyPlanner:
        def __init__(self) -> None:
            self.calls = 0

        def plan(self, task: str, *, context: str = "") -> Plan:
            self.calls += 1
            return Plan(steps=["planner ran"], raw="planner ran")

    class _RecordingWorker:
        def __init__(self) -> None:
            self.prompt = ""

        def run(self, task: str) -> AgentResult:
            self.prompt = task
            return AgentResult(answer="did it", steps=1, stopped_reason="final")

    ws = tmp_path / "ws"
    ws.mkdir()
    spy = _SpyPlanner()
    worker = _RecordingWorker()
    agent = AutonomousAgent(
        worker,
        planner=spy,  # would be called if no plan were injected...
        plan=Plan.from_text("1. Approved step one\n2. Approved step two"),  # ...but this wins
        guard=WorkspaceGuard(ws),
        workspace=ws,
        config=AutonomousConfig(max_attempts=1, use_manager=False),
    )
    result = agent.run("do the task")
    assert result.success is True
    assert spy.calls == 0  # the injected plan was used; the planner was NOT invoked
    assert result.plan is not None and result.plan.steps == ["Approved step one", "Approved step two"]
    assert "Approved step one" in worker.prompt  # the approved plan reached the worker's prompt


def test_build_solve_agent_default_path_and_model_mode_plumbing(tmp_path: Any) -> None:
    """The default (no flags) build is a plain single-model loop — no escalate worker, no injected
    plan; model/plan plumb through, and --fuse/--cascade each wire a fusion escalate worker."""
    from chimera.api.app import RunRequest, _build_solve_agent

    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))

    def _sink(_e: Any) -> None:
        return None

    default_agent = _build_solve_agent(RunRequest(task="t"), ws, _sink, settings)
    assert default_agent.escalate_worker is None  # single-model: no fusion retry path
    assert default_agent.provided_plan is None  # plans for itself, as before

    fuse_agent = _build_solve_agent(RunRequest(task="t", fuse=True), ws, _sink, settings)
    assert fuse_agent.escalate_worker is not None  # fusion escalate worker wired

    cascade_agent = _build_solve_agent(RunRequest(task="t", cascade=True), ws, _sink, settings)
    assert cascade_agent.escalate_worker is not None  # cascade tops out in fusion too

    planned = _build_solve_agent(
        RunRequest(task="t", model="vendor/model", plan="1. do it\n2. verify it"), ws, _sink, settings
    )
    assert planned.provided_plan is not None
    assert planned.provided_plan.steps == ["do it", "verify it"]
    assert planned.worker.config.model == "vendor/model"


def test_fs_tree_and_file_endpoints_scope_to_the_workspace(tmp_path: Any) -> None:
    """The read-only fs endpoints list a workspace's tree and read a file, guarded by the app's
    workspace and the path-escape check (a `..` → 400; an invalid workspace param → 400)."""
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "main.py").write_text("print('x')\n", encoding="utf-8")
    (ws / ".git").mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(
        build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings, workspace=ws)
    )

    tree = client.get("/api/fs/tree").json()
    names = [e["name"] for e in tree["entries"]]
    assert names == ["src"] and ".git" not in names  # dir only, ignored dir pruned

    sub = client.get("/api/fs/tree", params={"path": "src"}).json()
    assert [e["path"] for e in sub["entries"]] == ["src/main.py"]

    f = client.get("/api/fs/file", params={"path": "src/main.py"}).json()
    assert f["content"] == "print('x')\n" and f["note"] == ""

    # A path escape is a clean 400 (never a 500), and an invalid workspace param is a 400 too.
    assert client.get("/api/fs/file", params={"path": "../secret"}).status_code == 400
    assert (
        client.get("/api/fs/tree", params={"workspace": str(tmp_path / "nope")}).status_code == 400
    )


def test_fs_file_put_writes_and_guards(tmp_path: Any) -> None:
    """PUT /api/fs/file writes a (new) file atomically inside the workspace and returns its byte
    count; a path escape or oversize content is a clean 400 (never a 500)."""
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(
        build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings, workspace=ws)
    )

    resp = client.put("/api/fs/file", json={"path": "pkg/new.py", "content": "x = 1\n"})
    assert resp.status_code == 200
    assert resp.json() == {"path": "pkg/new.py", "bytes": 6}
    assert (ws / "pkg" / "new.py").read_bytes() == b"x = 1\n"  # parent dir created, content on disk

    # A `..` escape and content over the 1 MB cap both map to 400 (never a 500).
    assert client.put("/api/fs/file", json={"path": "../evil", "content": "x"}).status_code == 400
    big = "a" * 1_000_001
    assert client.put("/api/fs/file", json={"path": "big.txt", "content": big}).status_code == 400


def test_session_is_persisted_and_listed_and_deletable(tmp_path: Any) -> None:
    client = _client(tmp_path)
    resp = client.post("/api/chat/stream", json={"message": "remember me", "stream": True})
    sid = next(d for e, d in _read_sse(resp.text) if e == "session")["session_id"]

    listed = client.get("/api/sessions").json()
    assert any(s["id"] == sid and s["turns"] == 1 for s in listed)
    assert listed[0]["title"] == "remember me"  # title = first user message

    got = client.get(f"/api/sessions/{sid}").json()
    assert got["turns"] == [{"user": "remember me", "assistant": "Hello"}]

    assert client.delete(f"/api/sessions/{sid}").json() == {"deleted": True}
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def _token_client(monkeypatch: Any, tmp_path: Any, token: str) -> TestClient:
    # The guard reads get_settings() fresh (so a runtime token change enforces), so the token must be
    # in the process settings, not just the injected Settings — set it via env + clear the cache.
    from chimera.config import Settings, get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", token)
    get_settings.cache_clear()
    from chimera.api import build_api_app

    return TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=Settings()))


def test_bearer_token_guards_chat_when_configured(monkeypatch: Any, tmp_path: Any) -> None:
    from chimera.config import get_settings

    client = _token_client(monkeypatch, tmp_path, "s3cret")
    assert client.post("/api/chat/stream", json={"message": "hi"}).status_code == 401
    ok = client.post(
        "/api/chat/stream", json={"message": "hi"}, headers={"Authorization": "Bearer s3cret"}
    )
    assert ok.status_code == 200
    get_settings.cache_clear()


def test_reads_require_token_when_configured(monkeypatch: Any, tmp_path: Any) -> None:
    from chimera.config import get_settings

    client = _token_client(monkeypatch, tmp_path, "s3cret")
    # A GET read now requires the token too (transcripts/memory/config must not be readable without it).
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/memory").status_code == 401
    assert client.get("/api/config", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/api/health").status_code == 200  # health stays open for liveness checks
    get_settings.cache_clear()


def test_patch_config_rejects_newline_in_value(tmp_path: Any) -> None:
    # A newline in the value would inject extra .env lines even though the key is allowlisted.
    from chimera.api.config_api import patch_config

    with pytest.raises(ValueError, match="newline"):
        patch_config(
            {"CHIMERA_CACHE": "1\nOPENROUTER_API_KEY=sk-evil"}, env_path=tmp_path / ".env"
        )
    assert not (tmp_path / ".env").exists()  # nothing was written


def test_health_ok(tmp_path: Any) -> None:
    assert _client(tmp_path).get("/api/health").json()["status"] == "ok"


def test_read_config_masks_every_secret(tmp_path: Any) -> None:
    from chimera.api.config_api import read_config

    settings = Settings(
        CHIMERA_HOME=str(tmp_path), OPENROUTER_API_KEY="sk-supersecretvalue9999", CHIMERA_SERVER_TOKEN="tok"
    )
    cfg = read_config(settings)
    blob = json.dumps(cfg)
    assert "sk-supersecretvalue9999" not in blob  # the raw key never appears anywhere
    openrouter = next(p for p in cfg["providers"] if p["env"] == "OPENROUTER_API_KEY")
    assert openrouter["set"] is True and openrouter["hint"] == "…9999"  # only a last-4 hint
    assert cfg["server"]["token_set"] is True  # server token: presence only, no hint field leaked


def test_patch_config_rejects_unknown_keys(tmp_path: Any) -> None:
    from chimera.api.config_api import patch_config

    with pytest.raises(ValueError, match="not editable"):
        patch_config({"CHIMERA_HOME": "/etc/evil", "PATH": "x"}, env_path=tmp_path / ".env")


def test_patch_config_writes_env_atomically(tmp_path: Any) -> None:
    from chimera.api.config_api import patch_config

    env = tmp_path / ".env"
    env.write_text("EXISTING=1\n", encoding="utf-8")
    result = patch_config(
        {"CHIMERA_DEFAULT_MODEL": "openrouter/x", "OPENROUTER_API_KEY": "sk-new"}, env_path=env
    )
    assert result["updated"] == ["CHIMERA_DEFAULT_MODEL", "OPENROUTER_API_KEY"]
    text = env.read_text(encoding="utf-8")
    assert "EXISTING=1" in text  # pre-existing lines preserved
    assert "CHIMERA_DEFAULT_MODEL=openrouter/x" in text
    assert not list(tmp_path.glob(".env.tmp"))  # atomic temp cleaned up


def test_patch_config_updates_process_env_live(monkeypatch: Any, tmp_path: Any) -> None:
    # A key set through the wizard must be usable THIS session (no restart): patch_config also writes
    # os.environ, so the running gateway / get_settings() sees it immediately, not only the .env file.
    from chimera.api.config_api import patch_config

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    patch_config({"OPENROUTER_API_KEY": "sk-live-now"}, env_path=tmp_path / ".env")
    import os

    assert os.environ["OPENROUTER_API_KEY"] == "sk-live-now"


def test_config_endpoint_shape(tmp_path: Any) -> None:
    cfg = _client(tmp_path).get("/api/config").json()
    assert {"models", "memory", "cache", "sandbox", "server", "providers"} <= set(cfg)
    # no provider entry ever carries a raw key field
    assert all(set(p) == {"env", "label", "set", "hint"} for p in cfg["providers"])


def test_cron_list_enable_disable_delete(monkeypatch: Any, tmp_path: Any) -> None:
    # features.py reads get_settings().home, so point HOME at tmp_path and clear the cache; the client
    # then shares that settings instance.
    from chimera.config import Settings, get_settings
    from chimera.scheduler import CronJob, CronStore

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    get_settings.cache_clear()
    store = CronStore(tmp_path / "scheduler" / "jobs.json")
    store.add(CronJob(id="j1", name="daily", trigger="cron", schedule="0 9 * * *", action="brief"))

    from fastapi.testclient import TestClient

    from chimera.api import build_api_app

    client = TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=Settings()))
    jobs = client.get("/api/cron").json()
    assert [j["id"] for j in jobs] == ["j1"] and jobs[0]["action"] == "brief"

    assert client.post("/api/cron/j1/disable").json()["enabled"] is False
    assert client.post("/api/cron/j1/enable").json()["enabled"] is True
    assert client.post("/api/cron/nope/enable").status_code == 404
    assert client.delete("/api/cron/j1").json() == {"deleted": True}
    assert client.get("/api/cron").json() == []
    get_settings.cache_clear()


def _feature_client(monkeypatch: Any, tmp_path: Any) -> TestClient:
    from chimera.config import Settings, get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    get_settings.cache_clear()
    from chimera.api import build_api_app

    return TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=Settings()))


def test_memory_add_list_delete(monkeypatch: Any, tmp_path: Any) -> None:
    from chimera.config import get_settings

    client = _feature_client(monkeypatch, tmp_path)
    r = client.post("/api/memory", json={"content": "Bruno prefers HSL palettes", "kind": "semantic"})
    assert r.json()["status"] in ("ADD", "UPDATE")
    item_id = r.json()["item"]["id"]
    listed = client.get("/api/memory").json()
    assert any(m["content"] == "Bruno prefers HSL palettes" for m in listed)
    assert client.post("/api/memory", json={"content": "x", "kind": "bogus"}).status_code == 400
    assert client.delete(f"/api/memory/{item_id}").json() == {"deleted": True}
    get_settings.cache_clear()


def test_skills_list_and_approve(monkeypatch: Any, tmp_path: Any) -> None:
    from chimera.config import get_settings
    from chimera.evolution import SkillStore
    from chimera.evolution.learned_skill import LearnedSkill

    store = SkillStore(tmp_path / "skills.json")
    store.add(LearnedSkill(name="reread", description="reread trick premises", do="x", check="y", status="pending"))
    client = _feature_client(monkeypatch, tmp_path)
    data = client.get("/api/skills").json()
    assert any(s["name"] == "reread" for s in data["stats"])
    assert client.post("/api/skills/reread/approve").json() == {"approved": True}
    assert client.post("/api/skills/nope/approve").status_code == 404
    get_settings.cache_clear()


def test_create_cron_from_the_ui_schedules_an_enabled_job(monkeypatch: Any, tmp_path: Any) -> None:
    # The desktop app can now create a schedule (the CLI's `chimera cron add`, over HTTP). A
    # human-created job is enabled immediately, so it will fire — unlike an agent-proposed one.
    from chimera.config import get_settings

    client = _feature_client(monkeypatch, tmp_path)
    assert client.get("/api/cron").json() == []  # empty to start

    created = client.post(
        "/api/cron",
        json={"name": "morning brief", "schedule": "0 7 * * *", "action": "summarise my day"},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["name"] == "morning brief"
    assert body["enabled"] is True  # human-created ⇒ enabled, so it actually runs
    assert body["created_by"] == "human"
    assert body["next_run"] is not None  # scheduled forward on the clock

    listed = client.get("/api/cron").json()
    assert [j["id"] for j in listed] == [body["id"]]
    get_settings.cache_clear()


def test_create_cron_rejects_an_invalid_expression(monkeypatch: Any, tmp_path: Any) -> None:
    from chimera.config import get_settings

    client = _feature_client(monkeypatch, tmp_path)
    bad = client.post("/api/cron", json={"name": "x", "schedule": "not a cron", "action": "y"})
    assert bad.status_code == 400  # a client error, not a 500
    assert client.get("/api/cron").json() == []  # nothing was created
    get_settings.cache_clear()


def test_patch_config_allows_the_proactive_toggles(monkeypatch: Any, tmp_path: Any) -> None:
    # REGRESSION: the "Remember from chat" and in-app cron toggles PATCH these keys — they must be in
    # the allowlist or the toggle silently 400s ("Couldn't save") even though the setting exists.
    from chimera.api.config_api import patch_config

    # patch_config also writes to the live os.environ (so a change takes effect without a restart).
    # Own these keys via monkeypatch first so that side effect is reverted at teardown and cannot
    # leak into another test (e.g. the app-cron default test, which reads CHIMERA_APP_CRON).
    monkeypatch.setenv("CHIMERA_CHAT_MEMORY", "")
    monkeypatch.setenv("CHIMERA_APP_CRON", "")
    env = tmp_path / ".env"
    result = patch_config(
        {"CHIMERA_CHAT_MEMORY": "true", "CHIMERA_APP_CRON": "false"}, env_path=env
    )
    assert set(result["updated"]) == {"CHIMERA_CHAT_MEMORY", "CHIMERA_APP_CRON"}
    written = env.read_text(encoding="utf-8")
    assert "CHIMERA_CHAT_MEMORY=true" in written
    assert "CHIMERA_APP_CRON=false" in written
