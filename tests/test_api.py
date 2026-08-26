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

from chimera.config import Settings, get_settings  # noqa: E402
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
        def __init__(self, workspace: Any) -> None:
            self.workspace = workspace

        def run(self, task: str) -> AgentResult:
            (self.workspace / "done.py").write_text("# edited\n", encoding="utf-8")
            return AgentResult(answer="did it", steps=1, stopped_reason="final")

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))

    def solve_factory(
        req: RunRequest,
        ws: Any,
        on_event: EventSink,
        settings: Any,
        should_stop: Callable[[], bool] | None = None,
    ) -> AutonomousAgent:
        # No verifier / planner / manager, but the worker edits a file: the diff is the evidence that
        # carries the run to success on attempt 1. What is under test is the SSE marshalling and the
        # receipt, so the run has to succeed for an ordinary reason.
        return AutonomousAgent(
            _FakeWorker(ws),
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
    ws = tmp_path / "ws"
    ws.mkdir()
    # The workspace is explicit because the worker now WRITES. Omitting it falls back to the
    # directory the app was launched from — under pytest, the repository itself.
    resp = client.post(
        "/api/runs", json={"task": "make it so", "max_attempts": 2, "workspace": str(ws)}
    )
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


def test_post_agents_a_hung_task_does_not_hold_the_batch(tmp_path: Any) -> None:
    """A task that stops making progress must not pin the stream open — and must SAY that is why.

    This endpoint is the one surface with nobody at a terminal. Before the batch had a deadline, a
    worker that never returned held `run_isolated` forever: the client sat on an SSE stream that
    would never end, and the `finally` that pops `_agents_cancels[batch_id]` never ran, so the
    batch's cancel Events leaked for the life of the process. Both are asserted below, because
    "the response came back" alone would still pass if the registry leaked.

    The stub blocks for 15s and then reports SUCCESS rather than blocking forever. That is
    deliberate: a unit that truly never returns turns a regression here into a hung test suite, and
    a hang is the one failure that does not read as a failure. Reporting success on the far side
    means an unbounded batch comes back *wrong* (a pass, no error) instead of not coming back.
    """
    import threading as _threading

    from chimera.api import app as app_module
    from chimera.api import build_api_app
    from chimera.api.app import RunRequest
    from chimera.core.events import EventSink

    ws = tmp_path / "ws"
    _init_repo(ws)
    release = _threading.Event()

    class _HungAgent:
        def run(self, task: str) -> Any:
            from chimera.core.autonomous import Attempt, AutonomousResult

            release.wait(15.0)
            return AutonomousResult(
                answer="late",
                success=True,
                attempts=[
                    Attempt(
                        index=0, answer="late", approved=True, verified=True, reverted=False, success=True
                    )
                ],
            )

    def factory(
        _req: RunRequest,
        _ws: Any,
        _on_event: EventSink,
        _settings: Any,
        _should_stop: Callable[[], bool] | None = None,
    ) -> Any:
        return _HungAgent()

    # 1s via the INJECTED settings — an app built with its own Settings must run under those, which
    # is only true because the endpoint reads the deadline from live_settings() instead of leaving
    # run_isolated to fall back on the process-wide value.
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_BATCH_TIMEOUT=1.0)
    client = TestClient(
        build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings, solve_agent_factory=factory)
    )
    try:
        resp = client.post("/api/agents", json={"tasks": [{"task": "hangs"}], "workspace": str(ws)})
    finally:
        release.set()  # let the abandoned worker finish instead of parking a thread for 15s

    assert resp.status_code == 200
    events = _read_sse(resp.text)
    assert [e for e, _ in events][-1] == "batch_done"  # the stream ENDED, on the batch's own terms
    only = next(d for e, d in events if e == "batch_done")["results"][0]
    assert only["success"] is False
    # The reason is named. A timeout that arrives as a bare `success: false` is indistinguishable
    # from a task that ran and did not pass, which is the lie this field exists to stop.
    assert only["error"].startswith("timed out after"), only["error"]
    assert only["attempts"] == 0 and only["changed_paths"] == [] and only["diffs"] == []
    batch_id = next(d for e, d in events if e == "batch")["batch_id"]
    assert batch_id not in app_module._agents_cancels  # the finally ran: nothing leaked


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
        def __init__(self, workspace: Any) -> None:
            self.prompt = ""
            self.workspace = workspace

        def run(self, task: str) -> AgentResult:
            self.prompt = task
            # Writes, so this is a real solve and the run reaches success on its own merits. A
            # worker that only narrates no longer passes the gate, and the seam under test here is
            # the injected plan, not what counts as done.
            (self.workspace / "touched.py").write_text("# edited\n", encoding="utf-8")
            return AgentResult(answer="did it", steps=1, stopped_reason="final")

    ws = tmp_path / "ws"
    ws.mkdir()
    spy = _SpyPlanner()
    worker = _RecordingWorker(ws)
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


def _solve_agent(tmp_path: Any, **fields: Any) -> Any:
    """Build the real solve agent for a fresh workspace. No model is ever called — every assertion
    below is about how the agent was *assembled*, which is where the run's ceilings live."""
    from chimera.api.app import RunRequest, _build_solve_agent

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return _build_solve_agent(RunRequest(task="t", **fields), ws, lambda _e: None, settings)


def test_the_coding_seams_are_all_off_unless_asked(tmp_path: Any) -> None:
    """A request that names none of the new fields builds the loop it always built.

    The one deliberate change: the step ceiling is no longer a hard-coded 6 that nothing justified
    while ``AgentConfig`` documents 8 — a run through the API was quietly capped lower than the same
    run through ``chimera solve``. This pins the parity so it cannot drift back.
    """
    from chimera.core import AgentConfig

    agent = _solve_agent(tmp_path)
    assert agent.worker.config.max_steps == AgentConfig.max_steps
    assert agent.worker.config.context_budget is None  # compaction stays off unless asked for
    # And no dollar ceiling, which is the one seam that can END a run: a limiter that switched
    # itself on for callers who never asked for one is a regression shipped as a feature.
    assert agent.worker.config.max_usd is None
    assert agent.repo_map is False
    assert "explore_repository" not in agent.worker.tools.names()


def test_max_steps_is_clamped_at_both_ends(tmp_path: Any) -> None:
    """The ceiling is a UI field, so it is a number a client can get wrong in both directions."""
    from chimera.api.code_api import MAX_RUN_STEPS

    assert _solve_agent(tmp_path, max_steps=40).worker.config.max_steps == 40
    assert _solve_agent(tmp_path, max_steps=0).worker.config.max_steps == 1
    assert _solve_agent(tmp_path, max_steps=10_000).worker.config.max_steps == MAX_RUN_STEPS


def test_context_budget_reaches_the_worker(tmp_path: Any) -> None:
    """Raising the step ceiling without a budget raises the odds of dying on overflow instead of
    finishing, so the two fields have to be reachable together — not one without the other."""
    agent = _solve_agent(tmp_path, max_steps=40, context_budget=0.6)
    assert agent.worker.config.context_budget == 0.6


def test_the_spend_ceiling_reaches_the_worker(tmp_path: Any) -> None:
    """The loose wire this reconnects.

    ``AgentConfig.max_usd`` has stopped a loop before the call that would break the cap since it was
    written, and no HTTP route could set it — the cron dispatcher was its only caller in the whole
    codebase. So the one surface where a person watches money being spent could not name a number,
    and the mechanism read as absent rather than as unreachable.
    """
    assert _solve_agent(tmp_path, max_usd=0.25).worker.config.max_usd == 0.25


def test_a_batch_task_carries_the_ceiling_its_batch_was_given(tmp_path: Any) -> None:
    """A seam a batch accepts and drops is worse than one it refuses.

    The caller reads back a capped batch and gets an uncapped one — per task, concurrently, which is
    the shape that spends fastest. Every other seam already travels here (see
    ``test_a_batch_task_is_governed_exactly_like_a_single_run``); this is the one that costs money.
    """
    from chimera.api import build_api_app
    from chimera.api.app import RunRequest
    from chimera.core.events import EventSink

    ws = tmp_path / "plain"
    ws.mkdir()
    seen: list[float | None] = []

    def factory(
        req: RunRequest,
        task_ws: Any,
        on_event: EventSink,
        _settings: Any,
        _should_stop: Callable[[], bool] | None = None,
    ) -> Any:
        seen.append(req.max_usd)
        return _WritingAgent(task_ws, on_event, "x.txt", req.task)

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(
        build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings, solve_agent_factory=factory)
    )

    client.post(
        "/api/agents",
        json={"tasks": [{"task": "t"}], "workspace": str(ws), "max_usd": 0.5},
    )

    assert seen == [0.5]


def test_the_api_no_longer_seeds_run_state_by_hand(tmp_path: Any) -> None:
    """Being the only entry point that filled these fields is how they drifted: this one wrote
    `plan`, copied the steps into `tasks` (a field documented as carrying status) and put the raw
    task in `current_state` (documented as progress) — while a run that planned for itself wrote
    none of them and got the plan restored inside `task` instead, as part of the composed prompt.

    The solve loop now writes `task` and `plan` once per attempt, downstream of all four points
    where the plan is decided. Asserted there, against a run that actually runs — see
    `tests/test_autonomous.py`; an assembled-but-never-started agent cannot show what a compaction
    would restore. What this pins is that the endpoint no longer writes a second, staler copy.
    """
    agent = _solve_agent(tmp_path, plan="1. do it\n2. verify it")
    state = agent.worker.run_state
    assert (state.task, state.plan, state.tasks, state.current_state) == ("", "", [], "")
    assert agent.provided_plan is not None  # the approved plan still reaches the loop that seeds it
    assert agent.provided_plan.steps == ["do it", "verify it"]


def test_write_region_actually_refuses_a_write_outside_it(tmp_path: Any) -> None:
    """Asserted through the tool, not the field: a region the write tools do not consult is a
    setting that reads as a guarantee and is not one."""
    agent = _solve_agent(tmp_path, write_region=["src/**"])
    tools = agent.worker.tools
    assert tools.run("write_file", path="src/ok.py", content="x = 1\n").startswith("wrote")
    assert tools.run("write_file", path="secrets.env", content="TOKEN=1\n").startswith("error:")


def test_blank_write_region_globs_are_not_an_empty_region(tmp_path: Any) -> None:
    """An empty region forbids EVERY write. That is a thing to say on purpose, not to reach via a
    trailing comma in a text field."""
    agent = _solve_agent(tmp_path, write_region=["", "  "])
    assert agent.worker.tools.run("write_file", path="anywhere.py", content="x\n").startswith("wrote")


def test_allow_and_deny_lists_scope_the_session(tmp_path: Any) -> None:
    names = _solve_agent(tmp_path, allow_tools=["read_file", "write_file"]).worker.tools.names()
    assert set(names) == {"read_file", "write_file"}

    denied = _solve_agent(tmp_path, deny_tools=["run_shell"]).worker.tools.names()
    assert "run_shell" not in denied and "read_file" in denied

    # Deny wins over allow — the rule `restrict_registry` documents, pinned at the API boundary too.
    both = _solve_agent(tmp_path, allow_tools=["read_file"], deny_tools=["read_file"]).worker.tools
    assert both.names() == []


def test_explorer_is_registered_only_when_asked_and_inherits_the_allowlist(tmp_path: Any) -> None:
    """The explorer is added AFTER the allowlist on purpose (the CLI's order): the allowlist scopes
    the native tools the sub-agent will inherit, and the explorer itself is what the caller asked
    for — so an allowlist that omits it does not silently cancel the request."""
    agent = _solve_agent(tmp_path, explorer=True, allow_tools=["read_file"])
    assert set(agent.worker.tools.names()) == {"read_file", "explore_repository"}


def test_repo_map_flag_reaches_the_loop(tmp_path: Any) -> None:
    assert _solve_agent(tmp_path, repo_map=True).repo_map is True


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


def _fs_client(tmp_path: Any, ws: Any) -> Any:
    from chimera.api import build_api_app

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return TestClient(
        build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings, workspace=ws)
    )


def test_fs_image_serves_the_bytes_a_chart_tool_wrote(tmp_path: Any) -> None:
    """`render_chart` writes a PNG into the workspace and the viewer could only say "binary".

    This is the endpoint that makes our own tool's output reachable from our own app.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    png = b"\x89PNG\r\n\x1a\n" + b"payload"
    (ws / "chart.png").write_bytes(png)
    client = _fs_client(tmp_path, ws)

    resp = client.get("/api/fs/image", params={"path": "chart.png"})

    assert resp.status_code == 200
    assert resp.content == png  # the file's own bytes, not a re-encoding
    assert resp.headers["content-type"] == "image/png"


def test_fs_image_refuses_to_serve_html_from_this_origin(tmp_path: Any) -> None:
    """The refusal this endpoint is built around, asserted where the header is actually set.

    The app's page carries the bearer token in a `<meta>` tag, so an HTML document served from this
    same origin can fetch index.html, read the token, and then drive the whole API as the user. The
    status is what matters; the assertion on the body is there because a 200 with `text/html` would
    be the vulnerability and must not be reachable by any future response-model change.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "steal.html").write_text("<script>fetch('/')</script>", encoding="utf-8")
    (ws / "chart.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    client = _fs_client(tmp_path, ws)

    for name in ("steal.html", "chart.svg"):  # svg is script-capable under a top-level navigation
        resp = client.get("/api/fs/image", params={"path": name})
        assert resp.status_code == 415, name
        assert "text/html" not in resp.headers["content-type"], name


def test_fs_image_tells_the_browser_not_to_sniff_past_the_label(tmp_path: Any) -> None:
    """The allowlist picks the label; `nosniff` is what makes the browser honour it.

    A file named `.png` whose bytes are `<html>` is on the allowlist and IS served as `image/png` —
    that is fine and it is the point: without this header a browser may sniff the body, decide it is
    a document, and render it in this origin, which is the same hole reached by a longer road.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "evil.html.png").write_bytes(b"<html><script>fetch('/')</script></html>")
    client = _fs_client(tmp_path, ws)

    resp = client.get("/api/fs/image", params={"path": "evil.html.png"})

    assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in resp.headers["content-security-policy"]


def test_fs_image_guards_the_path_and_the_missing_file(tmp_path: Any) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "secret.png").write_bytes(b"\x89PNG")  # outside the workspace, on purpose
    client = _fs_client(tmp_path, ws)

    assert client.get("/api/fs/image", params={"path": "../secret.png"}).status_code == 400
    assert client.get("/api/fs/image", params={"path": "gone.png"}).status_code == 404


def test_git_init_endpoint_makes_a_repo_with_a_snapshot(tmp_path: Any) -> None:
    """The button that replaces "run `git init` in this folder" in an app that has no terminal."""
    import shutil

    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("print('x')\n", encoding="utf-8")
    client = _fs_client(tmp_path, ws)

    resp = client.post("/api/git/init", json={})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["commit"] and body["error"] is None
    # The panels that were empty before now have a repo to describe, which is the user-visible point.
    assert client.get("/api/git/status").json()["is_repo"] is True
    # And a second press is refused rather than committing over the first snapshot.
    assert client.post("/api/git/init", json={}).json()["error"] == "already a git repo"


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


def test_read_config_declares_when_a_saved_setting_starts_applying(tmp_path: Any) -> None:
    """The screen must not have to guess, and must not keep its own copy of the answer.

    Whether a saved value takes effect now, on the next conversation or on the next launch is a
    property of where it is READ. Publishing it from beside the allowlist is what lets the label stay
    true when a read moves — a list maintained in the frontend would go stale silently, which is the
    exact failure the label exists to prevent.
    """
    from chimera.api.config_api import _EDITABLE_SETTINGS, APPLIES_WHEN, read_config

    cfg = read_config(Settings(CHIMERA_HOME=str(tmp_path)))
    applies = cfg["applies"]

    # These start something at boot — a daemon thread, a set of MCP subprocesses — so re-reading the
    # value cannot undo it.
    assert applies["CHIMERA_APP_CRON"] == "next_launch"
    assert applies["CHIMERA_MCP_AUTOLOAD"] == "next_launch"
    # These are decided when a conversation is built, so an open one keeps what it started with.
    assert applies["CHIMERA_CASCADE"] == "next_conversation"
    assert applies["CHIMERA_GUARD_CHAT"] == "next_conversation"
    assert applies["CHIMERA_CHAT_MEMORY"] == "next_conversation"
    # Absence is the claim "this applies to the next call" — the default after the gateway and the
    # request handlers stopped holding a boot-time snapshot. Naming one of these would be a caveat
    # about a delay that no longer exists.
    assert "CHIMERA_DEFAULT_MODEL" not in applies
    assert "CHIMERA_CACHE" not in applies
    assert "CHIMERA_SANDBOX" not in applies
    # A delay declared for something nobody can change from the screen is dead text.
    assert set(APPLIES_WHEN) <= _EDITABLE_SETTINGS


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
    # no provider entry ever carries a raw key field. `name` is the provider's routing slug
    # ("openrouter"), sent so a client asking a provider-scoped question — the model list the wizard
    # shows — does not re-derive it from the env var name and drift from the server's rule.
    fields = {"env", "name", "label", "set", "hint", "llm", "model", "keys_url"}
    assert all(set(p) == fields for p in cfg["providers"])


def test_pool_endpoints_add_and_remove_without_ever_carrying_a_key_back(
    tmp_path: Any, monkeypatch: Any
) -> None:
    monkeypatch.setenv("CHIMERA_OPENROUTER_KEYS", "")
    monkeypatch.chdir(tmp_path)  # patch_config/_write_env_var resolve .env against the cwd
    get_settings.cache_clear()
    client = _client(tmp_path)

    assert client.post("/api/config/pool/openrouter", json={"key": "sk-or-first1111"}).json() == {
        "provider": "openrouter",
        "count": 1,
    }
    client.post("/api/config/pool/openrouter", json={"key": "sk-or-second2222"})

    pools = {p["provider"]: p for p in client.get("/api/config").json()["pools"]}
    assert pools["openrouter"]["keys"] == [
        {"index": 0, "hint": "…1111"},
        {"index": 1, "hint": "…2222"},
    ]

    assert client.delete("/api/config/pool/openrouter/0").json()["count"] == 1
    assert client.get("/api/config").json()
    remaining = {p["provider"]: p for p in client.get("/api/config").json()["pools"]}
    assert remaining["openrouter"]["keys"] == [{"index": 0, "hint": "…2222"}]


def test_pool_endpoint_400s_instead_of_writing_a_mask(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("CHIMERA_OPENROUTER_KEYS", "sk-or-real1111")
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    client = _client(tmp_path)

    assert client.post("/api/config/pool/openrouter", json={"key": "…1111"}).status_code == 400
    assert client.post("/api/config/pool/nope", json={"key": "x"}).status_code == 400
    assert client.delete("/api/config/pool/openrouter/9").status_code == 400
    # and the pool the client tried to overwrite is untouched
    pools = {p["provider"]: p for p in client.get("/api/config").json()["pools"]}
    assert pools["openrouter"]["keys"] == [{"index": 0, "hint": "…1111"}]


def test_the_settings_that_only_the_env_file_could_reach(tmp_path: Any) -> None:
    """Two values the interface used to hide, one of them under a switch that depends on it.

    `semantic` degrades to lexical recall on ANY embedder failure, without a word — so an embed
    model the user's key cannot serve made that toggle confirm a change it had not made. And the
    Ollama URL is not covered by `api_base`: that one is sent on every call, this one only points
    the Ollama provider, which is what someone running it on another machine needs.
    """
    from chimera.api.config_api import is_editable

    cfg = _client(tmp_path).get("/api/config").json()
    assert cfg["memory"]["embed_model"]
    assert cfg["models"]["ollama_base_url"]
    assert is_editable("CHIMERA_EMBED_MODEL") and is_editable("CHIMERA_OLLAMA_BASE_URL")


def test_config_says_which_credentials_serve_models(tmp_path: Any) -> None:
    """The first-run wizard filters on this, and getting it wrong is a dead end that confirms.

    A search or speech key does not make ``has_any_key`` true, so a wizard that offered one would
    take the key, report it saved, and then stay on screen forever waiting for a provider.
    """
    providers = {p["env"]: p for p in _client(tmp_path).get("/api/config").json()["providers"]}

    assert providers["ANTHROPIC_API_KEY"]["llm"] is True
    assert providers["ANTHROPIC_API_KEY"]["model"]  # a slug to start on, not just a key slot
    assert providers["ANTHROPIC_API_KEY"]["keys_url"].startswith("https://")
    for env in ("TAVILY_API_KEY", "ELEVENLABS_API_KEY", "STABILITY_API_KEY"):
        assert providers[env]["llm"] is False
        assert providers[env]["model"] == ""


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

    # The folder a job works in, carried from the screen and read back. Without it every schedule
    # ran at the process root, which on a packaged build is the app's install directory.
    criado = client.post(
        "/api/cron",
        json={"name": "resumo", "schedule": "0 7 * * *", "action": "liste", "workspace": "/proj/a"},
    ).json()
    assert criado["workspace"] == "/proj/a"
    assert client.get("/api/cron").json()[0]["workspace"] == "/proj/a"

    # And a client that sends none keeps the previous behaviour rather than being refused.
    sem = client.post(
        "/api/cron", json={"name": "outro", "schedule": "0 8 * * *", "action": "liste"}
    ).json()
    assert sem["workspace"] is None
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


class _FakeMessaging:
    """A stand-in MessagingManager for the endpoint tests: records start/stop, no threads."""

    def __init__(self) -> None:
        self.state: dict[str, dict[str, Any]] = {
            "discord": {"configured": True, "running": False, "error": None}
        }

    def status(self) -> dict[str, dict[str, Any]]:
        return self.state

    def start(self, platform: str) -> None:
        if platform not in self.state:
            raise ValueError(f"unknown messaging platform: {platform!r}")
        if not self.state[platform]["configured"]:
            raise ValueError(f"{platform} is not configured (no token set)")
        self.state[platform]["running"] = True

    def stop(self, platform: str) -> None:
        if platform in self.state:
            self.state[platform]["running"] = False


def _messaging_client(tmp_path: Any, manager: Any) -> TestClient:
    from chimera.api import build_api_app

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return TestClient(
        build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings, messaging_manager=manager)
    )


def test_messaging_status_start_stop_roundtrip(tmp_path: Any) -> None:
    client = _messaging_client(tmp_path, _FakeMessaging())

    assert client.get("/api/messaging").json()["discord"]["running"] is False
    assert client.post("/api/messaging/discord/start").json()["discord"]["running"] is True
    assert client.get("/api/messaging").json()["discord"]["running"] is True
    assert client.post("/api/messaging/discord/stop").json()["discord"]["running"] is False


def test_messaging_start_unconfigured_is_400(tmp_path: Any) -> None:
    mgr = _FakeMessaging()
    mgr.state["discord"]["configured"] = False
    client = _messaging_client(tmp_path, mgr)
    assert client.post("/api/messaging/discord/start").status_code == 400


def test_messaging_unavailable_without_a_manager(tmp_path: Any) -> None:
    # An API-only build (no manager) reports nothing and refuses start with a 503, never a crash.
    client = _messaging_client(tmp_path, None)
    assert client.get("/api/messaging").json() == {}
    assert client.post("/api/messaging/discord/start").status_code == 503


def test_a_batch_task_is_governed_exactly_like_a_single_run(tmp_path: Any) -> None:
    """A batch used to carry no posture, no profile, and a hard-coded three attempts.

    So the same task run as one of five was quietly granted different tool permissions, a different
    reviewer, and a different attempt budget than the same task run alone. That was survivable while
    the user had to choose the Agents screen deliberately. It stops being survivable the moment the
    composer can route into a batch on its own, because the downgrade becomes invisible AND unchosen.
    """
    from chimera.api.app import AgentsRequest, RunRequest
    from chimera.api.posture import Posture

    req = AgentsRequest(
        tasks=[{"task": "a"}, {"task": "b"}],
        posture=Posture(reach="read_only", approval="always"),
        profile="max",
        max_attempts=5,
    )

    # The seams exist on the batch at all — the regression this guards is a field silently absent.
    assert req.posture is not None and req.profile == "max" and req.max_attempts == 5
    # And they are the SAME fields a single run declares, not a parallel set that can drift.
    assert {"posture", "profile", "roles", "write_region", "allow_tools", "deny_tools"} <= set(
        RunRequest.model_fields
    ) & set(AgentsRequest.model_fields)


def test_the_screenshot_endpoint_is_gone_along_with_its_private_host_exception(tmp_path: Any) -> None:
    """`POST /api/verify/screenshot` and `GET /api/artifacts/{id}` are deleted.

    They served a manual "verify in browser" panel that captured a URL the user typed. Despite the
    name and the `/api/verify/` path, it fed nothing — not `evidence`, not a receipt, not the cost
    panel — and the code said so itself: "NOT a claim that the agent verified anything". Removing the
    panel removed the endpoint's only caller, and the endpoint was the only caller of
    `BrowserTool.capture_local`, which deliberately ALLOWED private hosts. That was this codebase's
    single intentional SSRF exception, and it now has no reason to exist.

    The agent's own screenshot action is untouched and never had the exception.
    """
    client = _client(tmp_path)

    assert client.post("/api/verify/screenshot", json={"url": "http://localhost:5173"}).status_code == 404
    assert client.get("/api/artifacts/anything").status_code == 404


def test_a_fused_turn_says_it_could_not_use_tools(tmp_path: Any) -> None:
    """The failure this pins is the worst kind the app can produce, and it shipped for months.

    `FusionEngine.complete` drops the tool schemas it is handed (it logs at DEBUG and moves on), and
    a panel of models has nothing to call a tool with. So a fused turn finishes in ONE step having
    touched nothing, and answers from the prompt alone. Ask it to read a file and it describes a file
    it never opened — with the authority of three models agreeing.

    From outside, that turn was indistinguishable from a turn that legitimately needed no tool: both
    report zero tool calls. `fused` is the entire difference, and it is what lets the UI say which
    happened at the moment someone is reading the answer.
    """
    from chimera.api import build_api_app

    fuse_backend = object()

    class _ToollessWhenFused:
        def __init__(self) -> None:
            self.backend: Any = object()

        def run(self, task: str, *, on_token: Any = None, on_tool: Any = None) -> AgentResult:
            # Exactly what the real engine produces: one step, no tool call, a confident answer.
            return AgentResult(answer="the config looks fine", steps=1, stopped_reason="final")

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(
        build_api_app(
            lambda: ChatSession(_ToollessWhenFused()), settings=settings, fuse_backend=fuse_backend
        )
    )

    fused = next(
        d
        for e, d in _read_sse(
            client.post(
                "/api/chat/stream", json={"message": "read config.yaml and tell me what is wrong", "fuse": True}
            ).text
        )
        if e == "done"
    )
    assert fused["fused"] is True
    assert fused["tool_names"] == []  # the point: zero tools, and now the turn admits why

    plain = next(
        d
        for e, d in _read_sse(
            client.post("/api/chat/stream", json={"message": "hello"}).text
        )
        if e == "done"
    )
    # A turn that simply did not need a tool must NOT be marked — otherwise the warning means nothing.
    assert plain["fused"] is False
    assert plain["tool_names"] == []
