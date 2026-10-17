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

# The discovery file is read by `chimera mcp desktop`, which runs with the `mcp` extra only and so
# has no FastAPI. Its helpers live in a stdlib-only module and are re-exported here unchanged.
from chimera.api.bridge_discovery import (
    bridge_path,
    read_discovery,
    write_discovery,
)
from chimera.api.bridge_routes import (
    DESCRIBE_ONLY_ROUTES,
    OWNER_DECISION_ROUTES,
    OWNER_ONLY_SETTINGS,
    ROUTES,
    SUGGESTABLE_SETTINGS,
    VIA_BRIDGE_SCOPE_KEY,
    BridgeRoute,
    Tier,
    is_secret_file,
    is_secret_setting,
    model_choices_in,
    scrub,
    switches_off,
    wider_than,
    without_held_text,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.config import Settings

_log = logging.getLogger(__name__)

#: The longest a single call may hold a request before it answers — a job keeps running after it.
MAX_WAIT_SECONDS = 300.0
DEFAULT_WAIT_SECONDS = 60.0
#: A non-streaming route that has not answered in this long is reported as timed out, not awaited.
PLAIN_CALL_TIMEOUT = 300.0
_MAX_JOBS = 64
_MAX_EVENTS = 400
_MAX_TEXT = 20_000
_PATH_PARAM = re.compile(r"^[^/\\?#%\x00-\x1f]{1,512}$")


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

    def detach(self) -> None:
        """No window any more (a background service whose last window went, S1): delete the file
        and forget the port, so a later :meth:`sync` — a Settings save — cannot publish it again
        until :meth:`attach` is called for the next window."""
        self.close()
        self.url = None

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
        self._publish(self.token or secrets.token_urlsafe(32), full)

    def _publish(self, token: str, full: bool) -> None:
        from chimera import __version__

        self.token = token
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

    def rotate(self) -> bool:
        """A new token, written over the old one; False while the bridge is off (nothing to rotate).

        Until this existed the token changed only when the switch went off and on again, which also
        drops the full-control choice for a moment. The old token stops working the moment this
        returns: :meth:`authorize` compares against the new one, and the discovery file a client
        reads next holds only the new one.
        """
        if self.token is None:
            return False
        settings = self._settings()
        if not settings.desktop_bridge or self.url is None:
            self.close()  # switched off since the last sync: there is no token to hand out
            return False
        self._publish(secrets.token_urlsafe(32), bool(settings.desktop_bridge_full))
        return True

    def hint(self) -> str:
        """The last four characters of the live token, or "" while off — never more of it."""
        return f"…{self.token[-4:]}" if self.token else ""

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
        # Every request this function makes is the bridge's, and the app's handlers can tell: a
        # settings suggestion the bridge made must never be approved by a request the bridge
        # forwards (`POST /api/approvals/{id}` reads this). In the scope rather than a header,
        # because no request that arrives over a socket can put a key here.
        VIA_BRIDGE_SCOPE_KEY: True,
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
    workspace: str = ""
    """Where a turn opened with ``project`` landed (the first frame says it); "" otherwise."""
    branch: str = ""
    approvals: list[dict[str, Any]] = field(default_factory=list)
    blocked: bool = False
    """The last thing the stream said was an approval question: it is waiting for a person."""
    folder_wait: bool = False
    """The turn said it is waiting for its folder, and has said nothing else since."""
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
            self.folder_wait = False
        elif event == "frame":
            return  # a screenshot for the screen; base64 JPEG is noise to a text client
        else:
            self.seq += 1
            self.events.append({"n": self.seq, "event": event, "data": data})
            del self.events[:-_MAX_EVENTS]
            self.blocked = event == "approval"
            # The coding turn's "folder_busy" notice: queued behind another conversation or run in
            # the same folder. Anything else it says after that means it got the folder.
            self.folder_wait = (
                event == "notice" and isinstance(data, dict) and data.get("code") == "folder_busy"
            )
            if event == "approval" and isinstance(data, dict):
                self.approvals.append(data)
            elif event == "session" and isinstance(data, dict):
                self.session_id = str(data.get("session_id") or "")
                self.turn_id = str(data.get("turn_id") or "")
                self.workspace = str(data.get("workspace") or "")
                self.branch = str(data.get("branch") or "")
            elif event == "done":
                self.final = data
            elif event == "error":
                self.error = str(data.get("message", data) if isinstance(data, dict) else data)
        self.changed.set()

    @property
    def ended(self) -> bool:
        """The turn said how it ended — an answer or an error — whether or not the stream closed.

        `done` is set only when the stream closes, and the app closes it after the turn's
        bookkeeping, not at its last word. Read alone, a turn that had failed showed as "still
        running" for as long as the stream stayed open (observed 2026-10-09: a failure at ~15:43
        read as running for 90 minutes; plan M1).
        """
        return self.done or bool(self.error) or self.final is not None


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
    workspace: str = ""
    """The folder a turn opened with ``project`` runs in — the worktree Chimera made, with
    ``worktree: "new"``. Send it back with ``session_id`` to continue there."""
    branch: str = ""
    error: str = ""
    events: list[dict[str, Any]]
    next: int
    elapsed_seconds: float
    ended: bool = False
    waiting_for_folder: bool = False
    turn_running: bool | None = None


class BridgeJobStopOut(BaseModel):
    job_id: str
    turn_id: str
    stopping: bool


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


def _still_waiting(
    job: BridgeJob, is_waiting: Callable[[str], bool], now: float
) -> list[dict[str, Any]]:
    """The questions this job asked that are still waiting, each once, with the time silence leaves.

    ``job.approvals`` is every question the stream ever announced; the stream never says when one
    is answered or refused by silence, so a list built from it alone kept every answered question
    of a long run (2026-10-10: ~20 of them, while the queue on disk was empty). ``is_waiting`` reads
    the queue — the same files ``GET /api/approvals`` lists.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in job.approvals:
        qid = str(a.get("id") or "")
        if not qid or qid in seen or not is_waiting(qid):
            continue
        seen.add(qid)
        item: dict[str, Any] = {
            "id": qid,
            "action": a.get("action", ""),
            "reason": a.get("reason", ""),
        }
        asked, wait = a.get("asked_at"), a.get("wait_seconds")
        if isinstance(asked, (int, float)) and isinstance(wait, (int, float)):
            # Silence refuses it at the end of its wait (`pending.ask_durably`): a client polling
            # the job can tell a question with minutes left from one about to be refused.
            item["seconds_left"] = max(0, round(float(asked) + float(wait) - now))
        out.append(item)
    return out


def _job_out(
    job: BridgeJob,
    since: int,
    tokens: list[str],
    is_waiting: Callable[[str], bool],
    turn_running: Callable[[str], bool | None] = lambda _turn_id: None,
) -> dict[str, Any]:
    pending = [] if job.done else _still_waiting(job, is_waiting, time.time())
    return dict(
        scrub(
            {
                "job_id": job.id,
                "route": job.route,
                "done": job.done,
                # From the queue, not from the last frame: a question answered or refused by silence
                # leaves no frame behind it, and one still waiting is still waiting whatever a
                # parallel tool call printed after it.
                "waiting_for_approval": bool(pending),
                "pending_approvals": pending,
                "session_id": job.session_id,
                "turn_id": job.turn_id,
                "workspace": job.workspace,
                "branch": job.branch,
                "text": job.text,
                "result": job.final,
                "error": job.error,
                "events": [e for e in job.events if e["n"] > since],
                "next": job.seq,
                "elapsed_seconds": round(time.monotonic() - job.started, 1),
                "ended": job.ended,
                "waiting_for_folder": job.folder_wait and not job.ended,
                # What the app's own registry says, beside what the stream said: the two disagree
                # exactly when a stream outlives its turn, and then this is the one to believe.
                "turn_running": turn_running(job.turn_id) if job.turn_id and not job.done else None,
            },
            tokens,
        )
    )


#: How much of a refused streamed call's body is kept to read its `detail` from.
_ERROR_HEAD = 4096


def _detail_of(raw: bytes) -> str:
    """``": <detail>"`` from a FastAPI error body, or "" when there is none to read."""
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return ""
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, str) and detail:
        return f": {detail[:_MAX_TEXT]}"
    if isinstance(detail, list) and detail:
        return f": {json.dumps(detail, ensure_ascii=False)[:1000]}"
    return ""


#: Routes that start or schedule work and carry no `CodeSeams` (see `guard_places`).
_UNSEAMED_STARTS = frozenset(
    {
        "chat.send",
        "kanban.run",
        "orchestration.hierarchy",
        "cron.create",
        "spec_projects.create",
        "spec_projects.step",
    }
)

#: Body and query fields that name a place on disk, at any depth.
#: ``project`` too: a turn opened in a project runs there (study 03, A5), and the worktree listing
#: reads one; a device spelling of it is the same door as one of a workspace.
_PATH_FIELDS = frozenset({"workspace", "path", "paths", "cwd", "project"})


def _patch_without(patch: str, hidden: Callable[[str], bool]) -> str:
    """``patch`` without the per-file sections whose path ``hidden`` names (`diff --git a/X b/Y`)."""
    kept: list[str] = []
    skip = False
    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git "):
            names = re.findall(r"(?:^| )[ab]/(\S+)", line[len("diff --git ") :])
            skip = any(hidden(name) for name in names)
        if not skip:
            kept.append(line)
    return "".join(kept)


def _named_fields(node: Any) -> list[tuple[str, Any]]:
    """Every ``(key, value)`` of every dict in ``node``, however deep."""
    found: list[tuple[str, Any]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.append((str(key), value))
            found += _named_fields(value)
    elif isinstance(node, list):
        for item in node:
            found += _named_fields(item)
    return found


def _path_texts(node: Any) -> list[str]:
    """Every string carried under a path-naming field of ``node``, however deep."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _PATH_FIELDS:
                values = value if isinstance(value, list) else [value]
                found += [v for v in values if isinstance(v, str)]
            found += _path_texts(value)
    elif isinstance(node, list):
        for item in node:
            found += _path_texts(item)
    return found


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

    def waiting(request_id: str) -> bool:
        from chimera.governance.pending import is_waiting

        return is_waiting(Path(live_settings().home), request_id)

    def turn_running(turn_id: str) -> bool | None:
        """Whether the app's registry of coding turns still lists ``turn_id``; None without one."""
        live = getattr(app.state, "live_turns", None)
        return None if live is None else live.get(turn_id) is not None

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

    def guard_places(
        route_id: str, query: dict[str, Any], body: Any, params: dict[str, Any] | None = None
    ) -> None:
        """Refuse a call aimed at the app's own data, the bridge file, or a credential file.

        At every tier. The app's data directory holds the approval questions and their answers
        (`home/approvals/<id>.answer.json`): a workspace pointed there would let a file write — or an
        agent told to write one — answer an approval without the switch that allows answering. A
        workspace that CONTAINS it is the same door one level up. And `.env` is the key the settings
        screen masks; reading it as a file would hand it over by a side door.
        """
        from chimera.core.own_files import (
            contains,
            is_device_or_unc,
            is_own_env,
            normal_name,
            own_env_file,
            within,
        )

        roots = [Path(live_settings().home).expanduser(), bridge_path().parent.expanduser()]
        fields = dict(query)
        if isinstance(body, dict):
            fields.update({k: v for k, v in body.items() if k not in fields})
        # Every path-shaped value, at any depth (a batch carries one workspace per task). A device
        # or network spelling — `\\?\C:\…`, `\\localhost\C$\…` — is refused before any comparison:
        # `Path.resolve` keeps the prefix, so it compared unequal to the very folder it names, and
        # the review of 2026-10-04 answered an approval through `\\?\<home>` at the operate tier.
        for text in _path_texts(query) + _path_texts(body):
            if is_device_or_unc(text):
                raise HTTPException(
                    status_code=403,
                    detail="device and network paths (\\\\?\\, \\\\.\\, \\\\server\\share) are not "
                    "reachable through the bridge",
                )
        own_env = own_env_file()

        def holds_chimeras_files(folder: Path) -> bool:
            """Whether ``folder`` is or contains Chimera's own `.env` or its data folder."""
            return contains(folder, own_env) or any(contains(folder, r) for r in roots)

        refusal = HTTPException(
            status_code=403,
            detail="that folder holds the app's own data or its .env; it is not reachable through "
            "the bridge",
        )
        # Every workspace the call names, at any depth (a batch names one per task): inside the
        # data folder, or holding it or Chimera's `.env`. Holding the `.env` was let through until
        # the final review of 2026-10-04: `git.init` on the install folder ran `git add -A` and
        # committed it, and `git.diff` then returned its changed lines.
        places = [v for k, v in _named_fields(query) + _named_fields(body) if k == "workspace"]
        if route_id in {"projects.add", "projects.remove", "files.mkdir"}:
            places.append(fields.get("path"))
        named_workspace = any(isinstance(p, str) and p.strip() for p in places)
        # Where a new worktree would be checked out: held to "not inside the data folder" only. The
        # folder worktrees go in may well CONTAIN the data folder (temp, on a machine whose home
        # lives there); the turn works in its own `chimera-wt-*` subfolder, not in the parent.
        worktree_parents: list[str] = []
        if route_id == "conversations.send":
            # A turn opened IN A PROJECT runs in the project, or in a worktree Chimera makes of it
            # (study 03, A5). The project is held to the rule a named workspace is — the app refuses
            # an unknown project on its side; this side refuses a known one that is in or holds
            # Chimera's data or `.env`.
            project = fields.get("project")
            if isinstance(project, str) and project.strip():
                places.append(project)
                if fields.get("worktree") == "new":
                    from chimera.core.checkout import checkout_of
                    from chimera.core.worktree import durable_worktree_parent

                    found = checkout_of(Path(project).expanduser())
                    repo = Path(found.project_root) if found is not None else Path(project)
                    target_dir = durable_worktree_parent(repo)
                    if target_dir is not None:
                        worktree_parents.append(str(target_dir))
        # A turn that continues a conversation without naming a folder runs in the conversation's
        # stored folder (`code_turn`), so that folder is held to the same rule as a named one —
        # otherwise a session started in the app's install folder in the app itself could be
        # driven from here by its id alone.
        sid = fields.get("session_id")
        if (
            route_id == "conversations.send"
            and not named_workspace
            and isinstance(sid, str)
            and sid.strip()
        ):
            from chimera.core.code_session import CodeSessionStore

            with contextlib.suppress(ValueError):
                stored = CodeSessionStore(
                    Path(live_settings().home).expanduser() / "code_sessions"
                ).stored_workspace(sid)
                if stored:
                    places.append(stored)
        # Guidance steers a turn already running, by its id alone — the same door as continuing a
        # conversation by its id, so the turn's folder is held to the same rule. A turn started in
        # the app in its install folder must not be driven from here one message at a time.
        tid = (params or {}).get("turn_id")
        if route_id == "conversations.guidance" and isinstance(tid, str):
            turns = getattr(app.state, "live_turns", None)
            turn = turns.get(tid) if turns is not None else None
            if turn is not None and turn.workspace:
                places.append(turn.workspace)
        for place in places:
            if not isinstance(place, str) or not place.strip():
                continue
            target = Path(place).expanduser()
            # By file identity (`own_files`), both ways: a workspace inside the data, or holding it.
            if any(within(target, r) for r in roots) or holds_chimeras_files(target):
                raise refusal
        for parent in worktree_parents:
            if any(within(Path(parent).expanduser(), r) for r in roots):
                raise HTTPException(
                    status_code=403,
                    detail="the folder Chimera makes worktrees in (CHIMERA_WORKTREE_DIR) is inside "
                    "the app's own data; a worktree there is not reachable through the bridge",
                )
        explicit_ws = fields.get("workspace")
        effective = Path(
            explicit_ws if isinstance(explicit_ws, str) and explicit_ws.strip() else workspace
        ).expanduser()
        # Git works on the whole tree whatever the path: `init` adds everything, `status` lists what
        # is untracked, `revert` cleans. So the app's OWN workspace is held to the rule too when the
        # call names none — an app started in its install folder holds the `.env`.
        if route_id.startswith("git.") and holds_chimeras_files(effective):
            raise refusal
        # The routes that start or schedule work WITHOUT the run seams, so without the per-run
        # `hide_own_env` the seams carry: a chat, a board run, a hierarchy, a cron job, a spec
        # project. Their tools get no approver for paths outside the folder, so keeping the folder
        # clear of Chimera's files keeps the file out of their reach (owner's decision, 2026-10-04).
        if route_id in _UNSEAMED_STARTS and holds_chimeras_files(effective):
            raise refusal
        files = [fields.get("path")] if route_id.startswith(("files.", "git.")) else []
        files += list(fields.get("paths") or []) if route_id.startswith("git.") else []
        if any(isinstance(f, str) and is_secret_file(f) for f in files):
            raise HTTPException(
                status_code=403, detail="credential files are not reachable through the bridge"
            )
        if route_id.startswith("git."):
            # A path that CONTAINS Chimera's files is them too: `git.revert {paths: ["."]}` ran
            # `git clean -fd -- .` over the data folder, and `git.commit` of "." committed the .env.
            for f in files:
                if isinstance(f, str) and f.strip() and holds_chimeras_files(effective / f):
                    raise refusal
        # The FILE, not only the workspace. A call that names no workspace runs in the app's own,
        # and when that folder contains the data directory (an app started from the home folder,
        # the test suite's temporary folder) a plain `files.write` of
        # `<home>/approvals/<id>.answer.json` answered an approval with no workspace field for the
        # check above to see. Resolved the way the route resolves it: an absolute path stands on its
        # own, a relative one is read inside the workspace the call runs in.
        explicit = fields.get("workspace")
        base = Path(explicit if isinstance(explicit, str) and explicit.strip() else workspace)
        for f in files:
            if not isinstance(f, str) or not f.strip():
                continue
            target = base.expanduser() / f
            try:
                opened = normal_name(target.resolve().name)
            except (OSError, ValueError):
                opened = ""
            # The name the path OPENS, not the text: an 8.3 short name (`ENV~1`) resolves to `.env`
            # on a file system that keeps them, and no pattern over the typed text could see that.
            # And Chimera's own `.env` by identity, whatever it is called on the way.
            if (opened and is_secret_file(opened)) or is_own_env(target):
                raise HTTPException(
                    status_code=403,
                    detail="credential files are not reachable through the bridge",
                )
            if any(within(target, r) for r in roots):
                raise HTTPException(
                    status_code=403,
                    detail="that file is in the app's own data folder; it is not reachable "
                    "through the bridge",
                )

    def hidden_place(query: dict[str, Any], body: Any, path: str) -> bool:
        """Whether a path a listing or a search returned is Chimera's `.env` or in its data folder.

        Resolved the way the route resolved it: inside the workspace the call named, else the app's.
        """
        from chimera.core.own_files import is_own_env, within

        if not path:
            return False
        fields = dict(query)
        if isinstance(body, dict):
            fields.update({k: v for k, v in body.items() if k not in fields})
        explicit = fields.get("workspace")
        base = Path(explicit if isinstance(explicit, str) and explicit.strip() else workspace)
        target = base.expanduser() / path
        roots = [Path(live_settings().home).expanduser(), bridge_path().parent.expanduser()]
        return is_own_env(target) or any(within(target, r) for r in roots)

    def police(
        route_id: str,
        route: BridgeRoute,
        body: Any,
        tier: Tier,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Refuse what no tier may do, or a widening; give a run that names no posture the owner's."""
        if route_id == "settings.edit":
            if not isinstance(body, dict) or not body:
                raise HTTPException(status_code=400, detail="body must be {ENV_NAME: value}")
            refused = sorted(
                k for k in body if is_secret_setting(str(k)) or k in OWNER_ONLY_SETTINGS
            )
            if refused:
                raise HTTPException(
                    status_code=403,
                    detail=f"not editable through the bridge: {', '.join(refused)}",
                )
            suggestable = sorted(k for k in body if k in SUGGESTABLE_SETTINGS)
            if suggestable and len(suggestable) != len(body):
                # Refused whole, like a body mixing an owner-only key: half a body written and half
                # turned into a card would leave the client unsure which of its edits happened.
                raise HTTPException(
                    status_code=403,
                    detail="only suggested to the owner through the bridge, never written: "
                    f"{', '.join(suggestable)}; send them alone to suggest them, and the other "
                    "settings in a separate call",
                )
        if route_id == "approve.approval":
            from chimera.governance.setting_suggestions import is_suggestion

            request_id = str((params or {}).get("request_id") or "")
            if is_suggestion(Path(live_settings().home), request_id):
                # The first of two locks on the same door. The app's answering route refuses a
                # forwarded request for a suggestion too (it reads the bridge's scope mark), so a
                # route added here later that reaches it some other way still meets the second.
                raise HTTPException(
                    status_code=403,
                    detail="a settings suggestion is answered by the owner in the app, "
                    "never through the bridge",
                )
        if route_id == "skills.bundle_status" and not switches_off(body):
            # A bundle switched on puts a stranger's text into every run's prompt, on every
            # surface — the same standing as a learned card the owner approves, and approving one
            # is Full (`approve.skill`). Switching one OFF only narrows, so the operate tier keeps
            # this route for that and nothing else. Switching ON is `approve.skill_bundle`, a Full
            # route in the `approve` area: the MCP server holds a whole area to one tier, so an
            # approval let through here at Full would sit inside `desktop_skills`, a tool the
            # operate tier lists — refused for every tier, not only below Full.
            raise HTTPException(
                status_code=403,
                detail='skills.bundle_status only switches a bundle off ({"status": "inactive"}); '
                "switching one on is approve.skill_bundle, which needs Full control in Settings",
            )
        if route_id not in DESCRIBE_ONLY_ROUTES:
            # Every tier, Full control included (the owner's decisions of 2026-10-04). Which model
            # answers is the owner's: a run the bridge starts uses the configured models.
            chosen = model_choices_in(body)
            if chosen:
                raise HTTPException(
                    status_code=403,
                    detail="which model answers is the owner's decision, made in the app: a run the "
                    "bridge starts uses the configured models; not accepted through the bridge: "
                    + ", ".join(chosen),
                )
            # And how far a run reaches: equal to the owner's posture, or narrower — never wider.
            reach, approval = owner_posture(body)
            wider = wider_than(body, reach=reach, approval=approval)
            if wider:
                raise HTTPException(
                    status_code=403,
                    detail="the posture is the owner's decision, made in the app: a run the bridge "
                    f"starts may not reach further than the owner's ({reach}, approval {approval}); "
                    "not accepted through the bridge: " + ", ".join(wider),
                )
        # `not body.get`, not `"posture" not in body`: an explicit `null` posture means NO posture —
        # nothing denied, no pause — which is wider than any corner an owner could have chosen.
        if route.seams and isinstance(body, dict) and not body.get("posture"):
            # What the Code screen sends when the owner has changed nothing: the configured posture
            # (or the stock pair) and shell only where the owner granted shell everywhere. The server
            # applies the configured posture as a floor regardless; sending it keeps the posture line
            # honest about the run that is happening.
            reach, approval = owner_posture(body)
            body = {**body, "posture": {"reach": reach, "approval": approval}}
            body.setdefault("allow_host_exec", reach == "workspace_shell")
        if route.seams and isinstance(body, dict):
            # The owner's decision of 2026-10-04: a run the bridge starts never gets Chimera's own
            # `.env`, whatever CHIMERA_AGENT_READS_OWN_ENV says for the owner's own runs. Set, never
            # read from the client: a body that sent `false` gets `true` like every other.
            body = {**body, "hide_own_env": True}
        return body

    def owner_posture(body: Any) -> tuple[str, str]:
        """The posture the owner configured for a run in ``body``'s folder: reach and approval.

        The folder's own grant is read from the same record the turn is held to — what the Code
        screen does with its switch. It RAISES `workspace` and nothing else: an owner who set
        `read_only` meant it, and the server applies that floor regardless.
        """
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
        folder = body.get("workspace") if isinstance(body, dict) else None
        if (
            isinstance(body, dict)
            and not (isinstance(folder, str) and folder.strip())
            and body.get("worktree") is None
        ):
            # A turn opened in a project with no folder runs in the project itself. A new worktree
            # is a folder nobody granted yet, so it gets no grant from here.
            folder = body.get("project")
        if reach == "workspace" and isinstance(folder, str) and folder.strip():
            from chimera.api.code_api import server_grants_shell

            if server_grants_shell(settings, Path(folder).expanduser()):
                reach = "workspace_shell"
        return reach, approval

    def suggest_settings(body: dict[str, Any]) -> dict[str, Any]:
        """Leave the owner a card for a change to the settings the bridge may only suggest.

        Nothing is written. The value is held to every check a save would make NOW, so the card
        never offers the owner a change the app would refuse; the owner's yes runs the checks again
        and applies only if the setting still holds the value shown here.
        """
        from chimera.api.config_api import check_parses, check_updates, setting_value
        from chimera.core.redact import redact
        from chimera.governance.setting_suggestions import Change, suggest

        updates: dict[str, str] = {}
        for key, value in body.items():
            if not isinstance(value, str):
                raise HTTPException(status_code=400, detail=f"the value for {key} must be a string")
            if redact(value) != value:
                # A model slug never looks like a key. A value the redactor would mask is one the
                # card could not show the owner whole, and a card that shows less than it applies
                # is not asking.
                raise HTTPException(
                    status_code=400, detail=f"the value for {key} looks like a credential"
                )
            updates[str(key)] = value
        try:
            check_updates(updates, workspace=workspace)
            check_parses(updates)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        settings = live_settings()
        changes = [
            Change(key=key, current=setting_value(settings, key), proposed=value)
            for key, value in sorted(updates.items())
        ]
        changes = [c for c in changes if c.current != c.proposed]
        if not changes:
            return {
                "route": "settings.edit",
                "status": 200,
                "data": {"suggestion": None, "unchanged": sorted(updates), "written": []},
            }
        try:
            request_id = suggest(
                Path(settings.home),
                changes,
                suggested_by="desktop_bridge",
                client_hint=bridge.hint(),
            )
        except OSError as exc:
            _log.warning("could not record a settings suggestion: %s", exc)
            raise HTTPException(
                status_code=500, detail="the suggestion could not be recorded"
            ) from exc
        data = {
            "suggestion": request_id,
            "written": [],
            "changes": [
                {"key": c.key, "current": c.current, "proposed": c.proposed} for c in changes
            ],
            "message": "Nothing was written. The owner sees this change as a card in the app "
            "and approves or refuses it there; it expires in 24 hours.",
        }
        return {"route": "settings.edit", "status": 202, "data": scrub(data, hidden())}

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
            closed = OWNER_DECISION_ROUTES.get(req.route)
            if closed is not None:
                # At every tier: these widen what the agent may reach, and the owner decides that
                # in the app. The sentence says where, so a client can tell the person.
                raise HTTPException(status_code=403, detail=closed)
            raise HTTPException(status_code=404, detail=f"no such bridge route: {req.route}")
        tier = bridge.authorize(request, route.tier)
        path, query = _resolve_path(route, req.params)
        guard_places(req.route, query, req.body, req.params)
        body = police(req.route, route, req.body, tier, req.params)
        if (
            req.route == "settings.edit"
            and isinstance(body, dict)
            and set(body) <= SUGGESTABLE_SETTINGS
        ):
            return suggest_settings(body)
        if not route.stream:
            try:
                status, data = await plain(route.method, path, query, body)
            except TimeoutError:
                return {"route": req.route, "status": 504, "data": "the app did not answer in time"}
            if req.route == "files.search" and isinstance(data, dict):
                # A search is a read of every file it matches: the same rule as `files.read`.
                hits = data.get("hits") or []
                data["hits"] = [
                    h
                    for h in hits
                    if not is_secret_file(str(h.get("path", "")))
                    and not hidden_place(query, req.body, str(h.get("path", "")))
                ]
            if req.route == "git.status" and isinstance(data, dict):
                # Behind the workspace refusal above, and kept anyway: what git reports is the
                # repository's view, and a repository can hold more than the workspace named.
                data["files"] = [
                    f
                    for f in data.get("files") or []
                    if not is_secret_file(str(f.get("path", "")))
                    and not hidden_place(query, req.body, str(f.get("path", "")))
                ]
            if req.route == "git.diff" and isinstance(data, dict):
                data["patch"] = _patch_without(
                    str(data.get("patch") or ""),
                    lambda p: is_secret_file(p) or hidden_place(query, req.body, p),
                )
            if req.route in {"files.tree", "files.browse"} and isinstance(data, dict):
                # A listing names what it lists. The app's own data folder and Chimera's `.env` are
                # not the bridge's to see, so they are not named to it either (review of 2026-10-04,
                # second round: a workspace that holds the install folder listed both).
                entries = data.get("entries") or []
                data["entries"] = [
                    e
                    for e in entries
                    if not is_secret_file(str(e.get("name", "")))
                    and not hidden_place(query, req.body, str(e.get("path", "")))
                ]
            if req.route == "app.messaging" and isinstance(data, dict):
                # A pairing code makes whoever sends it first the bot's owner, and that is the
                # owner's to do from the app. The bridge learns only that one is pending (plan M7).
                for row in data.values():
                    if isinstance(row, dict):
                        row["pairing_code"] = None
            if req.route == "app.mcp_servers":
                # A held server's diff carries the text the hold keeps from a model; the owner reads
                # it on the MCP screen, and the bridge gets the shape of the change only (S30-24).
                data = without_held_text(data)
            return {"route": req.route, "status": status, "data": scrub(data, hidden())}

        async def run(job: BridgeJob) -> None:
            parser = _SSEParser()
            head = bytearray()

            def on_chunk(chunk: bytes) -> None:
                # The first bytes, kept: a route that refuses before it streams answers with a JSON
                # `detail`, which is no event — and "HTTP 400" alone told the caller nothing about
                # which of its fields was wrong (an unknown project, a bad branch name).
                if len(head) < _ERROR_HEAD:
                    head.extend(chunk[: _ERROR_HEAD - len(head)])
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
                job.error = f"HTTP {job.status}" + _detail_of(bytes(head))

        job = jobs.start(req.route, run)
        wait = DEFAULT_WAIT_SECONDS if req.wait_seconds is None else req.wait_seconds
        await jobs.wait(job, wait)
        if job.blocked:
            await asyncio.sleep(0.2)  # let a question's sibling frames land with it
        return {
            "route": req.route,
            "status": 200,
            "job": _job_out(job, 0, hidden(), waiting, turn_running),
        }

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
        return _job_out(job, since, hidden(), waiting, turn_running)

    @app.post(
        "/api/bridge/jobs/{job_id}/stop", response_model=BridgeJobStopOut, tags=["bridge"]
    )
    async def bridge_job_stop(job_id: str, request: Request) -> dict[str, Any]:
        """Stop the coding turn a ``conversations.send`` job started — running, or queued for its folder.

        The app could always stop a turn (`POST /api/code/turns/{id}/stop`, the operate tier), but a
        client of the bridge had to know that route and dig the turn id out of the job, and a turn
        queued behind another in the same folder looked like one that hung (plan M1). Stopping only
        narrows, so the operate tier is enough. A refusal says why, never a 200 that stopped nothing.
        """
        bridge.authorize(request)
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=404, detail="no such job (they are kept in memory only)"
            )
        if job.route != "conversations.send":
            raise HTTPException(
                status_code=400,
                detail=f"a {job.route} job is stopped with its own area's action (for a run, "
                "runs.cancel); this stops a conversation's turn",
            )
        if job.ended:
            raise HTTPException(status_code=409, detail="this job has already ended")
        if not job.turn_id:
            raise HTTPException(
                status_code=409, detail="the turn has not started yet; ask again in a moment"
            )
        live = getattr(app.state, "live_turns", None)
        if live is None or not live.request_stop(job.turn_id):
            raise HTTPException(status_code=409, detail="that turn is not running any more")
        return {"job_id": job.id, "turn_id": job.turn_id, "stopping": True}
