"""The project's dev servers, started from its ``launch.json`` and nothing else.

Two doors lead here, and starting a server is RUNNING A COMMAND in both:

* **The owner's panel** on the Code screen (`chimera/api/preview_api.py`): a click, on the desktop
  app's own screen, in a folder whose commands are on, in a plain host sandbox. It calls
  :meth:`DevServerManager.start`, which spawns the child itself.
* **The agent's ``preview`` tool** (`chimera/tools/preview.py`): it does NOT spawn. It starts the entry
  through the turn's own ``run_shell`` (background), so the policy kernel, the taint ledger, the
  host-exec question, the manual-mode card and every fence ``run_shell`` has apply to it, and then
  :meth:`DevServerManager.adopt_job` records the job here so the panel and the tool see one list.

What a started server is, so nothing says more than it is:

* **A child with a tree.** The panel's child runs with the secret-scrubbed environment every other child
  of this app gets (`redact.scrubbed_environ`) plus the entry's own ``env`` and ``PORT``; no console
  window; its own process group on POSIX and a kill-on-close Job Object on Windows, and a start where
  that Job Object cannot be made FAILS CLOSED (the child is killed). Stopping, and the app closing, reach
  every descendant, including when the direct child has already died (`exec_stream._kill_run` and the
  job object).
* **Its output is a bounded tail** (the last :data:`LOG_LINES` lines, in memory).
* **"running" means something answered.**
* **Ports.** ``autoPort`` true/absent: the configured port if free, else the next free one;
  ``autoPort: false``: refuse when it is busy. The process learns its port from ``PORT`` and from a
  ``${PORT}`` placeholder in its arguments.
* **Whose port the browser may use.** A port is a TURN's only while a process of THE CHILD'S OWN TREE
  listens on it (:meth:`DevServer.tree_ports`) and the turn started the server or asked for it by name
  (:class:`DevServerTurn`). A server attached by ``url`` is someone else's process: it is never a turn's
  port, and attaching to one is the owner's click, on a port that is not a Chimera server's.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import socket
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from chimera.core.launch_config import ServerConfig, resolve_cwd
from chimera.core.redact import scrubbed_environ
from chimera.proc.decode import console_line
from chimera.proc.stdio import (
    CmdArgumentRefused,
    batch_safe_arguments,
    no_window,
    resolve_program,
)
from chimera.telemetry import get_logger

_log = get_logger("core.dev_servers")

LOG_LINES = 300
#: How far past the configured port the search for a free one goes.
PORT_SEARCH = 50
#: Servers alive at once, in the whole app.
MAX_RUNNING = 6

State = Literal["stopped", "starting", "running", "exited", "failed", "timed_out"]


class DevServerError(Exception):
    """A server that could not be started. The message is a sentence for the person."""


@dataclass
class DevServer:
    root: Path
    config: ServerConfig
    port: int
    started_by: str
    proc: subprocess.Popen[str] | None = None
    job: Any = None  # the Windows Job Object around `proc`
    job_id: str | None = None  # a `run_shell` background job, when the agent's tool started it
    jobs: Any = None  # the registry that job lives in
    started_at: float = field(default_factory=time.time)
    log: deque[str] = field(default_factory=lambda: deque(maxlen=LOG_LINES))
    stopped: bool = False  # stopped on purpose, so a later exit is not read as a failure

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def pid(self) -> int | None:
        if self.proc is not None:
            return self.proc.pid
        if self.jobs is not None and self.job_id:
            job = self.jobs.get(self.job_id)
            return None if job is None else job.pid
        return None

    def exit_code(self) -> int | None:
        if self.proc is not None:
            return self.proc.poll()
        if self.jobs is not None and self.job_id:
            job = self.jobs.get(self.job_id)
            return None if job is None else job.exit_code
        return None

    def _job_state(self) -> str:
        if self.jobs is not None and self.job_id:
            job = self.jobs.get(self.job_id)
            return "" if job is None else str(job.state)
        return ""

    def limit_seconds(self) -> float | None:
        """The maximum runtime the job registry enforces on this server's job, when it has one."""
        if self.jobs is not None and self.job_id:
            job = self.jobs.get(self.job_id)
            if job is not None and job.max_runtime:
                return float(job.max_runtime)
        return None

    def _alive(self) -> bool:
        if self.proc is not None:
            return self.proc.poll() is None
        if self.jobs is not None and self.job_id:
            job = self.jobs.get(self.job_id)
            return job is not None and job.state == "running"
        return False

    def state(self) -> State:
        if self.config.attach_only:
            return "running" if _listening(self.port) else "stopped"
        if not self._alive():
            if self.limit_seconds() is not None and self._job_state() == "timed_out":
                return "timed_out"  # ended by the job registry's maximum runtime, not by anyone's choice
            code = self.exit_code()
            return "exited" if (code == 0 or self.stopped or code is None) else "failed"
        return "running" if _listening(self.port) else "starting"

    def tree_ports(self) -> frozenset[int]:
        """Loopback ports a process of THIS server's own tree listens on. Empty without psutil, for an
        attached server and for one that is not alive: no tree, no port."""
        if self.config.attach_only or not self._alive():
            return frozenset()
        if self.jobs is not None and self.job_id:
            from chimera.tools.jobs import process_tree_listening_ports

            return process_tree_listening_ports(self.jobs, self.root, current_job_ids={self.job_id})
        return listening_ports_of_tree(self.proc.pid) if self.proc is not None else frozenset()


def psutil_available() -> bool:
    """Whether the process tree can be inspected. Without it no port can be confirmed as a server's own."""
    try:
        import psutil  # noqa: F401
    except ImportError:
        return False
    return True


def listening_ports_of_tree(pid: int) -> frozenset[int]:
    """Loopback/any-address ports a live process of ``pid``'s tree listens on."""
    try:
        import psutil
    except ImportError:
        return frozenset()
    ports: set[int] = set()
    try:
        root = psutil.Process(pid)
        tree = [root, *root.children(recursive=True)]
    except (OSError, psutil.Error):
        return frozenset()
    for proc in tree:
        try:
            connections = getattr(proc, "net_connections", None) or proc.connections
            for conn in connections(kind="inet"):
                if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.ip in {
                    "127.0.0.1", "::1", "0.0.0.0", "::",  # noqa: S104 — comparing, not binding
                }:
                    ports.add(conn.laddr.port)
        except (OSError, AttributeError, psutil.Error):
            continue
    return frozenset(ports)


def _listening(port: int) -> bool:
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.15)
                if sock.connect_ex((host, port)) == 0:
                    # A connect to a closed ephemeral-range port can "succeed" by connecting the socket
                    # to ITSELF (TCP simultaneous open on loopback): not a listener.
                    if sock.getsockname() == sock.getpeername():
                        continue
                    return True
        except OSError:
            continue
    return False


def _port_free(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            # No SO_REUSEADDR: on Windows it would let a bind succeed over a live listener.
            sock.bind(("127.0.0.1", port))
    except OSError:
        return False
    return not _listening(port)


def choose_port(config: ServerConfig) -> int:
    """The port this start will use, or :class:`DevServerError` when ``autoPort: false`` finds it busy."""
    if _port_free(config.port):
        return config.port
    if config.autoPort is False:
        raise DevServerError(
            f"port {config.port} is busy and this entry has autoPort: false, so it was not started"
        )
    for port in range(config.port + 1, min(config.port + 1 + PORT_SEARCH, 65536)):
        if _port_free(port):
            return port
    raise DevServerError(f"no free port found from {config.port} to {config.port + PORT_SEARCH}")


def _pump(stream: Any, server: DevServer) -> None:
    try:
        for raw in stream:
            server.log.append(console_line(raw).rstrip("\r\n")[:2000])
    except (OSError, ValueError):
        pass
    finally:
        with contextlib.suppress(Exception):
            stream.close()


def _kill_tree(server: DevServer) -> None:
    """Kill ``server``'s whole tree, whether or not its direct child is still alive."""
    from chimera.api.exec_stream import _kill_run

    if server.jobs is not None and server.job_id:
        with contextlib.suppress(Exception):
            server.jobs.cancel(server.job_id, reason="dev server stopped")
        return
    if server.job is not None:
        with contextlib.suppress(Exception):
            server.job.terminate()
    if server.proc is not None:
        # POSIX: the group is addressable while any member lives, even with the leader gone. Windows: the
        # job object above has already ended every member; `_kill_run` covers a live direct child.
        _kill_run(server.proc)
    if server.job is not None:
        with contextlib.suppress(Exception):
            server.job.close()
        server.job = None


class DevServerManager:
    """Every dev server the app started or recorded, by (folder, name)."""

    def __init__(self) -> None:
        self._servers: dict[tuple[str, str], DevServer] = {}
        self._lock = threading.RLock()
        self._turn_watch: dict[str, Callable[[], list[str]]] = {}

    @staticmethod
    def _key(root: Path, name: str) -> tuple[str, str]:
        return (str(root.resolve()), name)

    def get(self, root: Path, name: str) -> DevServer | None:
        with self._lock:
            return self._servers.get(self._key(root, name))

    def watch_turn(self, root: Path, changed: Callable[[], list[str]]) -> None:
        """The newest agent turn in ``root`` says which recipe or script files changed since it began."""
        with self._lock:
            self._turn_watch[str(root.resolve())] = changed

    def changed_since_turn(self, root: Path) -> list[str]:
        with self._lock:
            watch = self._turn_watch.get(str(root.resolve()))
        try:
            return watch() if watch is not None else []
        except Exception:  # noqa: BLE001 — a warning is never worth failing the listing
            return []

    def running_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._servers.values() if not s.config.attach_only and s._alive())

    def start(
        self,
        root: Path,
        config: ServerConfig,
        *,
        started_by: str,
        forbidden_ports: Iterable[int] = (),
    ) -> DevServer:
        """The owner's start: spawn ``config`` in ``root``, or return the server already running under that name.

        An attached entry (``url``) is only recorded, never started, and only on a port that is not one
        of ``forbidden_ports`` (the ports Chimera's own servers listen on).
        """
        with self._lock:
            existing = self._servers.get(self._key(root, config.name))
            if existing is not None and existing.state() in ("starting", "running"):
                return existing
            if config.attach_only:
                if config.port in set(forbidden_ports):
                    raise DevServerError(f"port {config.port} is one Chimera itself serves on; it is never attached")
                if not _listening(config.port):
                    raise DevServerError(f"nothing is answering on port {config.port} for {config.name!r}")
                server = DevServer(root.resolve(), config, config.port, started_by)
                self._servers[self._key(root, config.name)] = server
                return server
            if self.running_count() >= MAX_RUNNING:
                raise DevServerError(f"{MAX_RUNNING} dev servers are already running; stop one first")
            return self._spawn(root, config, started_by)

    def adopt_job(
        self, root: Path, config: ServerConfig, port: int, *, job_id: str, jobs: Any, started_by: str
    ) -> DevServer:
        """Record a server the agent's ``run_shell`` background job started, so there is one list."""
        server = DevServer(root.resolve(), config, port, started_by, job_id=job_id, jobs=jobs)
        server.log.append(f"(job {job_id}; output: job_status)")
        with self._lock:
            self._servers[self._key(root, config.name)] = server
        return server

    def _spawn(self, root: Path, config: ServerConfig, started_by: str) -> DevServer:
        port = choose_port(config)
        cwd = resolve_cwd(root, config.cwd)
        if cwd is None or not cwd.is_dir():
            raise DevServerError(f"the working directory of {config.name!r} is not a folder inside the project")
        argv = config.argv(port)
        program = resolve_program(argv[0])
        try:
            args = batch_safe_arguments(program, argv[1:])
        except CmdArgumentRefused as exc:
            raise DevServerError(str(exc)) from exc
        env = {**scrubbed_environ(), **config.env, "PORT": str(port), "BROWSER": "none"}
        posix = os.name == "posix"
        try:
            proc = subprocess.Popen(
                [program, *args],
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="surrogateescape",
                bufsize=1,
                start_new_session=posix,
                **no_window(),
            )
        except OSError as exc:
            raise DevServerError(f"{argv[0]!r} could not be started: {exc.strerror or exc.__class__.__name__}") from exc
        server = DevServer(root.resolve(), config, port, started_by, proc=proc)
        server.log.append(f"$ {config.command_line(port)}   (port {port})")
        from chimera.proc import winjob

        server.job = winjob.contain(proc.pid)
        if server.job is None and os.name == "nt":
            # No Job Object, no way to promise the tree dies with the app or with Stop: fail closed.
            _kill_tree(server)
            raise DevServerError("the server was not started: it could not be placed in a Job Object")
        assert proc.stdout is not None
        threading.Thread(target=_pump, args=(proc.stdout, server), name=f"devserver-{config.name}", daemon=True).start()
        self._servers[self._key(root, config.name)] = server
        return server

    def stop(self, root: Path, name: str) -> bool:
        """Stop ``name`` and everything it started. False when there was nothing running."""
        with self._lock:
            server = self._servers.get(self._key(root, name))
        if server is None:
            return False
        server.stopped = True
        if server.config.attach_only:
            with self._lock:
                self._servers.pop(self._key(root, name), None)
            return True
        was_alive = server._alive()
        _kill_tree(server)
        if server.proc is not None:
            with contextlib.suppress(Exception):
                server.proc.wait(timeout=5)
        return was_alive

    def tail(self, root: Path, name: str, lines: int = 80) -> list[str]:
        server = self.get(root, name)
        if server is None:
            return []
        if server.jobs is not None and server.job_id:
            with contextlib.suppress(Exception):
                text = str(server.jobs.tail(server.job_id))
                return text.splitlines()[-max(1, min(lines, LOG_LINES)):]
        return list(server.log)[-max(1, min(lines, LOG_LINES)):]

    def servers_of(self, root: Path) -> list[DevServer]:
        key = str(root.resolve())
        with self._lock:
            return [s for (r, _), s in self._servers.items() if r == key]

    def close_all(self) -> None:
        """Stop every server's tree. The app is closing; nothing here may outlive it.

        Every spawned server, not only the ones whose direct child is alive: a launcher that exited and
        left its worker holding the port is the case this exists for.
        """
        with self._lock:
            servers = list(self._servers.values())
        for server in servers:
            if server.config.attach_only:
                continue
            server.stopped = True
            with contextlib.suppress(Exception):
                _kill_tree(server)


_MANAGER: DevServerManager | None = None
_MANAGER_LOCK = threading.Lock()


def manager() -> DevServerManager:
    """The one manager of this process, with its ``atexit`` hook registered once."""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = DevServerManager()
            atexit.register(_MANAGER.close_all)
        return _MANAGER


class DevServerTurn:
    """The servers one turn may point its browser at: the ones it started, or asked for by name.

    A port counts only while a process of the server's OWN tree listens on it, so a server somebody
    attached by ``url`` (or any other process on the port) is never the turn's. A turn that never calls
    ``preview`` has no ports, and the browser keeps the rule it had.
    """

    def __init__(self, root: Path, mgr: DevServerManager | None = None) -> None:
        self.root = root.resolve()
        self.token = f"turn-{id(self):x}"
        self._manager = mgr or manager()
        self._names: set[str] = set()

    def adopt(self, name: str) -> None:
        self._names.add(name)

    def started_here(self, name: str) -> bool:
        server = self._manager.get(self.root, name)
        return server is not None and server.started_by == self.token

    def ports(self) -> frozenset[int]:
        out: set[int] = set()
        for name in tuple(self._names):
            server = self._manager.get(self.root, name)
            if server is not None and server.state() == "running" and server.port in server.tree_ports():
                out.add(server.port)
        return frozenset(out)


def server_url(server: DevServer) -> str:
    """Where the page is: loopback on the port actually chosen, over the scheme an attached entry names."""
    scheme = "https" if (server.config.url or "").startswith("https:") else "http"
    return f"{scheme}://localhost:{server.port}"


def state_payload(server: DevServer | None, config: ServerConfig) -> dict[str, Any]:
    """What a screen or a tool says about one configured entry."""
    kind = "attach" if config.attach_only else "process"
    if server is None:
        return {
            "name": config.name, "state": "stopped", "port": config.port, "kind": kind,
            "pid": None, "url": None, "exit_code": None, "started_by": "", "limit_hours": None,
        }
    state = server.state()
    return {
        "name": config.name,
        "state": state,
        "port": server.port,
        "kind": kind,
        "pid": server.pid,
        "url": server_url(server) if state == "running" else None,
        "exit_code": server.exit_code(),
        "started_by": server.started_by,
        "limit_hours": None if (limit := server.limit_seconds()) is None else round(limit / 3600),
    }
