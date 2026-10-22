"""Where the background service is, and whether the answer can be trusted (S1, ``chimera service``).

A running ``chimera service`` writes ``~/.chimera/service-<channel>.json`` (``$CHIMERA_SERVICE_DIR``
overrides the folder): ``{id, version, protocol, url, pid, token, home, mode, started_at}``, 0600
and atomic, through the same writer the desktop bridge uses. Every client that reads it — the CLI
today, the desktop shell in the next change — **then asks the server** with ``GET /api/info`` and
trusts the file only when the server's own pid, version, id and data folder match it. A file is a
claim anyone on the machine could have left behind; the answer is what the process says about
itself, over the token the file names.

Kept apart from the bridge's ``desktop-bridge.json`` on purpose. The bridge's token is Claude's:
short-lived, and limited to a table of routes. This token is the owner's and opens the whole API,
so nothing on the MCP side reads this file (``tests/test_the_mcp_bridge_never_reads_the_service_
token.py`` pins it) and the bridge refuses to read or write it as a file.

Standard library only: ``chimera service status`` must run where FastAPI is not installed.

Two channels, so an installed app and a source checkout never take each other's record or port:
``stable`` for a frozen build, ``dev`` for everything else (``$CHIMERA_SERVICE_CHANNEL`` decides
when set).
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

from chimera.api.bridge_discovery import write_discovery

SERVICE_DIR_ENV = "CHIMERA_SERVICE_DIR"
CHANNEL_ENV = "CHIMERA_SERVICE_CHANNEL"
TOKEN_FILE = "service.token"
CHANNELS = ("stable", "dev")
#: Bumped only when a client of the previous protocol could no longer drive this server. It is
#: what separates "an older version" — which the app may replace — from "incompatible".
PROTOCOL = 1
#: Below 49152, the start of the ephemeral range, so the OS does not hand the port to a random
#: outgoing connection; one per channel, so stable and dev never fight over it. A busy port still
#: falls back to a free one — the lock is the data-folder claim, never the port.
DEFAULT_PORTS: dict[str, int] = {"stable": 47615, "dev": 47616}

ProbeState = Literal["alive", "stale", "unreachable", "unauthorized", "incompatible", "stopping"]


def channel() -> str:
    """``stable`` for a frozen build, ``dev`` otherwise, unless ``$CHIMERA_SERVICE_CHANNEL`` says."""
    named = os.environ.get(CHANNEL_ENV, "").strip().lower()
    if named in CHANNELS:
        return named
    return "stable" if getattr(sys, "frozen", False) else "dev"


def service_dir() -> Path:
    override = os.environ.get(SERVICE_DIR_ENV, "").strip()
    return Path(override).expanduser() if override else Path.home() / ".chimera"


def record_path(for_channel: str | None = None) -> Path:
    return service_dir() / f"service-{for_channel or channel()}.json"


def normal_home(home: Path | str) -> str:
    """One spelling of a data folder, so the file and the server compare the same string."""
    return str(Path(home).expanduser().resolve())


@dataclass(frozen=True)
class ServiceRecord:
    id: str
    version: str
    protocol: int
    url: str
    pid: int
    token: str
    home: str
    mode: str
    started_at: float
    stopping: bool = False
    process_created_at: float = 0.0
    process_exe: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def public(self) -> dict[str, Any]:
        """The record without its token — what ``status --json`` prints."""
        data = self.to_dict()
        data.pop("token", None)
        return data

    def with_token(self, token: str) -> ServiceRecord:
        return replace(self, token=token)

    @classmethod
    def from_dict(cls, data: Any) -> ServiceRecord | None:
        if not isinstance(data, dict):
            return None
        try:
            record = cls(
                id=str(data["id"]),
                version=str(data["version"]),
                protocol=int(data["protocol"]),
                url=str(data["url"]),
                pid=int(data["pid"]),
                token=str(data["token"]),
                home=str(data["home"]),
                mode=str(data["mode"]),
                started_at=float(data["started_at"]),
                stopping=bool(data.get("stopping", False)),
                process_created_at=float(data.get("process_created_at", 0.0)),
                process_exe=str(data.get("process_exe", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None
        return record if record.id and record.url and record.token else None


def new_record(*, url: str, token: str, home: Path | str, mode: str = "service") -> ServiceRecord:
    from chimera import __version__

    try:
        import psutil

        process = psutil.Process(os.getpid())
        process_created_at = process.create_time()
        process_exe = process.exe()
    except Exception:  # noqa: BLE001 - record remains useful when process inspection is restricted
        process_created_at = 0.0
        process_exe = ""
    return ServiceRecord(
        id=uuid.uuid4().hex,
        version=__version__,
        protocol=PROTOCOL,
        url=url,
        pid=os.getpid(),
        token=token,
        home=normal_home(home),
        mode=mode,
        started_at=time.time(),
        process_created_at=process_created_at,
        process_exe=process_exe,
    )


def write_record(record: ServiceRecord, path: Path | None = None) -> None:
    write_discovery(path or record_path(), record.to_dict())


def read_record(path: Path | None = None) -> ServiceRecord | None:
    try:
        data = json.loads((path or record_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return ServiceRecord.from_dict(data)


def remove_record_if_ours(record_id: str, path: Path | None = None) -> bool:
    """Delete the record only while it still names ``record_id`` — never a successor's."""
    target = path or record_path()
    current = read_record(target)
    if current is None or current.id != record_id:
        return False
    with contextlib.suppress(OSError):
        target.unlink()
        return True
    return False


def remove_record(path: Path | None = None) -> None:
    with contextlib.suppress(OSError):
        (path or record_path()).unlink()


# ---- the owner's token --------------------------------------------------------------------------


def token_path(home: Path | str) -> Path:
    return Path(home).expanduser() / TOKEN_FILE


def load_or_create_token(home: Path | str) -> str:
    """The data folder's service token, created once and kept, so a restart does not change it.

    Created with ``O_EXCL`` at mode 0600: two services racing to create it cannot both win, and the
    loser reads the winner's. Restarting must not rotate it, or every client holding the old one
    (the desktop shell, a script) is locked out by a routine restart.
    """
    path = token_path(home)
    existing = _read_token(path)
    if existing:
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raced = _read_token(path)
        if raced:
            return raced
        save_token(home, token)  # an empty file someone left: replace it
        return token
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
    return token


def save_token(home: Path | str, token: str) -> None:
    """Replace the token on disk (a token changed from the Settings screen), 0600 and atomic."""
    path = token_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def _read_token(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# ---- is it alive, and is it what the file says ---------------------------------------------------


def pid_alive(pid: int) -> bool:
    """Whether ``pid`` runs. Not ``os.kill(pid, 0)`` on Windows, where it would TERMINATE it."""
    if pid <= 0:
        return False
    if os.name == "posix":
        try:
            os.kill(pid, 0)
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]  # Windows-only branch
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong(0)
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return code.value == 259  # STILL_ACTIVE
        return True
    finally:
        kernel32.CloseHandle(handle)


@dataclass(frozen=True)
class Probe:
    state: ProbeState
    reason: str = ""
    info: dict[str, Any] | None = None

    @property
    def trusted(self) -> bool:
        return self.state == "alive"


def call(record: ServiceRecord, method: str, path: str, *, timeout: float = 3.0) -> tuple[int, Any]:
    """One request to the service with the record's token. ``(0, None)`` when nothing answered."""
    return request(record.url, record.token, method, path, timeout=timeout)


def request(
    url: str, token: str, method: str, path: str, *, timeout: float = 3.0, body: Any = None
) -> tuple[int, Any]:
    """One request to a local Chimera server; ``token`` may be empty. ``(0, None)``: no answer.

    Through an opener with no proxy: an ``HTTP_PROXY`` in the environment would otherwise route a
    loopback call to a proxy, which would see the owner's token.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if method == "GET" else b""
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    outgoing = urllib.request.Request(url.rstrip("/") + path, method=method, headers=headers, data=data)
    try:
        with opener.open(outgoing, timeout=timeout) as response:
            status, body = int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        status, body = int(exc.code), exc.read()
    except (OSError, ValueError):
        return 0, None
    try:
        return status, json.loads(body.decode("utf-8") or "null")
    except ValueError:
        return status, None


def probe(record: ServiceRecord, *, timeout: float = 3.0) -> Probe:
    """Ask the server who it is, and trust the record only when the answer matches it.

    ``stale``: the pid is gone, the file is litter. ``unreachable``: the pid runs but nothing
    answers (still starting, or the pid was reused). ``incompatible``: something answered and it is
    not what the file says — another pid, another version, another folder, another protocol. A
    file whose version was edited is incompatible, not trusted: the version is how a client decides
    whether it may replace the server.
    """
    if not pid_alive(record.pid):
        return Probe("stale", f"pid {record.pid} is not running")
    if record.stopping:
        return Probe("stopping", "the service is stopping")
    status, info = call(record, "GET", "/api/info", timeout=timeout)
    if status == 0:
        return Probe("unreachable", f"nothing answered at {record.url}")
    if status == 401:
        return Probe("unauthorized", "the service refused the record's token")
    if status != 200 or not isinstance(info, dict):
        return Probe("incompatible", f"/api/info answered {status}")
    if int(info.get("protocol") or 0) != PROTOCOL:
        return Probe("incompatible", f"protocol {info.get('protocol')}, this client speaks {PROTOCOL}", info)
    for field in ("pid", "version", "id", "home"):
        if str(info.get(field)) != str(getattr(record, field)):
            return Probe(
                "incompatible",
                f"the record says {field}={getattr(record, field)!r}, the server says {info.get(field)!r}",
                info,
            )
    return Probe("alive", "", info)
