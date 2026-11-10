"""Real terminals (a PTY) for the Code screen: the owner's shell, not the agent's.

`exec_stream.py` is an honest command-runner: each call is a fresh process, nothing is interactive.
This module is the other thing — a long-lived shell behind a pseudo-terminal, so `cd` and `export`
stick, `python` and `vim` and `ssh` work, and the window can be resized. It is for the OWNER typing.
Nothing here is reachable by the agent, the Claude bridge, a channel bot, a guest or a paired device
(see `terminal_api.py` for the door); this file only manages processes.

Backends, one per platform, behind one small class:

* POSIX: `os.openpty()` + `subprocess.Popen(start_new_session=True)`, the child being its own session
  and group leader with the pty as controlling terminal. Standard library only.
* Windows: ConPTY through `pywinpty` (the `desktop` extra, Windows only). Without it the terminal is
  *unavailable*, and says so with the install hint; it never falls back to a pipe that would look like
  a terminal and behave like the Runner (that is the half-terminal `Runner.tsx` refused to be).

**Closing means the whole tree.** A shell is a launcher; the dev server it started is what holds the
port. `close()` reuses `exec_stream._kill_run` — the group kill on POSIX, `taskkill /T` on Windows —
for the same reason that function exists. Sessions also die with their stream, after a grace for a
session nobody attached to, and with the process (`close_all`, wired to app shutdown and `atexit`).

**What is never logged: what is typed or printed.** Input goes to the pty and nowhere else. The log
line for a session carries its id, the shell's name and nothing the person wrote.
"""

from __future__ import annotations

import atexit
import codecs
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from chimera.api.exec_stream import _kill_run
from chimera.proc.stdio import no_window
from chimera.telemetry import get_logger

_log = get_logger("api.pty_terminal")

#: Tabs at once, across the whole app. A terminal is a process tree; unbounded tabs are a fork bomb
#: with a friendly face. Eight is more than anyone reads, and the screen shows the same number.
MAX_SESSIONS = 8
#: A session nobody has attached a stream to within this long is closed. A client that created one and
#: lost the page must not leave a shell behind.
ATTACH_GRACE_S = 30.0
#: One write from a client. A paste of this size is already a very large paste.
MAX_INPUT_CHARS = 65_536
#: Output chunks waiting for the stream. A full queue blocks the reader thread, which blocks the shell
#: on its next write — what any terminal does when the window is not reading — instead of growing
#: memory for an output nobody drains (`yes`, `cat` of a large file).
_QUEUE_CHUNKS = 512
_READ_BYTES = 16_384
COLS_RANGE = (10, 500)
ROWS_RANGE = (2, 200)
DEFAULT_SIZE = (80, 24)
INSTALL_HINT = "terminal on Windows needs pywinpty: pip install 'chimera-agent[desktop]'"


class TerminalUnavailable(RuntimeError):
    """This platform or install cannot host a terminal. The message is safe to show the owner."""


class TerminalLimitReached(RuntimeError):
    """The session cap is full."""


def _clamp(value: int, bounds: tuple[int, int]) -> int:
    return max(bounds[0], min(bounds[1], int(value)))


def _shell_env() -> dict[str, str]:
    """The environment of the owner's shell: the process's, minus provider secrets.

    The same scrub the agent's shell gets (`redact.scrubbed_environ`) — a key that lives in the app's
    environment does not need to be one `env` away from every command typed — but WITHOUT that
    function's non-interactive overrides (`PAGER=cat`, `GIT_EDITOR=true`, `CI=1`): those exist so a
    command nobody is watching cannot block, and here somebody is watching.
    """
    from chimera.core.redact import scrubbed_environ

    env = scrubbed_environ()
    env.setdefault("TERM", "xterm-256color")
    env["COLORTERM"] = "truecolor"
    env["CHIMERA_TERMINAL"] = "1"
    return env


def default_shell() -> list[str]:
    """The argv of the shell a new tab runs."""
    if os.name == "nt":
        for name in ("pwsh", "powershell"):
            found = shutil.which(name)
            if found:
                return [found, "-NoLogo"]
        return [os.environ.get("COMSPEC") or "cmd.exe"]
    configured = os.environ.get("SHELL", "")
    if configured and Path(configured).is_file():
        return [configured]
    for candidate in ("/bin/bash", "/bin/sh"):
        if Path(candidate).is_file():
            return [candidate]
    return ["sh"]


def available() -> tuple[bool, str]:
    """Whether this machine can host a terminal, and the owner-facing reason when it cannot."""
    if os.name == "nt":
        import importlib.util

        if importlib.util.find_spec("winpty") is None:
            return False, INSTALL_HINT
    return True, ""


def _session_members(sid: int) -> list[int]:
    """Every live process in the shell's session, the shell included. POSIX only.

    The group kill in `_kill_run` is not enough for an INTERACTIVE shell: with job control on, bash
    and dash put each background job (`npm run dev &`) in a group of its own, so `killpg(shell)`
    misses exactly the processes a person leaves running and forgets. They all stay in the shell's
    session, which is what this walks. Needs `psutil` (the `desktop` extra); without it, nothing
    extra is killed and the group kill is all there is.
    """
    try:
        import psutil
    except ImportError:
        return []
    found: list[int] = []
    for pid in psutil.pids():
        with suppress(OSError, ProcessLookupError):
            if os.getsid(pid) == sid:  # type: ignore[attr-defined,unused-ignore]  # POSIX-only
                found.append(pid)
    return found


def _kill_pids(pids: list[int]) -> None:
    import signal

    for pid in pids:
        with suppress(OSError):
            os.kill(pid, signal.SIGKILL)  # type: ignore[attr-defined,unused-ignore]  # POSIX-only


class _WinProc:
    """The few `Popen` members `_kill_run` touches, over a pywinpty process.

    The shell lives in a kill-on-close Job Object (`chimera.proc.winjob`, which the background jobs
    already use). `taskkill /T` follows parent links, and a background process whose parent, the shell,
    has exited is no longer on that walk: `exit` after `ping -t ...` would leave it running. The job
    holds every descendant whatever became of its parent, and because the handle is this backend's,
    the kernel ends the job if the backend itself dies, which no cleanup code can promise.
    """

    def __init__(self, pty: Any) -> None:
        from chimera.proc.winjob import contain

        self._pty = pty
        self.pid: int = int(pty.pid)
        self.job = contain(self.pid)

    def terminate_tree(self) -> None:
        if self.job is not None:
            self.job.terminate()
            self.job.close()
            self.job = None

    def poll(self) -> int | None:
        try:
            return None if self._pty.isalive() else int(self._pty.exitstatus or 0)
        except Exception:  # noqa: BLE001 — a dead handle is a dead process
            return 0

    def kill(self) -> None:
        with suppress(Exception):
            self._pty.terminate(force=True)


class PtySession:
    """One shell behind one pseudo-terminal."""

    def __init__(self, cwd: Path, cols: int, rows: int, argv: list[str] | None = None) -> None:
        self.id = uuid.uuid4().hex
        self.cwd = cwd
        self.created = time.monotonic()
        self._attached = False
        self._io_lock = threading.Lock()
        #: When the folder's commands were last re-checked for this session (`terminal_api`).
        self.checked_at = 0.0
        self.exit_code: int | None = None
        self._out: queue.Queue[str | None] = queue.Queue(maxsize=_QUEUE_CHUNKS)
        self._closed = threading.Event()
        self._close_lock = threading.Lock()
        argv = argv or default_shell()
        self.shell = Path(argv[0]).name
        cols = _clamp(cols, COLS_RANGE)
        rows = _clamp(rows, ROWS_RANGE)
        self._proc: Any
        self._master = -1
        self._pty: Any = None
        if os.name == "nt":
            self._spawn_windows(argv, cols, rows)
        else:
            self._spawn_posix(argv, cols, rows)
        self._reader = threading.Thread(target=self._pump, name=f"pty-{self.id[:6]}", daemon=True)
        self._reader.start()

    # -- spawn ---------------------------------------------------------------------------------

    def _spawn_posix(self, argv: list[str], cols: int, rows: int) -> None:
        import fcntl
        import pty
        import termios

        master, slave = pty.openpty()  # type: ignore[attr-defined]  # POSIX-only; never runs on Windows
        try:
            self._set_winsize_posix(master, cols, rows)

            def _controlling_tty() -> None:  # runs in the child, after setsid
                with suppress(OSError):
                    fcntl.ioctl(0, termios.TIOCSCTTY, 0)  # type: ignore[attr-defined]  # POSIX-only

            self._proc = subprocess.Popen(
                argv,
                cwd=self.cwd,
                env=_shell_env(),
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
                start_new_session=True,  # its own group: the kill reaches the grandchildren
                preexec_fn=_controlling_tty,  # noqa: PLW1509 — the controlling tty cannot be set otherwise
                **no_window(),
            )
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        self._master = master

    def _spawn_windows(self, argv: list[str], cols: int, rows: int) -> None:
        import importlib

        try:
            winpty = importlib.import_module("winpty")
        except ImportError as exc:
            raise TerminalUnavailable(INSTALL_HINT) from exc
        self._pty = winpty.PtyProcess.spawn(argv, cwd=str(self.cwd), env=_shell_env(), dimensions=(rows, cols))
        self._proc = _WinProc(self._pty)

    # -- output --------------------------------------------------------------------------------

    def _put(self, item: str | None) -> bool:
        """Queue one chunk, waiting while the stream is behind. False once the session is closing."""
        while not self._closed.is_set():
            try:
                self._out.put(item, timeout=0.25)
                return True
            except queue.Full:
                continue
        return False

    def _pump(self) -> None:
        try:
            if os.name == "nt":
                self._pump_windows()
            else:
                self._pump_posix()
        finally:
            self.exit_code = self._wait_exit()
            self._closed_output()

    def _pump_posix(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        import selectors

        # `selectors` (epoll/kqueue/poll), NOT `select.select`: select raises ValueError for a
        # descriptor numbered 1024 or more, and a long-lived backend with many files and sockets open
        # (the full test suite, a busy app) gets such a number for the pty. The reader then died on its
        # first iteration, silently, and the terminal showed nothing at all.
        with selectors.DefaultSelector() as selector:
            selector.register(self._master, selectors.EVENT_READ)
            while not self._closed.is_set():
                try:
                    # A timeout, so `close()` is noticed even when a grandchild still holds the slave
                    # side open and the read would never see EOF.
                    if not selector.select(timeout=0.25):
                        continue
                    raw = os.read(self._master, _READ_BYTES)
                except (OSError, ValueError):
                    return  # EIO: every writer on the slave side is gone
                if not raw:
                    return
                text = decoder.decode(raw)
                if text and not self._put(text):
                    return

    def _pump_windows(self) -> None:
        while not self._closed.is_set():
            try:
                text = self._pty.read(_READ_BYTES)
            except EOFError:
                return
            except Exception:  # noqa: BLE001 — a closed handle ends the stream, it is not a crash
                return
            if text and not self._put(text):
                return

    def _closed_output(self) -> None:
        # The end marker must get through even to a full queue, or the stream would wait forever.
        with suppress(queue.Full):
            self._out.put(None, timeout=1.0)

    def _wait_exit(self) -> int | None:
        if self._proc is None:
            return None
        for _ in range(20):
            code = self._proc.poll()
            if code is not None:
                return int(code)
            time.sleep(0.05)
        return None

    def read(self, timeout: float) -> str | None:
        """Next chunk of output; `""` after `timeout` with nothing; `None` at the end of the session."""
        try:
            item = self._out.get(timeout=timeout)
        except queue.Empty:
            return ""
        if item is None:
            # Leave the marker for a second reader (a re-attach after the end still ends).
            with suppress(queue.Full):
                self._out.put_nowait(None)
        return item

    # -- attach --------------------------------------------------------------------------------

    @property
    def attached(self) -> bool:
        return self._attached

    def claim(self) -> bool:
        """Take the one output stream. False when another already has it. Atomic."""
        with self._close_lock:
            if self._attached or self._closed.is_set():
                return False
            self._attached = True
            return True

    # -- input ---------------------------------------------------------------------------------

    def write(self, data: str) -> None:
        """Send what the person typed. Never logged, never inspected.

        Under the I/O lock, the same one `close()` takes before it releases the descriptor, so a write
        can never land on a number that was closed and handed to something else.
        """
        data = data[:MAX_INPUT_CHARS]
        with self._io_lock:
            if self._closed.is_set():
                raise TerminalUnavailable("the terminal is closed")
            if os.name == "nt":
                self._pty.write(data)
                return
            view = memoryview(data.encode("utf-8", errors="replace"))
            while view:
                try:
                    sent = os.write(self._master, view)
                except BlockingIOError:
                    if self._closed.is_set():
                        raise TerminalUnavailable("the terminal is closed") from None
                    time.sleep(0.005)
                    continue
                except OSError as exc:
                    raise TerminalUnavailable("the terminal is closed") from exc
                view = view[sent:]

    @staticmethod
    def _set_winsize_posix(fd: int, cols: int, rows: int) -> None:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))  # type: ignore[attr-defined]  # POSIX-only

    def resize(self, cols: int, rows: int) -> None:
        cols = _clamp(cols, COLS_RANGE)
        rows = _clamp(rows, ROWS_RANGE)
        with self._io_lock:
            if self._closed.is_set():
                return
            with suppress(Exception):
                if os.name == "nt":
                    self._pty.setwinsize(rows, cols)
                else:
                    self._set_winsize_posix(self._master, cols, rows)

    # -- end -----------------------------------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def close(self) -> None:
        """Kill the shell and everything it started, then release the pty. Idempotent.

        Blocks for up to a second on POSIX (the SIGTERM grace in `_kill_run`); callers on an event
        loop use `PtyManager.close_in_background`.
        """
        with self._close_lock:
            if self._closed.is_set():
                return
            self._closed.set()
        members = _session_members(self._proc.pid) if os.name == "posix" else []
        _kill_run(self._proc)
        _kill_pids(members)
        if isinstance(self._proc, _WinProc):
            self._proc.terminate_tree()
        with suppress(Exception):
            if isinstance(self._proc, subprocess.Popen):
                self._proc.wait(timeout=2)
        # The reader leaves on its own (the flag, the select timeout); the descriptor is closed
        # only after it has, so no thread is ever reading a number that was reassigned.
        self._reader.join(timeout=2)
        with self._io_lock:
            if os.name == "nt":
                with suppress(Exception):
                    self._pty.close(force=True)
            elif self._master >= 0:
                with suppress(OSError):
                    os.close(self._master)
                self._master = -1
        self._closed_output()


class PtyManager:
    """The open terminals of this app, with a cap."""

    def __init__(self, *, max_sessions: int = MAX_SESSIONS, attach_grace: float = ATTACH_GRACE_S) -> None:
        self._sessions: dict[str, PtySession] = {}
        self._lock = threading.Lock()
        self.max_sessions = max_sessions
        self.attach_grace = attach_grace

    def open(self, cwd: Path, cols: int = DEFAULT_SIZE[0], rows: int = DEFAULT_SIZE[1],
             argv: list[str] | None = None) -> PtySession:
        ok, why = available()
        if not ok:
            raise TerminalUnavailable(why)
        self._reap_unattached()
        with self._lock:
            live = [s for s in self._sessions.values() if not s.closed]
            if len(live) >= self.max_sessions:
                raise TerminalLimitReached(f"at most {self.max_sessions} terminals at once")
            # Reserved under the lock so two racing opens cannot both take the last slot.
            session = PtySession(cwd, cols, rows, argv)
            self._sessions[session.id] = session
        # Its own timer, not a sweep that waits for the next `open()`: a client that created a shell
        # and lost the page may never open another, and the shell must not live until it does.
        timer = threading.Timer(self.attach_grace, self._reap_if_unattached, args=(session.id,))
        timer.daemon = True
        timer.start()
        _log.info("terminal opened id=%s shell=%s", session.id[:8], session.shell)
        return session

    def get(self, session_id: str) -> PtySession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def close(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        session.close()
        _log.info("terminal closed id=%s", session_id[:8])
        return True

    def close_in_background(self, session_id: str) -> None:
        """`close`, not waited for: for the event loop, where a kill must not freeze other requests."""
        threading.Thread(target=self.close, args=(session_id,), name="pty-close", daemon=True).start()

    def close_all(self) -> None:
        """Close every shell, in parallel: each close waits up to a second on the kill, and eight of
        them in a row would hold app shutdown for the better part of ten."""
        with self._lock:
            ids = list(self._sessions)
        threads = [
            threading.Thread(target=self.close, args=(sid,), name="pty-close", daemon=True) for sid in ids
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

    def count(self) -> int:
        with self._lock:
            return sum(1 for s in self._sessions.values() if not s.closed)

    def _reap_if_unattached(self, session_id: str) -> None:
        session = self.get(session_id)
        if session is not None and not session.attached:
            _log.info("terminal never attached, closing id=%s", session_id[:8])
            self.close(session_id)

    def _reap_unattached(self) -> None:
        """Forget sessions that already ended, so their slots are free."""
        with self._lock:
            stale = [sid for sid, s in self._sessions.items() if s.closed]
        for sid in stale:
            self.close_in_background(sid)


_manager: PtyManager | None = None
_manager_lock = threading.Lock()


def manager() -> PtyManager:
    """The app-wide manager (one per process, like the Runner's registry)."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = PtyManager()
            atexit.register(_manager.close_all)
        return _manager


__all__ = [
    "MAX_SESSIONS",
    "PtyManager",
    "PtySession",
    "TerminalLimitReached",
    "TerminalUnavailable",
    "available",
    "default_shell",
    "manager",
]
