"""The desktop app's windows attached to a background service, and what they lease it (S1, PR 3).

A ``chimera service`` outlives the window that started it. Two things it does must still depend
on a window being there, and the owner decided both on 2026-10-16:

* **The desktop bridge ("Allow Claude") is published only while a window is connected.** In
  ``app`` mode the bridge lives as long as the app process; a service with no window has no owner
  in front of it, so ``desktop-bridge.json`` is written when the first window connects and removed
  when the last one goes (``on_first`` / ``on_last``).
* **A code-less approval needs the screen credential** (``chimera/api/screen_credential.py``), and a
  service holds none of its own. A connected window *leases* it one, for :data:`LEASE_SECONDS`,
  renewed by the shell's heartbeat and dropped the moment the window disconnects or stops renewing.

The two are not authorised the same way, on purpose:

* **Presence** — "a window is here" — takes the owner's bearer token alone. All it can cause is the
  bridge file being written while the owner's "Allow Claude" switch is on, and a holder of the
  owner's token can already do anything the bridge's narrower token can.
* **The screen lease** takes the token AND the **launcher key**: the secret the desktop shell drew
  for this launch and handed to the service it spawned, in the environment of the child, never on
  disk (:data:`chimera.api.screen_credential.LAUNCHER_ENV`). ``service.token`` is a file, and the
  agent's own shell can read it; if the token alone could lease a screen credential, a ``cat`` and
  two ``curl`` calls would approve the agent's own questions without a code — exactly the hole the
  screen credential exists to close (#950). A service the current shell did not spawn (a terminal's
  ``chimera service start``, the app's previous run) holds no key this shell knows, so the lease is
  refused there and a yes takes the code until the app restarts the service.

Nothing here is ever written to a file, a record, a log or a route's answer: the routes answer
with counts and a reason, never a value.
"""

from __future__ import annotations

import hmac
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from chimera.api.screen_credential import MIN_SECRET, ScreenCredential
from chimera.telemetry import get_logger

_log = get_logger("api.service_windows")

#: How long one heartbeat keeps a window — and its screen lease — alive. The shell renews every
#: ten seconds, so a window is forgotten within half a minute of the app dying without saying so.
LEASE_SECONDS = 30.0
#: How often expired windows are looked for.
SWEEP_SECONDS = 2.0
#: A window id is the shell's own label for itself: short and printable.
MAX_WINDOW_ID = 128

#: Why a screen credential was not leased. ``no_launcher``: this service was not started by the
#: desktop app that is asking (it holds no launcher key at all). ``not_the_launcher``: the request
#: did not carry the key this service was started with. ``bad_screen``: the offered value is too
#: short to be one the shell drew, or equals a secret that may sit on disk.
Refusal = Literal["no_launcher", "not_the_launcher", "bad_screen"]


@dataclass(frozen=True)
class WindowAnswer:
    """What ``/api/service/window`` answers: counts and a reason, never a credential."""

    windows: int
    screen: bool
    reason: Refusal | None
    lease_seconds: float

    def to_dict(self) -> dict[str, object]:
        return {
            "windows": self.windows,
            "screen": self.screen,
            "reason": self.reason,
            "lease_seconds": self.lease_seconds,
        }


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


class ServiceWindows:
    """The connected windows of one service, the screen lease one of them holds, and the bridge.

    ``bearer`` is read on every lease, because the owner can change the token while the service
    runs; a screen credential equal to it is refused (that one may be in ``.env``).
    """

    def __init__(
        self,
        screen: ScreenCredential,
        *,
        launcher_key: str | None,
        bearer: Callable[[], str | None],
        on_first: Callable[[], None],
        on_last: Callable[[], None],
        lease: float = LEASE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._screen = screen
        self._launcher_key = launcher_key or None
        self._bearer = bearer
        self._on_first = on_first
        self._on_last = on_last
        self._lease = lease
        self._clock = clock
        self._windows: dict[str, float] = {}
        # The windows whose shell will restart this service once it is idle (the app adopting a
        # service it did not start); forgotten with the window.
        self._restart_pending: set[str] = set()
        self._holder: str | None = None
        self._lock = threading.Lock()
        # Transitions of the bridge, serialised apart from the window table so a slow file write
        # never holds a heartbeat — and so two threads cannot publish and retract out of order.
        self._bridge_lock = threading.Lock()
        self._published = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def launched_here(self) -> bool:
        """Whether a desktop shell started this service (and so holds a key it can lease with)."""
        return self._launcher_key is not None

    # ---- the routes -----------------------------------------------------------------------------

    def connect(
        self,
        window_id: str,
        *,
        screen: str | None,
        launcher: str | None,
        restart_pending: bool = False,
    ) -> WindowAnswer:
        """A heartbeat: ``window_id`` is here for another lease; ``screen`` leased when allowed.

        A refused screen leaves any lease already held as it is — a stranger's bad request must not
        cost the real window its one-click approvals — and still counts the window as present.
        ``restart_pending`` is what the window's shell said about restarting this service; it only
        changes the words of a refused approval, so the owner's token is enough to say it.
        """
        with self._lock:
            now = self._clock()
            self._windows[window_id] = now + self._lease
            if restart_pending:
                self._restart_pending.add(window_id)
            else:
                self._restart_pending.discard(window_id)
            reason: Refusal | None = None
            if screen is not None:
                reason = self._refusal(screen, launcher)
                if reason is None:
                    self._screen.lease(screen, now + self._lease)
                    self._holder = window_id
            granted = self._holder == window_id and self._screen.held(now)
            answer = WindowAnswer(len(self._windows), granted, reason, self._lease)
        self._apply()
        return answer

    def disconnect(self, window_id: str) -> WindowAnswer:
        """The window is gone (the app is quitting): forget it, and its lease, now."""
        with self._lock:
            self._windows.pop(window_id, None)
            self._restart_pending.discard(window_id)
            if self._holder == window_id:
                self._drop_lease()
            answer = WindowAnswer(len(self._windows), False, None, self._lease)
        self._apply()
        return answer

    def windows(self) -> int:
        with self._lock:
            return len(self._windows)

    def restart_pending(self) -> bool:
        """Whether a connected window's shell will restart this service once it is idle."""
        with self._lock:
            now = self._clock()
            return any(self._windows.get(w, 0.0) > now for w in self._restart_pending)

    # ---- the clock ------------------------------------------------------------------------------

    def sweep(self) -> None:
        """Forget every window whose lease ran out, and the screen credential with its holder."""
        with self._lock:
            now = self._clock()
            for window_id, until in list(self._windows.items()):
                if until <= now:
                    del self._windows[window_id]
                    self._restart_pending.discard(window_id)
                    _log.info("desktop window %s stopped renewing; forgotten", window_id)
            if self._holder is not None and (
                self._holder not in self._windows or not self._screen.held(now)
            ):
                self._drop_lease()
        self._apply()

    def start(self, interval: float = SWEEP_SECONDS) -> None:
        def loop() -> None:
            while not self._stop.wait(interval):
                try:
                    self.sweep()
                except OSError as exc:  # a bridge file that cannot be written; the next tick retries
                    _log.warning("desktop window sweep failed: %s", exc)

        self._thread = threading.Thread(target=loop, name="service-windows", daemon=True)
        self._thread.start()

    def close(self) -> None:
        """The service is stopping: no window, no lease, no bridge."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=SWEEP_SECONDS + 1)
        with self._lock:
            self._windows.clear()
            self._restart_pending.clear()
            self._drop_lease()
        self._apply()

    # ---- inside ---------------------------------------------------------------------------------

    def _refusal(self, screen: str, launcher: str | None) -> Refusal | None:
        if self._launcher_key is None:
            return "no_launcher"
        if not launcher or not _same(launcher.strip(), self._launcher_key):
            return "not_the_launcher"
        value = screen.strip()
        bearer = (self._bearer() or "").strip()
        if len(value) < MIN_SECRET or (bearer and _same(value, bearer)) or _same(value, self._launcher_key):
            return "bad_screen"
        return None

    def _drop_lease(self) -> None:
        self._holder = None
        self._screen.revoke()

    def _apply(self) -> None:
        """Publish the bridge on the first window, retract it after the last — transitions only."""
        with self._bridge_lock:
            live = self.windows() > 0
            if live == self._published:
                return
            self._published = live
            try:
                (self._on_first if live else self._on_last)()
            except OSError as exc:  # the bridge must never take the service down
                _log.warning("desktop bridge %s failed: %s", "publish" if live else "retract", exc)


def valid_window_id(window_id: str) -> bool:
    """A label the shell made: non-empty, short, printable ASCII without spaces."""
    return 0 < len(window_id) <= MAX_WINDOW_ID and all(33 <= ord(c) < 127 for c in window_id)
