"""Full control through the bridge: its switch, the record of what used it, and the screen's channel.

The owner's decision of 2026-10-18: through the MCP bridge, Claude Code may do everything in the
running app — approve, export, delete, apply, ask the model, and move around the app's screens —
except two things (`bridge_routes.FULL_CONTROL_NEVER`): credentials, and widening who can reach the
app. What the bridge then reaches is :data:`chimera.api.bridge_routes.FULL_CONTROL_ROUTES`; this
module holds what surrounds it.

**The switch is the screen's.** ``CHIMERA_BRIDGE_FULL_CONTROL`` is not in the Settings screen's
editable allowlist, so ``PATCH /api/config`` refuses it to every caller, and the bridge's
``settings.edit`` refuses it by name. It has one route, ``POST /api/security/bridge-full-control``,
which a bridged request may not call at all and which turns the switch ON only for a request that
carries the screen credential (`chimera/api/screen_credential.py`): the owner's bearer token alone is
``service.token``, a file any local process can read. Turning it OFF only narrows, so any owner
credential may do that — never the bridge itself, though: a client that could switch its own
authority could also switch it back.

**What used it is written down.** Every bridge call made on full control's authority leaves an
entry in the hash-chained audit log (`chimera/governance/audit.py`): ``via: "bridge"``,
``scope: "full_control"``, the route id, the status, and the ids the call named — never a body,
never a value. The Security screen lists the newest (:func:`recent_actions`).

**The screen's channel.** ``ui.navigate`` asks the running desktop to open a screen. The backend
checks the address against the app's own screens (:func:`parse_target` — an address inside the
app, never a URL), publishes it on :class:`UiEvents`, and the desktop, which listens on
``GET /api/ui/events`` while full control is on, moves its router there.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from chimera.api.bridge_routes import (
    FULL_CONTROL_SCOPE_KEY,
    FULL_CONTROL_SETTING,
    VIA_BRIDGE_SCOPE_KEY,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.api.desktop_bridge import DesktopBridge
    from chimera.config import Settings

_log = logging.getLogger(__name__)

#: The audit event a call made on full control's authority writes, and the one the switch writes.
ACTION_EVENT = "bridge.full_control"
SWITCH_EVENT = "bridge.full_control.switch"
#: How many of them the Security screen lists.
RECENT_LIMIT = 20

#: The app's screens a navigation may name: `VIEWS` in `apps/desktop/src/components/IconRail.tsx`,
#: without `maturity`, which a shipped build does not render. A test reads the TypeScript and holds
#: the two equal, so a screen added there cannot be unreachable here without a red test.
NAVIGATION_VIEWS: tuple[str, ...] = ("knowledge", "automation", "work", "code", "edit", "settings")
#: What a query key in an app address looks like (`tab`, `item`, `file`, `section`, ...).
_PARAM_KEY = re.compile(r"^[a-z][a-z_]{0,31}$")
_MAX_PARAMS = 12
_MAX_VALUE = 512
_MAX_TARGET = 2048
#: A navigation older than this is not replayed to a screen that connects late: "open Settings" asked
#: a minute ago, and acted on now, would move a screen the owner has since started using.
STALE_SECONDS = 30.0


# ---- navigation targets -------------------------------------------------------------------------


def parse_target(target: str) -> tuple[str, dict[str, str]]:
    """``(view, params)`` for an address inside the app, e.g. ``#/settings?tab=security``.

    Raises ``ValueError`` saying what is wrong. Only the app's own screens, only short plain query
    values: no scheme, no host, no path below a screen, nothing a webview could read as somewhere
    else. The desktop builds the hash again from what this returns (`formatHash`), so a value is
    only ever a query value there.
    """
    text = target.strip()
    if len(text) > _MAX_TARGET:
        raise ValueError("that address is too long")
    if not text.startswith("#/"):
        raise ValueError(
            "a target is an address inside the app, such as #/settings?tab=security; "
            "never a URL"
        )
    path, _, query = text[2:].partition("?")
    if "#" in path or "#" in query:
        raise ValueError("an address has one # at its start")
    if path not in NAVIGATION_VIEWS:
        raise ValueError(
            f"unknown screen {path!r}; the screens are: {', '.join(NAVIGATION_VIEWS)}"
        )
    pairs = parse_qsl(query, keep_blank_values=True)
    if len(pairs) > _MAX_PARAMS:
        raise ValueError(f"at most {_MAX_PARAMS} parameters")
    params: dict[str, str] = {}
    for key, value in pairs:
        if not _PARAM_KEY.match(key) or key in params:
            raise ValueError(f"bad parameter name {key!r}")
        if len(value) > _MAX_VALUE or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError(f"bad value for {key}")
        params[key] = value
    return path, params


def format_target(view: str, params: dict[str, str]) -> str:
    """The inverse of :func:`parse_target` — what the desktop's `formatHash` writes."""
    return f"#/{view}?{urlencode(params)}" if params else f"#/{view}"


# ---- the screen's channel -----------------------------------------------------------------------


class UiEvents:
    """What the backend asks the running screen to do, newest last, bounded. Thread-safe: a sync
    route publishes from the worker pool, the stream reads on the event loop."""

    def __init__(self, keep: int = 32) -> None:
        self._lock = threading.Lock()
        self._events: deque[dict[str, Any]] = deque(maxlen=keep)
        self._seq = 0
        self._listeners = 0

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    @property
    def listeners(self) -> int:
        """How many screens are listening right now."""
        with self._lock:
            return self._listeners

    def publish(self, event: str, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            item = {"seq": self._seq, "event": event, "data": data, "at": time.time()}
            self._events.append(item)
            return item

    def after(self, seq: int, now: float | None = None) -> list[dict[str, Any]]:
        """The events newer than ``seq`` and not stale."""
        moment = time.time() if now is None else now
        with self._lock:
            return [
                e for e in self._events if e["seq"] > seq and moment - e["at"] <= STALE_SECONDS
            ]

    def _join(self, delta: int) -> None:
        with self._lock:
            self._listeners += delta


async def ui_event_frames(
    bus: UiEvents,
    since: int,
    disconnected: Callable[[], Awaitable[bool]],
    *,
    poll: float = 0.25,
) -> AsyncIterator[dict[str, str]]:
    """The frames of one screen's stream: ``hello`` with where it starts, then each event.

    ``since`` below zero means "only what is published from now on" — a screen that just opened has
    nothing to catch up on. A reconnecting screen sends the last ``seq`` it saw and gets what it
    missed, unless that is already stale.
    """
    bus._join(1)
    try:
        last = bus.seq if since < 0 else since
        yield {"event": "hello", "data": json.dumps({"seq": last})}
        while not await disconnected():
            for item in bus.after(last):
                last = int(item["seq"])
                yield {"event": str(item["event"]), "data": json.dumps({**item["data"], "seq": last})}
            await asyncio.sleep(poll)
    finally:
        bus._join(-1)


# ---- the audit ----------------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def record_action(home: Path, route_id: str, status: int, ids: dict[str, Any]) -> None:
    """One audit entry for one call made on full control's authority: ids only, never content."""
    from chimera.governance.audit import AuditLog

    payload = {
        "via": "bridge",
        "scope": "full_control",
        "route": route_id,
        "status": int(status),
        "ids": {str(k): v for k, v in ids.items() if isinstance(v, str | int | bool | list)},
        "at": _now(),
    }
    try:
        AuditLog(Path(home) / "audit.jsonl").record(ACTION_EVENT, payload)
    except OSError as exc:
        # The call already happened; failing to write it down must say so in the log, loudly.
        _log.error("full-control audit entry for %s not recorded: %s", route_id, exc)


def recent_actions(home: Path, limit: int = RECENT_LIMIT) -> list[dict[str, Any]]:
    """The newest full-control entries of the audit log (actions and switches), newest first."""
    path = Path(home) / "audit.jsonl"
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        if len(out) >= limit:
            break
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") not in (ACTION_EVENT, SWITCH_EVENT):
            continue
        ids = entry.get("ids")
        out.append(
            {
                "event": str(entry.get("type")),
                "at": str(entry.get("at") or ""),
                "route": str(entry.get("route") or ""),
                "status": entry.get("status") if isinstance(entry.get("status"), int) else None,
                "ids": ids if isinstance(ids, dict) else {},
                "on": entry.get("on") if isinstance(entry.get("on"), bool) else None,
                "via": str(entry.get("via") or ""),
            }
        )
    return out


# ---- the routes ---------------------------------------------------------------------------------


class BridgeFullControlActionOut(BaseModel):
    event: str
    """``bridge.full_control`` for a call, ``bridge.full_control.switch`` for the switch."""
    at: str = ""
    route: str = ""
    status: int | None = None
    ids: dict[str, Any] = Field(default_factory=dict)
    on: bool | None = None
    via: str = ""


class BridgeFullControlOut(BaseModel):
    on: bool
    """The owner's switch as set."""
    available: bool
    """Whether the two switches it needs are on: Allow Claude, and Full control."""
    active: bool
    """Whether a bridge client holds it right now — the switch on, the bridge listening."""
    actions: list[BridgeFullControlActionOut]


class BridgeFullControlIn(BaseModel):
    model_config = {"extra": "forbid"}

    on: bool


class UiNavigateIn(BaseModel):
    model_config = {"extra": "forbid"}

    target: str = Field(max_length=_MAX_TARGET)


class UiNavigateOut(BaseModel):
    ok: bool
    target: str
    view: str
    screens: int
    """How many screens were listening when it was sent; 0 means nobody will move."""


def register_full_control_api(
    app: FastAPI,
    guard: Any,
    bridge: DesktopBridge,
    *,
    live_settings: Callable[[], Settings],
    screen_scope_key: str,
) -> UiEvents:
    """Mount the switch, the navigation route and the screen's event stream."""
    from chimera.api.sse import SSE_RESPONSE

    bus = UiEvents()
    app.state.ui_events = bus

    def status() -> dict[str, Any]:
        settings = live_settings()
        return {
            "on": bool(getattr(settings, "bridge_full_control", False)),
            "available": bool(settings.desktop_bridge) and bool(settings.desktop_bridge_full),
            "active": bridge.tier() == "full_control",
            "actions": recent_actions(Path(settings.home)),
        }

    @app.get(
        "/api/security/bridge-full-control",
        dependencies=[guard],
        response_model=BridgeFullControlOut,
    )
    def read_full_control() -> dict[str, Any]:
        return status()

    @app.post(
        "/api/security/bridge-full-control",
        dependencies=[guard],
        response_model=BridgeFullControlOut,
    )
    def switch_full_control(body: BridgeFullControlIn, request: Request) -> dict[str, Any]:
        """Turn full control through the bridge on (from the screen only) or off."""
        from chimera.api.key_vault import write_env_value
        from chimera.config import get_settings
        from chimera.governance.audit import AuditLog

        if request.scope.get(VIA_BRIDGE_SCOPE_KEY):
            raise HTTPException(
                status_code=403,
                detail="full control through the bridge is switched by the owner in the app, "
                "never through the bridge",
            )
        from_screen = bool(request.scope.get(screen_scope_key))
        if body.on and not from_screen:
            raise HTTPException(
                status_code=403,
                detail="full control through the bridge is turned on from the app's screen "
                "(Settings > Security)",
            )
        settings = live_settings()
        if body.on and not (settings.desktop_bridge and settings.desktop_bridge_full):
            raise HTTPException(
                status_code=409,
                detail="turn on 'Allow Claude to operate this app' and 'Full control' first",
            )
        value = "true" if body.on else "false"
        write_env_value(Path(".env"), FULL_CONTROL_SETTING, value)
        os.environ[FULL_CONTROL_SETTING] = value
        get_settings.cache_clear()
        bridge.sync()
        try:
            AuditLog(Path(live_settings().home) / "audit.jsonl").record(
                SWITCH_EVENT,
                {"on": body.on, "via": "screen" if from_screen else "owner", "at": _now()},
            )
        except OSError as exc:
            _log.error("full-control switch not recorded in the audit log: %s", exc)
        return status()

    @app.post("/api/ui/navigate", dependencies=[guard], response_model=UiNavigateOut)
    def navigate(body: UiNavigateIn, request: Request) -> dict[str, Any]:
        """Ask the running screen to open an address. Only the bridge, on full control, asks."""
        if not request.scope.get(FULL_CONTROL_SCOPE_KEY):
            # The second lock: the route is in no table below full control, and a request that did
            # not come through the bridge on full control's authority has no reason to be here.
            raise HTTPException(
                status_code=403,
                detail="opening a screen is asked through the bridge with full control on",
            )
        try:
            view, params = parse_target(body.target)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        target = format_target(view, params)
        screens = bus.listeners
        bus.publish("navigate", {"view": view, "params": params, "target": target})
        return {"ok": True, "target": target, "view": view, "screens": screens}

    @app.get("/api/ui/events", dependencies=[guard], responses=SSE_RESPONSE)
    async def ui_events(request: Request, since: int = -1) -> EventSourceResponse:
        """What the backend asks the screen to do (today: open an address). The desktop listens
        while full control through the bridge is on."""
        return EventSourceResponse(ui_event_frames(bus, since, request.is_disconnected), ping=15)

    return bus
