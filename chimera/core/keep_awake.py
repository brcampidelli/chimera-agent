"""Keep the computer awake while there is work, and let it sleep when there is none.

Before this, nothing stopped the machine from going to sleep under a running agent: a coding turn
left for ten minutes, a background work, a schedule due at 7 a.m. all died with the screen, and the
watchdog (`chimera/scheduler/watchdog.py`) could only report the silence afterwards. The owner's
machine is a laptop, and the schedule that did not run is the failure he sees most.

One mechanism covers every kind of work, so there is one thing to reason about:

* **Holds** — a counter per reason, for work this process starts and ends itself: a coding turn
  or a background work holds from its first line to its `finally`.
* **Probes** — a callable per reason that counts work living elsewhere: the autonomous runs still
  cancellable, the scheduled jobs due within :data:`CRON_LEAD_SECONDS`.

A single long-lived thread owns the operating system's side. That is not tidiness: on Windows
``SetThreadExecutionState`` is per THREAD, and the state is dropped when the thread that set it
exits — calling it from whichever request thread happened to start a turn would hold the machine
for as long as that thread lived and release it at random. Here the thread is created once, wakes
when a hold changes or every :data:`POLL_SECONDS`, and is the only caller of the OS.

Off by default (``CHIMERA_KEEP_AWAKE=off``), and ``off`` means the OS is never called at all — not
even to ask whether the machine is on battery. A headless server (no graphical session) and any
system without a mechanism here (macOS today) get the null inhibitor, and the status says so
instead of claiming to hold something it cannot.

What this prevents is IDLE sleep. A closed lid, the power button or Start › Sleep still sleep the
machine; the Settings hint says so, because a switch named "keep awake" invites the stronger
reading.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar

_log = logging.getLogger(__name__)

_T = TypeVar("_T")
_R = TypeVar("_R")

MODES = ("off", "working", "always")
#: How often the keeper looks at its probes when nothing woke it sooner. Windows' shortest idle
#: sleep timeout is one minute, so fifteen seconds of lag cannot lose the machine.
POLL_SECONDS = 15.0
#: A schedule this close to firing keeps the machine awake for it. Wider than the cron daemon's
#: tick (30 s in the app) by a lot, so a job is held for before the tick that fires it, and narrow
#: enough that a nightly job does not keep a laptop up all evening.
CRON_LEAD_SECONDS = 10 * 60

# Windows execution-state flags (winbase.h).
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class Inhibitor(Protocol):
    """The operating system's side. Every method is called from the keeper's own thread."""

    name: str

    def acquire(self, why: str) -> None: ...

    def release(self) -> None: ...

    def on_battery(self) -> bool | None:
        """True on battery, False on mains, None when the machine cannot say."""
        ...


class NullInhibitor:
    """Nothing to hold: a headless server, macOS, or a platform this module has no mechanism for."""

    name = "none"

    def acquire(self, why: str) -> None:
        return None

    def release(self) -> None:
        return None

    def on_battery(self) -> bool | None:
        return None


class WindowsInhibitor:
    """``SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`` from the keeper's thread.

    ``ES_DISPLAY_REQUIRED`` is deliberately absent: the screen may go dark, the machine stays up.
    The work does not need a lit display, and a laptop with its screen forced on is the battery cost
    nobody asked for.
    """

    name = "windows"

    def __init__(self, kernel32: Any = None) -> None:
        # Resolved lazily, so constructing one on a test machine touches nothing.
        self._kernel32 = kernel32

    def _k(self) -> Any:
        if self._kernel32 is None:
            import ctypes

            # `windll` exists only on Windows; this class is only built there (`default_inhibitor`).
            self._kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
        return self._kernel32

    def acquire(self, why: str) -> None:
        if not self._k().SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
            _log.warning("keep awake: SetThreadExecutionState refused (%s)", why)

    def release(self) -> None:
        self._k().SetThreadExecutionState(ES_CONTINUOUS)

    def on_battery(self) -> bool | None:
        import ctypes

        class _PowerStatus(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_ubyte),
                ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte),
                ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]

        status = _PowerStatus()
        if not self._k().GetSystemPowerStatus(ctypes.byref(status)):
            return None
        # 0 offline (battery), 1 online (mains), 255 unknown.
        return {0: True, 1: False}.get(int(status.ACLineStatus))


class SystemdInhibitor:
    """``systemd-inhibit`` holding a sleep lock for as long as its child runs.

    The child is ``tail --pid=<this process>``, not ``sleep infinity``: if this process dies
    without releasing — a crash, a kill -9 — the lock must die with it. With ``sleep infinity`` an
    orphaned inhibitor would keep the machine awake until someone found it in a process list.
    """

    name = "systemd-inhibit"

    def __init__(
        self,
        exe: str,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        power_supply: Path = Path("/sys/class/power_supply"),
    ) -> None:
        self._exe = exe
        self._popen = popen
        self._power_supply = power_supply
        self._proc: Any = None

    def acquire(self, why: str) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        self._proc = self._popen(
            [
                self._exe,
                "--what=sleep:idle",
                "--who=Chimera",
                f"--why={why}",
                "--mode=block",
                "tail",
                f"--pid={os.getpid()}",
                "-f",
                "/dev/null",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def release(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        with contextlib.suppress(Exception):
            proc.terminate()
            proc.wait(timeout=5)

    def on_battery(self) -> bool | None:
        """From the kernel's power-supply class: a mains supply that reports offline is battery."""
        try:
            supplies = list(self._power_supply.iterdir())
        except OSError:
            return None
        for supply in supplies:
            try:
                if (supply / "type").read_text(encoding="utf-8").strip() != "Mains":
                    continue
                return (supply / "online").read_text(encoding="utf-8").strip() == "0"
            except OSError:
                continue
        return None


def default_inhibitor(
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> Inhibitor:
    """The mechanism this machine has, or the null one.

    Linux needs a graphical session as well as the binary: a VPS has ``systemd-inhibit`` too and
    nothing to put to sleep, and holding a lock there would only be a process that does nothing.
    """
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    if platform == "win32":
        return WindowsInhibitor()
    if platform.startswith("linux"):
        graphical = bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")) or env.get(
            "XDG_SESSION_TYPE", ""
        ).lower() in ("x11", "wayland")
        exe = which("systemd-inhibit")
        if graphical and exe:
            return SystemdInhibitor(exe)
    return NullInhibitor()


def normalize_mode(value: object) -> str:
    word = str(value or "").strip().lower()
    return word if word in MODES else "off"


@dataclass(frozen=True)
class KeepAwakeState:
    """What the status bar and the Settings card read. ``active`` is the truth about the OS."""

    mode: str = "off"
    active: bool = False
    reasons: tuple[str, ...] = ()
    #: Why work is present and the machine is NOT held: ``battery`` or ``unsupported``. Empty when
    #: held, when there is no work, or when the mode is off.
    blocked: str = ""
    mechanism: str = ""
    on_battery_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "active": self.active,
            "reasons": list(self.reasons),
            "blocked": self.blocked,
            "mechanism": self.mechanism,
            "on_battery_allowed": self.on_battery_allowed,
        }


@dataclass
class KeepAwake:
    """The keeper. Construct one per test; production uses :func:`service`."""

    settings: Callable[[], Any]
    inhibitor_factory: Callable[[], Inhibitor] = default_inhibitor
    poll: float = POLL_SECONDS
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _holds: Counter[str] = field(default_factory=Counter, init=False, repr=False)
    _probes: dict[str, Callable[[], int]] = field(default_factory=dict, init=False, repr=False)
    _wake: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _inhibitor: Inhibitor | None = field(default=None, init=False, repr=False)
    _held: bool = field(default=False, init=False, repr=False)
    _state: KeepAwakeState = field(default_factory=KeepAwakeState, init=False, repr=False)

    # ------------------------------------------------------------------ the counter

    def acquire(self, reason: str) -> None:
        with self._lock:
            self._holds[reason] += 1
        self._wake.set()

    def release(self, reason: str) -> None:
        """One release per acquire. An extra release is a bug elsewhere, and it must not drive the
        count below zero — a negative count would let one later acquire read as zero and leave the
        machine to sleep under a turn."""
        with self._lock:
            if self._holds[reason] > 1:
                self._holds[reason] -= 1
            else:
                self._holds.pop(reason, None)
        self._wake.set()

    @contextlib.contextmanager
    def hold(self, reason: str) -> Iterator[None]:
        self.acquire(reason)
        try:
            yield
        finally:
            self.release(reason)

    def add_probe(self, reason: str, probe: Callable[[], int]) -> None:
        """Count work this process does not start or end itself. Replaces a probe of the same name,
        so a second cron daemon in one process (tests) does not leave a dead one counting."""
        with self._lock:
            self._probes[reason] = probe
        self._wake.set()

    def nudge(self) -> None:
        """Have the keeper decide again now — after a setting changed, say.

        Starts the thread when the mode is on and it is not running: the thread exists only while
        the mode is not ``off`` (see :meth:`ensure_running`), so a switch turned on from the screen
        has to bring it up here rather than at the next launch."""
        self.ensure_running()

    def count(self) -> int:
        with self._lock:
            return sum(self._holds.values())

    def _reasons(self) -> tuple[str, ...]:
        with self._lock:
            held = [reason for reason, n in self._holds.items() if n > 0]
            probes = list(self._probes.items())
        for reason, probe in probes:
            try:
                live = int(probe())
            except Exception:  # noqa: BLE001 — a broken probe must not stop the keeper
                _log.debug("keep awake: probe %s failed", reason, exc_info=True)
                continue
            if live > 0 and reason not in held:
                held.append(reason)
        return tuple(sorted(held))

    # ------------------------------------------------------------------ the OS side

    def tick(self) -> KeepAwakeState:
        """Decide and act once. Called by the keeper's thread; tests call it directly."""
        settings = self.settings()
        mode = normalize_mode(getattr(settings, "keep_awake", "off"))
        on_battery_allowed = bool(getattr(settings, "keep_awake_on_battery", False))
        reasons: tuple[str, ...] = ()
        blocked = ""
        want = False
        if mode != "off":
            reasons = self._reasons()
            if mode == "always":
                reasons = ("always", *reasons)
            if reasons:
                inhibitor = self._get_inhibitor()
                if inhibitor.name == "none":
                    blocked = "unsupported"
                elif not on_battery_allowed and inhibitor.on_battery() is True:
                    blocked = "battery"
                else:
                    want = True
        if want and not self._held:
            self._get_inhibitor().acquire("Chimera: " + ", ".join(reasons))
            self._held = True
        elif not want and self._held:
            # Only ever reached with an inhibitor already built: `_held` is set right after one is.
            self._get_inhibitor().release()
            self._held = False
        state = KeepAwakeState(
            mode=mode,
            active=self._held,
            reasons=reasons,
            blocked=blocked,
            mechanism=self._inhibitor.name if self._inhibitor is not None else "",
            on_battery_allowed=on_battery_allowed,
        )
        with self._lock:
            self._state = state
        return state

    def _get_inhibitor(self) -> Inhibitor:
        if self._inhibitor is None:
            self._inhibitor = self.inhibitor_factory()
        return self._inhibitor

    def state(self) -> KeepAwakeState:
        with self._lock:
            return self._state

    # ------------------------------------------------------------------ the thread

    def ensure_running(self) -> None:
        """Start the thread if the mode asks for one, or wake it if it is already running.

        With the mode ``off`` no thread is started at all. Not tidiness: the thread reads the
        settings every :data:`POLL_SECONDS`, and here a settings read on an empty cache exports
        ``.env`` credentials into ``os.environ`` and caches whatever the environment holds at that
        instant — from a background thread, racing whoever else is changing it (every test in the
        suite, since every process that builds the API builds a keeper). A switch that ships off
        should cost nothing, and a thread that wakes four times a minute to read "off" is not
        nothing. The mode is read here, on the caller's thread.

        A running thread is woken under the lock, so it cannot slip out between this check and the
        wake: the thread decides to exit only under the same lock, and only with no wake pending.
        """
        try:
            wanted = normalize_mode(getattr(self.settings(), "keep_awake", "off")) != "off"
        except Exception:  # noqa: BLE001 — a settings read that fails must not break its caller
            _log.debug("keep awake: settings read failed", exc_info=True)
            wanted = False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._wake.set()
                return
            if wanted:
                self._start_locked()

    def start(self) -> None:
        """Start the keeper's thread whatever the mode. Idempotent; a daemon, so it never holds the
        process up. Production goes through :meth:`ensure_running`; tests drive this directly."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._start_locked()

    def _start_locked(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="chimera-keep-awake", daemon=True)
        self._thread.start()

    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            state: KeepAwakeState | None = None
            try:
                state = self.tick()
            except Exception:  # noqa: BLE001 — the keeper outlives a bad tick
                _log.debug("keep awake: tick failed", exc_info=True)
            # Off, holding nothing, and nobody asked for another look since this tick: nothing is
            # left for the thread to do, so it ends rather than read "off" four times a minute until
            # the process exits. `nudge` starts a fresh one when the mode comes back on.
            with self._lock:
                if (
                    state is not None
                    and state.mode == "off"
                    and not self._held
                    and not self._wake.is_set()
                ):
                    self._thread = None
                    return
            self._wake.wait(self.poll)
            self._wake.clear()
        if self._held and self._inhibitor is not None:
            with contextlib.suppress(Exception):
                self._inhibitor.release()
            self._held = False

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop the thread and let go of the OS. The thread does the release: it is the owner."""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)


def cron_due_probe(
    scheduler: Any, *, lead: float = CRON_LEAD_SECONDS, clock: Callable[[], float] = time.time
) -> Callable[[], int]:
    """How many enabled cron jobs fire within ``lead`` seconds — or are overdue, which includes
    the one running now: the engine advances ``next_run`` only after the dispatch returns.

    Clock jobs only, because only they have a time to look ahead to. A job of any trigger is held
    while it RUNS by :func:`holding` around the dispatch that runs it."""

    def probe() -> int:
        horizon = clock() + lead
        return sum(
            1
            for job in scheduler.store.list()
            if job.enabled
            and job.trigger == "cron"
            and job.next_run is not None
            and job.next_run <= horizon
        )

    return probe


_service: KeepAwake | None = None
_service_lock = threading.Lock()


def service() -> KeepAwake:
    """The process's keeper. One per process because the OS state is.

    Its thread starts only when the mode is not ``off`` (:meth:`KeepAwake.ensure_running`): with
    the default, building the API leaves no thread behind and nothing reads the settings in the
    background."""
    global _service
    with _service_lock:
        if _service is None:
            from chimera.config import get_settings

            _service = KeepAwake(settings=get_settings)
            atexit.register(_service.shutdown, 2.0)
        keeper = _service
    keeper.ensure_running()
    return keeper


def holding(reason: str, fn: Callable[[_T], _R]) -> Callable[[_T], _R]:
    """``fn`` wrapped in a hold on the process's keeper — for the scheduler's dispatches.

    A job that is RUNNING holds the machine whatever fired it: the clock, a webhook, an event.
    :func:`cron_due_probe` sees clock jobs only, because only they have a ``next_run`` to look
    ahead to, and a webhook job that was already running held nothing. The keeper is looked up per
    call rather than when the wrapper is built, so wrapping costs nothing until a job runs.
    """

    def wrapped(arg: _T) -> _R:
        with service().hold(reason):
            return fn(arg)

    return wrapped
