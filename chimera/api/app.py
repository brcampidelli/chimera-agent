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
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from chimera.api.benchmarks_api import benchmark_report
from chimera.api.code_api import (
    CodeSeams,
    assemble_registry,
    register_code_api,
    resolve_posture,
    resolve_role_plan,
    resolve_steps,
)
from chimera.api.governance import read_audit, run_injection_suite
from chimera.api.maturity_api import maturity_report
from chimera.api.roles import fusion_for_role, review_model_for
from chimera.api.runs import load_runs
from chimera.api.schemas import (
    AcceptanceOut,
    AgentDefOut,
    AgentIdentityOut,
    AgentsBatchOut,
    BatchCancelOut,
    BenchmarksOut,
    CancelOut,
    CompletionOut,
    ConfigOut,
    ConfigTestOut,
    DeletedOut,
    DiagnosticsOut,
    DoctorOut,
    ExecCancelOut,
    FsBrowseOut,
    FsFileOut,
    FsFileWrittenOut,
    FsTreeOut,
    GitCommitOut,
    GitDiffOut,
    GitInitOut,
    GitRevertOut,
    GitStatusOut,
    GovernanceAuditOut,
    HealthOut,
    HitlOut,
    HitlRequest,
    InjectionReportOut,
    MaturityOut,
    McpAddRequest,
    McpServersOut,
    McpTestOut,
    MessagingPlatformOut,
    NewSessionOut,
    PausedRunOut,
    PlanOut,
    PoolAddIn,
    PoolWriteOut,
    ResourcesOut,
    RunReceiptOut,
    SearchOut,
    SessionDetailOut,
    SessionMetaOut,
    ToolsOut,
    UpdatedOut,
    UsageSummaryOut,
    VersionOut,
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
    from chimera.server import MessagingManager

_log = get_logger("api.app")

# Live single-run cancel registry: run_id -> the threading.Event whose set() the run's cooperative
# stop check polls. A run inserts its id on start and pops it on cleanup, so only in-flight runs are
# cancellable; POST /api/runs/{id}/cancel sets the event. In-process only (a single-user local app).
_run_cancels: dict[str, threading.Event] = {}

# Live BATCH cancel registry: batch_id -> {task index -> that task's stop Event}. Exactly the
# single-run mechanism above, one Event per task, so POST /api/agents/{batch_id}/cancel can halt one
# task (index) or every task in the batch (index=null). A batch inserts its id on start and pops it on
# cleanup, so only in-flight batches are cancellable. In-process only (a single-user local app).
_agents_cancels: dict[str, dict[int, threading.Event]] = {}


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


class DiagnosticsRequest(BaseModel):
    """Ask the language server what is wrong with one file.

    The TEXT travels, not just the path, and that is the whole design. The editor's buffer is what
    the person is looking at; the file on disk is what they last saved. Diagnosing the saved copy
    means every squiggle is one save behind, which is worse than none — it points at problems the
    user already fixed.
    """

    path: str
    text: str
    workspace: str | None = None


class CompletionRequest(BaseModel):
    """Ask what comes after the cursor.

    The text is split by the CLIENT rather than sent whole with an offset, because the editor is the
    only party that knows where the caret is at the moment the request leaves — and a stale offset
    against a fresher document is an off-by-one that produces a plausible suggestion in the wrong
    place, which is the hardest kind to notice.

    `key` is the single-flight bucket: one per editor tab. Requests sharing a key supersede one
    another, so a burst of keystrokes leaves exactly one generation running.
    """

    prefix: str
    suffix: str = ""
    key: str = "default"


class CompletionOutcomeRequest(BaseModel):
    """What became of a suggestion. `id` is the one the completion came back with."""

    id: str
    accepted: bool


class SearchRequest(BaseModel):
    """Find a string across the workspace."""

    query: str
    workspace: str | None = None
    #: False (the default) searches for the text EXACTLY as typed. `connect()` is what someone means
    #: when they type `connect()`; read as a pattern it means "connect with an empty group" and
    #: sweeps in every bare `connect` — MORE results than the literal search, which reads as working.
    regex: bool = False
    case_sensitive: bool = False
    #: A ripgrep-style glob narrowing which files are searched (`*.ts`). Empty searches all of them.
    glob: str = ""


class ExecRequest(BaseModel):
    workspace: str | None = None
    """The workspace root the command runs in. None = the app's launch workspace."""
    command: str
    cwd: str = ""
    """Working directory, relative to the workspace (default: workspace root). Does NOT persist —
    each command is a fresh subprocess."""
    timeout: float = 60


class ExecCancelRequest(BaseModel):
    """Stop the run whose id came back on the `started` event."""

    id: str


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


class GitInitRequest(BaseModel):
    workspace: str | None = None
    """The folder to turn into a repo. None = the app's launch workspace."""


class PlanRequest(BaseModel):
    task: str
    workspace: str | None = None
    """The workspace root the plan is framed for. None = the app's launch workspace. (The planner
    itself reads no files — it is a pure model call — but the field mirrors the run request's shape.)"""



class RunRequest(CodeSeams):
    """An autonomous run: plan → execute → verify-or-revert → receipt.

    Inherits the coding seams (``max_steps``, ``context_budget``, ``repo_map``, ``explorer``,
    ``allow_tools``/``deny_tools``, ``write_region``) from :class:`~chimera.api.code_api.CodeSeams`
    rather than redeclaring them, so a run and a conversational turn cannot end up meaning different
    things by the same field name.
    """

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
    thread_id: str | None = None
    """The run's durable identity. With one, the run is checkpointed: it can pause for approval and
    a later POST with the SAME thread_id resumes it. None = a one-shot run that cannot be paused."""
    profile_source: str = "user"
    """Who chose ``profile``: ``user`` or ``system``.

    The screen stopped asking, so the app now sends a default rather than a decision. Recording that
    default under the same label the user used to pick would merge two populations permanently in
    the one view built to compare configurations — a run somebody chose "max" for and a run that got
    "balanced" because nobody was asked are not the same evidence, and `worth.py` refuses to
    back-attribute for exactly this reason."""

    pause_on_taint: bool = False
    """Stop before finalizing a run that consumed untrusted content, and wait for a human verdict
    (POST /api/runs/{thread_id}/respond). Requires ``thread_id`` — there is nowhere to park an
    unfinished run without one. Off = finalize as before."""


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


class AgentsRequest(CodeSeams):
    """A batch of coding tasks for the Agent Manager: each runs concurrently in its OWN git worktree.

    Mirrors ``chimera solve-batch``. Isolation is REAL only in a git repo — outside one the tasks run
    in-place against ``workspace`` with no isolation (so concurrent edits can collide and conflicts
    can't be detected); the response's ``is_repo`` flag says which happened, honestly.

    **Inherits ``CodeSeams`` so a batch is not structurally weaker than a single run.** It used to
    carry no posture and no profile, and to hard-code three attempts — so the same task run as one of
    two was quietly granted different tool permissions, a different reviewer, and a different attempt
    budget than the same task run alone. That was survivable while the user had to choose the Agents
    screen deliberately; it stops being survivable the moment the composer can route into a batch on
    its own, because then the downgrade is invisible AND unchosen."""

    tasks: list[AgentTaskIn]
    max_attempts: int = 3
    """Attempts per task before giving up. Was hard-coded; a batch task now gets the same budget a
    single run does."""
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


class BatchCancelRequest(BaseModel):
    """Which of a batch's tasks to cooperatively stop."""

    index: int | None = None
    """The 0-based task index to cancel. None = cancel EVERY task in the batch."""


_MAX_AGENT_TASKS = 8

# The honest degradation message when the browser runtime isn't available — the UI surfaces this
# verbatim; no placeholder image is ever fabricated.
_BROWSER_MISSING_HINT = "Browser not installed — run: playwright install chromium"

# Artifact ids are uuid4 hex (32 hex chars). This strict allowlist (hex only — no '.', '/', '\\')
# is the primary guard on GET /api/artifacts/{id}: a traversal id can't match, so the endpoint can


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
    messaging_manager: MessagingManager | None = None,
    memory: Any = None,
    graph: Any = None,
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
    settings_override = settings
    settings = settings or get_settings()
    workspace = (workspace or Path.cwd()).expanduser().resolve()
    solve_factory = solve_agent_factory or _build_solve_agent

    def live_settings() -> Settings:
        """Settings as they are NOW — for everything a run reads that the user can change.

        The app builds this once and keeps it for the life of the process, so the ``settings`` above
        is a photograph taken at launch. It is the right thing for ``home`` (the data directory must
        not move under an open session) and the wrong thing for the model, the sandbox or the
        allowlists: ``PATCH /api/config`` writes those, clears the cached ``Settings`` and returns
        success, and a run built from the photograph would still use the old ones. Saving a setting
        that silently does nothing is worse than not offering it, because the confirmation is what
        turns it into a lie.

        An explicitly injected ``settings`` stays frozen: passing one means "use THIS", which is what
        a test or a bench comparing two configurations is asking for.
        """
        return settings_override or get_settings()
    store = SessionStore(settings.home / "sessions")
    manager = SessionManager(factory, store)
    guard = Depends(_require_token())

    app = FastAPI(title="Chimera Desktop API", version="1", docs_url="/api/docs", openapi_url="/api/openapi.json")

    # Cross-origin, only for the origins the operator named, and only when they named some.
    #
    # The desktop app pointed at THIS instance from another machine is served by its own loopback
    # sidecar, so its requests here are cross-origin and the browser discards the responses unless
    # this instance names that origin. Serving the bundled SPA is same-origin and needs none of
    # this, which is why an instance nobody configured behaves exactly as it did before.
    #
    # `allow_credentials` stays False on purpose. The app authenticates with a bearer header, not a
    # cookie, so it does not need credentialed CORS — and turning it on would let a browser attach
    # this instance's cookies to a request some other page made.
    #
    # Read once, here, rather than per request: middleware is installed at construction, and a
    # setting that pretended to be live would be a promise this layer cannot keep.
    origins = [o.strip() for o in (settings.allowed_origins or "").split(",") if o.strip()]
    if origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

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

    # Pools are edited by OPERATION, not by value. `PATCH /api/config` writes a string, and a string
    # is exactly what a pool must not be edited as: the client would have to know every key to change
    # one, so rendering the mask and re-submitting the field — the natural implementation — silently
    # replaces a working rotation with `…abcd`. Add takes one key and never the list; remove takes a
    # position and never a value. Neither request can carry a key backwards.
    @app.post("/api/config/pool/{provider}", dependencies=[guard], response_model=PoolWriteOut)
    def pool_add_endpoint(provider: str, body: PoolAddIn) -> dict[str, Any]:
        from chimera.api.config_api import pool_add

        try:
            return pool_add(provider, body.key)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete(
        "/api/config/pool/{provider}/{index}", dependencies=[guard], response_model=PoolWriteOut
    )
    def pool_remove_endpoint(provider: str, index: int) -> dict[str, Any]:
        from chimera.api.config_api import pool_remove

        try:
            return pool_remove(provider, index)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/instructions", dependencies=[guard], response_model=AgentIdentityOut)
    def read_instructions_endpoint() -> dict[str, Any]:
        from chimera.core.instructions import load as load_identity

        return load_identity(live_settings().home).model_dump()

    @app.put("/api/instructions", dependencies=[guard], response_model=AgentIdentityOut)
    def write_instructions_endpoint(identity: AgentIdentityOut) -> dict[str, Any]:
        # Returns the STORED record, not the submitted one — the free text is capped, and someone
        # who pasted more than the budget has to see what the agent will actually read.
        from chimera.core.instructions import AgentIdentity
        from chimera.core.instructions import save as save_identity

        stored = save_identity(
            live_settings().home,
            AgentIdentity(
                name=identity.name,
                language=identity.language,
                instructions=identity.instructions,
            ),
        )
        return stored.model_dump()

    @app.get("/api/agents/registry", dependencies=[guard], response_model=list[AgentDefOut])
    def list_agents_endpoint() -> list[Any]:
        from chimera.core.registry import load as load_agents

        return [a.model_dump() for a in load_agents(live_settings().home)]

    @app.put("/api/agents/registry", dependencies=[guard], response_model=list[AgentDefOut])
    def upsert_agent_endpoint(agent: AgentDefOut) -> list[Any]:
        # Whole-record replace, not merge: a PUT that kept fields the caller omitted would make
        # clearing a pinned model impossible, and "I removed that and it came back" is the shape of
        # bug nobody reports because they assume they did it wrong.
        from chimera.core.registry import AgentDef
        from chimera.core.registry import upsert as upsert_agent

        try:
            entry = AgentDef.model_validate(agent.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return [a.model_dump() for a in upsert_agent(live_settings().home, entry)]

    @app.delete("/api/agents/registry/{agent_id}", dependencies=[guard],
                response_model=list[AgentDefOut])
    def remove_agent_endpoint(agent_id: str) -> list[Any]:
        # Cards already filed under this agent's lane are deliberately untouched: they keep their
        # lane and stop being dispatched, which is recoverable. Deleting the work is not.
        from chimera.core.registry import remove as remove_agent

        return [a.model_dump() for a in remove_agent(live_settings().home, agent_id)]

    @app.get("/api/doctor", dependencies=[guard], response_model=DoctorOut)
    def doctor_endpoint() -> dict[str, Any]:
        from chimera.api.config_api import doctor

        return doctor(get_settings())

    @app.get("/api/version", dependencies=[guard], response_model=VersionOut)
    async def version_endpoint() -> dict[str, Any]:
        # Report the running version and, ONLY when GitHub confirms a strictly-newer release, signal an
        # update. Honest/fail-silent: offline or any error → latest=None, update_available=False (never a
        # false "update available"). PRIVACY: a plain GET of GitHub's PUBLIC releases API — no user data
        # is sent. The blocking urllib fetch runs on a worker thread (like POST /api/plan) and its result
        # is cached for an hour so repeated GETs don't hammer the API.
        from chimera.api.version_api import check_version

        return await asyncio.get_running_loop().run_in_executor(None, check_version)

    @app.post("/api/config/test", dependencies=[guard], response_model=ConfigTestOut)
    def config_test_endpoint(req: ConfigTestRequest) -> dict[str, Any]:
        # The ONLY endpoint that makes a real model call: a minimal 1-token completion so the
        # onboarding wizard can honestly say "key works" — presence checks (doctor) never authenticate.
        # Failures come back as {ok:false, error} (short, secret-free), never a 500.
        from chimera.api.config_test import test_provider

        return test_provider(req.model)

    # --- Messaging: reach the user on Discord/Telegram, controlled from the UI --------------------
    @app.get("/api/messaging", dependencies=[guard], response_model=dict[str, MessagingPlatformOut])
    def messaging_status() -> dict[str, Any]:
        # No manager (API-only build) → nothing is available; the UI shows the card as unavailable.
        return messaging_manager.status() if messaging_manager is not None else {}

    @app.post(
        "/api/messaging/{platform}/start",
        dependencies=[guard],
        response_model=dict[str, MessagingPlatformOut],
    )
    def messaging_start(platform: str) -> dict[str, Any]:
        if messaging_manager is None:
            raise HTTPException(status_code=503, detail="messaging is not available in this build")
        try:
            messaging_manager.start(platform)
        except ValueError as exc:  # unknown platform or no token configured
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return messaging_manager.status()

    @app.post(
        "/api/messaging/{platform}/stop",
        dependencies=[guard],
        response_model=dict[str, MessagingPlatformOut],
    )
    def messaging_stop(platform: str) -> dict[str, Any]:
        if messaging_manager is None:
            raise HTTPException(status_code=503, detail="messaging is not available in this build")
        messaging_manager.stop(platform)
        return messaging_manager.status()

    @app.get("/api/usage", dependencies=[guard], response_model=UsageSummaryOut)
    def usage_endpoint() -> dict[str, Any]:
        return summarize_usage(load_usage(settings.home / "usage.jsonl"))

    @app.get("/api/runs", dependencies=[guard], response_model=list[RunReceiptOut])
    def runs_endpoint(workspace: str | None = None) -> list[Any]:
        # Read-only: the last 100 run receipts, most recent first. Each was persisted by the
        # autonomous loop (CLI `solve` or the POST trigger below) via its ``run_log``.
        #
        # `workspace` narrows to one project; omitting it returns every project's runs, which is
        # what this endpoint has always done and what a client with no project selected wants.
        return list(reversed(load_runs(settings.home / "runs.jsonl", workspace=workspace)))[:100]

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
        # the defenses, side by side. GET is fine — it reads nothing and writes nothing. Settings go
        # in so the report describes THIS install: the layer it measures is switchable, and a
        # scoreboard for a build the reader does not have is worse than no scoreboard.
        return run_injection_suite(live_settings())

    @app.get("/api/governance/audit", dependencies=[guard], response_model=GovernanceAuditOut)
    def governance_audit_endpoint() -> dict[str, Any]:
        # Read-only, newest-first. Written by CLI guarded/tainted runs AND, since the ledger got an
        # audit log, by the app itself whenever a defence fires. Empty is still expected — it just
        # stopped meaning "nothing is recording" and started meaning "nothing has happened".
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
        # weak-model lift (internal suite, n=100, CI excludes 0) AND the humbling external
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

    @app.post("/api/runs", dependencies=[guard])
    async def run_stream(req: RunRequest) -> EventSourceResponse:
        # In-app trigger for an autonomous run (`chimera solve` semantics), streamed live as SSE.
        # SAFETY POSTURE: this executes file-writing and (if given) a user-supplied shell verify
        # command inside ``ws`` — the same capability the chat endpoint's file/shell tools already
        # have, and the same as running `chimera solve` in a terminal. It stays behind the bearer
        # guard + the localhost bind, and never runs outside ``ws``. It is the PLAIN solve core
        # (plan → run → verify-or-revert → receipt); the advanced CLI seams are intentionally omitted.
        ws = Path(req.workspace).expanduser().resolve() if req.workspace else workspace
        # A pause needs somewhere to park. A client that asked for one via `posture` but sent no
        # thread would otherwise get a run that quietly never pauses, so mint the identity here —
        # once, before anything reads it, so the agent, `auto.run` and the `paused` frame all agree.
        if resolve_posture(req.posture).needs_thread and not req.thread_id:
            req.thread_id = uuid.uuid4().hex
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

        # What is about to judge this run, said BEFORE it starts, in the transcript rather than in a
        # form field. Both branches matter and the second matters more: "no verify command found —
        # this run is judged by a model reading the answer, not by tests" has ALWAYS been true
        # whenever the box was left empty, and the interface never once said it. A user could not
        # tell an executable verdict from an approving paragraph, and the panel that counts passes
        # called them the same thing.
        _verify_cmd, _verify_src = resolve_verify(req.verify, ws)
        emit("verify", {"command": _verify_cmd, "source": _verify_src})

        def work() -> None:
            try:
                auto = solve_factory(req, ws, on_event, live_settings(), cancel.is_set)
                # The receipt persists itself via the agent's run_log at run() — no extra write here.
                result = auto.run(req.task, thread_id=req.thread_id)
                if getattr(result, "paused", False):
                    # The run stopped BEFORE finalizing and is parked under its thread. Its own frame,
                    # not `done`: `done` carries a verdict, and this run has not reached one. A client
                    # that treated a pause as an ordinary failure would quietly discard work that is
                    # sitting there waiting to be sanctioned.
                    emit(
                        "paused",
                        {
                            "thread_id": req.thread_id or "",
                            "answer": (result.answer or "")[:2000],
                            "attempts": len(result.attempts),
                        },
                    )
                    return
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

    @app.get("/api/runs/paused", dependencies=[guard], response_model=list[PausedRunOut])
    def paused_runs() -> list[dict[str, Any]]:
        # Every run parked awaiting a verdict. Without this the app could only ever know about a
        # pause it personally witnessed on an open stream — close the window mid-run and the run is
        # still sitting there, invisible, holding work nobody can release.
        from chimera.core.runstate import RunCheckpointer

        store = RunCheckpointer(settings.home / "runs.db")
        out: list[dict[str, Any]] = []
        for thread in store.threads():
            state = store.load(thread)
            if not state or not state.get("awaiting_approval"):
                continue  # checkpointed but already answered — resumable, not waiting on a human
            out.append(
                {
                    "thread_id": thread,
                    "answer": str(state.get("paused_answer") or "")[:2000],
                    "tainted": bool(state.get("was_tainted", False)),
                }
            )
        return out

    @app.post("/api/runs/{thread_id}/respond", dependencies=[guard], response_model=HitlOut)
    def respond_run(thread_id: str, req: HitlRequest) -> dict[str, Any]:
        # The human verdict on a paused run. The whole HITL envelope has lived in the core since
        # M13 and was reachable only from the CLI, which meant the desktop app could arm a pause it
        # had no way to answer. Answering does not itself resume: "respond" needs another attempt,
        # so the client re-POSTs /api/runs with the same thread_id to carry it out.
        from chimera.core.runstate import RunCheckpointer

        store = RunCheckpointer(settings.home / "runs.db")
        ok = store.respond(thread_id, req.action, answer=req.answer, feedback=req.feedback)
        return {"ok": ok, "resume_required": ok, "retries": ok and req.action == "respond"}

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
        # Cooperative-cancel plumbing, per task: a batch id + one stop Event per task index. The
        # frontend learns the id from the first `batch` frame and hits POST /api/agents/{id}/cancel to
        # set one task's Event (index) or all of them (index=null); each task's stop check
        # (event.is_set) then halts THAT task's loop before its NEXT attempt — an in-flight model call
        # is never interrupted. Registered here, popped in the work() finally so the map never leaks
        # past a finished batch.
        batch_id = uuid.uuid4().hex
        cancels = {i: threading.Event() for i in range(len(req.tasks))}
        _agents_cancels[batch_id] = cancels

        def emit(event: str, payload: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (event, payload))

        # Tell the client its batch_id immediately (its own SSE frame), so the per-task Stop controls
        # can target this batch from the first moment — before any task has run.
        emit("batch", {"batch_id": batch_id})

        def make_run(index: int, spec: AgentTaskIn) -> Callable[[Path], AutonomousResult]:
            # Reuse the exact single-run builder (``solve_factory`` == _build_solve_agent by default, or
            # the injected test factory) per task, framing a RunRequest from the shared batch knobs. The
            # closure receives its ISOLATED worktree path ``ws_i`` and runs the agent there.
            sub = RunRequest(
                task=spec.task,
                verify=spec.verify,
                max_attempts=req.max_attempts,
                model=req.model,
                fuse=req.fuse,
                cascade=req.cascade,
                # The seams travel with the batch, so one task of five is governed exactly as it
                # would be alone. `workspace` is deliberately NOT copied: each task gets its own
                # isolated worktree path, handed to the closure below.
                posture=req.posture,
                profile=req.profile,
                roles=req.roles,
                write_region=req.write_region,
                allow_tools=req.allow_tools,
                deny_tools=req.deny_tools,
                max_steps=req.max_steps,
                context_budget=req.context_budget,
                repo_map=req.repo_map,
                explorer=req.explorer,
            )

            def run(ws_i: Path) -> AutonomousResult:
                # Tag EVERY event with this task's index so the board can route it to the right card.
                # The task index is set LAST so it always wins: an ``attempt``/``result`` event's own
                # ``data["index"]`` (the attempt number) must NOT clobber the task tag — the attempt
                # number is still carried in the event's ``text`` (e.g. "attempt 2/3").
                def on_event(event: AgentEvent) -> None:
                    emit("event", {**_event_dict(event), "index": index})

                # This task's OWN cooperative-stop probe: the Event registered for THIS index, so a
                # Stop on one card halts only that task (the batch's other workers run on).
                agent = solve_factory(sub, ws_i, on_event, live_settings(), cancels[index].is_set)
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
                # done (or crashed): the batch is no longer cancellable
                _agents_cancels.pop(batch_id, None)
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

    @app.post("/api/agents/{batch_id}/cancel", dependencies=[guard], response_model=BatchCancelOut)
    def cancel_agents(batch_id: str, req: BatchCancelRequest) -> dict[str, Any]:
        # Cooperative cancel inside an in-flight batch: sets ONE task's stop Event (``index``) or every
        # task's (``index`` null). Each task's loop polls its Event BETWEEN attempts (never mid
        # model-call), so this halts a task after its current attempt — it never kills instantly.
        # An unknown/finished batch id (or an out-of-range index) is a no-op {ok:false, cancelled:0}
        # with a 200 — not a 404 — since a batch that already ended is exactly the state a stale Stop
        # click hits. ``cancelled`` counts the events actually set (already-set ones don't recount).
        events = _agents_cancels.get(batch_id)
        if events is None:
            return {"ok": False, "cancelled": 0}
        if req.index is None:
            targets = list(events.values())  # whole batch: every task's stop flag
        else:
            target = events.get(req.index)  # one task; an out-of-range index targets nothing
            targets = [target] if target is not None else []
        fresh = [e for e in targets if not e.is_set()]
        for event in fresh:
            event.set()
        return {"ok": bool(targets), "cancelled": len(fresh)}

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

    @app.get("/api/fs/browse", dependencies=[guard], response_model=FsBrowseOut)
    def fs_browse_endpoint(path: str = "") -> dict[str, Any]:
        """Sub-directories of ``path`` (home when empty), so a person can PICK a project.

        The tree endpoint cannot answer this: it is scoped inside a workspace, and the question here
        is which workspace. Directories only, no hidden entries, nothing read — it enumerates folder
        names and that is all it can do.
        """
        from chimera.api.fs_api import browse_dirs

        return browse_dirs(path)

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

    @app.get("/api/fs/image", dependencies=[guard], response_class=Response)
    def fs_image_endpoint(path: str, workspace: str | None = None) -> Response:
        """The raw bytes of an image in the workspace, so `render_chart`'s PNG can reach the screen.

        The response headers are the security half and each one is load-bearing:

        * The **content type comes from the allowlist in `fs_api`** and can only ever be `image/*`.
          A `.html` in the workspace is a 415, not a labelled document — this origin's page carries
          the bearer token in a `<meta>` tag, so a document served from here can read it with one
          same-origin fetch and then drive the API as the user.
        * **`nosniff`**, because the allowlist only chooses the label. A file named `.png` whose
          bytes are `<html>` is still served as `image/png`; without this header a browser is free to
          sniff past that label and render it as a document, which is the same hole by a longer road.
        * **A `sandbox` CSP**, for the one case `<img>` does not cover: someone opening the URL
          directly. An opaque origin has no same-origin anything to read.

        No `Content-Disposition`: the filename would have to be echoed from the request path into a
        header, and header-quoting a user-controlled string is a bug waiting to be written for a
        field nothing here needs.
        """
        from chimera.api.fs_api import ImageTooLargeError, UnsupportedImageError, read_image
        from chimera.tools.workspace import PathEscapesWorkspaceError

        ws = _resolve_fs_workspace(workspace)
        try:
            data, media_type = read_image(ws, path)
        except PathEscapesWorkspaceError as exc:
            raise HTTPException(status_code=400, detail="invalid path") from exc
        except UnsupportedImageError as exc:
            raise HTTPException(status_code=415, detail="not a displayable image type") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="not found") from exc
        except ImageTooLargeError as exc:
            raise HTTPException(status_code=413, detail="image too large to preview") from exc
        return Response(
            content=data,
            media_type=media_type,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    @app.post("/api/fs/search", dependencies=[guard], response_model=SearchOut)
    def fs_search(req: SearchRequest) -> dict[str, Any]:
        """Find a string across the workspace, as structured hits.

        A POST because the query is user text — a `q=` in a URL is logged by every proxy between
        here and nowhere, and someone searching their own repository for a token they are trying to
        remove should not have it written to an access log on the way.

        Read-only and scoped to the workspace, like the tree and the file reader beside it. The
        answer names the engine that produced it: ripgrep where it exists, a bounded Python walk
        where it does not, and never a silent swap between the two.
        """
        from chimera.core.search import search as run_search

        ws = _resolve_fs_workspace(req.workspace)
        result = run_search(
            req.query,
            ws,
            regex=req.regex,
            case_sensitive=req.case_sensitive,
            glob=req.glob,
        )
        return {
            "hits": [
                {"path": h.path, "line": h.line, "text": h.text, "start": h.start, "end": h.end}
                for h in result.hits
            ],
            "engine": result.engine,
            "capped": result.capped,
            "timed_out": result.timed_out,
            "elapsed_ms": result.elapsed_ms,
            "error": result.error,
        }

    @app.post("/api/lsp/diagnostics", dependencies=[guard], response_model=DiagnosticsOut)
    def lsp_diagnostics(req: DiagnosticsRequest) -> dict[str, Any]:
        """What `ruff server` says about this buffer.

        POST, and the request carries the text — see :class:`DiagnosticsRequest`. Not a WebSocket:
        this app has never served one, `uvicorn` here is installed without `[standard]` so there is
        no `wsproto`, a browser WebSocket cannot send an `Authorization` header, and WS does not go
        through CORS. A new unauthenticated channel on a memorable localhost port, in a product
        whose pitch is governance, is the worst trade available.

        Synchronous rather than streamed. Diagnostics arrive as a server NOTIFICATION, so a stream
        would be honest — but it would also be a second long-lived connection per open file, and the
        wait here is the tens of milliseconds ruff takes on one buffer. A stream can replace this
        without the caller changing, which is the reason the response is a list rather than an event.
        """
        from chimera.api.lsp_api import diagnostics_for_buffer

        ws = _resolve_fs_workspace(req.workspace)
        return diagnostics_for_buffer(ws, req.path, req.text)

    @app.post("/api/complete/inline", dependencies=[guard], response_model=CompletionOut)
    async def complete_inline_endpoint(req: CompletionRequest) -> dict[str, Any]:
        """A fill-in-the-middle suggestion from the local model.

        Async, unlike its neighbours, and for one reason: cancellation. A keystroke supersedes the
        request before it, and the superseded one must stop OCCUPYING THE GPU — not merely have its
        answer discarded. A thread-pool handler could not close the upstream connection early, and
        the leak reads as "the model is slow", which sends you tuning the wrong thing.
        """
        from chimera.complete.inline import complete_inline
        from chimera.complete.outcomes import record_shown

        found = await complete_inline(
            req.prefix,
            req.suffix,
            base_url=settings.ollama_base_url,
            model=settings.complete_model,
            key=req.key,
            budget_ms=settings.complete_budget_ms,
        )
        ident = ""
        if found.text:
            ident = record_shown(
                Path(settings.home), ms=found.ms, chars=len(found.text), model=found.model
            )
        return {
            "text": found.text,
            "id": ident,
            "available": found.available,
            "note": found.note,
            "ms": found.ms,
            "model": found.model,
        }

    @app.post("/api/complete/outcome", dependencies=[guard], response_model=AcceptanceOut)
    def complete_outcome_endpoint(req: CompletionOutcomeRequest) -> dict[str, Any]:
        """Record a Tab or an Escape, and answer with the rate so far."""
        from chimera.complete.outcomes import acceptance, record_outcome

        home = Path(settings.home)
        record_outcome(home, ident=req.id, accepted=req.accepted)
        return acceptance(home)

    @app.get("/api/complete/stats", dependencies=[guard], response_model=AcceptanceOut)
    def complete_stats_endpoint() -> dict[str, Any]:
        """The acceptance rate measured on THIS machine — the only evidence that would justify a
        claim about how good these suggestions are. Until it has samples, `rate` is null."""
        from chimera.complete.outcomes import acceptance

        return acceptance(Path(settings.home))

    @app.get("/api/resources", dependencies=[guard], response_model=ResourcesOut)
    def resources_endpoint() -> dict[str, Any]:
        """What this machine is spending, right now.

        Every field is nullable and that is the contract: a measurement that could not be taken is
        absent, never zero. A panel showing 0% VRAM on an AMD card would be believed, and it would
        be wrong about hardware the user is looking at.
        """
        from chimera.core.resources import snapshot

        return snapshot().as_dict()

    @app.post("/api/fs/exec", dependencies=[guard])
    async def fs_exec_stream(req: ExecRequest) -> EventSourceResponse:
        # HONEST command-runner (NOT an interactive terminal): each call is a FRESH subprocess — cwd/env
        # do NOT persist between commands. Streams combined stdout+stderr line by line on the local
        # sandbox; honors CHIMERA_SANDBOX (non-local runs one-shot inside the sandbox, isolated). Same
        # side-effecting power as `run_shell` / a terminal in the workspace, gated the same way (bearer
        # + localhost bind). The child env scrubs provider secrets (reused from LocalSandbox).
        from chimera.api.exec_stream import cancel as cancel_exec
        from chimera.api.exec_stream import resolve_exec_cwd, run_streamed

        ws = _resolve_fs_workspace(req.workspace)
        try:
            resolve_exec_cwd(ws, req.cwd)  # pre-validate so a cwd escape is a clean 400, not a stream
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid cwd") from exc
        timeout = max(1.0, min(float(req.timeout), 3600.0))  # clamp to the shell tool's 1..3600 cap
        run_id = uuid.uuid4().hex
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
                    run_id=run_id,
                )
                push({"kind": "exit", "code": code})
            except Exception as exc:  # noqa: BLE001 — surfaced to the client as a non-zero exit
                _log.warning("exec failed: %s", exc)
                push({"kind": "exit", "code": 1})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel: end of stream

        threading.Thread(target=work, daemon=True).start()

        async def events() -> AsyncIterator[dict[str, str]]:
            # The id first, so a Stop button exists before there is any output to stop.
            yield {"event": "started", "data": json.dumps({"kind": "started", "id": run_id})}
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    yield {"event": item["kind"], "data": json.dumps(item)}
            finally:
                # Closing the panel, navigating away, or losing the window ends the stream — and
                # must end the command with it. Without this, a `npm run dev` started here holds
                # its port until the timeout, which for a long command is an hour, and by then
                # nobody remembers starting it. Killing an already-finished run is a no-op.
                cancel_exec(run_id)

        return EventSourceResponse(events())

    @app.post("/api/fs/exec/cancel", dependencies=[guard], response_model=ExecCancelOut)
    def fs_exec_cancel(req: ExecCancelRequest) -> dict[str, Any]:
        """Stop a running command and everything it started.

        `cancelled: false` when there was nothing to stop — a command that already finished, or one
        running inside a non-local sandbox, where there is no host process to signal. Answering true
        regardless would put a button on screen that does nothing and reports success.
        """
        from chimera.api.exec_stream import cancel as cancel_exec

        return {"cancelled": cancel_exec(req.id)}

    @app.post("/api/git/init", dependencies=[guard], response_model=GitInitOut)
    def git_init_endpoint(req: GitInitRequest) -> dict[str, Any]:
        # `git init` + a snapshot commit, so the folder has a point of return BEFORE the agent is
        # given write and shell access to it. Strictly less power than POST /api/fs/exec, which can
        # already run `git init` and anything else; this exists so the app stops telling people to
        # open a terminal to get the isolation and the undo its own panels depend on.
        # An already-initialised folder returns {ok: False, error} — never a 500.
        from chimera.api.git_api import git_init

        return git_init(_resolve_fs_workspace(req.workspace))

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
                emit("done", _report_dict(report, session_id, fused=swap))
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
    from chimera.api.openai_compat import register_openai_compat

    # `workspace` is where a dispatched Kanban card works when the request names none.
    register_features(app, guard, workspace=workspace)
    # POST /api/code/turn — a conversational coding turn that keeps the previous turn's tool calls.
    register_code_api(
        app,
        guard,
        workspace,
        settings,
        # `settings` above stays the mount-time snapshot (it is what `home` should be read from);
        # everything the Settings screen can change reads through this instead.
        live_settings=live_settings,
        # Read-only, and one-way on purpose — see register_code_api. Passed explicitly rather than
        # dug out of a probe session, so the direction of the dependency is visible here.
        memory=memory,
        graph=graph,
        fuse_backend=fuse_backend,
    )
    # /v1/chat/completions — any OpenAI client or LLM benchmark harness can drive the agent loop.
    register_openai_compat(app, guard, manager)

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
            # The PROJECT, not the worktree the task actually edited. A worktree is an ephemeral
            # checkout of `ws` whose changes merge back into it and whose path never exists again —
            # recording it would file every batch run under an address nothing can be grouped by.
            receipt = build_receipt(auto, spec.task, spec.verify, ts, workspace=str(ws))
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

    "Mirror" includes the top rung: ``fusion_for_role``, never a bare ``FusionEngine(gateway)``. The
    bare form falls through to the frontier default panel, which `roles.py` documents as a measured
    billing bug — and a mirror that copied the shape while dropping that fix would put the desktop
    on frontier rates for a user who chose cheap tiers.
    """
    from chimera.api.roles import fusion_for_role
    from chimera.fusion.cascade import CascadeBackend, CascadeConfig

    ladder = settings.tier_ladder()
    config = CascadeConfig(
        weak=ladder.weak,
        mid=ladder.mid,
        entry=ladder.entry,
        log_path=settings.home / "routes.jsonl",
    )
    return CascadeBackend(gateway, cast("SupportsComplete", fusion_for_role(gateway, settings)), config)


def resolve_verify(requested: str | None, workspace: Path) -> tuple[str | None, str]:
    """``(command, source)`` — where ``source`` is ``user`` | ``inferred:<file>`` | ``none``.

    The source travels into the receipt so a stored run can never be read as "the user chose this".
    A receipt that cannot tell a typed command from an inferred one is a receipt that will eventually
    be cited as evidence of a decision nobody made.

    An empty STRING is an explicit "no verifier": someone who cleared the field asked for exactly
    that, and inferring over the top of it would be overriding a choice with a guess. Only ``None``
    — the field absent from the request — means "look at the project".
    """
    if requested is not None:
        return (requested or None), ("user" if requested else "none")
    from chimera.core.verify_infer import infer_verify

    found = infer_verify(workspace)
    if found is None:
        return None, "none"
    return found.command, f"inferred:{found.source_file}"


def _build_solve_agent(
    req: RunRequest,
    ws: Path,
    on_event: EventSink,
    settings: Settings,
    should_stop: Callable[[], bool] | None = None,
) -> AutonomousAgent:
    """Build the solve-core agent for the desktop trigger: plan → run → verify-or-revert → receipt.

    The same capability as ``chimera solve TASK --verify CMD`` in a terminal. The worker's file/shell
    tools write inside ``ws`` and ``CommandVerifier`` runs the verify command there; that
    side-effecting power is the same the chat endpoint already exposes, gated the same way.

    Every per-run knob mirrors a CLI flag, and every one of them is off unless the caller asks:
    ``model`` / ``fuse`` / ``cascade`` (backend routing), ``plan`` (an approved plan injected
    verbatim, skipping the planning call), ``max_steps`` + ``context_budget`` (how long the loop may
    run and when it compacts), ``repo_map`` / ``explorer`` (how it finds its way around a
    repository), and ``allow_tools`` / ``deny_tools`` / ``write_region`` (what it is allowed to
    touch). With none set, the build is the plain single-model core loop.

    Still deliberately absent, and still a real difference from the CLI: evolution, strong-verify,
    contracts, checklists and the trust kernel. Those are not ceilings on an ordinary run — they are
    separate machinery, and each would need its own surface before it meant anything here.
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
    from chimera.core.instructions import load as load_identity
    from chimera.core.instructions import render as render_identity
    from chimera.core.planner import Plan
    from chimera.core.runstate import RunCheckpointer
    from chimera.core.verify import CommandVerifier
    from chimera.providers import LLMGateway

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

    # Roles. The multi-LLM story for coding is a model per JOB, not a fuse switch on the loop: the
    # router sends any turn carrying tool schemas to a single model, and every turn in a coding loop
    # carries tools, so "fuse the loop" would never fire and would report that it had. Planning and
    # review are the two turns with no tools, which is exactly where fusion is both safe and useful.
    roles = resolve_role_plan(req, settings)

    # ONE meter per run, and it wraps the gateway UNDERNEATH everything non-worker rather than
    # wrapping each collaborator on top. That placement is what makes it complete: a fusion engine
    # built on the meter routes every panelist, the judge and the synthesizer through it, so a fused
    # plan is counted call by call instead of being one opaque result. Wrapping the engine from
    # outside would count the same run once and miss which stage spent it.
    #
    # Per run, never global: a module-level tally would blend the concurrent runs solve-batch
    # starts, and be wrong in a way nobody notices because it would still look plausible.
    from chimera.orchestration.metering import MeteredBackend

    meter = cast("SupportsComplete", MeteredBackend(gateway, label="overhead"))
    # The worker is deliberately NOT metered — it prices itself through AgentResult.usd, and routing
    # it here too would double-count every step.
    #
    # fusion_for_role, never a bare FusionEngine: a bare one falls through to the frontier default
    # panel, so a profile picked under a cheap cost mode silently convenes (and bills) Opus +
    # GPT-5.5 + Gemini. The panel has to mean what the profile means — see chimera.api.roles.
    planner_backend = meter if planner_backend is gateway else planner_backend
    if roles.models.fuse_plan and not (req.fuse or req.cascade):
        planner_backend = cast("SupportsComplete", fusion_for_role(meter, settings))
    manager_backend: SupportsComplete = meter
    if roles.models.fuse_review:
        manager_backend = cast("SupportsComplete", fusion_for_role(meter, settings))

    # Registry assembly and the ledger watching it, shared with the conversational endpoint so the
    # two cannot drift — the order inside is load-bearing (see assemble_registry).
    #
    # narrow_on_taint is ARMED here by default (CHIMERA_TAINT_NARROW). It used to be hard-coded off
    # because the server has no tool-level approver, so narrowing resolves to a refusal — but "no one
    # can approve it" is an argument for refusing a dangerous tool after untrusted input, not for
    # allowing it. Leaving it off meant the headless product surface was the one place the advertised
    # defence did not run. A deployment that must keep acting autonomously after reading the web sets
    # CHIMERA_TAINT_NARROW=0 deliberately. Routing the approval to the desktop's HITL UI (so it can
    # be answered rather than only refused) is the follow-up.
    steps = resolve_steps(req.max_steps)
    posture = resolve_posture(req.posture)
    registry, ledger = assemble_registry(req, ws, settings, gateway, steps=steps)
    # insist_on_action: solve is task completion, so a described-but-unexecuted plan is pushed back
    # to actually run (mirrors the CLI worker config).
    cfg = AgentConfig(
        # The EDITOR's model. Never fused: synthesising three different patches is how you get a
        # patch that applies cleanly and means nothing.
        model=roles.models.edit or req.model,
        max_steps=steps,
        context_budget=req.context_budget,
        insist_on_action=True,
        # The workspace's own AGENTS.md. This repository ships one written for AI agents to follow
        # and, until this line, the agent of this project did not read it.
        project_root=ws,
        # And the owner's own instructions, which outrank it — a repository is a convention, this is
        # the person who runs the agent. Read per run, not held from boot.
        instructions=render_identity(load_identity(settings.home)),
        # Same trace the CLI writes, in the same place — a run started from the app and one started
        # from a terminal should leave the same evidence.
        trace_path=settings.home / "traces.jsonl",
    )
    worker = Agent(backend, registry, cfg)
    escalate_worker = (
        Agent(escalate_backend, registry, cfg) if escalate_backend is not None else None
    )
    # Plan mode: an approved/edited plan is injected verbatim (parsed the same way the planner parses
    # its own output), so the run follows the human-reviewed steps and makes no planning call.
    provided_plan = Plan.from_text(req.plan) if req.plan else None
    # What a compaction has to put back. Without this the budget above would compact blind: it would
    # keep the recent tail and drop the plan the run is executing, which is the one thing an agent
    # cannot re-derive from its own tail. Seeded only from what is known here — the task and the
    # approved plan; the open file arrives per-turn from the UI and is set there, not remembered.
    for agent in (worker, escalate_worker):
        if agent is None:
            continue
        agent.run_state.current_state = req.task
        if provided_plan is not None:
            agent.run_state.plan = provided_plan.as_text()
            agent.run_state.tasks = list(provided_plan.steps)
    # The verify command: what the caller typed, or what the project says about itself.
    #
    # `None` means "look", not "none". The field is gone from the screen, and deleting it without
    # this would have quietly demoted every desktop run from an executable verdict to "a model read
    # the answer and approved it" — while `worth.py` kept calling both of them "passed". Inference is
    # what lets the control disappear and the capability stay.
    verify_command, verify_source = resolve_verify(req.verify, ws)

    # The flywheel: experience, trajectories, memory, the auto-evolver, skill cards and the playbook.
    #
    # The desktop app has always been the surface that does the most work and learned the least from
    # it — `chimera solve` in a terminal accumulated memory, minted skills and grew a playbook, while
    # every run started from the app threw all of it away. The same shared factory the Kanban lanes,
    # the orchestration lifecycle and the workflow executors already use, with the same flags, so
    # this is a path joining the flywheel rather than a second copy of the wiring.
    from chimera.evolution import build_evolution_context

    evo = build_evolution_context(
        settings,
        gateway,
        roles.models.edit or req.model,
        home=settings.home,
        include_memory=True,
        include_playbook=True,
    )

    return _AutonomousAgent(
        worker,
        should_stop=should_stop,
        escalate_worker=escalate_worker,
        planner=Planner(planner_backend, roles.models.plan or req.model),
        plan=provided_plan,
        # review_model_for refuses to let the reviewer be the model that wrote the patch:
        # generate-and-verify collapses when both are the same model, because it grades its own
        # work and agrees with itself.
        manager=Manager(manager_backend, review_model_for(roles) or req.model),
        verifier=CommandVerifier(verify_command, ws) if verify_command else None,
        guard=WorkspaceGuard(ws),
        workspace=ws,
        spine_workspace=ws,
        repo_map=req.repo_map,
        on_event=on_event,
        # Persist the run receipt (step 3a) to the same append-only log GET /api/runs reads.
        run_log=settings.home / "runs.jsonl",
        # An opaque label recorded on the receipt so runs can be grouped later. Only what the
        # client actually named — never the resolved default, because attributing a run to a
        # profile nobody chose is fabricated evidence in the one view built to judge profiles.
        run_profile=req.profile,
        profile_source=req.profile_source,
        verify_source=verify_source,
        meter=meter,
        # Every learning seam the CLI has. Passed AFTER the explicit arguments so a future field
        # here cannot silently shadow one of them.
        **evo.apply_to(),
        # The ledger, ALWAYS — decoupled from the thread id, which used to gate it alongside the
        # checkpointer. That gating was right for the checkpointer (a pause with no durable identity
        # is a run nobody can come back to) and wrong for the ledger, which only observes. With no
        # ledger the agent answers "was this run tainted?" with False rather than "I don't know", and
        # now that the run WRITES to long-term memory that false answer would store a fact learned
        # from untrusted content as clean — the exact poisoning path the provenance field exists to
        # prevent. Passing it always costs nothing: pausing is still governed by `pause_on_taint`,
        # which stays gated on the thread.
        taint=ledger,
        checkpointer=RunCheckpointer(settings.home / "runs.db") if req.thread_id else None,
        # Either the explicit flag or the posture can ask for a pause; both still need a thread,
        # because a paused run with no durable identity is one nobody can ever come back to.
        pause_on_taint=bool(req.thread_id and (req.pause_on_taint or posture.pause_on_taint)),
        pause_always=bool(req.thread_id and posture.pause_always),
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


def _report_dict(report: Any, session_id: str, *, fused: bool = False) -> dict[str, Any]:
    """Serialize a TurnReport for the ``done`` event (all real signals; ``usd`` may be null).

    ``fused`` is not decoration. A fused turn cannot call tools — ``FusionEngine.complete`` drops the
    tool schemas and a panel has nothing to call them with — so the agent finishes in one step having
    touched nothing, and answers from the prompt alone. Ask it to read a file and it will describe a
    file it never opened, with the authority of three models agreeing. From outside, that turn is
    indistinguishable from one that legitimately needed no tool: both report zero tool calls. This
    flag is the difference, and it is the only thing that lets the UI say which happened.
    """
    return {
        "session_id": session_id,
        "fused": fused,
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
        #
        # Inside the guard because asking the filesystem about a path is not a safe question. A
        # request path can carry bytes no filesystem accepts: `%00` decodes to a NUL and `stat`
        # raises **ValueError**, which "is this a file" does not catch — so a request for a page
        # that does not exist became a 500, reading as the server being broken rather than the URL
        # being wrong. Reproduced, and the two `%00` cases in the tests fail without this.
        #
        # `OSError` is caught beside it for the platform shapes that raise instead of returning
        # False — some Windows paths are documented to. That branch is defensive: on the CPython
        # and Windows build measured here, `:` and `|` came back as a plain miss, so it is guarding
        # a case I did not manage to reproduce rather than one I watched happen.
        #
        # The traversal guard is the line below and it is doing its job — `..` and an absolute path
        # both resolve outside `static_dir` and are refused. This is robustness, not a hole: what
        # was missing is that a malformed path should reach the same answer as any other unknown
        # path, which is the app.
        try:
            candidate = (static_dir / full_path).resolve()
            if candidate.is_file() and static_dir.resolve() in candidate.parents:
                return FileResponse(candidate)
        except (OSError, ValueError):
            pass
        return _index_html(index, request)
