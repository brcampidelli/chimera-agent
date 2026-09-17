"""Background jobs: a shell command the agent starts and does not wait for.

Item 6(b) of the list audited on 2026-09-16. A turn ran one tool at a time and `run_shell` waited
for the command, so "download the 100 images and, while that runs, draft the post" was a sentence
the loop could not act on: the download held the turn, the person held the chat. A background job
is the same command — judged by the same kernel, the same taint ledger and the same host-exec gate,
because the tool call the wrappers see is `run_shell` with `background: true` and the command
string unchanged — started detached, with its output on disk, and the turn moves on.

What a job is, precisely, so nothing here is more than it says:

- **Started detached, in its own process group**, with the sandbox's own child environment (the
  secret-scrubbed, non-interactive one `LocalSandbox` builds) and the workspace-relative `cwd` the
  foreground tool resolves. Output — stdout and stderr, interleaved — goes to
  ``<home>/jobs/<id>.log``; the record to ``<home>/jobs/<id>.json``.
- **Not cancelled by the turn's Stop.** That is the point of it, and it is said on the receipt of
  the tool call and in the record: a job outlives the turn that started it. ``job_cancel`` is what
  ends one, by killing the whole tree (the same `kill_tree` the foreground path uses on a timeout).
- **Host sandbox only.** An isolated sandbox runs a command to completion inside a container;
  there is no detached form of that here, and pretending with a thread would make the container's
  lifetime the job's. The tool says so and refuses, rather than running the job somewhere else.
- **Known to the next turn.** A finished job the person has not been told about is one nobody
  told anyone about: the Code turn reads ``finished_unreported()`` and hands the model a line per
  job, and marks them reported. The API lists them for a screen that wants to.
- **Honest about a previous process.** A record whose process this Python did not start (the app
  restarted) is ``lost`` once its pid is gone: the exit code was never seen, and saying "finished"
  would invent one.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("core.jobs")

#: How much of a job's log the status tool hands back. The whole file is on disk for `read_file`.
TAIL_CHARS = 4_000


@dataclass
class Job:
    id: str
    command: str
    cwd: str
    pid: int
    started_at: float
    log: str
    #: ``running`` | ``finished`` | ``cancelled`` | ``lost``
    state: str = "running"
    exit_code: int | None = None
    finished_at: float | None = None
    #: Whether the finish has been handed to a turn already. False until `finished_unreported`
    #: reports it, so a job ends up in exactly one turn's notice.
    reported: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobRegistry:
    """The jobs of one home, on disk, with the live handles of the ones this process started."""

    def __init__(self, home: Path) -> None:
        self.root = Path(home) / "jobs"
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._lock = threading.Lock()

    # --- starting ---------------------------------------------------------------------------

    def start(self, command: str, *, cwd: Path, env: dict[str, str], argv: list[str] | str,
              shell: bool) -> Job:
        """Spawn ``argv`` detached and record it. Raises ``OSError`` when the spawn itself fails —
        the tool turns that into an error observation, the way a foreground failure is."""
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
                start_new_session=(os.name == "posix"),
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        job = Job(
            id=job_id, command=command, cwd=str(cwd), pid=proc.pid,
            started_at=time.time(), log=str(log_path),
        )
        with self._lock:
            self._procs[job_id] = proc
            self._write(job)
        _log.info("job %s started (pid %s): %s", job_id, proc.pid, command[:120])
        return job

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

    def tail(self, job_id: str, chars: int = TAIL_CHARS) -> str:
        """The last ``chars`` of the job's log, or ``""``."""
        job = self.get(job_id)
        if job is None:
            return ""
        try:
            data = Path(job.log).read_bytes()
        except OSError:
            return ""
        from chimera.proc.decode import console_text

        text = console_text(data)
        return text[-chars:] if len(text) > chars else text

    def finished_unreported(self) -> list[Job]:
        """Jobs that ended and have not been handed to a turn yet — and are, now."""
        out: list[Job] = []
        for job in self.all():
            if job.state in ("finished", "cancelled", "lost") and not job.reported:
                job.reported = True
                with self._lock:
                    self._write(job)
                out.append(job)
        return out

    # --- ending -----------------------------------------------------------------------------

    def cancel(self, job_id: str) -> Job | None:
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
                # A previous process started it: nothing to signal through, only a pid that may
                # by now belong to somebody else — refused rather than guessed at.
                job.state = "lost"
                job.finished_at = time.time()
                self._write(job)
                return job
            from chimera.proc.stdio import kill_tree

            kill_tree(proc)
            job.state = "cancelled"
            job.exit_code = proc.poll()
            job.finished_at = time.time()
            self._write(job)
            return job

    # --- storage ----------------------------------------------------------------------------

    def _path(self, job_id: str) -> Path:
        safe = "".join(ch for ch in job_id if ch.isalnum())[:32]
        if not safe:
            raise ValueError("a job id must contain at least one usable character")
        return self.root / f"{safe}.json"

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
        tmp = self._path(job.id).with_suffix(".tmp")
        tmp.write_text(json.dumps(job.to_dict()), encoding="utf-8")
        tmp.replace(self._path(job.id))

    def _refresh(self, job: Job) -> None:
        """Bring a running job's state up to date from its process — or from the fact that this
        process never held it."""
        if job.state != "running":
            return
        proc = self._procs.get(job.id)
        if proc is None:
            if not _pid_alive(job.pid):
                job.state = "lost"
                job.finished_at = time.time()
                self._write(job)
            return
        code = proc.poll()
        if code is None:
            return
        job.state = "finished"
        job.exit_code = code
        job.finished_at = time.time()
        self._write(job)


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
        return True
    # Windows: a handle that can be opened is a process that exists.
    import ctypes

    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # type: ignore[attr-defined]
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
    return True


_REGISTRIES: dict[str, JobRegistry] = {}
_REGISTRIES_LOCK = threading.Lock()


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
