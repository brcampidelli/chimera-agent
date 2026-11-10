"""`/api/terminal` — the owner's real terminal on the Code screen.

A shell is the most power an API can hand out, so this door is narrower than the app's other routes.

**Only the desktop app's own screen opens one.** The bearer token is a file the agent's shell can
read, so it is not enough (`screen_credential.py`); a browser tab paired with a code is the owner's
screen for READING and CHATTING and is refused here on purpose — that pairing exists so a phone can
review work, not so it can type into a shell on the computer. The Claude bridge, a messaging bot, a
guest link and a paired device are refused by the same check, and the route is absent from the
bridge's table (`bridge_routes.ROUTES`), so nothing can forward to it either.

**Commands off means no terminal.** A folder whose commands the owner has not granted, or whose
deployment denies `run_shell`, answers 403 with the reason. A terminal that opened anyway would be a
second, ungoverned way to do what the switch on the Code screen says the agent may not.

**Why SSE and POST, and not a WebSocket.** The task asked for the other thing first, so the reason is
written down: `uvicorn` here is installed without `wsproto`/`websockets` (see the note on
`/api/lsp/diagnostics`), a browser WebSocket cannot send the `Authorization` or screen-credential
headers, and the HTTP middleware that decides who the screen is never runs for a WebSocket scope.
A WebSocket would have needed its own authentication, designed and audited, in front of a shell. SSE
for the output and a POST per burst of keystrokes ride the existing middleware unchanged; on loopback
the added latency is not perceptible, and the client coalesces keystrokes.

Not logged: anything typed or printed. See `pty_terminal.py`.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, params
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from chimera.api.pty_terminal import (
    COLS_RANGE,
    DEFAULT_SIZE,
    MAX_INPUT_CHARS,
    ROWS_RANGE,
    TerminalLimitReached,
    TerminalUnavailable,
    available,
    manager,
)
from chimera.api.sse import SSE_RESPONSE
from chimera.config import Settings
from chimera.telemetry import get_logger

_log = get_logger("api.terminal")

#: How often an open terminal's folder is re-checked for commands still being on.
_RECHECK_S = 1.0
#: Seconds the stream waits on a quiet shell before looping (the loop notices a closed session).
_POLL_S = 0.5

COMMANDS_OFF_DETAIL = (
    "commands are off for this folder: turn them on (the switch on the Code screen) to open a terminal"
)
SANDBOX_DETAIL = (
    "the terminal runs on this computer and the sandbox is set to a container: the owner's shell "
    "would step around it. Open a terminal outside the app, or set CHIMERA_SANDBOX to auto/os"
)


class TerminalOpenIn(BaseModel):
    workspace: str | None = Field(default=None, description="Folder to open in (the conversation's, its worktree when it has one).")
    cols: int = DEFAULT_SIZE[0]
    rows: int = DEFAULT_SIZE[1]


class TerminalOpenOut(BaseModel):
    id: str
    shell: str
    cwd: str


class TerminalInputIn(BaseModel):
    data: str = Field(max_length=MAX_INPUT_CHARS)


class TerminalResizeIn(BaseModel):
    cols: int = Field(ge=COLS_RANGE[0], le=COLS_RANGE[1])
    rows: int = Field(ge=ROWS_RANGE[0], le=ROWS_RANGE[1])


class TerminalOkOut(BaseModel):
    ok: bool


class TerminalStatusOut(BaseModel):
    available: bool
    reason: str = ""
    open: int
    max: int


def owner_refusal(request: Request) -> str:
    """Why this request may not use a terminal, or `""` when it may. Pure, for the tests.

    Each refusal is its own sentence because each is a different caller, and "forbidden" would not
    tell a person which door they came through.
    """
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
    from chimera.api.browser_screen import BROWSER_SCOPE_KEY, DESKTOP_SCREEN_SCOPE_KEY
    from chimera.api.devices import DEVICE_SCOPE_KEY

    scope = request.scope
    if scope.get(VIA_BRIDGE_SCOPE_KEY):
        return "the bridge cannot open a terminal"
    if scope.get(DEVICE_SCOPE_KEY):
        return "a paired device cannot open a terminal"
    if scope.get(BROWSER_SCOPE_KEY):
        return "a browser tab cannot open a terminal: it is opened from the desktop app"
    if not scope.get(DESKTOP_SCREEN_SCOPE_KEY):
        return "the terminal is opened from the desktop app's own screen"
    return ""


def folder_refusal(settings: Settings, folder: Path) -> str:
    """Why a terminal may not open in ``folder``, or `""`: commands off, a denylist, a container sandbox."""
    from chimera.api.code_api import server_grants_shell
    from chimera.api.posture import deployment_posture

    if not server_grants_shell(settings, folder):
        return COMMANDS_OFF_DETAIL
    denied = {*deployment_posture(settings).deny_tools, *settings.tool_denylist}
    if "run_shell" in denied:
        return COMMANDS_OFF_DETAIL
    if (settings.sandbox or "auto").lower() == "docker":
        return SANDBOX_DETAIL
    return ""


def register_terminal_api(
    app: FastAPI,
    guard: params.Depends,
    resolve_workspace: Callable[[str | None], Path],
    settings: Settings,
    *,
    live_settings: Callable[[], Settings] | None = None,
) -> None:
    """Mount the terminal routes. ``resolve_workspace`` is the app's own (existence + UNC refusal)."""
    live = live_settings or (lambda: settings)

    def owner_only(request: Request) -> None:
        why = owner_refusal(request)
        if why:
            raise HTTPException(status_code=403, detail=why)

    app.router.add_event_handler("shutdown", manager().close_all)

    @app.get("/api/terminal/status", dependencies=[guard], response_model=TerminalStatusOut)
    def terminal_status(request: Request) -> dict[str, Any]:
        owner_only(request)
        ok, reason = available()
        mgr = manager()
        return {"available": ok, "reason": reason, "open": mgr.count(), "max": mgr.max_sessions}

    @app.post("/api/terminal", dependencies=[guard], response_model=TerminalOpenOut)
    def terminal_open(body: TerminalOpenIn, request: Request) -> dict[str, Any]:
        owner_only(request)
        folder = resolve_workspace(body.workspace)
        why = folder_refusal(live(), folder)
        if why:
            raise HTTPException(status_code=403, detail=why)
        try:
            session = manager().open(folder, body.cols, body.rows)
        except TerminalLimitReached as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except TerminalUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except OSError as exc:
            _log.warning("terminal could not start: %s", exc.__class__.__name__)
            raise HTTPException(status_code=500, detail="the shell could not be started") from exc
        return {"id": session.id, "shell": session.shell, "cwd": str(session.cwd)}

    @app.get("/api/terminal/{terminal_id}/stream", dependencies=[guard], responses=SSE_RESPONSE)
    async def terminal_stream(terminal_id: str, request: Request) -> EventSourceResponse:
        owner_only(request)
        session = manager().get(terminal_id)
        if session is None:
            raise HTTPException(status_code=404, detail="no such terminal")
        if session.attached:
            raise HTTPException(status_code=409, detail="this terminal is already shown")
        loop = asyncio.get_running_loop()

        async def events() -> AsyncIterator[dict[str, str]]:
            # Claimed HERE, when the stream actually starts, and not in the route above: a connection
            # that drops before the first iteration never runs this, the session stays unattached,
            # and its own timer closes it. Claiming earlier left a shell nobody could read.
            if not session.claim():
                # Lost the race (or the session closed): say so, so the client shows a reason and
                # does not sit on an empty stream. It must not close the session: the winner owns it.
                yield {"event": "error", "data": json.dumps({"message": "this terminal is already shown"})}
                return
            try:
                while True:
                    chunk = await loop.run_in_executor(None, session.read, _POLL_S)
                    if chunk is None:
                        yield {"event": "exit", "data": json.dumps({"code": session.exit_code})}
                        break
                    if chunk:
                        yield {"event": "data", "data": json.dumps({"text": chunk})}
            finally:
                # The stream going away (tab closed, page reloaded, window gone) ends the shell:
                # a terminal nobody can see must not keep a dev server alive behind it.
                manager().close_in_background(terminal_id)

        return EventSourceResponse(events())

    @app.post("/api/terminal/{terminal_id}/input", dependencies=[guard], response_model=TerminalOkOut)
    def terminal_input(terminal_id: str, body: TerminalInputIn, request: Request) -> dict[str, Any]:
        owner_only(request)
        session = manager().get(terminal_id)
        if session is None:
            raise HTTPException(status_code=404, detail="no such terminal")
        # Commands may have been turned off since the shell opened. Re-checked at most once a second
        # (the grant is a file read), and refused here rather than left to type into a shell the
        # folder's switch says may not run.
        now = time.monotonic()
        if now - session.checked_at > _RECHECK_S:
            why = folder_refusal(live(), session.cwd)
            if why:
                raise HTTPException(status_code=403, detail=why)
            session.checked_at = now
        try:
            session.write(body.data)
        except TerminalUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/terminal/{terminal_id}/resize", dependencies=[guard], response_model=TerminalOkOut)
    def terminal_resize(terminal_id: str, body: TerminalResizeIn, request: Request) -> dict[str, Any]:
        owner_only(request)
        session = manager().get(terminal_id)
        if session is None:
            raise HTTPException(status_code=404, detail="no such terminal")
        session.resize(body.cols, body.rows)
        return {"ok": True}

    @app.delete("/api/terminal/{terminal_id}", dependencies=[guard], response_model=TerminalOkOut)
    def terminal_close(terminal_id: str, request: Request) -> dict[str, Any]:
        owner_only(request)
        manager().close_in_background(terminal_id)
        return {"ok": True}
