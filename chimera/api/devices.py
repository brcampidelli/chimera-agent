"""The owner's paired devices: a one-time code becomes a 30-day cookie (S3, `chimera pair`).

Until S3 the only way to reach the app from a phone was a guest link: one conversation, no approval
card, and a guest's name on everything it said. The owner using their own phone on the sofa is not a
guest, and making them one was the wrong answer; giving the phone the owner's bearer token would be
worse — that token never expires, opens every route, and would sit in a browser's storage.

So a device gets its own credential, and this module is all of it:

* **A pairing code** (:class:`PairingCodes`): eight characters from an alphabet without look-alikes,
  drawn from the OS's CSPRNG, kept in memory only, valid for five minutes and for one use. A wrong
  guess counts against every code alive at that moment, and :data:`MAX_FAILURES` wrong guesses kill
  them all; one address that keeps guessing is refused outright for a while (:class:`Attempts`).
* **A device cookie** (:meth:`DeviceRegistry.issue`): ``v1.<device id>.<expiry>.<HMAC>``, signed with
  a key derived from the owner's device secret (``<home>/devices.key``, 0600). Thirty days. It is
  honoured only while the device is still in ``<home>/devices.json``, so revoking one device works
  the moment it is removed, and replacing the secret (:meth:`DeviceRegistry.revoke_all`) breaks the
  signature of every cookie ever issued.

What a device may do once paired is decided in :func:`device_may_reach` — the owner's routes for
reading and talking, never the ones that hand out access, change the settings, or widen the agent's
reach — and the approval card's code-less yes is not among them at all: a device request never
carries the screen credential (`chimera/api/screen_credential.py`), so a yes from a phone takes the
question's code like any other remote answer (study 30, S30-30).

Nothing here is reached through the desktop bridge: the routes that mint, list and revoke live in
``devices_api.py`` and are absent from ``bridge_routes.ROUTES``, and they refuse a request the bridge
marked anyway.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from chimera.core.filelock import atomic_write_text
from chimera.telemetry import get_logger

_log = get_logger("api.devices")

#: Set on a request's ASGI scope by the device door (``device_door.py``) when it carried a valid
#: device cookie: the device's id. Only this process sets scope keys — no socket can.
DEVICE_SCOPE_KEY = "chimera.via_device"

COOKIE_NAME = "chimera_device"
CODE_SECONDS = 5 * 60
COOKIE_SECONDS = 30 * 24 * 3600
#: Wrong guesses, from anyone, after which every live code is void and the owner mints a new one.
MAX_FAILURES = 5
#: Wrong guesses one address may make inside :data:`IP_WINDOW_SECONDS` before it is refused unheard.
IP_LIMIT = 5
IP_WINDOW_SECONDS = 15 * 60
#: No 0/O, 1/I/L: the code is read off one screen and typed into another.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
MAX_NAME = 40
#: How often a device's "last used" is written back: a page polls, and the file is not a log.
TOUCH_SECONDS = 60.0

DEVICES_FILE = "devices.json"
KEY_FILE = "devices.key"


def normalise_code(text: str) -> str:
    """What the person typed, as the code is stored: upper case, no spaces or dashes."""
    return "".join(ch for ch in (text or "").upper() if ch.isalnum())


def shown_code(code: str) -> str:
    """``ABCD-EFGH``: two groups of four are read and typed without losing one's place."""
    return f"{code[:4]}-{code[4:]}" if len(code) == CODE_LENGTH else code


def approximate_ip(address: str) -> str:
    """Enough of an address to recognise a network, not a host: ``192.168.1.x``, ``2001:db8:1::/48``."""
    try:
        ip = ipaddress.ip_address((address or "").strip())
    except ValueError:
        return ""
    if ip.version == 4:
        head = str(ip).rsplit(".", 1)[0]
        return f"{head}.x"
    network = ipaddress.ip_network(f"{ip}/48", strict=False)
    return str(network)


def clean_name(name: str | None, fallback: str = "") -> str:
    text = " ".join((name or "").split())[:MAX_NAME]
    return text or fallback


def name_from_agent(user_agent: str) -> str:
    """A default name from the browser's own description, for a person who typed none."""
    ua = (user_agent or "").lower()
    for needle, label in (
        ("iphone", "iPhone"),
        ("ipad", "iPad"),
        ("android", "Android"),
        ("windows", "Windows"),
        ("mac os", "Mac"),
        ("linux", "Linux"),
    ):
        if needle in ua:
            return label
    return "Device"


@dataclass
class _Code:
    value: str
    expires_at: float
    failures: int = 0


class PairingCodes:
    """The codes alive right now. In memory only: a restart voids every one, which is correct."""

    def __init__(self, *, clock: Any = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._codes: list[_Code] = []

    def _live(self, now: float) -> list[_Code]:
        self._codes = [c for c in self._codes if c.expires_at > now and c.failures < MAX_FAILURES]
        return self._codes

    def mint(self) -> tuple[str, float]:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        now = self._clock()
        with self._lock:
            self._live(now)
            expires = now + CODE_SECONDS
            self._codes.append(_Code(code, expires))
        return code, expires

    def redeem(self, offered: str) -> bool:
        """True once, for a live code. A wrong one is a failure against EVERY live code: the person
        guessing does not know which code they are aiming at, so none of them can be spared."""
        typed = normalise_code(offered)
        now = self._clock()
        with self._lock:
            live = self._live(now)
            match = next(
                (c for c in live if hmac.compare_digest(c.value.encode(), typed.encode())), None
            )
            if match is not None and typed:
                live.remove(match)
                return True
            for code in live:
                code.failures += 1
            self._live(now)
            return False

    def clear(self) -> None:
        with self._lock:
            self._codes = []

    def count(self) -> int:
        with self._lock:
            return len(self._live(self._clock()))


class Attempts:
    """Wrong guesses per address in a sliding window. Past :data:`IP_LIMIT`, refused unheard."""

    def __init__(self, *, clock: Any = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, address: str, now: float) -> deque[float]:
        times = self._failures.setdefault(address, deque())
        while times and times[0] <= now - IP_WINDOW_SECONDS:
            times.popleft()
        return times

    def blocked(self, address: str) -> bool:
        with self._lock:
            return len(self._recent(address, self._clock())) >= IP_LIMIT

    def fail(self, address: str) -> None:
        now = self._clock()
        with self._lock:
            self._recent(address, now).append(now)


@dataclass
class Device:
    id: str
    name: str
    created_at: float
    last_used_at: float
    last_ip: str = ""
    """Approximate (:func:`approximate_ip`): a network, never the host."""
    expires_at: float = 0.0

    def out(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _State:
    devices: dict[str, Device] = field(default_factory=dict)
    door_port: int = 0


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class DeviceRegistry:
    """``<home>/devices.json`` and ``<home>/devices.key``, and the cookies they vouch for."""

    def __init__(self, home: Path | str, *, clock: Any = time.time) -> None:
        self._home = Path(home).expanduser()
        self._clock = clock
        self._lock = threading.Lock()
        self._state: _State | None = None
        self._touched: dict[str, float] = {}
        self.codes = PairingCodes(clock=clock)
        self.attempts = Attempts(clock=clock)

    @property
    def home(self) -> Path:
        return self._home

    # ---- the owner's device secret -------------------------------------------------------------

    @property
    def _key_path(self) -> Path:
        return self._home / KEY_FILE

    def _secret(self) -> bytes:
        try:
            text = self._key_path.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if len(text) >= 32:
            return text.encode("utf-8")
        fresh = secrets.token_hex(32)
        atomic_write_text(self._key_path, fresh)
        return fresh.encode("utf-8")

    def _signing_key(self) -> bytes:
        # Derived, not the secret itself: the file's bytes never sign anything directly, so a use
        # of the same secret elsewhere later cannot collide with this one.
        return hmac.new(self._secret(), b"chimera device cookie v1", hashlib.sha256).digest()

    def _sign(self, device_id: str, expires: int) -> str:
        message = f"v1.{device_id}.{expires}".encode()
        return _b64(hmac.new(self._signing_key(), message, hashlib.sha256).digest())

    # ---- the list ------------------------------------------------------------------------------

    @property
    def _path(self) -> Path:
        return self._home / DEVICES_FILE

    def _load(self) -> _State:
        if self._state is not None:
            return self._state
        state = _State()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            for item in raw.get("devices") or []:
                try:
                    device = Device(**{k: item[k] for k in Device.__dataclass_fields__ if k in item})
                except (TypeError, KeyError):
                    continue
                state.devices[device.id] = device
            port = raw.get("door_port")
            state.door_port = int(port) if isinstance(port, int) and 0 < port < 65536 else 0
        self._state = state
        return state

    def _save(self, state: _State) -> None:
        payload = {
            "devices": [d.out() for d in state.devices.values()],
            "door_port": state.door_port,
        }
        atomic_write_text(self._path, json.dumps(payload, indent=2))

    def devices(self) -> list[Device]:
        with self._lock:
            now = self._clock()
            return sorted(
                (d for d in self._load().devices.values() if d.expires_at > now),
                key=lambda d: d.last_used_at,
                reverse=True,
            )

    def door_port(self) -> int:
        with self._lock:
            return self._load().door_port

    def remember_door_port(self, port: int) -> None:
        """The port the door opened on, reused next time so a phone's bookmark keeps working."""
        with self._lock:
            state = self._load()
            if state.door_port != port:
                state.door_port = port
                self._save(state)

    # ---- issuing, checking, revoking ------------------------------------------------------------

    def issue(self, *, name: str, address: str) -> tuple[Device, str]:
        """A new device and the cookie value that proves it. Called only after a code was redeemed."""
        now = self._clock()
        expires = int(now + COOKIE_SECONDS)
        device = Device(
            id=secrets.token_hex(8),
            name=clean_name(name, "Device"),
            created_at=now,
            last_used_at=now,
            last_ip=approximate_ip(address),
            expires_at=float(expires),
        )
        with self._lock:
            state = self._load()
            state.devices[device.id] = device
            self._save(state)
        _log.info("paired a device (%s) from %s", device.name, device.last_ip or "an unknown address")
        return device, f"v1.{device.id}.{expires}.{self._sign(device.id, expires)}"

    def verify(self, cookie: str | None) -> Device | None:
        """The device this cookie belongs to, while it is signed, unexpired and still listed."""
        parts = (cookie or "").split(".")
        if len(parts) != 4 or parts[0] != "v1":
            return None
        _, device_id, expiry, signature = parts
        try:
            expires = int(expiry)
        except ValueError:
            return None
        expected = self._sign(device_id, expires)
        if not hmac.compare_digest(signature.encode("ascii", "replace"), expected.encode("ascii")):
            return None
        if expires <= self._clock():
            return None
        with self._lock:
            device = self._load().devices.get(device_id)
            if device is None or int(device.expires_at) != expires:
                return None
            return device

    def is_active(self, device_id: str) -> bool:
        with self._lock:
            device = self._load().devices.get(device_id)
        return device is not None and device.expires_at > self._clock()

    def touch(self, device_id: str, address: str) -> None:
        """Record a use, at most once a minute per device."""
        now = self._clock()
        if now - self._touched.get(device_id, 0.0) < TOUCH_SECONDS:
            return
        self._touched[device_id] = now
        with self._lock:
            state = self._load()
            device = state.devices.get(device_id)
            if device is None:
                return
            device.last_used_at = now
            device.last_ip = approximate_ip(address) or device.last_ip
            self._save(state)

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            state = self._load()
            gone = state.devices.pop(device_id, None)
            if gone is not None:
                self._save(state)
        if gone is not None:
            _log.info("revoked device %s (%s)", gone.id, gone.name)
        return gone is not None

    def revoke_all(self) -> int:
        """Every device, and the secret that signed them: no cookie issued before now verifies."""
        with self._lock:
            state = self._load()
            count = len(state.devices)
            state.devices = {}
            self._save(state)
            atomic_write_text(self._key_path, secrets.token_hex(32))
        self.codes.clear()
        _log.info("revoked every device (%d) and replaced the device secret", count)
        return count


# ---- what a paired device may reach -----------------------------------------------------------

#: Never, whatever the method: the routes that hand out access, show it, or close doors (a device
#: does not pair another device or revoke the owner's), the bridge, and stopping the service.
_NEVER = (
    "/api/security/",
    "/api/bridge/",
    "/api/service/",
    "/api/config/pool",
    "/api/config/test",
    "/api/config/vault",
    "/api/code/share/",
    # Reads that reach FILES, not conversations. `GET /api/fs/file` takes any existing folder as its
    # workspace, so a device (or a stolen cookie) read `~/.chimera/service-stable.json` — the
    # owner's token — and stopped being limited to reading and talking (review of 2026-10-16).
    # The bridge polices these routes path by path (`desktop_bridge.guard_places`); a device does
    # not need them to read a conversation or talk in it, so it gets none of them.
    "/api/fs/",
    "/api/git/",
    "/api/lsp/",
    # Answers that ARE a door: a messaging bot's pending pairing code makes whoever sends it the
    # bot's owner; the memory export, the diagnostics and the troubleshooting report carry the
    # machine's paths, logs and settings wholesale.
    "/api/messaging",
    "/api/memory/export",
    "/api/diagnostics",
    "/api/troubleshoot",
)

#: Query parameters that name a place on disk. Any of them pointing into the owner's data folder,
#: the service folder (`service-<channel>.json`, `service.token`, `desktop-install.json`) or the
#: bridge's folder — or holding one of them — is refused, whatever the route.
_PLACE_PARAMS = frozenset({"workspace", "path", "file", "dir", "root", "cwd", "folder", "paths"})

#: The writes a device makes: talking. A turn, its guidance and its stop; a chat; an answer to the
#: agent's own question; a refusal or (with the question's code only — the route checks) an
#: approval; the attachments and voice a message carries.
_TALK_PREFIXES = (
    "/api/code/turn",
    "/api/code/turns/",
    "/api/chat/stream",
    "/api/questions/",
    "/api/attachments",
    "/api/transcribe",
)


#: The conversation's own state, which the sidebar changes: archive, done, fork, seen, unarchive, redo,
#: and dropping a queued message. Not deleting it, not its pull request, not undoing its file edits.
_CONVERSATION_WRITE = re.compile(
    r"^/api/code/sessions/[^/]+/(archive|done|fork|seen|unarchive|redo|queue/[^/]+)$"
)
#: The window's own furniture: where the panels are, and the suggestion counters the screens keep.
_UI_WRITES = frozenset({("PUT", "/api/ui/layout"), ("POST", "/api/suggestions/event")})
_APPROVAL = re.compile(r"^/api/approvals/(?!reject-all$)[^/]+$")


def device_may_reach(method: str, path: str) -> str:
    """``""`` when a paired device may make this request, else the reason it may not.

    A read (GET/HEAD) reaches what the owner's screens read, except :data:`_NEVER` and the share
    links' tokens. A write reaches only talking (:data:`_TALK_PREFIXES`), the conversation's own state
    (:data:`_CONVERSATION_WRITE`) and the approval route — where a yes still needs the question's
    code, because a device never carries the screen credential. Everything else — settings, folders,
    commands outside governance, bots, MCP servers, skills, cron, files on disk — is the owner's on
    their screen: a lost phone must not be the thing that turns the agent's governance off.
    """
    verb = method.upper()
    if any(path.startswith(prefix) for prefix in _NEVER):
        return "this is done on the owner's screen, not from a paired device"
    if path.startswith("/api/code/sessions/") and (path.endswith("/shares") or "/share" in path):
        return "share links are made and listed on the owner's screen"
    if verb in ("GET", "HEAD"):
        return ""
    if any(path.startswith(prefix) for prefix in _TALK_PREFIXES):
        return ""
    if _CONVERSATION_WRITE.match(path) and verb in ("POST", "DELETE"):
        return ""
    if _APPROVAL.match(path) and verb == "POST":
        return ""
    if (verb, path) in _UI_WRITES:
        return ""
    return "a paired device reads and talks; this change is made on the owner's screen"


def device_place_refusal(query: list[tuple[str, str]], roots: list[Path]) -> str:
    """``""``, or why a device request's query names a place a device may not reach.

    The same nets the bridge uses, from the same helpers (`bridge_routes.is_secret_file`,
    `chimera/core/own_files.py`): a value that names a credential file in any component, as Windows
    opens it (``.env ``, ``.env::$DATA``); a device or network spelling (``\\?\``); and a place
    inside or holding the owner's data, service or bridge folder, or Chimera's own ``.env``.
    """
    from chimera.api.bridge_routes import is_secret_file
    from chimera.core.own_files import contains, is_device_or_unc, is_own_env, within

    for key, value in query:
        text = (value or "").strip()
        if not text:
            continue
        if is_secret_file(text):
            return "credential files are not reachable from a paired device"
        if key.lower() not in _PLACE_PARAMS:
            continue
        if is_device_or_unc(text):
            return "device and network paths are not reachable from a paired device"
        target = Path(text).expanduser()
        if is_own_env(target) or any(within(target, r) or contains(target, r) for r in roots):
            return "that folder holds the owner's own data; it is not reachable from a paired device"
    return ""
