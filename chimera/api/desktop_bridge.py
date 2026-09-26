"""The desktop bridge: lets an MCP client on this machine operate the RUNNING desktop app.

```
Claude --stdio MCP--> `chimera mcp desktop` --HTTP 127.0.0.1 + bearer--> this app's /api/bridge/*
```

It is a door into the app, not a second engine. Every call names a route from
:data:`chimera.api.bridge_routes.ROUTES` and is served by the app's own handler, in-process — the
same code the screens call, under the same governance, writing the same files.

**Consent, in three pieces.**

* Two switches in Settings, both off by default (``CHIMERA_DESKTOP_BRIDGE``,
  ``CHIMERA_DESKTOP_BRIDGE_FULL``). The second means nothing without the first.
* A discovery file, written only while the first switch is on and the app is running, holding the
  URL and a fresh random token (:func:`bridge_path`). Turning the switch off, or stopping the app,
  deletes it and forgets the token — so a client that read it yesterday holds nothing today.
* The token is checked on every bridge request: 403 while the switch is off, 401 when it is wrong.
  The SPA never sees this router and is untouched by it.

**Why the file is not under ``CHIMERA_HOME``.** The desktop app keeps its data in the platform's app
data directory (``main.rs`` sets ``CHIMERA_HOME`` to it), and ``chimera mcp desktop`` is launched by
Claude with none of that environment. A path both can compute without being told is the only one
that works: ``~/.chimera/desktop-bridge.json``, overridable with ``CHIMERA_BRIDGE_DIR`` (which is
how the tests keep out of the real one).

**Who can read the file.** On POSIX it is created 0600 in a 0700 directory, before any byte is
written, so there is no moment where it is readable by others. On Windows the mode bits do nothing;
the file relies on living inside the user's profile, whose default ACL admits only the user, SYSTEM
and administrators. It is written atomically (temp file + rename) on both.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from chimera.api.bridge_routes import (
    BRIDGE_SETTINGS,
    ROUTES,
    BridgeRoute,
    Tier,
    full_only_keys_in,
    is_secret_file,
    is_secret_setting,
    scrub,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.config import Settings

_log = logging.getLogger(__name__)

BRIDGE_DIR_ENV = "CHIMERA_BRIDGE_DIR"
BRIDGE_FILE = "desktop-bridge.json"

#: The longest a single call may hold a request before it answers — a job keeps running after it.
MAX_WAIT_SECONDS = 300.0
DEFAULT_WAIT_SECONDS = 60.0
#: A non-streaming route that has not answered in this long is reported as timed out, not awaited.
PLAIN_CALL_TIMEOUT = 300.0
_MAX_JOBS = 64
_MAX_EVENTS = 400
_MAX_TEXT = 20_000
_PATH_PARAM = re.compile(r"^[^/\\?#%\x00-\x1f]{1,512}$")


# ---- the discovery file -------------------------------------------------------------------------


def bridge_path() -> Path:
    """Where the discovery file lives: ``$CHIMERA_BRIDGE_DIR`` or ``~/.chimera``."""
    override = os.environ.get(BRIDGE_DIR_ENV, "").strip()
    base = Path(override).expanduser() if override else Path.home() / ".chimera"
    return base / BRIDGE_FILE


def write_discovery(path: Path, payload: dict[str, Any]) -> None:
    """Write ``payload`` so only the current user can read it, and atomically."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    # The mode is given to `open`, not applied afterwards: a chmod after the write would leave a
    # window where the token sits in a file anyone on the machine can read.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def read_discovery(path: Path | None = None) -> dict[str, Any] | None:
    """The discovery file's contents, or None when it is absent or unreadable."""
    target = path or bridge_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("url") or not data.get("token"):
        return None
    return data


def _pid_alive(pid: int) -> bool | None:
    """Whether ``pid`` is running; None when it cannot be told.

    Not ``os.kill(pid, 0)``: on Windows signal 0 is CTRL_C_EVENT, and that call would interrupt a
    live app rather than ask about it.
    """
    try:
        import psutil
    except ImportError:
        return None
    return bool(psutil.pid_exists(pid))


# ---- the bridge's own state ---------------------------------------------------------------------


class DesktopBridge:
    """The switch, the token and the file, kept in agreement.

    ``settings`` is read on every :meth:`sync` rather than captured, so a save on the Settings
    screen (``PATCH /api/config`` clears the settings cache) applies without a restart.
    """

    def __init__(self, settings: Callable[[], Settings], path: Path | None = None) -> None:
        self._settings = settings
        self._path = path
        self.url: str | None = None
        self.token: str | None = None
        self.full = False

    @property
    def path(self) -> Path:
        return self._path or bridge_path()

    def attach(self, url: str) -> None:
        """Called once the server knows its port. Clears a file a crashed app left behind."""
        self.url = url
        self._clear_stale()
        self.sync()

    def sync(self) -> None:
        """Make the file and the token match the switches now."""
        settings = self._settings()
        enabled = bool(settings.desktop_bridge) and self.url is not None
        if not enabled:
            self.close()
            return
        full = bool(settings.desktop_bridge_full)
        if self.token is not None and full == self.full and self.path.exists():
            return
        from chimera import __version__

        self.token = self.token or secrets.token_urlsafe(32)
        self.full = full
        write_discovery(
            self.path,
            {
                "url": self.url,
                "token": self.token,
                "pid": os.getpid(),
                "version": __version__,
                "full": full,
            },
        )

    def close(self) -> None:
        """Forget the token and delete the file — only if it is ours, never another app's."""
        token, self.token, self.full = self.token, None, False
        if token is None:
            return
        on_disk = read_discovery(self.path)
        if on_disk is not None and hmac.compare_digest(str(on_disk["token"]), token):
            with contextlib.suppress(OSError):
                self.path.unlink()

    def _clear_stale(self) -> None:
        on_disk = read_discovery(self.path)
        if on_disk is None:
            return
        pid = on_disk.get("pid")
        same_port = on_disk.get("url") == self.url  # we hold the port: its old owner is gone
        dead = isinstance(pid, int) and pid != os.getpid() and _pid_alive(pid) is False
        if same_port or dead:
            with contextlib.suppress(OSError):
                self.path.unlink()

    def tier(self) -> Tier | None:
        """What a correctly authenticated caller may do right now; None while the switch is off."""
        if self.token is None:
            return None
        return "full" if self.full else "operate"

    def authorize(self, request: Request, need: Tier = "operate") -> Tier:
        """403 while off, 401 on a wrong token, 403 for a full-control route without full control."""
        tier = self.tier()
        if tier is None or self.token is None:
            raise HTTPException(
                status_code=403, detail="the desktop bridge is off (Settings > Allow Claude)"
            )
        header = request.headers.get("authorization", "")
        if not hmac.compare_digest(header.encode(), f"Bearer {self.token}".encode()):
            raise HTTPException(status_code=401, detail="unauthorized")
        if need == "full" and tier != "full":
            raise HTTPException(status_code=403, detail="this needs Full control in Settings")
        return tier


# ---- calling the app's own routes in-process ----------------------------------------------------


async def asgi_call(
    app: Any,
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    body: Any = None,
    headers: dict[str, str] | None = None,
    on_chunk: Callable[[bytes], None] | None = None,
) -> tuple[int, bytes]:
    """One request to ``app``, answered by its own handler; ``(status, body)``.

    A hand-written ASGI call rather than ``httpx.ASGITransport`` because that transport buffers the
    whole response, and a run's event stream has to be read as it happens — ``on_chunk`` gets each
    piece as the handler sends it. The request never reaches a socket.
    """
    payload = b"" if body is None else json.dumps(body).encode("utf-8")
    header_list = [(b"host", b"127.0.0.1"), (b"content-type", b"application/json")]
    header_list += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    header_list.append((b"content-length", str(len(payload)).encode()))
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": quote(path).encode("ascii"),
        "root_path": "",
        "query_string": urlencode(query or {}, doseq=True).encode("ascii"),
        "headers": header_list,
        "client": ("127.0.0.1", 0),
        "server": ("127.0.0.1", 0),
    }
    sent = False
    never = asyncio.Event()

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        # Nobody disconnects: a stream is read to its end, so the run it belongs to is never cut
        # short because the caller stopped waiting.
        await never.wait()
        return {"type": "http.disconnect"}  # pragma: no cover - unreachable

    status = 500
    chunks: list[bytes] = []

    async def send(message: dict[str, Any]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = int(message["status"])
        elif message["type"] == "http.response.body":
            chunk = bytes(message.get("body", b""))
            if chunk:
                if on_chunk is not None:
                    on_chunk(chunk)
                else:
                    chunks.append(chunk)

    await app(scope, receive, send)
    return status, b"".join(chunks)


class _SSEParser:
    """Server-sent events, fed a chunk at a time; comments (the keep-alive pings) are skipped."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        # Normalised on the whole buffer, so a "\r\n" split across two chunks still joins.
        self._buf = (self._buf + chunk).replace("\r\n", "\n")
        out: list[tuple[str, str]] = []
        while "\n\n" in self._buf:
            block, self._buf = self._buf.split("\n\n", 1)
            event, data = "message", []
            for line in block.split("\n"):
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:][1:] if line[5:].startswith(" ") else line[5:])
            if data:
                out.append((event, "\n".join(data)))
        return out


# ---- jobs: a streamed route, read in the background ---------------------------------------------


@dataclass
class BridgeJob:
    """One streamed call, run to its end whether or not anyone is still asking about it."""

    id: str
    route: str
    started: float = field(default_factory=time.monotonic)
    done: bool = False
    status: int = 0
    text: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    seq: int = 0
    final: Any = None
    error: str = ""
    session_id: str = ""
    turn_id: str = ""
    approvals: list[dict[str, Any]] = field(default_factory=list)
    blocked: bool = False
    """The last thing the stream said was an approval question: it is waiting for a person."""
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None

    def take(self, event: str, raw: str) -> None:
        try:
            data: Any = json.loads(raw)
        except ValueError:
            data = raw
        if event == "token":
            piece = data.get("text", "") if isinstance(data, dict) else str(data)
            self.text = (self.text + str(piece))[-_MAX_TEXT:]
            self.blocked = False
        elif event == "frame":
            return  # a screenshot for the screen; base64 JPEG is noise to a text client
        else:
            self.seq += 1
            self.events.append({"n": self.seq, "event": event, "data": data})
            del self.events[:-_MAX_EVENTS]
            self.blocked = event == "approval"
            if event == "approval" and isinstance(data, dict):
                self.approvals.append(data)
            elif event == "session" and isinstance(data, dict):
                self.session_id = str(data.get("session_id") or "")
                self.turn_id = str(data.get("turn_id") or "")
            elif event == "done":
                self.final = data
            elif event == "error":
                self.error = str(data.get("message", data) if isinstance(data, dict) else data)
        self.changed.set()


class BridgeJobs:
    """The jobs this app started for the bridge, newest last, bounded."""

    def __init__(self) -> None:
        self._jobs: dict[str, BridgeJob] = {}

    def get(self, job_id: str) -> BridgeJob | None:
        return self._jobs.get(job_id)

    def start(self, route: str, run: Callable[[BridgeJob], Awaitable[None]]) -> BridgeJob:
        job = BridgeJob(id=secrets.token_hex(8), route=route)
        self._jobs[job.id] = job
        while len(self._jobs) > _MAX_JOBS:
            oldest = next((j for j in self._jobs.values() if j.done), None)
            if oldest is None:
                break
            del self._jobs[oldest.id]

        async def wrapper() -> None:
            try:
                await run(job)
            except Exception as exc:  # noqa: BLE001 — a job's failure is its result, not a crash
                job.error = job.error or f"{type(exc).__name__}: {exc}"
            finally:
                job.done = True
                job.blocked = False
                job.changed.set()

        job.task = asyncio.get_running_loop().create_task(wrapper())
        return job

    @staticmethod
    async def wait(job: BridgeJob, seconds: float) -> None:
        """Until the job ends, stops for a person, or ``seconds`` pass — whichever is first.

        "Stops for a person" means a question asked WHILE waiting. A poll that starts on a job
        already parked on one waits for what happens next — the answer, the end — rather than
        returning at once with the same news it returned last time.
        """
        deadline = time.monotonic() + max(0.0, min(seconds, MAX_WAIT_SECONDS))
        start = job.seq
        while not job.done and not (job.blocked and job.seq != start):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            job.changed.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(job.changed.wait(), timeout=remaining)


# ---- the router ---------------------------------------------------------------------------------


class BridgeCallIn(BaseModel):
    """One call through the bridge: a route id from the table, never a URL."""

    model_config = {"extra": "forbid"}

    route: str
    params: dict[str, Any] = Field(default_factory=dict)
    """Values for the route's ``{placeholders}``; everything else becomes the query string."""
    body: Any = None
    wait_seconds: float | None = None
    """For a streamed route: how long to wait before answering with what has happened so far."""


class BridgeJobOut(BaseModel):
    job_id: str
    route: str
    done: bool
    waiting_for_approval: bool
    pending_approvals: list[dict[str, Any]]
    session_id: str
    turn_id: str
    text: str
    result: Any = None
    error: str = ""
    events: list[dict[str, Any]]
    next: int
    elapsed_seconds: float


class BridgeCallOut(BaseModel):
    route: str
    status: int
    data: Any = None
    job: BridgeJobOut | None = None


class BridgeStatusOut(BaseModel):
    app: str = "chimera-desktop"
    version: str
    tier: str
    default_model: str
    workspace: str
    last_project: str
    projects: int
    conversations: int
    spent_today_usd: float | None
    pending_approvals: int


def _job_out(job: BridgeJob, since: int, tokens: list[str]) -> dict[str, Any]:
    pending = (
        [
            {"id": a.get("id", ""), "action": a.get("action", ""), "reason": a.get("reason", "")}
            for a in job.approvals
        ]
        if job.blocked
        else []
    )
    return dict(
        scrub(
            {
                "job_id": job.id,
                "route": job.route,
                "done": job.done,
                "waiting_for_approval": job.blocked,
                "pending_approvals": pending,
                "session_id": job.session_id,
                "turn_id": job.turn_id,
                "text": job.text,
                "result": job.final,
                "error": job.error,
                "events": [e for e in job.events if e["n"] > since],
                "next": job.seq,
                "elapsed_seconds": round(time.monotonic() - job.started, 1),
            },
            tokens,
        )
    )


def _resolve_path(route: BridgeRoute, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The concrete path for ``route``, and the params left over for the query string."""
    names = re.findall(r"{(\w+)}", route.path)
    path = route.path
    for name in names:
        if name not in params:
            raise HTTPException(status_code=400, detail=f"missing parameter: {name}")
        value = str(params[name])
        # A value can never introduce a segment: no slash, no traversal, nothing the router would
        # read as the start of another route.
        if not _PATH_PARAM.match(value) or value in {".", ".."}:
            raise HTTPException(status_code=400, detail=f"bad value for {name}")
        path = path.replace("{" + name + "}", value)
    rest = {k: v for k, v in params.items() if k not in names}
    for key, value in rest.items():
        scalars = value if isinstance(value, list) else [value]
        if not all(isinstance(v, str | int | float | bool) for v in scalars):
            raise HTTPException(status_code=400, detail=f"bad query value for {key}")
    return path, rest


def register_bridge_api(
    app: FastAPI,
    bridge: DesktopBridge,
    *,
    live_settings: Callable[[], Settings],
    workspace: Path,
) -> None:
    """Mount ``/api/bridge/*`` — the only routes that accept the bridge token."""
    jobs = BridgeJobs()
    app.state.desktop_bridge = bridge
    app.state.bridge_jobs = jobs

    def internal_headers() -> dict[str, str]:
        # The app's own guard still applies to the forwarded call; a deployment with a server token
        # gets it from here, never from the client.
        token = live_settings().server_token
        return {"authorization": f"Bearer {token}"} if token else {}

    def hidden() -> list[str]:
        return [t for t in (bridge.token, live_settings().server_token) if t]

    async def plain(
        method: str, path: str, query: dict[str, Any] | None = None, body: Any = None
    ) -> tuple[int, Any]:
        status, raw = await asyncio.wait_for(
            asgi_call(app, method, path, query=query, body=body, headers=internal_headers()),
            timeout=PLAIN_CALL_TIMEOUT,
        )
        try:
            data: Any = json.loads(raw) if raw else None
        except ValueError:
            data = raw.decode("utf-8", errors="replace")[:_MAX_TEXT]
        return status, data

    def guard_places(route_id: str, query: dict[str, Any], body: Any) -> None:
        """Refuse a call aimed at the app's own data, the bridge file, or a credential file.

        At every tier. The app's data directory holds the approval questions and their answers
        (`home/approvals/<id>.answer.json`): a workspace pointed there would let a file write — or an
        agent told to write one — answer an approval without the switch that allows answering. A
        workspace that CONTAINS it is the same door one level up. And `.env` is the key the settings
        screen masks; reading it as a file would hand it over by a side door.
        """
        roots = [Path(live_settings().home), bridge_path().parent]
        roots = [r.expanduser().resolve() for r in roots]
        fields = dict(query)
        if isinstance(body, dict):
            fields.update({k: v for k, v in body.items() if k not in fields})
        places = [fields.get("workspace")]
        if route_id in {"projects.add", "projects.remove", "files.mkdir"}:
            places.append(fields.get("path"))
        for place in places:
            if not isinstance(place, str) or not place.strip():
                continue
            target = Path(place).expanduser().resolve()
            if any(target == r or r in target.parents or target in r.parents for r in roots):
                raise HTTPException(
                    status_code=403,
                    detail="that folder holds the app's own data; it is not reachable through the bridge",
                )
        files = [fields.get("path")] if route_id.startswith(("files.", "git.")) else []
        files += list(fields.get("paths") or []) if route_id.startswith("git.") else []
        if any(isinstance(f, str) and is_secret_file(f) for f in files):
            raise HTTPException(
                status_code=403, detail="credential files are not reachable through the bridge"
            )

    def police(route_id: str, route: BridgeRoute, body: Any, tier: Tier) -> Any:
        """Refuse what the caller's tier may not do; set the owner's posture on an operate run."""
        if route_id == "settings.edit":
            if not isinstance(body, dict) or not body:
                raise HTTPException(status_code=400, detail="body must be {ENV_NAME: value}")
            refused = sorted(k for k in body if is_secret_setting(str(k)) or k in BRIDGE_SETTINGS)
            if refused:
                raise HTTPException(
                    status_code=403,
                    detail=f"not editable through the bridge: {', '.join(refused)}",
                )
        if tier != "full":
            widening = full_only_keys_in(body)
            if widening:
                raise HTTPException(
                    status_code=403,
                    detail=f"needs Full control in Settings: {', '.join(widening)}",
                )
        # `not body.get`, not `"posture" not in body`: an explicit `null` posture means NO posture —
        # nothing denied, no pause — which is wider than any corner an owner could have chosen.
        if route.seams and isinstance(body, dict) and not body.get("posture"):
            # What the Code screen sends when the owner has changed nothing: the configured posture
            # (or the stock pair) and shell only where the owner granted shell everywhere. The server
            # applies the configured posture as a floor regardless; sending it keeps the posture line
            # honest about the run that is happening.
            settings = live_settings()
            reach = (
                settings.reach
                if settings.reach in {"read_only", "workspace", "workspace_shell"}
                else "workspace"
            )
            approval = (
                settings.approval
                if settings.approval in {"always", "suspicious", "never"}
                else "suspicious"
            )
            body = {**body, "posture": {"reach": reach, "approval": approval}}
            body.setdefault("allow_host_exec", reach == "workspace_shell")
        return body

    @app.get("/api/bridge/status", response_model=BridgeStatusOut, tags=["bridge"])
    async def bridge_status(request: Request) -> dict[str, Any]:
        tier = bridge.authorize(request)
        from chimera import __version__

        settings = live_settings()
        _, sessions = await plain("GET", "/api/code/sessions")
        _, projects = await plain("GET", "/api/code/workspaces")
        _, usage = await plain("GET", "/api/usage")
        _, approvals = await plain("GET", "/api/approvals")
        sessions = sessions if isinstance(sessions, list) else []
        today = datetime.now(UTC).date().isoformat()
        spent = None
        if isinstance(usage, dict):
            day = next((d for d in usage.get("by_day", []) if d.get("day") == today), None)
            spent = float(day["usd"]) if day else 0.0
        return dict(
            scrub(
                {
                    "version": __version__,
                    "tier": tier,
                    "default_model": settings.default_model,
                    "workspace": str(workspace),
                    "last_project": str(sessions[0].get("workspace", "")) if sessions else "",
                    "projects": len(projects) if isinstance(projects, list) else 0,
                    "conversations": len(sessions),
                    "spent_today_usd": spent,
                    "pending_approvals": len(approvals) if isinstance(approvals, list) else 0,
                },
                hidden(),
            )
        )

    @app.post("/api/bridge/call", response_model=BridgeCallOut, tags=["bridge"])
    async def bridge_call(req: BridgeCallIn, request: Request) -> dict[str, Any]:
        route = ROUTES.get(req.route)
        if route is None:
            # Checked after the token so an unauthenticated caller cannot map the table.
            bridge.authorize(request)
            raise HTTPException(status_code=404, detail=f"no such bridge route: {req.route}")
        tier = bridge.authorize(request, route.tier)
        path, query = _resolve_path(route, req.params)
        guard_places(req.route, query, req.body)
        body = police(req.route, route, req.body, tier)
        if not route.stream:
            try:
                status, data = await plain(route.method, path, query, body)
            except TimeoutError:
                return {"route": req.route, "status": 504, "data": "the app did not answer in time"}
            if req.route == "files.search" and isinstance(data, dict):
                # A search is a read of every file it matches: the same rule as `files.read`.
                hits = data.get("hits") or []
                data["hits"] = [h for h in hits if not is_secret_file(str(h.get("path", "")))]
            return {"route": req.route, "status": status, "data": scrub(data, hidden())}

        async def run(job: BridgeJob) -> None:
            parser = _SSEParser()

            def on_chunk(chunk: bytes) -> None:
                for event, raw in parser.feed(chunk.decode("utf-8", errors="replace")):
                    job.take(event, raw)

            job.status, _ = await asgi_call(
                app,
                route.method,
                path,
                query=query,
                body=body,
                headers=internal_headers(),
                on_chunk=on_chunk,
            )
            if job.status >= 400 and not job.error:
                job.error = f"HTTP {job.status}"

        job = jobs.start(req.route, run)
        wait = DEFAULT_WAIT_SECONDS if req.wait_seconds is None else req.wait_seconds
        await jobs.wait(job, wait)
        if job.blocked:
            await asyncio.sleep(0.2)  # let a question's sibling frames land with it
        return {"route": req.route, "status": 200, "job": _job_out(job, 0, hidden())}

    @app.get("/api/bridge/jobs/{job_id}", response_model=BridgeJobOut, tags=["bridge"])
    async def bridge_job(
        job_id: str, request: Request, since: int = 0, wait_seconds: float = 0.0
    ) -> dict[str, Any]:
        bridge.authorize(request)
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=404, detail="no such job (they are kept in memory only)"
            )
        await jobs.wait(job, wait_seconds)
        return _job_out(job, since, hidden())
