"""Browser screen pairing: a code from the desktop app makes a browser tab the owner's screen, for a while.

A browser tab on the service's URL has no screen credential (`chimera/api/screen_credential.py`): the
desktop shell hands its webview one, and nothing else does. So an action only the screen may take fails
in that tab with nothing said. This module is the way across, and its properties are the whole argument:

* **Only the desktop app's own screen mints a code.** :func:`desktop_screen_refusal` refuses the bridge,
  a paired device, a browser session, and any screen the page offers (``chimera app`` puts its credential
  in the page, readable by the agent's own shell with a ``curl``).
* **A code is one-time and short-lived, and only one is live.** Five minutes; the first redemption uses
  it up; five wrong guesses kill it. That per-code count is the only limiter: there is no per-address
  bucket, so a local process cannot lock the owner out of pairing for longer than a minute's mint. Wrong,
  expired and spent codes get the same answer. Stored as a keyed HMAC, never in the clear.
* **A session is a browser's screen, not the owner's.** The tab keeps ``<id>.<secret>`` in an HttpOnly,
  SameSite=Strict cookie. The store keeps ``HMAC(process key, secret)``, and the key lives only in this
  process's memory, so a forged or copied ``browser-screen.json`` verifies nothing. Sessions end when the
  service restarts (the key goes with it); that is the stated trade-off.
* **The store is shared, and every change goes through it.** Each change re-reads the file under an
  exclusive lock, applies itself, and writes back, so a revoke by one process holds against another
  process's later write, and a verify always sees the file as it is now.
* **A session carries the screen's switch and nothing more.** It cannot mint, list or revoke sessions,
  turn "Ignore permissions" on, turn the bridge or its full control on. And on any unsafe method it must
  come from the server's own origin (:func:`same_origin_request`), so another localhost port cannot ride
  the cookie.
* **Nothing secret is written down after creation**: not the code, not the secret, in no response after
  the one that made them, in no log line, and not in the audit log, which records action and session id.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from chimera.api.devices import (
    CODE_ALPHABET,
    CODE_LENGTH,
    CODE_SECONDS,
    MAX_FAILURES,
    TOUCH_SECONDS,
    approximate_ip,
    clean_name,
    name_from_agent,
    normalise_code,
    shown_code,
)
from chimera.core.filelock import atomic_write_text, exclusively
from chimera.governance.audit import AuditLog
from chimera.telemetry import get_logger

_log = get_logger("api.browser_screen")

#: Set on a request's ASGI scope when a valid browser session carried the cookie: the session's id.
#: Only this process sets scope keys (no socket can), and only when the desktop screen did not.
BROWSER_SCOPE_KEY = "chimera.via_browser_screen"
#: Set when the request carried the desktop app's own screen credential, and that credential is not one
#: the page offers. The gate for minting, listing and revoking: a browser never has it.
DESKTOP_SCREEN_SCOPE_KEY = "chimera.via_desktop_screen"

COOKIE_NAME = "chimera_screen"
#: The response header that says "this refusal is for want of the owner's screen" to the frontend, which
#: then offers the code prompt. Set only where a browser could pair (never to a bridge or device).
NEEDS_SCREEN_HEADER = "X-Chimera-Needs-Screen"
NEEDS_SCREEN_TEXT = "This needs the owner's screen. Enter the pairing code from the desktop app."
BAD_CODE_TEXT = "That code is not right, or it has expired. Ask the desktop app for a new one."
#: What a browser session may not turn on: the bridge and its full control. Owner's screen only.
BROWSER_REFUSED_TURN_ON = (
    "{names} is turned on from the desktop app's screen, not from a browser tab"
)

#: How long a browser stays the owner's screen. Twelve hours by default; ``CHIMERA_BROWSER_SCREEN_SECONDS``
#: moves it, clamped to five minutes through a day.
SESSION_SECONDS_DEFAULT = 12 * 3600
SESSION_SECONDS_MIN = 5 * 60
SESSION_SECONDS_MAX = 24 * 3600
#: Live browser sessions at once. The oldest is dropped to make room — a cap, not a feature.
MAX_SESSIONS = 20
FILE_NAME = "browser-screen.json"
AUDIT_EVENT = "browser_screen"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
#: At most one warning a minute for refused exchanges, however many there are.
REFUSAL_LOG_SECONDS = 60.0


def session_seconds_from_env() -> int:
    """The configured session length, clamped. A value that does not parse takes the default."""
    raw = os.environ.get("CHIMERA_BROWSER_SCREEN_SECONDS", "").strip()
    try:
        seconds = int(raw) if raw else SESSION_SECONDS_DEFAULT
    except ValueError:
        seconds = SESSION_SECONDS_DEFAULT
    return max(SESSION_SECONDS_MIN, min(SESSION_SECONDS_MAX, seconds))


def desktop_screen_refusal(scope: Any) -> str:
    """``""`` when this request came from the desktop app's own screen, else why it did not."""
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
    from chimera.api.devices import DEVICE_SCOPE_KEY

    if scope.get(VIA_BRIDGE_SCOPE_KEY):
        return "the bridge cannot pair a browser"
    if scope.get(DEVICE_SCOPE_KEY):
        return "a paired device cannot pair a browser"
    if scope.get(DESKTOP_SCREEN_SCOPE_KEY) and not scope.get(BROWSER_SCOPE_KEY):
        return ""
    return "pairing a browser is done from the desktop app's screen"


def same_origin_request(request: Request) -> bool:
    """Whether a request came from this server's own origin, as a browser reports it.

    ``Sec-Fetch-Site: same-origin`` is the browser's own word for it. Failing that, an ``Origin`` header
    must equal the request's scheme and host exactly, so another port on localhost is a different origin.
    A request with neither (a script, an old browser) is not vouched for.
    """
    if request.headers.get("sec-fetch-site", "").strip().lower() == "same-origin":
        return True
    origin = request.headers.get("origin", "").strip()
    host = request.headers.get("host", "").strip()
    return bool(origin) and bool(host) and origin == f"{request.url.scheme}://{host}"


def needs_screen(request: Any, detail: str) -> HTTPException:
    """The 403 for a screen-only action refused here.

    Carries :data:`NEEDS_SCREEN_HEADER` only where a browser could pair: a bridge or a device's refusal
    gets no hint, because a code prompt there would be a prompt the caller cannot answer.
    """
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
    from chimera.api.devices import DEVICE_SCOPE_KEY

    scope = request.scope
    if scope.get(VIA_BRIDGE_SCOPE_KEY) or scope.get(DEVICE_SCOPE_KEY):
        return HTTPException(status_code=403, detail=detail)
    return HTTPException(status_code=403, detail=detail, headers={NEEDS_SCREEN_HEADER: "1"})


def browser_refused_turn_on(scope: Any, names: Sequence[str]) -> HTTPException | None:
    """The refusal for a browser session that asks to turn on the bridge or its full control, else None."""
    if not scope.get(BROWSER_SCOPE_KEY) or not names:
        return None
    return HTTPException(
        status_code=403, detail=BROWSER_REFUSED_TURN_ON.format(names=" and ".join(names))
    )


class BrowserCodes:
    """The one code that may be redeemed right now. In memory only: a restart voids it, which is right."""

    def __init__(self, *, clock: Any = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        # Keyed per process, so a digest of a short code is not a bare hash in any dump or snapshot.
        self._key = secrets.token_bytes(32)
        self._digest_value: bytes | None = None
        self._expires_at = 0.0
        self._failures = 0
        self._refusal_audited = False

    def claim_refusal_audit(self) -> bool:
        """True once per live code: the first refused exchange against it gets an audit row, and later
        ones against the same code do not, so a flood of guesses cannot fill the audit log."""
        with self._lock:
            if self._refusal_audited:
                return False
            self._refusal_audited = True
            return True

    def _digest(self, code: str) -> bytes:
        return hmac.new(self._key, normalise_code(code).encode("utf-8"), hashlib.sha256).digest()

    def mint(self) -> tuple[str, float]:
        """A new code, shown once, replacing any still live. Its failure count starts again at zero."""
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        expires = self._clock() + CODE_SECONDS
        with self._lock:
            self._digest_value = self._digest(code)
            self._expires_at = expires
            self._failures = 0
            self._refusal_audited = False
        return shown_code(code), expires

    def redeem(self, offered: str) -> bool:
        """True once, for the live code. Every wrong guess counts against it, from any caller.

        This one code is the only thing redeemable, so its count is the limiter: there is no bucket per
        address, which a local process could fill to lock the owner out for a quarter of an hour.
        """
        now = self._clock()
        with self._lock:
            if (
                self._digest_value is None
                or self._expires_at <= now
                or self._failures >= MAX_FAILURES
            ):
                self._digest_value = None
                return False
            typed = normalise_code(offered)
            if typed and hmac.compare_digest(self._digest(typed), self._digest_value):
                self._digest_value = None
                return True
            self._failures += 1
            if self._failures >= MAX_FAILURES:
                self._digest_value = None
            return False

    def live(self) -> bool:
        with self._lock:
            return self._digest_value is not None and self._expires_at > self._clock()


@dataclass
class BrowserSession:
    id: str
    name: str
    digest: str
    """``HMAC(process key, secret)``, hex. Never the secret, and useless without this process's key."""
    created_at: float
    last_used_at: float
    expires_at: float
    last_ip: str = ""

    def out(self) -> dict[str, Any]:
        """What the desktop may read: never the digest, never the secret."""
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "last_ip": self.last_ip,
            "expires_at": self.expires_at,
        }


class BrowserScreens:
    """The live browser sessions (one file, shared by every process on the home) and the one live code."""

    def __init__(
        self,
        home: Path | str,
        *,
        clock: Any = time.time,
        seconds: int | None = None,
    ) -> None:
        self._home = Path(home).expanduser()
        self._clock = clock
        self._seconds = seconds if seconds is not None else session_seconds_from_env()
        # The per-process store key: generated here, held only in memory, never written anywhere.
        self._key = secrets.token_bytes(32)
        self._lock = threading.Lock()
        self._touched: dict[str, float] = {}
        self.codes = BrowserCodes(clock=clock)
        self._refusal_lock = threading.Lock()
        self._refusals = 0
        self._refusal_logged_at = float("-inf")

    @property
    def seconds(self) -> int:
        return self._seconds

    @property
    def home(self) -> Path:
        return self._home

    @property
    def _path(self) -> Path:
        return self._home / FILE_NAME

    def _digest(self, secret: str) -> str:
        return hmac.new(self._key, secret.encode("utf-8"), hashlib.sha256).hexdigest()

    # ---- the shared store -------------------------------------------------------------------------

    def _read(self) -> dict[str, BrowserSession]:
        """What the file says now, unexpired. Read every time: another process may have changed it."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        sessions: dict[str, BrowserSession] = {}
        for item in (raw.get("sessions") or []) if isinstance(raw, dict) else []:
            if not isinstance(item, dict):
                continue
            with contextlib.suppress(TypeError, ValueError, KeyError):
                session = BrowserSession(
                    id=str(item["id"]),
                    name=clean_name(str(item.get("name") or ""), "Browser"),
                    digest=str(item["digest"]),
                    created_at=float(item["created_at"]),
                    last_used_at=float(item.get("last_used_at") or item["created_at"]),
                    expires_at=float(item["expires_at"]),
                    last_ip=str(item.get("last_ip") or ""),
                )
                sessions[session.id] = session
        now = self._clock()
        return {k: v for k, v in sessions.items() if v.expires_at > now}

    def _change(self, apply: Any) -> Any:
        """Re-read under an exclusive lock, apply one change, write back. The only write path."""
        self._home.mkdir(parents=True, exist_ok=True)
        with self._lock, exclusively(self._path):
            sessions = self._read()
            result = apply(sessions)
            payload = {"sessions": [{**s.out(), "digest": s.digest} for s in sessions.values()]}
            atomic_write_text(self._path, json.dumps(payload, indent=2))
        return result

    def _audit(self, action: str, **fields: Any) -> None:
        """One audit entry: the action and ids, never a code or a secret."""
        payload = {"action": action, "at": self._clock(), **fields}
        try:
            AuditLog(self._home / "audit.jsonl").record(AUDIT_EVENT, payload)
        except OSError as exc:
            _log.error("browser screen audit entry (%s) not recorded: %s", action, exc)

    def _refused(self, ip: str) -> None:
        """A refused exchange. One audit row per live code, and one warning a minute for the rest.

        Without this a local process guessing in a loop writes a row and a warning per guess, filling the
        audit log. The counter is per process and in memory; it says how many were refused since the last
        line, so the flood is still visible, just not written out one by one.
        """
        if self.codes.claim_refusal_audit():
            self._audit("exchange_refused", reason="wrong_or_spent", ip=ip)
        with self._refusal_lock:
            self._refusals += 1
            now = self._clock()
            if now - self._refusal_logged_at < REFUSAL_LOG_SECONDS:
                return
            count, self._refusals = self._refusals, 0
            self._refusal_logged_at = now
        _log.warning(
            "%d browser pairing exchange(s) refused in the last minute (last from %s)",
            count,
            ip or "an unknown address",
        )

    # ---- minting -------------------------------------------------------------------------------

    def mint(self) -> tuple[str, float]:
        code, expires = self.codes.mint()
        self._audit("mint", seconds=CODE_SECONDS)
        _log.info("minted a browser pairing code (valid %d seconds)", CODE_SECONDS)
        return code, expires

    # ---- exchanging ----------------------------------------------------------------------------

    def exchange(
        self, offered: str, *, user_agent: str, address: str
    ) -> tuple[BrowserSession, str] | None:
        """A session and its cookie value for the live code, else ``None`` (and nothing said about why)."""
        ip = approximate_ip(address)
        if not self.codes.redeem(offered):
            self._refused(ip)
            return None
        now = self._clock()
        secret = secrets.token_urlsafe(32)
        session = BrowserSession(
            id=secrets.token_hex(8),
            name=clean_name(name_from_agent(user_agent), "Browser"),
            digest=self._digest(secret),
            created_at=now,
            last_used_at=now,
            expires_at=now + self._seconds,
            last_ip=ip,
        )

        def add(sessions: dict[str, BrowserSession]) -> None:
            while len(sessions) >= MAX_SESSIONS:
                oldest = min(sessions.values(), key=lambda s: s.last_used_at)
                sessions.pop(oldest.id, None)
            sessions[session.id] = session

        self._change(add)
        self._audit("exchange", session=session.id, ip=ip)
        _log.info(
            "paired a browser as the owner's screen (%s) until %s",
            session.name,
            int(session.expires_at),
        )
        return session, f"{session.id}.{secret}"

    # ---- checking, listing, revoking ----------------------------------------------------------

    def verify(self, cookie: str | None) -> BrowserSession | None:
        """The session a cookie vouches for, while the shared file still lists it, unexpired, with this
        process's digest of the secret. Reads the file each time, so a revoke by any process holds here."""
        if not cookie or "." not in cookie:
            return None
        session_id, _, secret = cookie.partition(".")
        if not session_id or not secret:
            return None
        session = self._read().get(session_id)
        if session is None or session.expires_at <= self._clock():
            return None
        if not hmac.compare_digest(
            self._digest(secret).encode("ascii"), session.digest.encode("ascii")
        ):
            return None
        self._touch(session.id)
        return session

    def _touch(self, session_id: str) -> None:
        now = self._clock()
        if now - self._touched.get(session_id, 0.0) < TOUCH_SECONDS:
            return
        self._touched[session_id] = now

        def stamp(sessions: dict[str, BrowserSession]) -> None:
            session = sessions.get(session_id)
            if session is not None:
                session.last_used_at = now

        # Under the lock, and only for an entry that is still there: a touch never brings a revoked
        # session back, because it writes nothing for an id the file no longer lists.
        self._change(stamp)

    def sessions(self) -> list[BrowserSession]:
        return sorted(self._read().values(), key=lambda s: s.last_used_at, reverse=True)

    def revoke(self, session_id: str) -> bool:
        def remove(sessions: dict[str, BrowserSession]) -> BrowserSession | None:
            return sessions.pop(session_id, None)

        gone = self._change(remove)
        if gone is not None:
            self._touched.pop(session_id, None)
            self._audit("revoke", session=session_id)
            _log.info("revoked a browser screen (%s)", gone.name)
        return gone is not None

    def revoke_all(self) -> int:
        def clear(sessions: dict[str, BrowserSession]) -> int:
            count = len(sessions)
            sessions.clear()
            return count

        count: int = self._change(clear)
        self._touched.clear()
        self._audit("revoke_all", count=count)
        _log.info("revoked every browser screen (%d)", count)
        return count
