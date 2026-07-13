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
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from chimera.api.schemas import (
    ConfigOut,
    DeletedOut,
    DoctorOut,
    HealthOut,
    NewSessionOut,
    SessionDetailOut,
    SessionMetaOut,
    UpdatedOut,
)
from chimera.api.sessions import SessionManager, SessionStore
from chimera.config import Settings, get_settings
from chimera.core.agent import ToolActivity
from chimera.interface import ChatSession
from chimera.telemetry import get_logger

_log = get_logger("api.app")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    stream: bool = True


def _require_token(settings: Settings) -> Callable[[Request], None]:
    """A dependency that enforces the bearer token on mutating endpoints when one is configured.

    When ``CHIMERA_SERVER_TOKEN`` is unset (the localhost default) it is a no-op, matching the stdlib
    gateway's opt-in auth. When set, the constant-time check mirrors ``server/http.py``'s ``_bearer_ok``.
    """
    import hmac

    def check(request: Request) -> None:
        token = settings.server_token
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
) -> FastAPI:
    """Build the desktop API app over a session ``factory`` (the real agent stack).

    ``static_dir`` (the built SPA, ``apps/desktop/dist``) is served same-origin at ``/`` with SPA
    fallback, so the frontend needs no CORS. ``settings`` defaults to the process settings.
    """
    settings = settings or get_settings()
    store = SessionStore(settings.home / "sessions")
    manager = SessionManager(factory, store)
    guard = Depends(_require_token(settings))

    app = FastAPI(title="Chimera Desktop API", version="1", docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.get("/api/health", response_model=HealthOut)
    def health() -> dict[str, Any]:
        return {"status": "ok", "sessions": len(store.list())}

    @app.get("/api/config", response_model=ConfigOut)
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

    @app.get("/api/doctor", response_model=DoctorOut)
    def doctor_endpoint() -> dict[str, Any]:
        from chimera.api.config_api import doctor

        return doctor(get_settings())

    @app.get("/api/sessions", response_model=list[SessionMetaOut])
    def list_sessions() -> list[dict[str, Any]]:
        return [
            {"id": m.id, "title": m.title, "turns": m.turns, "updated_at": m.updated_at}
            for m in manager.list()
        ]

    @app.get("/api/sessions/{session_id}", response_model=SessionDetailOut)
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
                report = session.send_verbose(
                    req.message,
                    on_token=on_token if req.stream else None,
                    on_tool=on_tool,
                )
                manager.persist(session_id)  # durable transcript now includes this turn
                emit("done", _report_dict(report, session_id))
            except Exception as exc:  # noqa: BLE001 — surfaced to the client as an error event
                _log.warning("chat turn failed: %s", exc)
                emit("error", {"message": "the agent turn failed"})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel: end of stream

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
    def _root() -> FileResponse:
        return FileResponse(index)

    @app.get("/{full_path:path}")
    def _spa_fallback(full_path: str) -> FileResponse:
        # Any non-/api path that isn't a real asset falls back to the SPA entrypoint (client routing).
        candidate = (static_dir / full_path).resolve()
        if candidate.is_file() and static_dir.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)
