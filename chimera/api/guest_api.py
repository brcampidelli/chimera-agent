"""The guest's door, and the owner's controls for it.

Two apps, one trust boundary. The **guest app** (:func:`build_guest_app`) knows four routes, every
one behind a share token that opens exactly one conversation: read it, watch it live, say
something into it, see who else is there. It is mounted under ``/guest`` on the owner's own server
and it is the ONLY thing served when the owner opens the network door — so what a machine on the
LAN can reach is these four routes and nothing the owner's token protects.

The **owner's routes** (:func:`register_sharing_api`) mint and revoke tokens, watch a conversation
live themselves (how the owner's screen sees a turn a guest started), and open or close the
network listener. The listener is never open at launch: it is a decision made each time, on the
screen, because an app that answers the LAN because it did so last week is an app whose owner has
forgotten it does.

What a guest can do, once more, because the word suggests less than it is: send a message that
runs the agent in the owner's project with the owner's tools and the owner's spend. The seams a
guest turn runs under are the request defaults — the most cautious ones this surface has — and
the governance cards a turn raises are answered on the owner's screen alone: the guest app has no
route to answer one.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, params
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from chimera.api.sharing import SessionBus, Share, ShareStore, lan_addresses
from chimera.api.sse import SSE_RESPONSE
from chimera.telemetry import get_logger

_log = get_logger("api.guest")

#: The name a subscriber who gave none is listed under. Presence is for people, and "someone" is
#: what an unnamed window honestly is.
ANONYMOUS = "someone"
MAX_NAME = 40


def _clean_name(name: str | None) -> str:
    text = " ".join((name or "").split())[:MAX_NAME]
    return text or ANONYMOUS


# --- the shapes the owner's routes publish (the guest routes serve the same exchanges the owner's
# replay does, through the same models) ------------------------------------------------------


class ShareOut(BaseModel):
    token: str
    session_id: str
    created_at: float
    label: str = ""
    #: A link a guest can open, when the network door is open; otherwise None — a link that goes
    #: nowhere would be worse than no link.
    url: str | None = None


class SharesOut(BaseModel):
    shares: list[ShareOut]


class ShareIn(BaseModel):
    label: str = ""


class PresenceOut(BaseModel):
    names: list[str]


class NetworkShareIn(BaseModel):
    port: int = Field(default=0, ge=0, le=65535)


class NetworkShareOut(BaseModel):
    open: bool
    port: int | None = None
    #: Every address a machine on the LAN could use, best guess first. Shown, never guessed at
    #: silently: the owner reads their own network better than a heuristic does.
    urls: list[str] = Field(default_factory=list)


class GuestTurnIn(BaseModel):
    message: str
    name: str = ""


# --- the network door -----------------------------------------------------------------------


class GuestServer:
    """The LAN listener: one uvicorn server on its own thread, serving only the guest app.

    Bound and LISTENING before the thread starts, for the reason the app's own socket is
    (`_bind_app_socket`): a link handed out the moment this returns must connect on the first try.
    """

    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self._lock = threading.Lock()
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._port: int | None = None

    @property
    def open(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def port(self) -> int | None:
        return self._port if self.open else None

    def urls(self, token: str | None = None) -> list[str]:
        if not self.open or self._port is None:
            return []
        query = f"?t={token}" if token else ""
        return [f"http://{address}:{self._port}/{query}" for address in lan_addresses()]

    def start(self, port: int = 0, *, host: str = "0.0.0.0") -> int:
        """Open the door. Idempotent: an open listener stays as it is and reports its port."""
        import uvicorn

        with self._lock:
            if self.open and self._port is not None:
                return self._port
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind((host, port))
            except OSError:
                if port == 0:
                    sock.close()
                    raise
                sock.bind((host, 0))  # the asked-for port is taken: any free one, reported back
            sock.listen(128)
            bound = int(sock.getsockname()[1])
            server = uvicorn.Server(uvicorn.Config(self._app, log_level="warning"))
            thread = threading.Thread(
                target=server.run, kwargs={"sockets": [sock]}, name="chimera-guest", daemon=True
            )
            thread.start()
            self._server, self._thread, self._port = server, thread, bound
            return bound

    def stop(self) -> None:
        with self._lock:
            server, thread = self._server, self._thread
            self._server = self._thread = None
            self._port = None
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=5)


# --- the guest app ----------------------------------------------------------------------------


def build_guest_app(
    *,
    store: ShareStore,
    bus: SessionBus,
    session_view: Callable[[str], dict[str, Any] | None],
    session_workspace: Callable[[str], str],
    start_turn: Callable[..., Any],
) -> FastAPI:
    """The four routes a share token opens. ``start_turn`` is the coding turn itself, handed in
    from ``register_code_api`` so a guest's message runs through exactly the machinery the owner's
    does — one turn, one receipt shape, one bus."""
    guest = FastAPI(title="Chimera — shared conversation", docs_url=None, redoc_url=None)

    def share_of(request: Request) -> Share:
        header = request.headers.get("authorization", "")
        token = header[len("Bearer ") :] if header.startswith("Bearer ") else ""
        # `EventSource` cannot set a header, so the live stream takes the token in the query.
        token = token or str(request.query_params.get("t") or "")
        share = store.resolve(token)
        if share is None:
            raise HTTPException(status_code=401, detail="this link no longer opens a conversation")
        return share

    # One dependency object, built once — the shape ruff asks for (B008) and the one `guard` has.
    opened = Depends(share_of)

    @guest.get("/api/session")
    def session(share: Share = opened) -> dict[str, Any]:
        view = session_view(share.session_id)
        if view is None:
            raise HTTPException(status_code=404, detail="the conversation is gone")
        # The folder's NAME, not its path: a guest is in the conversation, not on the owner's disk.
        folder = Path(session_workspace(share.session_id)).name
        return {
            "session_id": share.session_id,
            "workspace_name": folder,
            "exchanges": view.get("exchanges", []),
            "presence": bus.presence(share.session_id),
            "seq": bus.seq(share.session_id),
        }

    @guest.get("/api/presence", response_model=PresenceOut)
    def presence(share: Share = opened) -> dict[str, Any]:
        return {"names": bus.presence(share.session_id)}

    @guest.get("/api/live", responses=SSE_RESPONSE)
    async def live(
        request: Request, since: int = 0, name: str = "", share: Share = opened
    ) -> EventSourceResponse:
        return live_stream(bus, share.session_id, since=since, name=_clean_name(name), request=request)

    @guest.post("/api/turn", responses=SSE_RESPONSE)
    async def turn(body: GuestTurnIn, share: Share = opened) -> Any:
        if not body.message.strip():
            raise HTTPException(status_code=400, detail="an empty message")
        from chimera.api.code_api import CodeTurnRequest

        req = CodeTurnRequest(
            message=body.message,
            session_id=share.session_id,
            workspace=session_workspace(share.session_id) or None,
        )
        return await start_turn(req, author=_clean_name(body.name))

    return guest


async def live_frames(
    bus: SessionBus,
    session_id: str,
    *,
    since: int,
    name: str,
    disconnected: Callable[[], Awaitable[bool]],
    heartbeat_seconds: float = 15,
) -> AsyncIterator[dict[str, str]]:
    """Replay what the viewer missed, then everything as it happens, until they leave.

    One implementation for the guest and the owner: the frames are the same, the presence list is
    the same, and the only difference between the two is which door they came through. Subscribed
    for exactly as long as the iteration runs — the presence list is who is iterating.
    """
    loop = asyncio.get_running_loop()
    sub = bus.subscribe(session_id, name=name, loop=loop)
    try:
        for frame in bus.replay(session_id, since):
            yield {"event": frame["event"], "data": json.dumps(frame)}
        while True:
            if await disconnected():
                break
            try:
                item = await asyncio.wait_for(sub.queue.get(), timeout=heartbeat_seconds)
            except TimeoutError:
                # A heartbeat, so a proxy between the two never decides the stream is dead.
                yield {"event": "heartbeat", "data": "{}"}
                continue
            if item is None:
                break
            yield {"event": item["event"], "data": json.dumps(item)}
    finally:
        bus.unsubscribe(session_id, sub.id)


def live_stream(
    bus: SessionBus, session_id: str, *, since: int, name: str, request: Request
) -> EventSourceResponse:
    return EventSourceResponse(
        live_frames(bus, session_id, since=since, name=name, disconnected=request.is_disconnected)
    )


# --- the owner's routes -----------------------------------------------------------------------


def register_sharing_api(
    app: FastAPI,
    guard: params.Depends,
    *,
    store: ShareStore,
    bus: SessionBus,
    guest: FastAPI,
    session_exists: Callable[[str], bool],
    server: GuestServer | None = None,
) -> GuestServer:
    """Mount the owner's sharing routes and the guest app under ``/guest``; return the network
    listener so the app can close it on shutdown."""
    door = server or GuestServer(guest)
    app.mount("/guest", guest)

    def _out(share: Share) -> dict[str, Any]:
        urls = door.urls(share.token)
        return {
            "token": share.token,
            "session_id": share.session_id,
            "created_at": share.created_at,
            "label": share.label,
            "url": urls[0] if urls else None,
        }

    @app.post("/api/code/sessions/{session_id}/share", dependencies=[guard], response_model=ShareOut)
    def share_session(session_id: str, body: ShareIn | None = None) -> dict[str, Any]:
        """A new token for this conversation. One per person, so one can be revoked alone."""
        if not session_exists(session_id):
            raise HTTPException(status_code=404, detail="no such conversation")
        return _out(store.mint(session_id, label=(body.label if body else "")))

    @app.get("/api/code/sessions/{session_id}/shares", dependencies=[guard], response_model=SharesOut)
    def list_shares(session_id: str) -> dict[str, Any]:
        return {"shares": [_out(s) for s in store.for_session(session_id)]}

    @app.delete("/api/code/sessions/{session_id}/shares/{token}", dependencies=[guard])
    def revoke_share(session_id: str, token: str) -> dict[str, bool]:
        share = store.resolve(token)
        if share is None or share.session_id != session_id:
            return {"ok": False}
        return {"ok": store.revoke(token)}

    @app.get("/api/code/sessions/{session_id}/presence", dependencies=[guard], response_model=PresenceOut)
    def session_presence(session_id: str) -> dict[str, Any]:
        return {"names": bus.presence(session_id)}

    @app.get("/api/code/sessions/{session_id}/live", dependencies=[guard], responses=SSE_RESPONSE)
    async def session_live(
        request: Request, session_id: str, since: int = 0, name: str = "owner"
    ) -> EventSourceResponse:
        """The owner's own window on a conversation: every frame of every turn, whoever started it."""
        return live_stream(bus, session_id, since=since, name=_clean_name(name), request=request)

    @app.get("/api/code/share/network", dependencies=[guard], response_model=NetworkShareOut)
    def network_state() -> dict[str, Any]:
        return {"open": door.open, "port": door.port, "urls": door.urls()}

    @app.post("/api/code/share/network", dependencies=[guard], response_model=NetworkShareOut)
    def network_open(body: NetworkShareIn | None = None) -> dict[str, Any]:
        """Open the LAN door: the guest app, and only it, on every interface."""
        try:
            door.start(body.port if body else 0)
        except OSError as exc:
            raise HTTPException(status_code=409, detail=f"could not listen: {exc}") from exc
        _log.info("guest listener open on port %s", door.port)
        return {"open": door.open, "port": door.port, "urls": door.urls()}

    @app.delete("/api/code/share/network", dependencies=[guard], response_model=NetworkShareOut)
    def network_close() -> dict[str, Any]:
        door.stop()
        return {"open": False, "port": None, "urls": []}

    return door
