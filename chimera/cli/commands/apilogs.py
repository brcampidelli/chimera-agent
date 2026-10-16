"""Direct local API calls and inspection of the backend's durable warning log.

These commands stay separate from the server command: using them must never start a listener or
change the governance decisions made by the API itself.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import time
from pathlib import Path
from typing import Annotated, NoReturn, TextIO
from urllib.parse import urlsplit

import httpx
import typer
from rich.markup import escape

from chimera.cli.commands._shared import app, console
from chimera.config import get_settings
from chimera.core.instance import running_url

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_METHODS = frozenset({"GET", "HEAD", "OPTIONS"}) | _MUTATING_METHODS

#: The service's documented port, used when no running server has announced its real one.
_DEFAULT_URL = "http://127.0.0.1:8765"

#: A record written by `chimera.file_log` starts with its `asctime`; a traceback line does not.
_RECORD_START = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]")
_LEVEL = re.compile(r"\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\b")
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "WARN": 30, "ERROR": 40, "CRITICAL": 50}


def _is_loopback(host: str | None) -> bool:
    """Whether ``host`` reaches only this machine (the rule of ``access_api.is_loopback_host``,
    repeated here so the CLI does not import the API layer and its optional dependencies)."""
    if not host:
        return False
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip().strip("[]")).is_loopback
    except ValueError:
        return False


def _parse_headers(headers: list[str] | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in headers or []:
        key, separator, value = item.partition(":")
        if not separator or not key.strip():
            console.print("[red]Invalid header; use -H 'Key: Value'.[/red]")
            raise typer.Exit(2)
        parsed[key.strip()] = value.strip()
    return parsed


def _base_url(url: str | None, home: Path) -> str:
    """The URL to connect to: the flag, else the one a running server announced, else the default.

    Desktop/serve publish their actual bound URL in CHIMERA_HOME/desktop.url (a busy 8765 falls
    back to a free port). A wildcard bind address is for listening, not connecting, and this
    command runs on the same host as the server, so it becomes loopback.
    """
    base = url or running_url(home) or _DEFAULT_URL
    if base.startswith("http://0.0.0.0:"):
        return base.replace("http://0.0.0.0:", "http://127.0.0.1:", 1)
    if base.startswith("http://[::]:"):
        return base.replace("http://[::]:", "http://[::1]:", 1)
    return base


@app.command(hidden=True)
def api(
    method: Annotated[str, typer.Argument(help="HTTP method, e.g. GET or POST.")],
    path: Annotated[str, typer.Argument(help="API path, e.g. /api/health.")],
    data: Annotated[str | None, typer.Option("--data", "-d", help="JSON request body.")] = None,
    headers: Annotated[
        list[str] | None,
        typer.Option("--header", "-H", help="Request header as 'Key: Value'. Repeatable."),
    ] = None,
    url: Annotated[str | None, typer.Option("--url", help="Server base URL.")] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Confirm a state-changing request.")
    ] = False,
) -> None:
    """Call a local Chimera API endpoint without displaying its access token.

    A mutating HTTP method is a request to change server state, not merely a diagnostic. Requiring
    an explicit --yes prevents an innocent-looking terminal query from approving a change by
    accident; the API's own authorization and governance remain authoritative after this check.
    """
    verb = method.upper()
    if verb not in _METHODS:
        console.print("[red]Unsupported HTTP method.[/red]")
        raise typer.Exit(2)
    if verb in _MUTATING_METHODS and not yes:
        console.print(
            f"[yellow]{verb} can change server state. Re-run with --yes to confirm; "
            "the server's own approval and authorization checks still apply.[/yellow]"
        )
        raise typer.Exit(2)

    try:
        payload = json.loads(data) if data is not None else None
    except json.JSONDecodeError as exc:
        console.print(f"[red]Invalid JSON body: {escape(str(exc))}[/red]")
        raise typer.Exit(2) from exc

    request_headers = _parse_headers(headers)
    settings = get_settings()
    token = settings.server_token
    if token:
        # Configuration owns the local access card; an ad-hoc header must not silently bypass it.
        request_headers["Authorization"] = f"Bearer {token}"

    base_url = _base_url(url, Path(settings.home).expanduser())
    parsed_url = urlsplit(base_url)
    loopback = _is_loopback(parsed_url.hostname)
    if token and parsed_url.scheme.lower() == "http" and not loopback:
        # Plain HTTP to another host hands the bearer token to every hop on the way.
        console.print(
            "[red]Refusing to send CHIMERA_SERVER_TOKEN to a remote HTTP URL; "
            "use HTTPS or a loopback URL.[/red]"
        )
        raise typer.Exit(2)
    target = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    try:
        # Loopback ignores HTTP(S)_PROXY: with a proxy set and no NO_PROXY covering 127.0.0.1, the
        # call would leave the machine, fail, and give the token to the proxy on the way out.
        with httpx.Client(trust_env=not loopback) as client:
            response = client.request(
                verb, target, json=payload, headers=request_headers, timeout=15.0
            )
    except httpx.RequestError:
        # No exception details: some HTTP errors retain request headers, including auth.
        console.print(
            f"[red]Cannot reach Chimera server at {escape(base_url)}. "
            "Is `chimera serve` or `chimera app` running?[/red]",
            soft_wrap=True,
        )
        raise typer.Exit(1) from None

    console.print(f"HTTP {response.status_code}")
    body = response.text
    if token:
        body = body.replace(token, "[redacted]")
    if body:
        console.print(body, markup=False, soft_wrap=True)


@app.command(hidden=True)
def logs(
    follow: Annotated[
        bool, typer.Option("--follow", help="Continue printing new log entries.")
    ] = False,
    run: Annotated[
        str | None, typer.Option("--run", help="Only records containing this run id.")
    ] = None,
    level: Annotated[str | None, typer.Option("--level", help="Minimum log level.")] = None,
    lines: Annotated[
        int, typer.Option("--lines", min=1, help="Maximum initial lines to print.")
    ] = 100,
) -> None:
    """Read and filter the backend warning/error file without starting the service."""
    path = Path(get_settings().home).expanduser() / "logs" / "backend.log"
    keep = _RecordFilter(run=run, minimum=_level_number(level))
    try:
        stream = path.open("r", encoding="utf-8", errors="replace")
    except FileNotFoundError:
        # soft_wrap: a path broken across two lines cannot be copied into the next command.
        console.print(
            f"No backend log found at {escape(str(path))}. `chimera serve` and `chimera app` "
            "write warnings and errors there (CHIMERA_HOME/logs/backend.log) once they start, "
            "if that folder is writable; start one of them, or point CHIMERA_HOME at the home "
            "the running server uses.",
            soft_wrap=True,
        )
        raise typer.Exit(1) from None
    except OSError as exc:
        _cannot_read(path, exc)
    try:
        with stream:
            history = [line for line in stream.readlines() if keep(line)]
            for line in history[-lines:]:
                _print_line(line)
            if follow:
                _follow_log(path, stream, keep)
    except KeyboardInterrupt:
        return
    except OSError as exc:
        _cannot_read(path, exc)


def _cannot_read(path: Path, exc: OSError) -> NoReturn:
    console.print(
        f"[red]Cannot read backend log at {escape(str(path))}: {escape(str(exc))}[/red]",
        soft_wrap=True,
    )
    raise typer.Exit(1) from exc


def _level_number(level: str | None) -> int:
    """Translate a standard level name while refusing silent misspellings."""
    if level is None:
        return 0
    number = _LEVELS.get(level.upper())
    if number is None:
        console.print("[red]Unknown level; use DEBUG, INFO, WARNING, ERROR, or CRITICAL.[/red]")
        raise typer.Exit(2)
    return number


class _RecordFilter:
    """Decides per RECORD, not per line: a traceback's lines follow their record's verdict.

    A traceback line carries neither a level nor a run id, so judging it alone dropped it under
    ``--level ERROR`` or ``--run X`` — precisely the lines that say why the error happened.
    """

    def __init__(self, *, run: str | None, minimum: int) -> None:
        self.run = run
        self.minimum = minimum
        # Before any record header (the tail began mid-traceback) only an unfiltered view keeps it.
        self.last = not run and not minimum

    def __call__(self, line: str) -> bool:
        if _RECORD_START.match(line):
            self.last = self._judge(line)
        return self.last

    def _judge(self, line: str) -> bool:
        if self.run and self.run not in line:
            return False
        if not self.minimum:
            return True
        match = _LEVEL.search(line)
        return match is not None and _LEVELS[match.group(1)] >= self.minimum


def _print_line(line: str) -> None:
    console.print(line.rstrip("\r\n"), markup=False, soft_wrap=True)


def _rotated(path: Path, stream: TextIO) -> bool:
    """Whether the file at ``path`` is no longer the one ``stream`` reads from its position.

    The writer is a `RotatingFileHandler` (1 MB): rotation renames the file away and starts an
    empty one, so a tail that kept its handle would wait on the old file forever.
    """
    try:
        current = os.stat(path)
    except OSError:  # between the rename and the new file: not yet anything to switch to
        return False
    if current.st_size < stream.tell():
        return True
    return current.st_ino != os.fstat(stream.fileno()).st_ino


def _follow_log(path: Path, stream: TextIO, keep: _RecordFilter) -> None:
    """Print records appended after the history, following the file across rotations.

    ``stream`` stays the caller's to close; a file reopened here after a rotation is closed here.
    """
    current = stream
    try:
        while True:
            line = current.readline()
            if line:
                if keep(line):
                    _print_line(line)
                continue
            if _rotated(path, current):
                if current is not stream:
                    current.close()
                current = path.open("r", encoding="utf-8", errors="replace")
                continue
            time.sleep(0.25)
    finally:
        if current is not stream:
            current.close()


__all__ = ["api", "logs"]
