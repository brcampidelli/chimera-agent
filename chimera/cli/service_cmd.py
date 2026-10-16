"""``chimera service`` — the desktop backend without a window, one per data folder (S1).

``start`` runs the same server ``chimera app`` runs, detached from the terminal that asked for it:
its own process group (its own session on POSIX), stdin closed, and its output in
``<home>/logs/service.log`` — never a pipe, which would fill and block the server the day nobody
reads it, or kill it when the reader exits. ``stop`` asks it to finish (``POST
/api/service/stop?drain=1`` waits for running turns), ``status`` reports what the record says and
whether the server agrees, ``restart`` is the two in order. ``run`` is what ``start`` spawns.

Which server is THE server is decided by the data-folder claim (``chimera/core/instance.py``), not
by the port: a second ``start`` that loses the claim finds the winner's record, checks it with
``GET /api/info`` and says it is already running. Two ``start`` commands at once therefore leave one
process, whichever won.

Hidden from ``chimera --help`` for now, as ``api``, ``logs`` and ``policy`` are: the top-level list
is a ratchet (``tests/test_s30_70_cli_structure.py``) and nothing a person reaches for daily is
here until the desktop app starts the service itself.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Literal

import typer
from rich.console import Console

from chimera.core.service_record import (
    CHANNEL_ENV,
    DEFAULT_PORTS,
    ServiceRecord,
    channel,
    normal_home,
    pid_alive,
    probe,
    read_record,
    record_path,
    remove_record,
    remove_record_if_ours,
)

if TYPE_CHECKING:
    from chimera.config import Settings
    from chimera.core.instance import HomeClaim
    from chimera.core.service_watch import ServicePublication

service_app = typer.Typer(help="The background service: the desktop backend, without a window.", no_args_is_help=True)
console = Console()

AppMode = Literal["app", "service", "standalone"]
LOG_FILE = "service.log"
#: A log past this size is moved to ``service.log.1`` before the next start appends to it.
LOG_ROTATE_BYTES = 5 * 1024 * 1024
#: Exit codes ``run`` uses when it loses the claim, so ``start`` can tell them apart.
ALREADY_RUNNING = 0
FOLDER_IN_USE = 3


@dataclass(frozen=True)
class AppServerOptions:
    """The flags of ``chimera app``, so ``chimera service run`` serves exactly the same app."""

    host: str = "127.0.0.1"
    port: int = 8765
    model: str | None = None
    max_steps: int = 6
    workspace: str | None = None
    fuse: bool = False
    no_memory: bool = False
    cron: bool | None = None
    open_browser: bool = True
    emit_port_file: str | None = None


# ---- the server side: called from `_run_app_server` in service mode ------------------------------


def prepare_service(host: str, claim: HomeClaim, settings: Settings) -> tuple[str, Settings]:
    """Loopback only, and the owner's token in place before anything is built or served."""
    from chimera.config import get_settings
    from chimera.core.service_watch import service_token

    if host not in {"127.0.0.1", "localhost", "::1"}:
        claim.release()
        console.print("[red]chimera service listens on loopback only.[/red]")
        raise typer.Exit(code=2)
    token = service_token(settings.server_token, Path(settings.home))
    return token, get_settings()


def publish_service(
    api: Any, *, url: str, token: str, home: Path, bridge: str, launcher_key: str | None = None
) -> ServicePublication:
    """Write the record, start its watch, and give the API what its service routes read.

    ``launcher_key`` is the secret the desktop shell started this service with, if it did — by
    default the one the app's credential took from the environment at build
    (``screen_credential.for_mode``): the only thing that lets a window lease this service a screen
    credential (``chimera/api/service_windows.py``). In memory only.
    """
    from chimera.api.service_windows import ServiceWindows
    from chimera.config import get_settings
    from chimera.core.service_watch import ServicePublication

    # No screen of its own, so no click approves without the question's code: the token this
    # publishes sits on disk, readable by any local process (`chimera/api/screen_credential.py`).
    # Before the record exists, so no client can find this server while it still holds one.
    screen = api.state.screen_credential
    screen.revoke()
    desktop_bridge = api.state.desktop_bridge
    # The bridge waits for a window: published when the first one connects, retracted after the
    # last — "Allow Claude" is for a window the owner has open (decision of 2026-10-16).
    api.state.publish_desktop_bridge = lambda: desktop_bridge.attach(bridge)
    windows = ServiceWindows(
        screen,
        launcher_key=launcher_key if launcher_key is not None else screen.launcher_key,
        bearer=lambda: get_settings().server_token,
        on_first=api.state.publish_desktop_bridge,
        on_last=desktop_bridge.detach,
    )
    api.state.service_windows = windows
    publication = ServicePublication.start(
        url=url, token=token, home=home, on_step_down=api.state.request_shutdown
    )
    windows.start()
    publication.closers.append(windows.close)
    api.state.service_info.update(id=publication.record.id, started_at=publication.record.started_at)
    api.state.on_server_token_changed = publication.token_changed
    return publication


def record_install(port: int, mode: AppMode) -> None:
    """Once bound, record how a terminal starts THIS backend again (decision D9).

    Only a frozen build writes it (``chimera/core/desktop_install.py``), and never a ``standalone``
    server, which publishes nothing. A file it cannot write must not keep the app from starting.
    """
    if mode == "standalone":
        return
    from chimera.core.desktop_install import record_this_install

    try:
        record_this_install(port)
    except OSError as exc:
        console.print(f"[yellow]install record not written: {exc}[/yellow]")


def lost_the_claim(home: Path) -> int:
    """``run`` found the data folder claimed: name the running service, or say who else holds it."""
    record = read_record()
    if record is not None and record.home == normal_home(home) and probe(record).trusted:
        console.print(f"Chimera service already running at {record.url} (pid {record.pid}).")
        return ALREADY_RUNNING
    console.print(
        "[yellow]Another Chimera server holds this data folder[/yellow] (the desktop app, or a "
        "service still starting). Close it, or give this one another CHIMERA_HOME."
    )
    return FOLDER_IN_USE


# ---- the client side ------------------------------------------------------------------------------


def _home() -> Path:
    from chimera.config import get_settings

    return Path(get_settings().home).expanduser()


def log_path(home: Path) -> Path:
    return home / "logs" / LOG_FILE


def _open_log(home: Path) -> IO[bytes]:
    path = log_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > LOG_ROTATE_BYTES:
            os.replace(path, path.with_name(LOG_FILE + ".1"))
    except OSError:
        pass
    return path.open("ab")


def _port(port: int | None) -> int:
    """The channel's port unless one was given; ``0`` asks the OS for any free port."""
    return DEFAULT_PORTS[channel()] if port is None else port


@dataclass(frozen=True)
class Launcher:
    """What ``start`` spawns, from where, with what added to the environment, on which channel.

    ``install``: the desktop app's own backend, from ``desktop-install.json`` (decision D9) — its
    executable, its working folder (where its ``.env`` with the keys is) and its four paths.
    ``interpreter``: this process's own Chimera, as before the install record existed.
    """

    base: tuple[str, ...]
    channel: str
    cwd: str
    env: dict[str, str]
    port: int | None
    source: Literal["install", "interpreter"]

    @property
    def home(self) -> Path:
        named = self.env.get("CHIMERA_HOME")
        return Path(named).expanduser() if named else _home()


def _forced_channel() -> str | None:
    named = os.environ.get(CHANNEL_ENV, "").strip().lower()
    return named or None


def client_channel() -> str:
    """The channel ``start`` would publish under, for ``stop`` and ``status`` to look in.

    An install record means ``start`` launches the installed app's backend, which is ``stable`` —
    so a terminal running a checkout must look there too, or ``status`` right after ``start``
    would say "not running". ``$CHIMERA_SERVICE_CHANNEL`` still decides when set.
    """
    from chimera.core.desktop_install import install_path

    if _forced_channel() is None and install_path().is_file():
        return "stable"
    return channel()


def launcher() -> Launcher:
    """The install's backend when the record exists and checks out; this interpreter otherwise.

    ``$CHIMERA_SERVICE_CHANNEL=dev`` skips the record: a checkout on a machine with the app
    installed can still start itself. Raises :class:`InvalidInstallRecord` for a record that
    exists and must not be run — never a silent fall back to another program.
    """
    from chimera.core.desktop_install import load

    if _forced_channel() != "dev":
        install = load()
        if install is not None:
            return Launcher(
                base=(install.exe, "service", "run"), channel="stable", cwd=install.cwd,
                env=dict(install.env), port=install.port or None, source="install",
            )
    base = (
        (sys.executable, "service", "run")
        if getattr(sys, "frozen", False)
        else (sys.executable, "-m", "chimera.cli.main", "service", "run")
    )
    return Launcher(base=base, channel=channel(), cwd=os.getcwd(), env={}, port=None, source="interpreter")


def _run_command(port: int, workspace: str | None, launch: Launcher | None = None) -> list[str]:
    """What ``start`` spawns: the install's backend, the frozen backend's own ``service run``, or
    this interpreter's."""
    tail = ["--port", str(port)]
    if workspace:
        tail += ["--workspace", workspace]
    return [*(launch or launcher()).base, *tail]


def spawn_detached(command: list[str], home: Path, launch: Launcher | None = None) -> subprocess.Popen[bytes]:
    """Start ``command`` so it outlives this process and this terminal, its output in the log."""
    env = dict(os.environ)
    # Only the record's four paths are added, and they win: the point is the app's data folder.
    if launch is not None:
        env.update(launch.env)
    # The child decides its channel the same way, but a frozen parent spawning a source child (or
    # the reverse) must not publish under the other channel's name.
    env["CHIMERA_SERVICE_CHANNEL"] = launch.channel if launch is not None else channel()
    # Rich writes dashes and arrows; a log file opened as cp1252 would crash the server on one.
    env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    log = _open_log(home)
    common: dict[str, Any] = {
        "stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT,
        "env": env, "cwd": launch.cwd if launch is not None else os.getcwd(), "close_fds": True,
    }
    try:
        if sys.platform == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            try:  # out of a kill-on-close job (a terminal, an IDE), when the job allows it
                return subprocess.Popen(command, creationflags=flags | 0x01000000, **common)
            except OSError:
                return subprocess.Popen(command, creationflags=flags, **common)
        return subprocess.Popen(command, start_new_session=True, **common)
    finally:
        log.close()  # the child holds its own handle


def _report(record: ServiceRecord, state: str, as_json: bool, **extra: Any) -> None:
    if as_json:
        print(json.dumps({"state": state, **record.public(), **extra}))
    else:
        verb = "already running" if state == "already_running" else "running"
        console.print(f"Chimera service {verb} at {record.url} (pid {record.pid}, {record.version}).")


def _held_by_a_window(home: Path) -> str | None:
    """The URL of a desktop app (not a service) that serves this folder, if one does."""
    from chimera.core.instance import running_url

    url = running_url(home)
    if not url:
        return None
    from chimera.core.service_record import request

    status, info = request(url, "", "GET", "/api/info", timeout=2.0)
    if status == 200 and isinstance(info, dict) and info.get("mode") in {"app", "standalone"}:
        return url
    return None


def _kill_tree(child: subprocess.Popen[bytes]) -> None:
    """The child and what it started: a venv's ``python.exe`` on Windows is a launcher whose own
    child is the interpreter, so killing only the pid ``Popen`` returned would orphan the server."""
    try:
        import psutil

        for descendant in psutil.Process(child.pid).children(recursive=True):
            descendant.kill()
    except Exception:  # noqa: BLE001 - best effort: psutil absent, or the tree already gone
        pass
    child.kill()


def start_service(*, port: int | None, workspace: str | None, timeout: float, as_json: bool) -> int:
    from chimera.core.desktop_install import InvalidInstallRecord, install_path

    try:
        launch = launcher()
    except InvalidInstallRecord as exc:
        console.print(
            f"[red]Not starting: {install_path()} {exc}.[/red] Remove it to start this "
            f"interpreter's Chimera instead, or set {CHANNEL_ENV}=dev."
        )
        return 1
    home = launch.home
    where = record_path(launch.channel)
    existing = read_record(where)
    if existing is not None:
        checked = probe(existing)
        if checked.trusted and existing.home != normal_home(home):
            console.print(f"[red]A service for another data folder ({existing.home}) holds this channel.[/red]")
            return 1
        if checked.trusted:
            _report(existing, "already_running", as_json)
            return 0
        if checked.state == "stale":
            remove_record(where)
        elif checked.state != "unreachable":
            console.print(f"[red]The service record at {where} is not trusted: {checked.reason}.[/red]")
            return 1
    # Spawned even when the record's pid runs but does not answer: a service still starting keeps
    # the folder claim and this child exits with FOLDER_IN_USE; a reused pid holds no claim, and
    # this child becomes the service. The claim decides, so neither case needs a guess here.
    serve_port = port if port is not None else (launch.port or DEFAULT_PORTS[launch.channel])
    child = spawn_detached(_run_command(serve_port, workspace, launch), home, launch)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = read_record(where)
        if record is not None and record.home == normal_home(home) and probe(record, timeout=2.0).trusted:
            _report(record, "started", as_json, spawned_pid=child.pid)
            return 0
        code = child.poll()
        if code is not None and code not in (ALREADY_RUNNING, FOLDER_IN_USE):
            console.print(f"[red]The service exited with code {code}.[/red] See {log_path(home)}")
            return 1
        if code == FOLDER_IN_USE and (window := _held_by_a_window(home)):
            console.print(f"[yellow]The desktop app serves this data folder at {window}.[/yellow]")
            return 1
        time.sleep(0.25)
    if child.poll() is None:
        _kill_tree(child)  # never published: an orphan nobody can find is worse than no service
    console.print(f"[red]The service did not come up in {timeout:.0f} s.[/red] See {log_path(home)}")
    return 1


def stop_service(*, drain: bool, timeout: float) -> int:
    from chimera.core.service_record import call

    where = record_path(client_channel())
    record = read_record(where)
    if record is None:
        console.print("Chimera service is not running.")
        return 0
    checked = probe(record)
    if checked.state == "stale":
        remove_record(where)
        console.print("Chimera service is not running (removed a stale record).")
        return 0
    if not checked.trusted:
        console.print(f"[red]Not stopping: the record is not trusted ({checked.reason}).[/red]")
        return 1
    status, body = call(record, "POST", f"/api/service/stop?drain={int(drain)}")
    if status != 200:
        console.print(f"[red]The service refused to stop ({status}).[/red]")
        return 1
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and pid_alive(record.pid):
        time.sleep(0.2)
    if pid_alive(record.pid):
        waiting = body.get("draining", 0) if isinstance(body, dict) else 0
        console.print(
            f"[yellow]Still finishing {waiting} turn(s).[/yellow] `chimera service stop --now` stops at once."
        )
        return 1
    remove_record_if_ours(record.id, where)
    console.print("Chimera service stopped.")
    return 0


def service_status(as_json: bool) -> int:
    on = client_channel()
    record = read_record(record_path(on))
    if record is None:
        if as_json:
            print(json.dumps({"state": "not_running", "channel": on}))
        else:
            console.print("Chimera service is not running.")
        return 1
    checked = probe(record)
    home = Path(record.home)
    if as_json:
        print(json.dumps({
            "state": checked.state, "reason": checked.reason, "channel": on,
            "log": str(log_path(home)), **record.public(),
        }))
    else:
        # Plain print: a path wrapped at the terminal's width cannot be copied back.
        print(f"{'state':<11}{checked.state}" + (f" ({checked.reason})" if checked.reason else ""))
        for key, value in record.public().items():
            print(f"{key:<11}{value}")
        print(f"{'log':<11}{log_path(home)}")
    return 0 if checked.trusted else 1


# ---- the commands ---------------------------------------------------------------------------------


@service_app.command("start")
def start(
    port: int = typer.Option(None, "--port", help="Port to serve on (default: the channel's own)."),
    workspace: str = typer.Option(None, "--workspace", "-w", help="Workspace root for the tools."),
    timeout: float = typer.Option(120.0, "--timeout", help="Seconds to wait for it to answer."),
    as_json: bool = typer.Option(False, "--json", help="Print the outcome as JSON (no token)."),
) -> None:
    """Start the service in the background, or say where it already runs."""
    raise typer.Exit(code=start_service(port=port, workspace=workspace, timeout=timeout, as_json=as_json))


@service_app.command("stop")
def stop(
    now: bool = typer.Option(False, "--now", help="Do not wait for running turns to finish."),
    timeout: float = typer.Option(60.0, "--timeout", help="Seconds to wait for the process to exit."),
) -> None:
    """Ask the service to finish its running turns and exit."""
    raise typer.Exit(code=stop_service(drain=not now, timeout=timeout))


@service_app.command("restart")
def restart(
    port: int = typer.Option(None, "--port", help="Port to serve on (default: the channel's own)."),
    workspace: str = typer.Option(None, "--workspace", "-w", help="Workspace root for the tools."),
    timeout: float = typer.Option(120.0, "--timeout", help="Seconds to wait for each step."),
) -> None:
    """Stop the service (waiting for its turns) and start it again. The token does not change."""
    code = stop_service(drain=True, timeout=timeout)
    if code != 0:
        raise typer.Exit(code=code)
    raise typer.Exit(code=start_service(port=port, workspace=workspace, timeout=timeout, as_json=False))


@service_app.command("status")
def status(as_json: bool = typer.Option(False, "--json", help="Print as JSON (never the token).")) -> None:
    """Where the service is and whether it answers as its record says. Exit 1 when not running."""
    raise typer.Exit(code=service_status(as_json))


@service_app.command("run", hidden=True)
def run(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host: loopback only."),
    port: int = typer.Option(None, "--port", help="Port to serve on (default: the channel's own)."),
    workspace: str = typer.Option(None, "--workspace", "-w", help="Workspace root for the tools."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per message."),
    no_memory: bool = typer.Option(False, "--no-memory", help="Don't recall long-term memory."),
    cron: bool = typer.Option(None, "--cron/--no-cron", help="Default: the CHIMERA_APP_CRON setting."),
) -> None:
    """Serve in the foreground as the service (what ``start`` spawns)."""
    from chimera.cli.commands.serve import _run_app_server

    options = AppServerOptions(
        host=host, port=_port(port), model=model, max_steps=max_steps,
        workspace=workspace, no_memory=no_memory, cron=cron, open_browser=False,
    )
    _run_app_server(options, "service")
