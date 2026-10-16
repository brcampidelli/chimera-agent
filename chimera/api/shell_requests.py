"""Asking the desktop shell to do the four troubleshooting actions only it can do (C7).

Open the logs folder, open the data folder, restart the backend, move the data folder to a dated
backup. Each acts on the NATIVE process or on files the running backend holds open, and the window
has no Tauri IPC — deliberately (`capabilities/default.json`): it loads this backend's http origin,
and a page that could call native commands is a page an injected script could call them from. So
the screen asks this backend, and this backend leaves the shell a request, the way
`chimera/api/shell_prefs.py` already does for start-at-sign-in.

What a request can carry is the whole point of the design: **an action NAME, never a path.** The
file holds one word from :data:`ACTIONS` and nothing else; the shell (`shell_request.rs`) accepts
exactly those words and maps each folder to a path it resolved itself at launch. A request that
names a folder by path — from the page, from a tool call, from a poisoned document the agent read —
has nowhere to put it.

The shell started this backend with two paths in its environment:

- ``CHIMERA_SHELL_REQUEST`` — where to leave a request. The shell looks on every tick of its watcher
  (three seconds), removes the file, then acts.
- ``CHIMERA_SHELL_RESULT`` — the shell's account of the last data reset: where the backup went, or
  why it did not happen. Read here, never written.

A backend started by anything but the desktop shell has neither variable: there is no shell to ask,
and a request is refused (:class:`ShellUnavailable`) rather than written into a file nobody reads.

These routes are the OWNER's. None is in the desktop bridge's table (`chimera/api/bridge_routes.py`)
and the guest app knows four routes, none of them these — both pinned by
`tests/test_troubleshooting_actions_are_the_owners_alone.py`.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

REQUEST_ENV = "CHIMERA_SHELL_REQUEST"
RESULT_ENV = "CHIMERA_SHELL_RESULT"

Action = Literal["open_logs", "open_data", "restart", "reset_data"]

#: Every word the shell accepts. Kept equal to `Request::parse` in `shell_request.rs` by
#: `tests/test_troubleshooting_actions_are_the_owners_alone.py`, which reads the Rust source.
ACTIONS: tuple[Action, ...] = ("open_logs", "open_data", "restart", "reset_data")

#: The word the owner types to reset. One word in every language, compared exactly: a translated
#: word would make the server's check depend on the screen's language, and a case-insensitive one
#: lets a sentence that merely contains it through.
RESET_WORD = "RESET"


class ShellUnavailable(RuntimeError):
    """This backend was not started by the desktop shell, so there is no shell to ask."""


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def available(environ: Mapping[str, str] | None = None) -> bool:
    return bool((_env(environ).get(REQUEST_ENV) or "").strip())


def request(action: Action, environ: Mapping[str, str] | None = None) -> None:
    """Leave ``action`` for the shell, replacing any request not yet taken.

    Replaced, not queued: two presses of "open logs" are one folder window, and a reset asked for
    after a restart supersedes it. Written through a temporary file and a rename, so the shell never
    reads half a word — a partial ``reset_data`` must not read as some other action, and the shell
    refuses anything that is not a whole word anyway.
    """
    if action not in ACTIONS:  # a Literal is a promise to mypy, not to a caller at runtime
        raise ValueError(f"unknown shell action: {action!r}")
    raw = (_env(environ).get(REQUEST_ENV) or "").strip()
    if not raw:
        raise ShellUnavailable("this backend was not started by the desktop app")
    path = Path(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\n") as handle:
            handle.write(action + "\n")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def last_reset(environ: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """The shell's account of the last reset, or None when there is none to read.

    The file is two lines the shell writes: ``moved <path>`` or ``failed <reason>``, then the Unix
    time it happened. Anything else reads as None — an account that cannot be parsed is not shown as
    one.
    """
    raw = (_env(environ).get(RESULT_ENV) or "").strip()
    if not raw:
        return None
    try:
        text = Path(raw).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    lines = text.splitlines()
    if len(lines) < 2:
        return None
    outcome, _, detail = lines[0].partition(" ")
    if outcome not in ("moved", "failed") or not detail.strip():
        return None
    try:
        at = int(lines[1].strip())
    except ValueError:
        return None
    return {"moved": outcome == "moved", "detail": detail.strip(), "at": at}
