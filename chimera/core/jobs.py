"""Background jobs: a shell command the agent starts and does not wait for.

Item 6(b) of the list audited on 2026-09-16. A turn ran one tool at a time and `run_shell` waited
for the command, so "download the 100 images and, while that runs, draft the post" was a sentence
the loop could not act on: the download held the turn, the person held the chat. A background job
is the same command — judged by the same kernel, the same taint ledger and the same host-exec gate,
because the tool call the wrappers see is `run_shell` with `background: true` and the command
string unchanged — started detached, with its output on disk, and the turn moves on.

What a job is, precisely, so nothing here is more than it says:

- **Started detached, in its own process group**, with the sandbox's own argv (the kernel sandbox's
  wrapper where there is one) and child environment (the secret-scrubbed, non-interactive one
  `LocalSandbox` builds), in the workspace-relative `cwd` the foreground tool resolves. Output —
  stdout and stderr, interleaved — goes to ``<home>/jobs/<id>.log``; the record to
  ``<home>/jobs/<id>.json``. The home is the app's data folder, never the workspace.
- **Watched.** A thread per job waits on the process and writes the ending the moment it happens —
  the exit code and the end time are what the process did, not when somebody next asked.
- **Bounded.** At most ``max_running`` jobs run at once (``CHIMERA_JOBS_MAX_RUNNING``, 3), and none
  runs longer than ``max_runtime`` seconds (``CHIMERA_JOBS_MAX_RUNTIME``, 6 h): the watcher kills
  the tree at the deadline and the record says ``timed_out``. Reads of the log are bounded too
  (:meth:`JobRegistry.read_log`): a job that printed a gigabyte is read as a slice, never loaded.
- **Not cancelled by the turn's Stop.** That is the point of it: a job outlives the turn that
  started it. ``job_cancel`` ends one, by killing the whole tree — on Windows through a Job Object
  (`chimera.proc.winjob`), which also reaches descendants whose parent already exited.
- **Not outliving the app.** A job is the app's work and dies with it: on Windows the job object is
  kill-on-close, so even a crash takes the tree with it; everywhere, interpreter exit kills what is
  still running (`chimera.proc.reap_all`) and records it as cancelled. What survives neither — a
  kill -9 of the backend on POSIX — is recognised on the next start (below).
- **Host sandbox family only.** An isolated container runs a command to completion inside itself;
  there is no detached form of that here. The tool says so and refuses, rather than running the job
  somewhere else. The kernel sandboxes (bubblewrap, Seatbelt) are a wrapper around the same process
  and run a job exactly as they run a foreground command.
- **Known to the next turn.** A finished job the person has not been told about is one nobody
  told anyone about: the Code turn reads ``finished_unreported()`` and hands the model a line per
  job, and marks them reported. The API lists them for a screen that wants to.
- **Honest about a previous process.** Every record names the process that started it (its
  ``owner``) and that process keeps a heartbeat beside the record while the job runs. A record whose
  owner is gone, or whose heartbeat went quiet, is ``lost``: the exit code was never seen, and
  saying "finished" would invent one — and "running" would claim a watcher that no longer exists.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from chimera.telemetry import get_logger

_log = get_logger("core.jobs")

#: How much of a job's log the status tool hands back by default. The whole file stays on disk.
TAIL_CHARS = 4_000

#: Defaults, and the ceilings a caller cannot raise (`CHIMERA_JOBS_MAX_RUNNING`/`_RUNTIME` in config).
DEFAULT_MAX_RUNNING = 3
DEFAULT_MAX_RUNTIME = 6 * 3600

#: Bounded log reads: lines asked for are capped, and so are the bytes read from each end.
MAX_HEAD_LINES = 200
MAX_TAIL_LINES = 500
READ_BYTES = 32_000

#: How often a running job's watcher touches its heartbeat, and how old a heartbeat may be before
#: the job is called lost. Module attributes so a test can shorten them.
HEARTBEAT_EVERY = 20.0
HEARTBEAT_STALE = 90.0

#: States in which a job is over.
ENDED = frozenset({"finished", "cancelled", "timed_out", "lost"})

#: Who this process is, written on every record it starts. The pid is how another process tells
#: whether the owner is still alive; the random part keeps a recycled pid from passing for it.
_PROCESS_TOKEN = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"


class JobLimitError(RuntimeError):
    """Refused before spawning: ``max_running`` jobs are already running."""

    def __init__(self, running: list[Job], limit: int) -> None:
        self.running = running
        self.limit = limit
        super().__init__(f"{len(running)} background jobs already running (limit {limit})")


@dataclass
class Job:
    id: str
    command: str
    cwd: str
    pid: int
    started_at: float
    log: str
    #: ``running`` | ``finished`` | ``cancelled`` | ``timed_out`` | ``lost``
    state: str = "running"
    exit_code: int | None = None
    finished_at: float | None = None
    #: Whether the finish has been handed to a turn already. False until `finished_unreported`
    #: reports it, so a job ends up in exactly one turn's notice.
    reported: bool = False
    #: The process that started it (``<pid>-<random>``); "" on records written before owners.
    owner: str = ""
    #: The runtime cap this job was started under, in seconds (0 = none).
    max_runtime: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LogSlice:
    """A bounded read of a job's log: the first and the last lines, never the whole file."""

    size: int
    head: str
    tail: str
    #: True when lines between ``head`` and ``tail`` were not read.
    gap: bool


class JobRegistry:
    """The jobs of one home, on disk, with the live handles of the ones this process started."""

    def __init__(
        self,
        home: Path,
        *,
        max_running: int = DEFAULT_MAX_RUNNING,
        max_runtime: float = DEFAULT_MAX_RUNTIME,
    ) -> None:
        self.root = Path(home) / "jobs"
        self.max_running = max_running
        self.max_runtime = max_runtime
        #: A limit is hard (it refuses, or kills) only when somebody set it. The two defaults above
        #: are advisory: said in the start message, never enforced. A registry built directly, with
        #: numbers, is a caller that meant them, so it is hard: `configure_from_settings` is the one
        #: door that can make it advisory.
        self.hard_running = True
        self.hard_runtime = True
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._contained: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()

    def configure(self, *, max_running: int | None = None, max_runtime: float | None = None) -> None:
        """Apply the deployment's limits. Values below 1 fall back to the defaults: a cap of zero
        would be a switch that turns the feature off by accident, and there is a better switch
        for that (`CHIMERA_TOOL_DENYLIST=run_shell`)."""
        if max_running is not None:
            self.max_running = max_running if max_running >= 1 else DEFAULT_MAX_RUNNING
        if max_runtime is not None:
            self.max_runtime = max_runtime if max_runtime >= 1 else DEFAULT_MAX_RUNTIME

    def configure_from_settings(self, max_running: int | None, max_runtime: int | None) -> None:
        """Apply ``CHIMERA_JOBS_MAX_RUNNING`` / ``CHIMERA_JOBS_MAX_RUNTIME``: a value the owner set is
        a hard limit, and an unset one leaves the default as a piece of advice.

        The owner decided on 2026-09-27 that "3 at once and 6 hours" stop being reasons for the agent
        to stop: a job that is refused, or killed at hour six, reads as the agent giving up on a task
        somebody handed it on purpose. A value below 1 is still "the default", as in `configure`.
        """
        self.hard_running = max_running is not None and max_running >= 1
        self.hard_runtime = max_runtime is not None and max_runtime >= 1
        self.max_running = max_running if self.hard_running and max_running else DEFAULT_MAX_RUNNING
        self.max_runtime = max_runtime if self.hard_runtime and max_runtime else DEFAULT_MAX_RUNTIME

    def over_advisory_limit(self) -> tuple[int, int] | None:
        """``(running, limit)`` when the next job would go past a limit nobody set, else None."""
        if self.hard_running:
            return None
        running = sum(1 for j in self.all() if j.state == "running")
        return (running, self.max_running) if running >= self.max_running else None

    # --- starting ---------------------------------------------------------------------------

    def start(self, command: str, *, cwd: Path, env: dict[str, str], argv: list[str] | str,
              shell: bool) -> Job:
        """Spawn ``argv`` detached and record it. Raises :class:`JobLimitError` when the cap is
        reached and ``OSError`` when the spawn itself fails — the tool turns both into an error
        observation, the way a foreground failure is."""
        from chimera.proc.stdio import _spawn_flags

        with self._start_lock:
            running = [j for j in self.all() if j.state == "running"]
            if self.hard_running and len(running) >= self.max_running:
                raise JobLimitError(running, self.max_running)
            self.root.mkdir(parents=True, exist_ok=True)
            job_id = uuid.uuid4().hex[:12]
            log_path = self.root / f"{job_id}.log"
            # The log handle belongs to the child from here on; this process closes its copy at once.
            with open(log_path, "wb") as log:
                proc = subprocess.Popen(
                    argv,
                    shell=shell,
                    cwd=str(cwd),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    env=env,
                    **_spawn_flags(),
                )
            from chimera.proc.winjob import contain

            contained = contain(proc.pid)
            job = Job(
                id=job_id, command=command, cwd=str(cwd), pid=proc.pid,
                started_at=time.time(), log=str(log_path), owner=_PROCESS_TOKEN,
                max_runtime=float(self.max_runtime) if self.hard_runtime else 0.0,
            )
            with self._lock:
                self._procs[job_id] = proc
                if contained is not None:
                    self._contained[job_id] = contained
                self._write(job)
                self._beat(job_id)
        _Reaped.track(self, job_id)
        threading.Thread(
            target=self._watch,
            args=(job_id, proc, float(self.max_runtime) if self.hard_runtime else 0.0),
            name=f"job-{job_id}", daemon=True,
        ).start()
        _log.info("job %s started (pid %s): %s", job_id, proc.pid, command[:120])
        return job

    def adopt(self, proc: subprocess.Popen[bytes], command: str, *, cwd: Path) -> Job | None:
        """Take over a FOREGROUND command that outlived its timeout, instead of killing it.

        The owner decided on 2026-09-27 that the shell timeout stops being a reason to stop: a
        command killed at sixty seconds is a benchmark stage killed at sixty seconds. The process is
        already running with its output on pipes this process reads, so the log is written when it
        ends (`communicate` picks up where the timed-out call left off, including what it had
        already read), and the job is otherwise an ordinary one: it is watched, it can be cancelled,
        and the next turn is told when it finishes. Returns None when a limit the owner SET forbids
        one more job, and the caller kills the command as it did before.
        """
        from chimera.proc.decode import console_text
        from chimera.proc.winjob import contain

        with self._start_lock:
            running = [j for j in self.all() if j.state == "running"]
            if self.hard_running and len(running) >= self.max_running:
                return None
            self.root.mkdir(parents=True, exist_ok=True)
            job_id = uuid.uuid4().hex[:12]
            log_path = self.root / f"{job_id}.log"
            log_path.write_bytes(b"")
            contained = contain(proc.pid)
            job = Job(
                id=job_id, command=command, cwd=str(cwd), pid=proc.pid,
                started_at=time.time(), log=str(log_path), owner=_PROCESS_TOKEN,
                max_runtime=float(self.max_runtime) if self.hard_runtime else 0.0,
                extra={"adopted": True},
            )
            with self._lock:
                self._procs[job_id] = proc
                if contained is not None:
                    self._contained[job_id] = contained
                self._write(job)
                self._beat(job_id)
        drained = threading.Event()

        def drain() -> None:
            try:
                raw_out, raw_err = proc.communicate()
                text = console_text(raw_out) + console_text(raw_err)
            except Exception as exc:  # noqa: BLE001 - a log that cannot be written is not a crash
                text = f"[the output could not be collected: {exc}]"
            with suppress(OSError):
                log_path.write_text(text, encoding="utf-8")
            drained.set()

        threading.Thread(target=drain, name=f"job-drain-{job_id}", daemon=True).start()
        _Reaped.track(self, job_id)
        threading.Thread(
            target=self._watch,
            args=(job_id, proc, float(self.max_runtime) if self.hard_runtime else 0.0, drained),
            name=f"job-{job_id}", daemon=True,
        ).start()
        _log.info("job %s adopted (pid %s): %s", job_id, proc.pid, command[:120])
        return job

    def _watch(
        self,
        job_id: str,
        proc: subprocess.Popen[bytes],
        max_runtime: float,
        drained: threading.Event | None = None,
    ) -> None:
        """Wait for the process, beating the heartbeat, and write how it ended. At the deadline,
        kill the tree and write ``timed_out``. The only writer of a natural ending."""
        deadline = time.monotonic() + max_runtime if max_runtime > 0 else None
        timed_out = False
        while True:
            wait = HEARTBEAT_EVERY
            if deadline is not None:
                wait = min(wait, max(0.0, deadline - time.monotonic()))
            try:
                proc.wait(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                pass
            if deadline is not None and time.monotonic() >= deadline:
                with self._lock:
                    job = self._read(job_id)
                    if job is not None and job.state == "running":
                        self._kill(job_id, proc)
                        timed_out = True
                break
            with self._lock:
                self._beat(job_id)
        with suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)
        if drained is not None:
            # An adopted command's log is written when its pipes close: the state is not final until
            # the output a reader will look for is on disk.
            drained.wait(timeout=30)
        with self._lock:
            contained = self._contained.pop(job_id, None)
            if contained is not None:
                contained.release()
            job = self._read(job_id)
            if job is not None and job.state == "running":
                job.state = "timed_out" if timed_out else "finished"
                job.exit_code = proc.returncode
                job.finished_at = time.time()
                self._write(job)
            self._drop_beat(job_id)
        _Reaped.untrack(self, job_id)

    # --- reading ----------------------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        """The job, with its state brought up to date. None when there is no such job."""
        with self._lock:
            job = self._read(job_id)
            if job is None:
                return None
            self._refresh(job)
            return job

    def all(self) -> list[Job]:
        """Every job of this home, newest first, states brought up to date."""
        if not self.root.is_dir():
            return []
        with self._lock:
            jobs: list[Job] = []
            for path in sorted(self.root.glob("*.json")):
                job = self._read(path.stem)
                if job is not None:
                    self._refresh(job)
                    jobs.append(job)
        return sorted(jobs, key=lambda j: j.started_at, reverse=True)

    def read_log(
        self, job_id: str, *, head_lines: int = 0, tail_lines: int = 40, max_bytes: int = READ_BYTES
    ) -> LogSlice | None:
        """The first ``head_lines`` and the last ``tail_lines`` lines of the log, reading at most
        ``max_bytes`` from each end of the file. None when there is no such job."""
        job = self.get(job_id)
        if job is None:
            return None
        head_lines = max(0, min(int(head_lines), MAX_HEAD_LINES))
        tail_lines = max(0, min(int(tail_lines), MAX_TAIL_LINES))
        max_bytes = max(1, min(int(max_bytes), READ_BYTES))
        from chimera.proc.decode import console_text

        try:
            with open(job.log, "rb") as fh:
                size = os.fstat(fh.fileno()).st_size
                if size <= max_bytes:
                    whole: bytes | None = fh.read(size)
                    head_raw = tail_raw = b""
                else:
                    whole = None
                    head_raw = fh.read(max_bytes) if head_lines else b""
                    fh.seek(size - max_bytes)
                    tail_raw = fh.read(max_bytes) if tail_lines else b""
        except OSError:
            return LogSlice(size=0, head="", tail="", gap=False)
        if whole is not None:
            # The whole log fits in one read: head and tail come from the same lines, never overlap.
            lines = console_text(whole).splitlines()
            if head_lines + tail_lines >= len(lines):
                return LogSlice(size=size, head="", tail="\n".join(lines), gap=False)
            return LogSlice(
                size=size,
                head="\n".join(lines[:head_lines]),
                tail="\n".join(lines[-tail_lines:]) if tail_lines else "",
                gap=True,
            )
        head = "\n".join(console_text(head_raw).splitlines()[:head_lines]) if head_lines else ""
        tail = ""
        if tail_lines:
            # The slice began mid-line (and maybe mid-character): drop that partial line.
            cut = tail_raw.find(b"\n")
            tail_raw = tail_raw[cut + 1:] if cut >= 0 else b""
            tail = "\n".join(console_text(tail_raw).splitlines()[-tail_lines:])
        return LogSlice(size=size, head=head, tail=tail, gap=True)

    def tail(self, job_id: str, chars: int = TAIL_CHARS) -> str:
        """The last ``chars`` of the job's log, or ``""``. Reads only the end of the file."""
        job = self.get(job_id)
        if job is None:
            return ""
        from chimera.proc.decode import console_text

        want = max(0, int(chars))
        try:
            with open(job.log, "rb") as fh:
                size = os.fstat(fh.fileno()).st_size
                start = max(0, size - want * 4)
                fh.seek(start)
                raw = fh.read(size - start)
        except OSError:
            return ""
        if start > 0:
            cut = raw.find(b"\n")
            raw = raw[cut + 1:] if cut >= 0 else raw
        text = console_text(raw)
        return text[-want:] if len(text) > want else text

    def finished_unreported(self, within: Path | None = None) -> list[Job]:
        """Jobs that ended and have not been handed to a turn yet — and are, now.

        ``within``: only jobs that ran inside this folder. A job's news used to go to the next turn
        of ANY project, which marked it reported and could not read its output (the job tools are
        fenced to the turn's folder), so the project that started it never heard.
        """
        root = Path(within).resolve() if within is not None else None
        out: list[Job] = []
        for job in self.all():
            if root is not None and not _inside(Path(job.cwd), root):
                continue
            if job.state in ENDED and not job.reported:
                job.reported = True
                with self._lock:
                    self._write(job)
                out.append(job)
        return out

    # --- ending -----------------------------------------------------------------------------

    def cancel(self, job_id: str, *, reason: str = "") -> Job | None:
        """Kill the job's whole process tree. None when unknown; a job already over is returned
        as it is, since there is nothing left to kill."""
        with self._lock:
            job = self._read(job_id)
            if job is None:
                return None
            self._refresh(job)
            if job.state != "running":
                return job
            proc = self._procs.get(job_id)
            if proc is None:
                # Another live process started it (its heartbeat is fresh): only that process can
                # signal it safely. A pid alone may by now belong to somebody else.
                job.extra["cancel_refused"] = "started by another process that is still running"
                return job
            self._kill(job_id, proc)
            with suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)
            job.state = "cancelled"
            job.exit_code = proc.poll()
            job.finished_at = time.time()
            if reason:
                job.extra["ended_by"] = reason
            self._write(job)
            self._drop_beat(job_id)
            return job

    def _kill(self, job_id: str, proc: subprocess.Popen[bytes]) -> None:
        """The job object first (every descendant), then the tree walk (anything it missed)."""
        from chimera.proc.stdio import kill_tree

        contained = self._contained.pop(job_id, None)
        if contained is not None:
            contained.terminate()
            contained.close()
        kill_tree(proc)

    # --- storage ----------------------------------------------------------------------------

    def _path(self, job_id: str, suffix: str = ".json") -> Path:
        safe = "".join(ch for ch in job_id if ch.isalnum())[:32]
        if not safe:
            raise ValueError("a job id must contain at least one usable character")
        return self.root / f"{safe}{suffix}"

    def _read(self, job_id: str) -> Job | None:
        try:
            path = self._path(job_id)
        except ValueError:
            return None
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Job(**{k: data[k] for k in Job.__dataclass_fields__ if k in data})
        except (OSError, ValueError, TypeError) as exc:
            _log.warning("job %s unreadable: %s", job_id, exc)
            return None

    def _write(self, job: Job) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._path(job.id, ".tmp")
        tmp.write_text(json.dumps(job.to_dict()), encoding="utf-8")
        tmp.replace(self._path(job.id))

    def _beat(self, job_id: str) -> None:
        with suppress(OSError, ValueError):
            self._path(job_id, ".beat").write_text(str(time.time()), encoding="utf-8")

    def _drop_beat(self, job_id: str) -> None:
        with suppress(OSError, ValueError):
            self._path(job_id, ".beat").unlink(missing_ok=True)

    def _beat_age(self, job: Job) -> float:
        try:
            return time.time() - self._path(job.id, ".beat").stat().st_mtime
        except (OSError, ValueError):
            return time.time() - job.started_at

    def _refresh(self, job: Job) -> None:
        """Decide whether a record that says ``running`` still has a watcher.

        One this process started has one — its thread writes the ending, so there is nothing to do.
        One another process started keeps its record only while that process is alive AND its
        heartbeat is fresh; otherwise nobody is watching it any more and it is ``lost``.
        """
        if job.state != "running" or job.id in self._procs:
            return
        owner_pid = _owner_pid(job.owner)
        if job.owner == _PROCESS_TOKEN:
            watched = True  # another registry object in this very process holds it
        elif owner_pid is not None:
            watched = _pid_alive(owner_pid) and self._beat_age(job) < HEARTBEAT_STALE
        else:
            # A record written before owners existed: the old rule, plus the heartbeat it lacks.
            watched = _pid_alive(job.pid) and self._beat_age(job) < HEARTBEAT_STALE
        if watched:
            return
        job.state = "lost"
        job.finished_at = time.time()
        job.extra["pid_alive_when_lost"] = _pid_alive(job.pid)
        self._write(job)
        self._drop_beat(job.id)


def _owner_pid(owner: str) -> int | None:
    head = (owner or "").split("-", 1)[0]
    return int(head) if head.isdigit() else None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "posix":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    # Windows: a handle that can be opened, on a process that has not exited, is a live process.
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
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


class _Reaped:
    """What interpreter exit kills: the jobs this process started that are still running.

    Registered with `chimera.proc.track`, the one exit-time reaper, so a job does not outlive the
    app on a platform without kill-on-close job objects. The record says why it ended.
    """

    _live: ClassVar[dict[tuple[int, str], _Reaped]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, registry: JobRegistry, job_id: str) -> None:
        self.registry = registry
        self.job_id = job_id

    @classmethod
    def track(cls, registry: JobRegistry, job_id: str) -> None:
        from chimera.proc.stdio import track

        handle = cls(registry, job_id)
        with cls._lock:
            cls._live[(id(registry), job_id)] = handle
        track(handle)

    @classmethod
    def untrack(cls, registry: JobRegistry, job_id: str) -> None:
        from chimera.proc.stdio import untrack

        with cls._lock:
            handle = cls._live.pop((id(registry), job_id), None)
        if handle is not None:
            untrack(handle)

    def close(self, timeout: float = 1.0) -> None:
        with suppress(Exception):
            self.registry.cancel(self.job_id, reason="app_exit")


_REGISTRIES: dict[str, JobRegistry] = {}
_REGISTRIES_LOCK = threading.Lock()


def _inside(path: Path, root: Path) -> bool:
    """The same rule the job tools apply to a turn's folder."""
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved == root or resolved.is_relative_to(root)


def jobs_for(home: Path) -> JobRegistry:
    """One registry per home per process — the live handles have to live somewhere shared by the
    tool that starts a job and the tool, the API and the turn that ask about it."""
    key = str(Path(home).resolve())
    with _REGISTRIES_LOCK:
        registry = _REGISTRIES.get(key)
        if registry is None:
            registry = JobRegistry(Path(home))
            _REGISTRIES[key] = registry
        return registry


def finished_note(home: Path, within: Path) -> str:
    """The turn note for the background jobs in ``within`` that ended since a turn last looked.

    ``""`` when none did. Each job is reported once (``finished_unreported`` marks it). The model is
    told to read the output rather than guess at what the job produced, and through ``job_status``,
    not ``read_file``: the log lives in the data folder, outside the workspace, so a read_file of it
    is a jail question for something the job tool reads freely.

    Here rather than in the coding route, which was the only caller. A shell command that outlives
    its timeout becomes a job on every surface, and the terminal and the chat bot never said when
    one finished, so the news went to nobody.
    """
    finished = jobs_for(home).finished_unreported(within=within)
    if not finished:
        return ""
    lines = [
        f"- job {j.id} {j.state}"
        + (f" (exit {j.exit_code})" if j.exit_code is not None else "")
        + f": {j.command[:160]}"
        for j in finished
    ]
    return (
        "Background jobs that finished since your last turn (read their output with "
        "job_status(job_id=...) before saying what they produced):\n" + "\n".join(lines)
    )
