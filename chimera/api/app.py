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
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from chimera.api.governance import read_audit, run_injection_suite
from chimera.api.runs import load_runs
from chimera.api.schemas import (
    ConfigOut,
    DeletedOut,
    DoctorOut,
    GovernanceAuditOut,
    HealthOut,
    InjectionReportOut,
    NewSessionOut,
    RunReceiptOut,
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
    from chimera.core.autonomous import AutonomousAgent

_log = get_logger("api.app")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    stream: bool = True
    fuse: bool = False
    """Route THIS turn through the fusion engine (panel → judge → synthesizer), tool-free — so the
    Fusion screen can show how the answer was composed. Off = the session's normal backend."""


class RunRequest(BaseModel):
    task: str
    verify: str | None = None
    """A shell command that judges the run (exit 0 == success); runs in the workspace. None = no
    executable verifier (the Manager's approval is then the gate)."""
    workspace: str | None = None
    """The workspace root to run in. None = the workspace the desktop app was launched with."""
    max_attempts: int = 3


# A builder for the per-run autonomous agent, injectable so the endpoint is testable without a real
# LLM (a test passes a factory that returns a stubbed-worker agent — see tests/test_api.py).
SolveAgentFactory = Callable[[RunRequest, Path, EventSink, "Settings"], "AutonomousAgent"]


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

        def emit(event: str, payload: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (event, payload))

        def on_event(event: AgentEvent) -> None:
            emit("event", _event_dict(event))

        def work() -> None:
            try:
                auto = solve_factory(req, ws, on_event, settings)
                # The receipt persists itself via the agent's run_log at run() — no extra write here.
                result = auto.run(req.task)
                emit(
                    "done",
                    {
                        "success": result.success,
                        "answer": (result.answer or "")[:2000],
                        "attempts": len(result.attempts),
                    },
                )
            except Exception as exc:  # noqa: BLE001 — surfaced to the client as an error event
                _log.warning("run failed: %s", exc)
                emit("error", {"message": "the run failed"})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel: end of stream

        # Each run is independent (its own agent + workspace snapshot), so no per-session lock is
        # needed. A client disconnecting mid-run leaves the worker to finish (an LLM call can't be
        # cleanly cancelled); the queue is drained-or-dropped, so memory stays bounded per run.
        threading.Thread(target=work, daemon=True).start()

        async def events() -> AsyncIterator[dict[str, str]]:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, payload = item
                yield {"event": event, "data": json.dumps(payload)}

        return EventSourceResponse(events())

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


def _audit_event(entry: dict[str, Any]) -> dict[str, Any]:
    """Flatten one arbitrary audit entry into the typed ``{seq, type, summary}`` shape for the UI.

    ``summary`` is a short ``key=value`` string built from whatever keys remain after ``seq``/``type``
    (e.g. ``action=… decision=… rule=… reason=…``) — only real captured keys, nothing invented.
    """
    seq = entry.get("seq", 0)
    etype = str(entry.get("type", ""))
    parts = [f"{k}={v}" for k, v in entry.items() if k not in ("seq", "type")]
    return {"seq": int(seq) if isinstance(seq, int) else 0, "type": etype, "summary": " ".join(parts)}


def _build_solve_agent(
    req: RunRequest, ws: Path, on_event: EventSink, settings: Settings
) -> AutonomousAgent:
    """Build the PLAIN solve-core agent for the desktop trigger: plan → run → verify-or-revert → receipt.

    Deliberately minimal versus the CLI ``solve`` command — none of the advanced seams (cascade,
    taint ledger, evolution, durable threads, strong-verify, contracts, write-region). It is the
    honest core loop, the same capability as ``chimera solve TASK --verify CMD`` in a terminal. The
    worker's file/shell tools write inside ``ws`` and ``CommandVerifier`` runs the verify command
    there; that side-effecting power is the same the chat endpoint already exposes, gated the same way.
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
    from chimera.core.verify import CommandVerifier
    from chimera.providers import LLMGateway
    from chimera.tools import default_registry

    gateway = LLMGateway()
    registry = default_registry(ws)
    # insist_on_action: solve is task completion, so a described-but-unexecuted plan is pushed back
    # to actually run (mirrors the CLI worker config).
    worker = Agent(gateway, registry, AgentConfig(max_steps=6, insist_on_action=True))
    return _AutonomousAgent(
        worker,
        planner=Planner(gateway),
        manager=Manager(gateway),
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
