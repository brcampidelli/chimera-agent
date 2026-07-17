"""Host-execution confirmation — the gate before the agent runs a command on your machine.

When the sandbox is ``local`` (the default, because most ``pip install`` users have no Docker), a
command the model chose to run executes on the host. ``CHIMERA_HOST_EXEC`` decides the posture:

* ``ask`` (default) — interactive terminal: confirm each host command; headless: run with a one-time
  warning (so cron / CI / the benchmark harness are not broken by a prompt nothing can answer).
* ``allow`` — run on the host without asking (the pre-2026-07 behaviour, now an explicit opt-in).
* ``deny`` — never run on the host; the command is refused with a pointer to ``CHIMERA_SANDBOX=docker``.

:func:`resolve_host_exec_confirm` returns the callback the shell / code tools consult before executing
on the host. It is ``None`` when no gate applies (isolated container, or ``allow``), so those paths keep
their exact previous behaviour.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from chimera.telemetry import get_logger

if TYPE_CHECKING:
    from chimera.config import Settings

_log = get_logger("sandbox.confirm")

# Given the command (shell) or a one-line summary (code), return True to run it on the host.
HostExecConfirm = Callable[[str], bool]

_warned_headless = False


def _deny(command: str) -> bool:
    """The ``deny`` posture: never run on the host, with an actionable message in the log."""
    _log.warning(
        "host execution denied (CHIMERA_HOST_EXEC=deny): refused to run on the host. "
        "Set CHIMERA_SANDBOX=docker to run in an isolated container, or CHIMERA_HOST_EXEC=allow "
        "to permit host execution. Command: %s",
        command[:200],
    )
    return False


def _prompt(command: str) -> bool:
    """Interactive confirm: ask the user before running the command on their machine."""
    # Imported lazily so the sandbox package does not hard-depend on the CLI's rich/typer stack.
    try:
        import typer

        typer.echo("")
        typer.secho("⚠  The agent wants to run this on your machine (host, not a sandbox):", fg="yellow")
        typer.secho(f"    {command}", fg="cyan")
        return bool(typer.confirm("Run it?", default=False))
    except Exception:  # noqa: BLE001 — no TTY / typer missing: fail safe (do not run)
        _log.warning("host-exec confirm could not prompt; refusing. Command: %s", command[:200])
        return False


def _headless_allow(command: str) -> bool:
    """Non-interactive ``ask``: run, but warn ONCE so the risk is visible in logs (non-breaking)."""
    global _warned_headless
    if not _warned_headless:
        _warned_headless = True
        _log.warning(
            "running the agent's commands on the host without confirmation (no TTY, "
            "CHIMERA_HOST_EXEC=ask). For isolation set CHIMERA_SANDBOX=docker; to silence this, "
            "set CHIMERA_HOST_EXEC=allow (accepts the risk) or =deny (refuses)."
        )
    return True


def resolve_host_exec_confirm(
    settings: Settings | None = None, *, interactive: bool | None = None
) -> HostExecConfirm | None:
    """Return the host-exec confirmation callback, or ``None`` when no gate applies.

    ``None`` means "run as before" — used for an isolated container sandbox and for ``allow``. A
    non-None callback is consulted by the shell/code tools before host execution; returning False
    turns into a clean ``error:`` tool result, never a crash.
    """
    from chimera.config import get_settings

    settings = settings or get_settings()

    # An isolated container needs no host-exec gate.
    if (settings.sandbox or "local").lower() == "docker":
        return None

    posture = (settings.host_exec or "ask").lower()
    if posture == "allow":
        return None
    if posture == "deny":
        return _deny

    # posture == "ask" (or anything unrecognised → treat as ask, the safe default)
    if interactive is None:
        interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    return _prompt if interactive else _headless_allow
