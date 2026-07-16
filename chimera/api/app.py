"""The desktop app's HTTP+SSE API (FastAPI).

This is the richer, GUI-facing surface that the React frontend in ``apps/desktop`` consumes. It is an
**opt-in** layer (``pip install chimera-agent[desktop]``) — the core CLI and the stdlib messaging
gateway don't need it. It reuses the real agent stack end to end: a session ``factory`` (the same one
``chimera serve`` builds) → :class:`~chimera.interface.ChatSession` → ``send_verbose`` for the live
token/tool/cost/memory signals, with :class:`~chimera.api.sessions.SessionManager` persisting each
conversation so the app has a durable session list.

The flagship endpoint is ``POST /api/chat/stream``: it runs the (blocking) agent turn on a worker
thread and bridges its ``on_token``/``on_tool`` callbacks onto an asyncio queue drained as
Server-Sent Events (``token`` / ``tool`` / ``done``), then persists the session. Under fusion the
backend can't stream tokens, so no ``token`` events fire and the answer arrives in the ``done`` event
— honest, no fake cursor.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from chimera.api.benchmarks_api import benchmark_report
from chimera.api.governance import read_audit, run_injection_suite
from chimera.api.maturity_api import maturity_report
from chimera.api.runs import load_runs
from chimera.api.schemas import (
    AgentsBatchOut,
    BenchmarksOut,
    CancelOut,
    ConfigOut,
    ConfigTestOut,
    DeletedOut,
    DoctorOut,
    FsFileOut,
    FsFileWrittenOut,
    FsTreeOut,
    GitCommitOut,
    GitDiffOut,
    GitRevertOut,
    GitStatusOut,
    GovernanceAuditOut,
    HealthOut,
    InjectionReportOut,
    MaturityOut,
    McpAddRequest,
    McpServersOut,
    McpTestOut,
    NewSessionOut,
    PlanOut,
    RunReceiptOut,
    ScreenshotOut,
    SessionDetailOut,
    SessionMetaOut,
    ToolsOut,
    UpdatedOut,
    UsageSummaryOut,
)
from chimera.api.sessions import SessionManager, SessionStore
from chimera.api.usage import UsageRecord, append_usage, load_usage, summarize_usage
from chimera.config import Settings, get_settings
from chimera.core.agent import ToolActivity
from chimera.core.events import AgentEvent, EventSink
from chimera.interface import ChatSession
from chimera.providers.gateway import SupportsComplete
from chimera.telemetry import get_logger

if TYPE_CHECKING:
    from chimera.core.autonomous import AutonomousAgent, AutonomousResult
    from chimera.orchestration import IsolatedBatch

_log = get_logger("api.app")

# Live single-run cancel registry: run_id -> the threading.Event whose set() the run's cooperative
# stop check polls. A run inserts its id on start and pops it on cleanup, so only in-flight runs are
# cancellable; POST /api/runs/{id}/cancel sets the event. In-process only (a single-user local app).
_run_cancels: dict[str, threading.Event] = {}


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    stream: bool = True
    fuse: bool = False
    """Route THIS turn through the fusion engine (panel → judge → synthesizer), tool-free — so the
    Fusion screen can show how the answer was composed. Off = the session's normal backend."""


class ConfigTestRequest(BaseModel):
    model: str | None = None
    """The model slug to test-call. None = the configured default model."""


class FsFileWriteRequest(BaseModel):
    workspace: str | None = None
    """The workspace root the write is scoped to. None = the app's launch workspace."""
    path: str
    content: str


class ExecRequest(BaseModel):
    workspace: str | None = None
    """The workspace root the command runs in. None = the app's launch workspace."""
    command: str
    cwd: str = ""
    """Working directory, relative to the workspace (default: workspace root). Does NOT persist —
    each command is a fresh subprocess."""
    timeout: float = 60


class GitCommitRequest(BaseModel):
    workspace: str | None = None
    """The workspace (repo) the commit is scoped to. None = the app's launch workspace."""
    message: str
    paths: list[str]
    """The EXPLICIT paths to stage + commit (never `add -A`)."""


class GitRevertRequest(BaseModel):
    workspace: str | None = None
    """The workspace (repo) the revert is scoped to. None = the app's launch workspace."""
    paths: list[str]
    """The run's changed paths to discard (git-backed revert, scoped to these only)."""


class PlanRequest(BaseModel):
    task: str
    workspace: str | None = None
    """The workspace root the plan is framed for. None = the app's launch workspace. (The planner
    itself reads no files — it is a pure model call — but the field mirrors the run request's shape.)"""


class ScreenshotRequest(BaseModel):
    url: str
    """The URL to capture — the browser navigates here and saves a full-page PNG. It is an HONEST
    capture of whatever the URL renders (the agent did not autonomously verify anything)."""
    workspace: str | None = None
    """Present for shape-parity with the other requests; the capture itself is workspace-independent
    (the artifact is stored under the app's home, not the workspace)."""


class RunRequest(BaseModel):
    task: str
    verify: str | None = None
    """A shell command that judges the run (exit 0 == success); runs in the workspace. None = no
    executable verifier (the Manager's approval is then the gate)."""
    workspace: str | None = None
    """The workspace root to run in. None = the workspace the desktop app was launched with."""
    max_attempts: int = 3
    plan: str | None = None
    """An approved/edited plan (the raw text from the plan preview). When set, the run uses THIS plan
    verbatim instead of re-planning — the worker follows the exact steps the human reviewed. None =
    the run plans for itself as before."""
    model: str | None = None
    """The model slug the worker runs on. None = the configured default model (unchanged behaviour)."""
    fuse: bool = False
    """Route the worker through the fusion engine (panel → judge → synthesizer), with fusion on the
    retry/plan path — the same wiring as ``chimera solve --fuse``. Off = single-model (default)."""
    cascade: bool = False
    """Route the worker through the FrugalGPT cascade (weak → gate → mid → gate → fusion), the same
    wiring as ``chimera solve --cascade``. Takes precedence over ``fuse``. Off = single-model."""


# A builder for the per-run autonomous agent, injectable so the endpoint is testable without a real
# LLM (a test passes a factory that returns a stubbed-worker agent — see tests/test_api.py). The
# trailing ``should_stop`` is the cooperative-cancel probe (``None`` = no cancel wired, unchanged path).
SolveAgentFactory = Callable[
    [RunRequest, Path, EventSink, "Settings", "Callable[[], bool] | None"], "AutonomousAgent"
]


class AgentTaskIn(BaseModel):
    task: str
    verify: str | None = None
    """A shell command that judges THIS task (exit 0 == success); runs inside the task's isolated
    worktree. None = no executable verifier for this task (the Manager's approval is then its gate)."""


class AgentsRequest(BaseModel):
    """A batch of coding tasks for the Agent Manager: each runs concurrently in its OWN git worktree.

    Mirrors ``chimera solve-batch``. Isolation is REAL only in a git repo — outside one the tasks run
    in-place against ``workspace`` with no isolation (so concurrent edits can collide and conflicts
    can't be detected); the response's ``is_repo`` flag says which happened, honestly."""

    tasks: list[AgentTaskIn]
    workspace: str | None = None
    """The workspace root (ideally a git repo, to isolate). None = the app's launch workspace."""
    max_workers: int = 4
    """Max concurrent isolated workers (clamped 1..8)."""
    model: str | None = None
    """The model slug every task's worker runs on. None = the configured default model."""
    fuse: bool = False
    """Route each worker through the fusion engine (same wiring as ``chimera solve --fuse``)."""
    cascade: bool = False
    """Route each worker through the FrugalGPT cascade (same wiring as ``chimera solve --cascade``).
    Takes precedence over ``fuse``."""


_MAX_AGENT_TASKS = 8

# The honest degradation message when the browser runtime isn't available — the UI surfaces this
# verbatim; no placeholder image is ever fabricated.
_BROWSER_MISSING_HINT = "Browser not installed — run: playwright install chromium"

# Artifact ids are uuid4 hex (32 hex chars). This strict allowlist (hex only — no '.', '/', '\\')
# is the primary guard on GET /api/artifacts/{id}: a traversal id can't match, so the endpoint can
# never be coaxed into an arbitrary-file read.
_ARTIFACT_ID_RE = re.compile(r"^[0-9a-f]{8,64}$")


def _capture_screenshot(url: str, path: Path) -> str | None:
    """Drive the real browser to ``url`` and save a full-page PNG to ``path``.

    Returns ``None`` on success, or a short, honest error message on failure — NEVER raises and
    never fabricates an image. Honest degradation: if Playwright is absent, or the Chromium binary
    can't be provisioned (the capture fails with a "playwright install" hint), the caller-facing
    message is the install hint. Kept module-level so a test can monkeypatch it (offline, no Chromium).
    """
    import importlib.util

    if importlib.util.find_spec("playwright") is None:
        return _BROWSER_MISSING_HINT
    from chimera.config import get_settings
    from chimera.tools.browser import BrowserTool

    tool = BrowserTool(headless=get_settings().browser_headless)
    try:
        # capture_local (not the run() screenshot action) — a user-initiated capture of the URL they
        # typed, so it may hit their own localhost app. The agent-facing screenshot action keeps the
        # full SSRF guard; this Python-only path allows private hosts but still enforces http(s).
        result = tool.capture_local(url, str(path))
    finally:
        tool.close()
    if not result.startswith("error:"):
        return None  # a fenced "saved screenshot to …" confirmation — success
    # A failure. A missing Chromium binary (or a broken Playwright install) maps to the friendly
    # install hint; any other failure (e.g. the navigation was blocked/failed) is surfaced honestly.
    if "playwright install" in result.lower():
        return _BROWSER_MISSING_HINT
    return "the screenshot could not be captured (the page did not load)"


def _require_token() -> Callable[[Request], None]:
    """A dependency that enforces the bearer token on protected endpoints when one is configured.

    When ``CHIMERA_SERVER_TOKEN`` is unset (the localhost default) it is a no-op, matching the stdlib
    gateway's opt-in auth. When set, the constant-time check mirrors ``server/http.py``'s ``_bearer_ok``.
    Reads the token from ``get_settings()`` on each call — NOT a build-time snapshot — so a token set at
    runtime (via ``PATCH /api/config``, which clears the settings cache) takes effect immediately.
    """
    import hmac

    def check(request: Request) -> None:
        token = get_settings().server_token
        if not token:
            return
        header = request.headers.get("authorization", "")
        expected = f"Bearer {token}"
        if not hmac.compare_digest(header, expected):
            raise HTTPException(status_code=401, detail="unauthorized")

    return check


def build_api_app(
    factory: Callable[[], ChatSession],
    *,
    settings: Settings | None = None,
    static_dir: Path | None = None,
    fuse_backend: SupportsComplete | None = None,
    workspace: Path | None = None,
    solve_agent_factory: SolveAgentFactory | None = None,
) -> FastAPI:
    """Build the desktop API app over a session ``factory`` (the real agent stack).

    ``static_dir`` (the built SPA, ``apps/desktop/dist``) is served same-origin at ``/`` with SPA
    fallback, so the frontend needs no CORS. ``settings`` defaults to the process settings.

    ``fuse_backend`` is an optional fusion engine used only for turns the client marks ``fuse=True``:
    that turn swaps the session agent's backend for it (under the per-session lock), so the answer is
    composed by panel → judge → synthesizer and the Fusion screen can show the real trace.

    ``workspace`` is where the ``POST /api/runs`` trigger runs a task when the request names none — it
    defaults to the process cwd (the ``chimera app --workspace`` value is threaded in from the CLI).
    ``solve_agent_factory`` builds the per-run autonomous agent; it defaults to the real LLM-backed
    builder and is injectable so the run endpoint is testable without a provider (see tests).
    """
    settings = settings or get_settings()
    workspace = (workspace or Path.cwd()).expanduser().resolve()
    solve_factory = solve_agent_factory or _build_solve_agent
    store = SessionStore(settings.home / "sessions")
    manager = SessionManager(factory, store)
    guard = Depends(_require_token())

    app = FastAPI(title="Chimera Desktop API", version="1", docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.get("/api/health", response_model=HealthOut)
    def health() -> dict[str, Any]:
        return {"status": "ok", "sessions": len(store.list())}

    @app.get("/api/config", dependencies=[guard], response_model=ConfigOut)
    def read_config_endpoint() -> dict[str, Any]:
        from chimera.api.config_api import read_config

        return read_config(get_settings())  # fresh settings (a prior PATCH cleared the cache)

    @app.patch("/api/config", dependencies=[guard], response_model=UpdatedOut)
    def patch_config_endpoint(updates: dict[str, str]) -> dict[str, Any]:
        from chimera.api.config_api import patch_config

        try:
            return patch_config(updates)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/doctor", dependencies=[guard], response_model=DoctorOut)
    def doctor_endpoint() -> dict[str, Any]:
        from chimera.api.config_api import doctor

        return doctor(get_settings())

    @app.post("/api/config/test", dependencies=[guard], response_model=ConfigTestOut)
    def config_test_endpoint(req: ConfigTestRequest) -> dict[str, Any]:
        # The ONLY endpoint that makes a real model call: a minimal 1-token completion so the
        # onboarding wizard can honestly say "key works" — presence checks (doctor) never authenticate.
        # Failures come back as {ok:false, error} (short, secret-free), never a 500.
        from chimera.api.config_test import test_provider

        return test_provider(req.model)

    @app.get("/api/usage", dependencies=[guard], response_model=UsageSummaryOut)
    def usage_endpoint() -> dict[str, Any]:
        return summarize_usage(load_usage(settings.home / "usage.jsonl"))

    @app.get("/api/runs", dependencies=[guard], response_model=list[RunReceiptOut])
    def runs_endpoint() -> list[Any]:
        # Read-only: the last 100 run receipts, most recent first. Each was persisted by the
        # autonomous loop (CLI `solve` or the POST trigger below) via its ``run_log``.
        return list(reversed(load_runs(settings.home / "runs.jsonl")))[:100]

    @app.get("/api/tools", dependencies=[guard], response_model=ToolsOut)
    def tools_endpoint() -> dict[str, Any]:
        # The agent's registered tools, so the screen reflects exactly the desktop agent's registry
        # (native + key-gated tools that light up when a credential/dep is present). Cheap and
        # side-effect free: building the registry only instantiates tool objects + reads settings
        # (the browser's Chromium binary downloads on first USE, not registration).
        from chimera.api.tools_api import list_tools
        from chimera.tools.builtin import default_registry

        tools = list_tools(default_registry(workspace))
        return {"tools": tools, "count": len(tools)}

    @app.get("/api/mcp", dependencies=[guard], response_model=McpServersOut)
    def mcp_list_endpoint() -> dict[str, Any]:
        # Read-only: the configured MCP servers from the store. NO connect — a listed server means
        # "configured", never "live". Env VALUES are never returned (only the key names).
        from chimera.api.mcp_api import list_servers

        return list_servers(settings.home)

    @app.post("/api/mcp", dependencies=[guard], response_model=McpServersOut)
    def mcp_add_endpoint(req: McpAddRequest) -> dict[str, Any]:
        # Cheap file write (replace-by-name or append). Persists to .chimera/mcp.json; still no connect.
        from chimera.api.mcp_api import add

        return add(settings.home, req.name, req.command, req.args, req.env)

    @app.delete("/api/mcp/{name}", dependencies=[guard], response_model=DeletedOut)
    def mcp_remove_endpoint(name: str) -> dict[str, bool]:
        from chimera.api.mcp_api import remove

        return {"deleted": remove(settings.home, name)}

    @app.post("/api/mcp/{name}/test", dependencies=[guard], response_model=McpTestOut)
    def mcp_test_endpoint(name: str) -> dict[str, Any]:
        # The ONLY connecting MCP endpoint: a real stdio connect + tool enumeration (short timeout).
        # It is the sole honest "connected" signal. Every failure is flattened to {ok:false, tools:[],
        # error} — never a stack trace, never an env value, never a 500. Runs the (blocking) connect on
        # a worker thread so the async event loop isn't blocked for the connect's duration.
        from chimera.api.mcp_api import test_server

        return test_server(settings.home, name)

    @app.get("/api/governance/injection", dependencies=[guard], response_model=InjectionReportOut)
    def governance_injection_endpoint() -> dict[str, Any]:
        # Cheap synthetic compute (no LLM, no side effects): the red-team corpus run with and without
        # the defenses, side by side. GET is fine — it reads nothing and writes nothing.
        return run_injection_suite()

    @app.get("/api/governance/audit", dependencies=[guard], response_model=GovernanceAuditOut)
    def governance_audit_endpoint() -> dict[str, Any]:
        # Read-only: the governance audit log (written by CLI guarded/tainted runs), newest-first.
        # The desktop chat doesn't write it, so an empty log is the honest, expected state.
        events = [_audit_event(e) for e in read_audit(settings.home / "audit.jsonl")]
        return {"events": events, "count": len(events), "populated": bool(events)}

    @app.get("/api/maturity", dependencies=[guard], response_model=MaturityOut)
    def maturity_endpoint() -> dict[str, Any]:
        # Cheap + keyless: the agent's coverage scorecard by surface — a pure filesystem glob of the
        # test suite (live), or the snapshot shipped with the release (pip installs have no tests dir).
        # No LLM, no network, no side effects. Coverage = evidence presence, not correctness.
        return maturity_report()

    @app.get("/api/benchmarks", dependencies=[guard], response_model=BenchmarksOut)
    def benchmarks_endpoint() -> dict[str, Any]:
        # Cheap + keyless: the agent's REAL recorded benchmark numbers from the shipped snapshot — the
        # promising weak-model lift (internal suite, n=6, not significant) AND the humbling external
        # Terminal-Bench number, each carrying its n/CI/significance. No LLM, no network. A missing
        # snapshot is an honest available:false, never a 500.
        return benchmark_report()

    @app.post("/api/plan", dependencies=[guard], response_model=PlanOut)
    async def plan_endpoint(req: PlanRequest) -> dict[str, Any]:
        # Plan-only preview: runs ONLY the planner (a single tool-free model call) — NO edits, NO
        # tools, NO agent loop, nothing touches the workspace. Returns the concrete steps so the user
        # can review/edit them before approving a real run. The (blocking) model call runs on a worker
        # thread so the event loop isn't blocked; a model hiccup degrades to empty steps + a note,
        # never a 500 (mirrors how /api/config/test and the MCP test endpoint flatten failures).
        def work() -> dict[str, Any]:
            from chimera.core import Planner
            from chimera.providers import LLMGateway

            try:
                plan = Planner(LLMGateway()).plan(req.task, context="")
                return {"steps": list(plan.steps), "text": plan.as_text(), "note": ""}
            except Exception as exc:  # noqa: BLE001 — a model hiccup is an honest empty plan, never a 500
                _log.warning("plan preview failed: %s", exc)
                return {"steps": [], "text": "", "note": "the planner call did not complete"}

        return await asyncio.get_running_loop().run_in_executor(None, work)

    @app.post("/api/verify/screenshot", dependencies=[guard], response_model=ScreenshotOut)
    async def verify_screenshot_endpoint(req: ScreenshotRequest) -> dict[str, Any]:
        # HONEST screenshot verification artifact: point the headless browser at ``url`` and save a
        # real full-page PNG under ``home/artifacts/<uuid>.png``, served back via GET /api/artifacts/{id}.
        # It is a capture of whatever the URL renders — NOT a claim that the agent verified anything.
        # Missing browser runtime OR a failed navigation degrades to {ok:false, error} (a clean 200,
        # never a 500) — no placeholder image. The (blocking) capture runs on a worker thread so the
        # event loop isn't blocked (mirrors how POST /api/plan runs the planner on a thread).
        artifacts = settings.home / "artifacts"

        def work() -> dict[str, Any]:
            artifacts.mkdir(parents=True, exist_ok=True)
            artifact_id = uuid.uuid4().hex
            path = artifacts / f"{artifact_id}.png"
            error = _capture_screenshot(req.url, path)
            if error is not None:
                return {"ok": False, "id": None, "error": error}
            if not path.is_file():  # defensive: no error but nothing on disk -> honest failure, not a lie
                return {"ok": False, "id": None, "error": _BROWSER_MISSING_HINT}
            return {"ok": True, "id": artifact_id, "error": None}

        return await asyncio.get_running_loop().run_in_executor(None, work)

    @app.get("/api/artifacts/{artifact_id}")
    def get_artifact(artifact_id: str) -> FileResponse:
        # Serve a stored screenshot PNG. Deliberately UNGUARDED so a same-origin <img src> can load it
        # (a browser <img> can't send the bearer header); safety rests on the localhost bind, the
        # unguessable uuid id, and the strict id allowlist below. SECURITY: the id must be hex-only
        # (^[0-9a-f]{8,64}$ — no '.', '/', '\\'), so a traversal id can't match; then, as defense in
        # depth, the resolved path must live inside the artifacts dir. Anything else is a 404 — this
        # is NOT an arbitrary-file read.
        if not _ARTIFACT_ID_RE.match(artifact_id):
            raise HTTPException(status_code=404, detail="not found")
        artifacts = (settings.home / "artifacts").resolve()
        path = (artifacts / f"{artifact_id}.png").resolve()
        if artifacts not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(path, media_type="image/png")

    @app.post("/api/runs", dependencies=[guard])
    async def run_stream(req: RunRequest) -> EventSourceResponse:
        # In-app trigger for an autonomous run (`chimera solve` semantics), streamed live as SSE.
        # SAFETY POSTURE: this executes file-writing and (if given) a user-supplied shell verify
        # command inside ``ws`` — the same capability the chat endpoint's file/shell tools already
        # have, and the same as running `chimera solve` in a terminal. It stays behind the bearer
        # guard + the localhost bind, and never runs outside ``ws``. It is the PLAIN solve core
        # (plan → run → verify-or-revert → receipt); the advanced CLI seams are intentionally omitted.
        ws = Path(req.workspace).expanduser().resolve() if req.workspace else workspace
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()
        # Cooperative-cancel plumbing: a per-run id + stop Event. The frontend learns the id from the
        # first `run` frame and hits POST /api/runs/{id}/cancel to set the Event; the run's stop check
        # (event.is_set) then halts the loop before its NEXT attempt — an in-flight model call is never
        # interrupted (it's a blocking step). Registered here, popped in the work() finally so the map
        # never leaks past a finished run.
        run_id = uuid.uuid4().hex
        cancel = threading.Event()
        _run_cancels[run_id] = cancel

        def emit(event: str, payload: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (event, payload))

        def on_event(event: AgentEvent) -> None:
            emit("event", _event_dict(event))

        # Tell the client its run_id immediately (its own SSE frame), so the Stop control can target
        # this run from the first moment — before any attempt has run.
        emit("run", {"run_id": run_id})

        def work() -> None:
            try:
                auto = solve_factory(req, ws, on_event, settings, cancel.is_set)
                # The receipt persists itself via the agent's run_log at run() — no extra write here.
                result = auto.run(req.task)
                emit(
                    "done",
                    {
                        "success": result.success,
                        "answer": (result.answer or "")[:2000],
                        "attempts": len(result.attempts),
                        # Honest terminal reason: "cancelled" when a cooperative stop ended it, else "".
                        "stopped_reason": getattr(result, "stopped_reason", ""),
                    },
                )
            except Exception as exc:  # noqa: BLE001 — surfaced to the client as an error event
                _log.warning("run failed: %s", exc)
                emit("error", {"message": "the run failed"})
            finally:
                _run_cancels.pop(run_id, None)  # done (or crashed): the run is no longer cancellable
                loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel: end of stream

        # Each run is independent (its own agent + workspace snapshot), so no per-session lock is
        # needed. A client disconnecting mid-run leaves the worker to finish (an in-flight LLM call
        # can't be interrupted; cancel only halts BETWEEN attempts); the queue is drained-or-dropped,
        # so memory stays bounded per run.
        threading.Thread(target=work, daemon=True).start()

        async def events() -> AsyncIterator[dict[str, str]]:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, payload = item
                yield {"event": event, "data": json.dumps(payload)}

        return EventSourceResponse(events())

    @app.post("/api/runs/{run_id}/cancel", dependencies=[guard], response_model=CancelOut)
    def cancel_run(run_id: str) -> dict[str, Any]:
        # Cooperative cancel of a single in-flight run. Sets the run's stop Event, which its loop polls
        # BETWEEN attempts (never mid model-call). A finished/unknown id is a no-op {ok: false} with a
        # 200 — not a 404 — since a run that already ended is exactly the state a stale Stop click hits.
        event = _run_cancels.get(run_id)
        if event is None:
            return {"ok": False}
        event.set()
        return {"ok": True}

    @app.get("/api/agents/schema", dependencies=[guard], response_model=AgentsBatchOut)
    def agents_schema_endpoint() -> dict[str, Any]:
        # An SSE stream can't declare a response_model, so the ``batch_done`` payload shape is surfaced
        # to OpenAPI (and the generated TS types) HERE — exactly as ``RunReceiptOut`` reaches the schema
        # via GET /api/runs. Returns the empty batch: an honest, side-effect-free shape sample, never
        # fabricated results. The real batch arrives over POST /api/agents's ``batch_done`` frame.
        return {"results": [], "conflicts": [], "merged": 0, "is_repo": False}

    @app.post("/api/agents", dependencies=[guard])
    async def agents_stream(req: AgentsRequest) -> EventSourceResponse:
        # In-app "Agent Manager": run SEVERAL coding tasks concurrently, EACH in its own git worktree
        # (``chimera solve-batch`` semantics), streamed live as SSE. Same safety posture as POST /api/runs
        # — every task writes files + runs its verify command inside ``ws``, behind the bearer guard and
        # the localhost bind. Isolation is REAL only in a git repo; outside one the tasks run in-place
        # (no isolation, conflicts undetectable) — surfaced honestly via the terminal frame's ``is_repo``.
        if not req.tasks:
            raise HTTPException(status_code=400, detail="no tasks")
        if len(req.tasks) > _MAX_AGENT_TASKS:
            raise HTTPException(status_code=400, detail=f"too many tasks (max {_MAX_AGENT_TASKS})")
        ws = Path(req.workspace).expanduser().resolve() if req.workspace else workspace
        max_workers = max(1, min(8, req.max_workers))
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()

        def emit(event: str, payload: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (event, payload))

        def make_run(index: int, spec: AgentTaskIn) -> Callable[[Path], AutonomousResult]:
            # Reuse the exact single-run builder (``solve_factory`` == _build_solve_agent by default, or
            # the injected test factory) per task, framing a RunRequest from the shared batch knobs. The
            # closure receives its ISOLATED worktree path ``ws_i`` and runs the agent there.
            sub = RunRequest(
                task=spec.task,
                verify=spec.verify,
                max_attempts=3,
                model=req.model,
                fuse=req.fuse,
                cascade=req.cascade,
            )

            def run(ws_i: Path) -> AutonomousResult:
                # Tag EVERY event with this task's index so the board can route it to the right card.
                # The task index is set LAST so it always wins: an ``attempt``/``result`` event's own
                # ``data["index"]`` (the attempt number) must NOT clobber the task tag — the attempt
                # number is still carried in the event's ``text`` (e.g. "attempt 2/3").
                def on_event(event: AgentEvent) -> None:
                    emit("event", {**_event_dict(event), "index": index})

                # Batch tasks aren't individually cancellable in this task (future work) — pass None.
                agent = solve_factory(sub, ws_i, on_event, settings, None)
                return agent.run(spec.task)

            return run

        def work() -> None:
            try:
                from chimera.orchestration import run_isolated

                emit(
                    "start",
                    {
                        "tasks": [t.task for t in req.tasks],
                        "workspace": str(ws),
                        "max_workers": max_workers,
                    },
                )
                units = [(f"task{i}", make_run(i, t)) for i, t in enumerate(req.tasks)]
                batch = run_isolated(
                    ws, units, succeeded=lambda r: r.success, max_workers=max_workers
                )
                emit("batch_done", _agents_batch_dict(req, ws, batch))
            except Exception as exc:  # noqa: BLE001 — surfaced to the client as an error event
                _log.warning("agents batch failed: %s", exc)
                emit("error", {"message": "the batch failed"})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel: end of stream

        # Each task is independent (its own agent + isolated worktree). A client disconnecting mid-batch
        # leaves the workers to finish (an LLM call can't be cleanly cancelled); the queue is drained-or-
        # dropped, so memory stays bounded per batch.
        threading.Thread(target=work, daemon=True).start()

        async def events() -> AsyncIterator[dict[str, str]]:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, payload = item
                yield {"event": event, "data": json.dumps(payload)}

        return EventSourceResponse(events())

    def _resolve_fs_workspace(ws_param: str | None) -> Path:
        # Which workspace the read-only fs endpoints are scoped to: the request's ``workspace`` query
        # when given (validated: must exist and be a dir, else 400 — mirrors how POST /api/runs
        # resolves its workspace), otherwise the app's launch workspace.
        if not ws_param:
            return workspace
        ws = Path(ws_param).expanduser().resolve()
        if not ws.is_dir():
            raise HTTPException(status_code=400, detail="workspace not found")
        return ws

    @app.get("/api/fs/tree", dependencies=[guard], response_model=FsTreeOut)
    def fs_tree_endpoint(path: str = "", workspace: str | None = None) -> dict[str, Any]:
        # Read-only, lazy one-level directory listing scoped to the workspace (path-guarded). A `..`
        # escape → 400; a huge dir is capped (flagged) so it never serializes unbounded.
        from chimera.api.fs_api import list_tree
        from chimera.tools.workspace import PathEscapesWorkspaceError

        ws = _resolve_fs_workspace(workspace)
        try:
            return list_tree(ws, path)
        except PathEscapesWorkspaceError as exc:
            raise HTTPException(status_code=400, detail="invalid path") from exc

    @app.get("/api/fs/file", dependencies=[guard], response_model=FsFileOut)
    def fs_file_endpoint(path: str, workspace: str | None = None) -> dict[str, Any]:
        # Read-only single-file read (path-guarded, capped at 20k). Binary/dir/missing → an honest
        # note, never a 500; only a path escape is a 400.
        from chimera.api.fs_api import read_file
        from chimera.tools.workspace import PathEscapesWorkspaceError

        ws = _resolve_fs_workspace(workspace)
        try:
            return read_file(ws, path)
        except PathEscapesWorkspaceError as exc:
            raise HTTPException(status_code=400, detail="invalid path") from exc

    @app.put("/api/fs/file", dependencies=[guard], response_model=FsFileWrittenOut)
    def fs_file_write_endpoint(req: FsFileWriteRequest) -> dict[str, Any]:
        # Editable-viewer save: atomic (temp+replace), newline-preserving, size-capped (1 MB). A path
        # escape or oversize content is a clean 400 — never a 500. Guarded + workspace-scoped, the same
        # capability the agent's WriteFileTool already has (localhost, bearer-guarded).
        from chimera.api.fs_api import write_file
        from chimera.tools.workspace import PathEscapesWorkspaceError

        ws = _resolve_fs_workspace(req.workspace)
        try:
            return write_file(ws, req.path, req.content)
        except PathEscapesWorkspaceError as exc:
            raise HTTPException(status_code=400, detail="invalid path") from exc
        except ValueError as exc:  # content over the byte cap
            raise HTTPException(status_code=400, detail="content too large") from exc

    @app.post("/api/fs/exec", dependencies=[guard])
    async def fs_exec_stream(req: ExecRequest) -> EventSourceResponse:
        # HONEST command-runner (NOT an interactive terminal): each call is a FRESH subprocess — cwd/env
        # do NOT persist between commands. Streams combined stdout+stderr line by line on the local
        # sandbox; honors CHIMERA_SANDBOX (non-local runs one-shot inside the sandbox, isolated). Same
        # side-effecting power as `run_shell` / a terminal in the workspace, gated the same way (bearer
        # + localhost bind). The child env scrubs provider secrets (reused from LocalSandbox).
        from chimera.api.exec_stream import resolve_exec_cwd, run_streamed

        ws = _resolve_fs_workspace(req.workspace)
        try:
            resolve_exec_cwd(ws, req.cwd)  # pre-validate so a cwd escape is a clean 400, not a stream
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid cwd") from exc
        timeout = max(1.0, min(float(req.timeout), 3600.0))  # clamp to the shell tool's 1..3600 cap
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        def push(frame: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, frame)

        def work() -> None:
            try:
                code = run_streamed(
                    req.command,
                    workspace=ws,
                    cwd=req.cwd,
                    timeout=timeout,
                    on_line=lambda text: push({"kind": "line", "text": text}),
                    settings=settings,
                )
                push({"kind": "exit", "code": code})
            except Exception as exc:  # noqa: BLE001 — surfaced to the client as a non-zero exit
                _log.warning("exec failed: %s", exc)
                push({"kind": "exit", "code": 1})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel: end of stream

        threading.Thread(target=work, daemon=True).start()

        async def events() -> AsyncIterator[dict[str, str]]:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield {"event": item["kind"], "data": json.dumps(item)}

        return EventSourceResponse(events())

    @app.get("/api/git/status", dependencies=[guard], response_model=GitStatusOut)
    def git_status_endpoint(workspace: str | None = None) -> dict[str, Any]:
        # Read-only porcelain status, gated on is_git_repo FIRST (a non-repo / git-missing folder is
        # the honest {is_repo: False} empty-state, never a 500). Workspace-scoped like the fs reads.
        from chimera.api.git_api import git_status

        return git_status(_resolve_fs_workspace(workspace))

    @app.get("/api/git/diff", dependencies=[guard], response_model=GitDiffOut)
    def git_diff_endpoint(
        workspace: str | None = None, path: str | None = None, staged: bool = False
    ) -> dict[str, Any]:
        # Read-only real unified diff (git diff [--cached] [-- path]); {is_repo: False} outside a repo.
        from chimera.api.git_api import git_diff

        return git_diff(_resolve_fs_workspace(workspace), path=path, staged=staged)

    @app.post("/api/git/commit", dependencies=[guard], response_model=GitCommitOut)
    def git_commit_endpoint(req: GitCommitRequest) -> dict[str, Any]:
        # Stage the EXPLICIT paths (never add -A) + commit. Same side-effecting power the fs/exec
        # endpoint already exposes, gated the same way (bearer + localhost). A non-repo / bad input
        # returns {ok: False, error} — never a 500.
        from chimera.api.git_api import git_commit

        return git_commit(_resolve_fs_workspace(req.workspace), req.message, req.paths)

    @app.post("/api/git/revert", dependencies=[guard], response_model=GitRevertOut)
    def git_revert_endpoint(req: GitRevertRequest) -> dict[str, Any]:
        # Discard a run's changes, SCOPED to the passed paths (git checkout + clean on those paths
        # only — never workspace-wide). Gated on is_git_repo; {ok: False} outside a repo.
        from chimera.api.git_api import git_revert_paths

        return git_revert_paths(_resolve_fs_workspace(req.workspace), req.paths)

    @app.get("/api/sessions", dependencies=[guard], response_model=list[SessionMetaOut])
    def list_sessions() -> list[dict[str, Any]]:
        return [
            {"id": m.id, "title": m.title, "turns": m.turns, "updated_at": m.updated_at}
            for m in manager.list()
        ]

    @app.get("/api/sessions/{session_id}", dependencies=[guard], response_model=SessionDetailOut)
    def get_session(session_id: str) -> dict[str, Any]:
        turns = store.load(session_id)
        if not turns and session_id not in [m.id for m in store.list()]:
            raise HTTPException(status_code=404, detail="session not found")
        return {
            "id": session_id,
            "turns": [{"user": t.user, "assistant": t.assistant} for t in turns],
        }

    @app.post("/api/sessions", dependencies=[guard], response_model=NewSessionOut)
    def new_session() -> dict[str, str]:
        return {"id": manager.new()}

    @app.delete("/api/sessions/{session_id}", dependencies=[guard], response_model=DeletedOut)
    def delete_session(session_id: str) -> dict[str, bool]:
        return {"deleted": manager.delete(session_id)}

    @app.post("/api/chat/stream", dependencies=[guard])
    async def chat_stream(req: ChatRequest) -> EventSourceResponse:
        session_id = req.session_id or manager.new()
        session = manager.get(session_id)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()

        def emit(event: str, payload: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (event, payload))

        def on_token(text: str) -> None:
            emit("token", {"text": text})

        def on_tool(activity: ToolActivity) -> None:
            emit("tool", {"name": activity.name, "ok": activity.ok})

        def work() -> None:
            try:
                # Serialize turns per session: a second concurrent turn on the same session waits here
                # rather than racing the non-thread-safe ChatSession (interleaved transcript / double save).
                with manager.lock_for(session_id):
                    # "Fuse this turn": swap the agent's backend for the fusion engine just for this
                    # call (safe under the per-session lock), then restore. Fusion ignores tools, so
                    # the turn is composed panel → judge → synthesizer and carries the trace.
                    agent: Any = getattr(session, "agent", None)
                    swap = bool(req.fuse) and fuse_backend is not None and hasattr(agent, "backend")
                    original = agent.backend if swap else None
                    if swap:
                        agent.backend = fuse_backend
                    try:
                        report = session.send_verbose(
                            req.message,
                            on_token=on_token if (req.stream and not swap) else None,
                            on_tool=on_tool,
                        )
                    finally:
                        if swap:
                            agent.backend = original
                    manager.persist(session_id)  # durable transcript now includes this turn
                    _append_usage(report, session_id, settings)
                emit("done", _report_dict(report, session_id))
            except Exception as exc:  # noqa: BLE001 — surfaced to the client as an error event
                _log.warning("chat turn failed: %s", exc)
                emit("error", {"message": "the agent turn failed"})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel: end of stream

        # KNOWN LIMITATION: if the client disconnects mid-turn, this worker still runs the agent turn
        # to completion (an LLM call can't be cleanly cancelled), so that turn's token cost can't be
        # reclaimed. The queue is drained-or-dropped either way, so memory stays bounded per turn.
        threading.Thread(target=work, daemon=True).start()

        async def events() -> AsyncIterator[dict[str, str]]:
            # Tell the client its session id up front (it may be freshly minted for this turn).
            yield {"event": "session", "data": json.dumps({"session_id": session_id})}
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, payload = item
                yield {"event": event, "data": json.dumps(payload)}

        return EventSourceResponse(events())

    from chimera.api.features import register_features

    register_features(app, guard)  # Memory / Skills / Cron / Tasks (Fase C)

    if static_dir is not None:
        _mount_spa(app, static_dir)

    return app


_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _index_html(index: Path, request: Request) -> Any:
    """Serve index.html, injecting the bearer token as a meta tag ONLY for a loopback client.

    When a token is configured, the guarded read endpoints need the same-origin browser to send it —
    but the browser can't hold a server secret. Injecting it into the page is safe only for a directly-
    connected local client (127.0.0.1): a remotely-exposed instance serves the page WITHOUT the token,
    so remote clients can't read it back. (Behind a reverse proxy the client host is the proxy; expose
    the UI remotely only behind your own auth layer — see docs.)
    """
    from html import escape

    from fastapi.responses import HTMLResponse

    html = index.read_text(encoding="utf-8")
    token = get_settings().server_token
    client = request.client.host if request.client else ""
    if token and client in _LOOPBACK:
        tag = f'<meta name="chimera-token" content="{escape(token, quote=True)}">'
        html = html.replace("</head>", tag + "</head>", 1)
    return HTMLResponse(html)


def _event_dict(event: AgentEvent) -> dict[str, Any]:
    """Serialize an ``AgentEvent`` into a small JSON dict for the SSE ``event`` frame.

    ``kind`` + ``text`` + the typed extras (attempt index, success flag, …). The ``final`` event's
    full ``answer`` is dropped here — it can be large and the ``done`` event already carries a
    truncated copy — so the live progress frames stay compact.
    """
    data = {k: v for k, v in event.data.items() if k != "answer"}
    return {"kind": event.kind, "text": event.text, **data}


def _agents_batch_dict(req: AgentsRequest, ws: Path, batch: IsolatedBatch[Any]) -> dict[str, Any]:
    """Build the terminal ``batch_done`` payload from a real ``IsolatedBatch`` — no fabrication.

    Per task, the ``AutonomousResult`` (``batch.results[i].value``) carries the true success/attempts/
    reverted flags, and ``build_receipt`` gives its real per-file diffs (we take the terminal attempt's,
    the final on-disk state for a success / what the last failed attempt attempted). ``changed_paths``
    is the worktree's authoritative merged set. ``conflicts``/``merged`` come straight off the batch;
    ``is_repo`` says whether isolation actually happened (it's a no-op outside a git repo).
    """
    from chimera.api.runs import build_receipt
    from chimera.core.worktree import is_git_repo

    ts = datetime.now(UTC).isoformat()
    results: list[dict[str, Any]] = []
    for index, (spec, isolated) in enumerate(zip(req.tasks, batch.results, strict=True)):
        auto = isolated.value  # AutonomousResult | None (None if the unit itself crashed)
        if auto is not None:
            receipt = build_receipt(auto, spec.task, spec.verify, ts)
            terminal = receipt.attempts[-1] if receipt.attempts else None
            diffs = [d.model_dump() for d in (terminal.diffs if terminal else [])]
            results.append(
                {
                    "index": index,
                    "task": (spec.task or "")[:2000],
                    "success": auto.success,
                    "attempts": len(auto.attempts),
                    "reverted": any(a.reverted for a in auto.attempts),
                    "changed_paths": isolated.changed_paths,
                    "diffs": diffs,
                }
            )
        else:
            # The unit crashed before returning a result (isolation caught it) — honest empty shape.
            results.append(
                {
                    "index": index,
                    "task": (spec.task or "")[:2000],
                    "success": False,
                    "attempts": 0,
                    "reverted": False,
                    "changed_paths": isolated.changed_paths,
                    "diffs": [],
                }
            )
    return {
        "results": results,
        "conflicts": batch.conflicts,
        "merged": batch.merged,
        "is_repo": is_git_repo(ws),
    }


def _audit_event(entry: dict[str, Any]) -> dict[str, Any]:
    """Flatten one arbitrary audit entry into the typed ``{seq, type, summary}`` shape for the UI.

    ``summary`` is a short ``key=value`` string built from whatever keys remain after ``seq``/``type``
    (e.g. ``action=… decision=… rule=… reason=…``) — only real captured keys, nothing invented.
    """
    seq = entry.get("seq", 0)
    etype = str(entry.get("type", ""))
    parts = [f"{k}={v}" for k, v in entry.items() if k not in ("seq", "type")]
    return {"seq": int(seq) if isinstance(seq, int) else 0, "type": etype, "summary": " ".join(parts)}


def _api_cascade_backend(gateway: SupportsComplete, settings: Settings) -> SupportsComplete:
    """The FrugalGPT cascade (weak → gate → mid → gate → fusion) over the tier ladder.

    A local mirror of the CLI's ``_cascade_backend`` (chimera/cli/main.py) so the desktop run uses the
    exact same routing without importing the whole CLI. Route decisions are appended to
    ``<home>/routes.jsonl`` (hash+tokens only, never prompt text), as in the CLI.
    """
    from chimera.fusion import FusionEngine
    from chimera.fusion.cascade import CascadeBackend, CascadeConfig

    ladder = settings.tier_ladder()
    config = CascadeConfig(
        weak=ladder.weak,
        mid=ladder.mid,
        entry=ladder.entry,
        log_path=settings.home / "routes.jsonl",
    )
    return CascadeBackend(gateway, FusionEngine(gateway), config)


def _build_solve_agent(
    req: RunRequest,
    ws: Path,
    on_event: EventSink,
    settings: Settings,
    should_stop: Callable[[], bool] | None = None,
) -> AutonomousAgent:
    """Build the PLAIN solve-core agent for the desktop trigger: plan → run → verify-or-revert → receipt.

    Deliberately minimal versus the CLI ``solve`` command — none of the advanced seams (taint
    ledger, evolution, durable threads, strong-verify, contracts, write-region). It is the honest
    core loop, the same capability as ``chimera solve TASK --verify CMD`` in a terminal. The worker's
    file/shell tools write inside ``ws`` and ``CommandVerifier`` runs the verify command there; that
    side-effecting power is the same the chat endpoint already exposes, gated the same way.

    Three per-run knobs mirror the CLI: ``req.model`` (the worker's model), ``req.fuse`` / ``req.cascade``
    (the backend routing, reproducing ``chimera solve --fuse`` / ``--cascade``), and ``req.plan`` (an
    approved/edited plan injected verbatim, skipping the planning call). With none set, the build is
    byte-identical to the plain single-model core loop.
    """
    from chimera.core import (
        Agent,
        AgentConfig,
        AutonomousConfig,
        Manager,
        Planner,
        WorkspaceGuard,
    )
    from chimera.core import (
        AutonomousAgent as _AutonomousAgent,
    )
    from chimera.core.planner import Plan
    from chimera.core.verify import CommandVerifier
    from chimera.providers import LLMGateway
    from chimera.tools import default_registry

    gateway = LLMGateway()
    # Backend selection, mirroring the CLI's solve wiring (main.py). Default = plain gateway on every
    # rung (single-model). --cascade takes precedence over --fuse (the cascade's top rung is fusion).
    backend: SupportsComplete = gateway
    planner_backend: SupportsComplete = gateway
    escalate_backend: SupportsComplete | None = None
    if req.cascade:
        from chimera.fusion import FusionEngine, RoutedBackend, RoutingPolicy

        backend = _api_cascade_backend(gateway, settings)
        escalate_backend = RoutedBackend(gateway, FusionEngine(gateway), RoutingPolicy(mode="always"))
    elif req.fuse:
        from chimera.fusion import FusionEngine, RoutedBackend, RoutingPolicy

        engine = FusionEngine(gateway)
        backend = RoutedBackend(gateway, engine)
        # Observed-difficulty escalation: a retry (the task already proved hard) fuses always.
        escalate_backend = RoutedBackend(gateway, engine, RoutingPolicy(mode="always"))
        # Planning is a deep, tool-free reasoning turn — route the plan through fusion under --fuse.
        planner_backend = engine

    registry = default_registry(ws)
    # insist_on_action: solve is task completion, so a described-but-unexecuted plan is pushed back
    # to actually run (mirrors the CLI worker config).
    cfg = AgentConfig(model=req.model, max_steps=6, insist_on_action=True)
    worker = Agent(backend, registry, cfg)
    escalate_worker = (
        Agent(escalate_backend, registry, cfg) if escalate_backend is not None else None
    )
    # Plan mode: an approved/edited plan is injected verbatim (parsed the same way the planner parses
    # its own output), so the run follows the human-reviewed steps and makes no planning call.
    provided_plan = Plan.from_text(req.plan) if req.plan else None
    return _AutonomousAgent(
        worker,
        should_stop=should_stop,
        escalate_worker=escalate_worker,
        planner=Planner(planner_backend, req.model),
        plan=provided_plan,
        manager=Manager(gateway, req.model),
        verifier=CommandVerifier(req.verify, ws) if req.verify else None,
        guard=WorkspaceGuard(ws),
        workspace=ws,
        spine_workspace=ws,
        on_event=on_event,
        # Persist the run receipt (step 3a) to the same append-only log GET /api/runs reads.
        run_log=settings.home / "runs.jsonl",
        config=AutonomousConfig(max_attempts=req.max_attempts),
    )


def _append_usage(report: Any, session_id: str, settings: Settings) -> None:
    """Append this turn's usage record to the usage log. Best-effort: usage logging must NEVER break
    a turn, so any failure (disk, serialization) is swallowed with a debug log."""
    try:
        route_kind = report.route_meta.get("kind") if report.route_meta else None
        record = UsageRecord(
            ts=datetime.now(UTC).isoformat(),
            session_id=session_id,
            model=report.model,
            prompt_tokens=report.prompt_tokens,
            completion_tokens=report.completion_tokens,
            cache_read_tokens=report.cache_read_tokens,
            cache_write_tokens=report.cache_write_tokens,
            usd=report.usd,
            tools=len(report.tool_names),
            memory_facts=report.memory_facts_used,
            route_kind=route_kind,
        )
        append_usage(settings.home / "usage.jsonl", record)
    except Exception as exc:  # noqa: BLE001 — usage logging is best-effort, never fatal to a turn
        _log.debug("usage logging skipped: %s", exc)


def _report_dict(report: Any, session_id: str) -> dict[str, Any]:
    """Serialize a TurnReport for the ``done`` event (all real signals; ``usd`` may be null)."""
    return {
        "session_id": session_id,
        "answer": report.answer,
        "prompt_tokens": report.prompt_tokens,
        "completion_tokens": report.completion_tokens,
        "cache_read_tokens": report.cache_read_tokens,
        "cache_write_tokens": report.cache_write_tokens,
        "usd": report.usd,
        "tool_names": list(report.tool_names),
        "memory_facts_used": report.memory_facts_used,
        "memory_layer": report.memory_layer,
        "steps": report.steps,
        "stopped_reason": report.stopped_reason,
        "route_meta": report.route_meta,
    }


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Serve the built SPA at ``/`` with a fallback so client-side routes resolve to index.html."""
    import mimetypes

    from fastapi.staticfiles import StaticFiles

    # Serve the PWA manifest with its proper type (mimetypes doesn't know .webmanifest by default);
    # the service worker (.js) already gets text/javascript, which the browser requires to register it.
    mimetypes.add_type("application/manifest+json", ".webmanifest")

    index = static_dir / "index.html"
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    def _root(request: Request) -> Any:
        return _index_html(index, request)

    @app.get("/{full_path:path}")
    def _spa_fallback(full_path: str, request: Request) -> Any:
        # An unknown /api/* path is a real 404, not the SPA — returning index.html there would mask
        # a wrong URL / stale generated client as a 200.
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        # Any other path that isn't a real asset falls back to the SPA entrypoint (client routing).
        candidate = (static_dir / full_path).resolve()
        if candidate.is_file() and static_dir.resolve() in candidate.parents:
            return FileResponse(candidate)
        return _index_html(index, request)
