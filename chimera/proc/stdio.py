"""A child process that speaks line-delimited JSON, and lives longer than one call.

Everything here is about the two ways this goes wrong in the field, neither of which is the protocol:

**The child outlives us.** A coding agent forks its own workers. Killing the process we started
leaves those running — holding the workspace, holding a model connection, and on Windows holding a
console window that the user cannot connect to anything. So the process is started in its own
group, killed as a tree, and registered so that interpreter exit takes it with us.

**The child cannot be started at all.** On Windows `npx` is `npx.CMD`, and `CreateProcess` does not
consult ``PATHEXT`` — ``Popen(["npx", ...])`` raises ``FileNotFoundError``, measured, while the same
call with the resolved path works. That failure looks exactly like "the adapter is not installed",
which is the wrong thing to tell someone who installed it.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import signal
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("proc.stdio")

_POSIX = os.name == "posix"

#: Lines of the child's stderr kept for diagnosis. A misbehaving adapter is diagnosed from its last
#: words, and an unbounded buffer is how a chatty one eats the host's memory over a long session.
_STDERR_KEEP = 200

#: Characters accepted in a single stdout line. An adapter that emits a megabyte on one line is
#: broken; reading it into memory to discover that is a second bug on top of the first.
_MAX_LINE = 4_000_000

#: Everything started through this module and not yet reaped. Weak references would be wrong here:
#: the whole point is that an abandoned process still gets killed, so the registry must be what
#: keeps it reachable.
_LIVE: list[Any] = []
_LIVE_LOCK = threading.Lock()

#: The cmd.exe metacharacters that turn an argument into a command. A double quote is deliberately
#: NOT here: Python quotes arguments with it, so banning it would refuse ordinary values while
#: catching nothing — the first version of this list did exactly that and broke every launch.
_CMD_META = set("&|<>^%")


def resolve_program(name: str) -> str:
    """The path to run for ``name``, or ``name`` unchanged when nothing resolves it.

    Returning the name unchanged rather than raising is deliberate: "not on PATH" and "on PATH but
    refuses to start" are different diagnoses, and the caller is better placed to say which — it
    knows what it was trying to launch and why.
    """
    if os.sep in name or (os.altsep and os.altsep in name):
        return name  # already a path; let the OS have the last word on whether it exists
    return shutil.which(name) or name


def is_batch_launcher(program: str) -> bool:
    """Whether running ``program`` means running cmd.exe.

    This is the whole scope of the hazard below, and getting it wrong in the safe-looking direction
    is expensive: a first version applied the argument ban to EVERY program on Windows, which
    refused perfectly ordinary launches — `CreateProcess` never involves a shell, so a `python -c`
    with an ampersand in the source was rejected for a risk that could not exist.
    """
    return not _POSIX and program.lower().endswith((".cmd", ".bat"))


def unsafe_windows_argument(argument: str, program: str = "") -> bool:
    """Whether ``argument`` would be read as shell syntax on its way to ``program``.

    Launching `npx` on Windows means launching `npx.CMD`, and cmd.exe parses the command line even
    though `shell=False`. An argument containing `&` is therefore a second command, and someone
    configuring an adapter's arguments would be writing a command line without knowing it.

    With no ``program`` this answers about the characters alone, which is what the caller wants when
    validating configuration before there is anything to launch.
    """
    if program and not is_batch_launcher(program):
        return False
    if not program and _POSIX:
        return False
    return any(c in _CMD_META for c in argument)


def _spawn_flags() -> dict[str, Any]:
    """Platform flags that make a child killable as a tree and invisible as a window."""
    if _POSIX:
        # Its own session and process group, so a kill reaches the grandchildren too.
        return {"start_new_session": True}
    flags = 0
    # CREATE_NO_WINDOW: without it a packaged (windowed) build flashes a console for every child.
    # CREATE_NEW_PROCESS_GROUP: makes the child the root of a group, which is what taskkill /T walks.
    flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return {"creationflags": flags}


def kill_tree(proc: subprocess.Popen[Any]) -> None:
    """Kill a child AND everything it started. Never raises.

    `Popen.kill()` kills one process. A coding agent is a launcher — `npx` starts node, node starts
    workers — so killing the one we hold leaves the ones doing the work, and the workspace stays
    locked by a process nobody can see. This is the difference between stopping an agent and
    orphaning it.

    ⚠️ **PRECONDITION: spawn the child with :func:`_spawn_flags` (or `start_new_session=True`).**
    On POSIX this sends `SIGKILL` to the child's whole process *group*, and a child that was not
    given its own group is still in **yours** — so this kills the caller. That is not theoretical:
    it took down a test runner during this function's own test, and the only symptom was an exit
    code with no output, which is about as hard to read as a failure gets.

    Every caller in this package already isolates (`exec_stream`, `sandbox/local`, `lsp/transport`
    all pass the flags). The requirement is written here because nothing enforces it, and the cost
    of forgetting is paid by the wrong process.
    """
    if proc.poll() is not None:
        return
    if _POSIX:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)  # type: ignore[attr-defined]
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass  # fall through to the single-process kill; better than nothing
    else:
        # taskkill rather than a Job Object: it is present on every supported Windows, needs no
        # ctypes, and `/T` is exactly "and its children". The cost is one short-lived process.
        with suppress(Exception):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.poll() is not None:
                return
    with suppress(Exception):
        proc.kill()


def track(child: Any) -> None:
    """Register anything with a ``close(timeout)`` for the exit-time reaper.

    Exists so there is exactly ONE reaper. A language server frames its messages with
    `Content-Length` in BYTES, which needs a binary stream, while an ACP agent sends one JSON object
    per line, which needs a text one — so the two cannot share a reader. They can and must share the
    thing that guarantees neither survives the app: a second registry is a second list somebody
    forgets to add to.
    """
    with _LIVE_LOCK:
        _LIVE.append(child)


def untrack(child: Any) -> None:
    """Forget a child that closed itself. Idempotent."""
    with _LIVE_LOCK, suppress(ValueError):
        _LIVE.remove(child)


def reap_all() -> None:
    """Kill everything still running. Registered with :mod:`atexit`, and callable directly.

    The registration is the point. A server that crashes, a test that forgets to close, a desktop
    app the user quits mid-turn — all of them end here rather than in a process the user has to
    find in a task manager to explain why their folder is locked.
    """
    with _LIVE_LOCK:
        live = list(_LIVE)
        _LIVE.clear()
    for child in live:
        with suppress(Exception):
            child.close(timeout=1.0)


atexit.register(reap_all)


class StdioProcess:
    """A child process exchanging one JSON object per line on stdin/stdout.

    Reading happens on a thread and arrives through ``on_message``, because the protocols that use
    this frame are bidirectional: the child sends requests of its own, not only answers, so there is
    no call to hang the read off.
    """

    def __init__(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        on_message: Callable[[dict[str, Any]], None],
        on_exit: Callable[[int], None] | None = None,
        name: str = "child",
    ) -> None:
        if not argv:
            raise ValueError("argv must name a program")
        self.argv = list(argv)
        self.cwd = cwd
        self.env = env
        self.name = name
        self._on_message = on_message
        self._on_exit = on_exit
        self._proc: subprocess.Popen[str] | None = None
        self._write_lock = threading.Lock()
        self._stderr: deque[str] = deque(maxlen=_STDERR_KEEP)
        self._closed = threading.Event()
        #: Lines the child wrote to stdout that were not JSON. An adapter that prints an npm notice
        #: before its first frame is not broken, and one that prints a stack trace is — both look
        #: the same to the parser, so both are kept and neither is thrown away.
        self.noise: deque[str] = deque(maxlen=_STDERR_KEEP)

    # --- lifetime ---------------------------------------------------------------------------

    def start(self) -> StdioProcess:
        """Launch the child. Raises ``FileNotFoundError`` when the program does not resolve."""
        program = resolve_program(self.argv[0])
        offenders = [a for a in self.argv[1:] if unsafe_windows_argument(a, program)]
        if offenders:
            # Refused rather than escaped: quoting rules for cmd.exe differ per launcher, and a
            # guess that gets it wrong turns an argument into a command on the user's machine.
            raise ValueError(f"argument contains shell syntax and cannot be passed safely: {offenders[0]!r}")

        self._proc = subprocess.Popen(  # noqa: S603 — argv list, never a shell string
            [program, *self.argv[1:]],
            cwd=str(self.cwd) if self.cwd else None,
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            # `errors="replace"`: a byte the child got wrong must not kill the reader thread, or one
            # mangled log line silently ends the conversation.
            errors="replace",
            bufsize=1,
            **_spawn_flags(),
        )
        track(self)
        threading.Thread(target=self._read_stdout, name=f"{self.name}-out", daemon=True).start()
        threading.Thread(target=self._read_stderr, name=f"{self.name}-err", daemon=True).start()
        return self

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def poll(self) -> int | None:
        """The child's exit code, or None while it is running."""
        return self._proc.poll() if self._proc else None

    def close(self, timeout: float = 5.0) -> None:
        """Ask the child to stop, then make it stop.

        Closing stdin first is what lets a well-behaved adapter finish its own shutdown — flushing a
        session, releasing a lock. The kill is what happens to one that does not, and it is not
        optional: waiting forever on a child that has decided not to exit is how an app that looks
        idle is actually holding a folder.
        """
        self._closed.set()
        proc = self._proc
        if proc is None:
            return
        untrack(self)
        with suppress(Exception), self._write_lock:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            with suppress(Exception):
                proc.wait(timeout=timeout)

    # --- messages ---------------------------------------------------------------------------

    def send(self, message: dict[str, Any]) -> None:
        """Write one JSON object, one line. Raises ``BrokenPipeError`` once the child is gone."""
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise BrokenPipeError(f"{self.name} is not running")
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._write_lock:
            if proc.stdin.closed:
                raise BrokenPipeError(f"{self.name} closed its input")
            proc.stdin.write(line)
            proc.stdin.flush()

    def stderr_tail(self, lines: int = 20) -> str:
        """The child's last words. What a failure report should carry instead of a bare exit code."""
        tail = list(self._stderr)[-lines:] + list(self.noise)[-lines:]
        return "\n".join(tail).strip()

    # --- reader threads ---------------------------------------------------------------------

    def _read_stdout(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            for line in proc.stdout:
                if len(line) > _MAX_LINE:
                    self.noise.append(f"[dropped a {len(line)}-character line]")
                    continue
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    message = json.loads(stripped)
                except json.JSONDecodeError:
                    # Not a protocol frame. Adapters print notices, npm prints warnings, and a
                    # crashing one prints a traceback — all on stdout, all indistinguishable here.
                    self.noise.append(stripped[:2000])
                    continue
                if not isinstance(message, dict):
                    self.noise.append(stripped[:2000])
                    continue
                try:
                    self._on_message(message)
                except Exception as exc:  # noqa: BLE001 — a handler must not kill the reader
                    _log.warning("%s: message handler failed: %s", self.name, exc)
        except (ValueError, OSError):
            # The pipe was closed under us — a kill, or the child exiting mid-line. Not an error to
            # report: `close()` and the exit callback below are the ones that say what happened.
            pass
        finally:
            code = proc.wait() if proc.poll() is None else proc.returncode
            if self._on_exit is not None and not self._closed.is_set():
                with suppress(Exception):
                    self._on_exit(code if code is not None else -1)

    def _read_stderr(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        with suppress(ValueError, OSError):
            for line in proc.stderr:
                self._stderr.append(line.rstrip("\n")[:2000])
