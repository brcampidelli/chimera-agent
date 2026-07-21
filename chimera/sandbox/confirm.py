"""Host-execution confirmation — the gate before the agent runs a command on your machine.

When the sandbox is ``local`` (the default, because most ``pip install`` users have no Docker), a
command the model chose to run executes on the host. ``CHIMERA_HOST_EXEC`` decides the posture:

* ``ask`` (default) — interactive terminal: confirm each host command; headless: run with a one-time
  warning (so cron / CI / the benchmark harness are not broken by a prompt nothing can answer).
* ``allow`` — run on the host without asking (the pre-2026-07 behaviour, now an explicit opt-in).
* ``deny`` — never run on the host; the command is refused with a pointer to ``CHIMERA_SANDBOX=docker``.

:func:`resolve_host_exec_confirm` returns the callback the shell / code tools consult before executing
on the host. It is ``None`` only under ``allow`` — the *isolated container* case is decided later, at
the tool, from the real :func:`sandbox_is_isolated` (a docker *config* is not proof of isolation).
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


def sandbox_is_isolated(sandbox: object) -> bool:
    """True when the sandbox genuinely isolates from the host (so no host-exec confirm is needed).

    Duck-typed and shared by every host-exec consumer, so they cannot drift apart: a backend that
    runs in a real container reports ``is_isolated() -> True``. A DockerSandbox that has fallen back
    to local (no daemon) reports False — its host execution stays gated, closing the "docker
    configured but silently ran on the host" gap. A backend whose ``is_isolated`` is not callable
    counts as host (the safe direction) instead of crashing.
    """
    fn = getattr(sandbox, "is_isolated", None)
    return bool(fn()) if callable(fn) else False


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


def _make_headless_deny() -> HostExecConfirm:
    """Non-interactive ``ask``: REFUSE, explaining once how to proceed deliberately.

    ``ask`` means "a human decides". Unattended there is no human, so the honest resolution of that
    posture is to refuse, not to assume consent — the previous behaviour ran the agent's chosen host
    commands after a single log line, which made the shipped default for every unattended surface
    (``chimera serve``, cron, CI, systemd) effectively ``allow``. An operator who genuinely wants
    host execution says so with ``CHIMERA_HOST_EXEC=allow``; one who wants the work to run *safely*
    sets ``CHIMERA_SANDBOX=docker``, where the gate is skipped because the container really isolates.

    The warning fires once per *resolve*, not per module: a long-lived process resolves the gate per
    run, so each run still explains why its host commands were refused instead of going silent.
    """
    warned = False

    def _headless_deny(command: str) -> bool:
        nonlocal warned
        if not warned:
            warned = True
            _log.warning(
                "refusing to run the agent's commands on the host: no TTY to confirm and "
                "CHIMERA_HOST_EXEC=ask. Set CHIMERA_SANDBOX=docker to run them isolated, or "
                "CHIMERA_HOST_EXEC=allow to accept host execution unattended."
            )
        _log.debug("host execution refused (headless ask). Command: %s", command[:200])
        return False

    return _headless_deny


def resolve_host_exec_confirm(
    settings: Settings | None = None, *, interactive: bool | None = None
) -> HostExecConfirm | None:
    """Return the host-exec confirmation callback, or ``None`` when no gate applies.

    ``None`` means "run as before", and is returned **only** for ``allow``. Isolation is *not*
    decided here (see the NOTE below); the tools skip a non-None callback themselves when
    :func:`sandbox_is_isolated` says the container is genuinely up. Returning False from the callback
    turns into a clean ``error:`` tool result, never a crash.
    """
    from chimera.config import get_settings

    settings = settings or get_settings()

    # NOTE: do NOT short-circuit on `settings.sandbox == "docker"` here. A docker *config* is not
    # proof of isolation — DockerSandbox falls back to the host when the daemon is down. Whether to
    # skip the gate must be decided from the ACTUAL sandbox at call time: the shell/code tools consult
    # `sandbox.is_isolated()` and skip confirm only when the container is really up (docker up → True →
    # skipped; docker down → host → gated). Returning None here would make `deny` a no-op and let a
    # fallen-back docker run on the host ungated — the exact hole SECURITY.md says is closed.
    posture = (settings.host_exec or "ask").lower()
    if posture == "allow":
        return None
    if posture == "deny":
        return _deny

    # posture == "ask" (or anything unrecognised → treat as ask, the safe default)
    if interactive is None:
        interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    return _prompt if interactive else _make_headless_deny()
