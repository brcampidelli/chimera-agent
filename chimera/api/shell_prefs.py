"""The desktop shell's own switches, as the Settings screen reads and changes them.

The tray's four switches — keep running in the tray, flash for an approval, the quick-entry chord,
start at sign-in — act on the NATIVE process, so the shell keeps them in a file of its own
(`apps/desktop/src-tauri/src/prefs.rs`), and until now only the tray menu could change them. The
window cannot reach the shell: it has no Tauri IPC, deliberately (`capabilities/default.json`). So
the screen asks this backend, which the shell started with two paths in its environment:

- ``CHIMERA_SHELL_PREFS`` — the preferences file the shell reads. Written here with the same keys
  the shell writes, preserving any key this module does not know. The shell looks at the file on
  every tick of its watcher (three seconds) and acts on a change, so a switch saved here is in
  force within seconds, and the tray's check items follow it.
- ``CHIMERA_SHELL_STATE`` — the shell's report back: the operating system's answer for
  start-at-sign-in, and the tray's problem line. Read here, never written.

Start-at-sign-in is the odd one. Its truth is the operating system's, so this module never writes
it as a state: it writes a REQUEST (``start_at_sign_in`` in the preferences file), the shell asks the
OS and removes the request, and what the screen shows is the OS's answer from the report.

A backend started by anything but the desktop shell has neither variable. Then there is no shell to
change, ``available`` is false, and a write is refused rather than creating a file nobody reads.

The chord itself is not writable here: it is free text in the accelerator syntax, and a typo would
take a key combination from every other program on the machine. It stays a hand edit, and the screen
shows which chord is in force.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

PREFS_ENV = "CHIMERA_SHELL_PREFS"
STATE_ENV = "CHIMERA_SHELL_STATE"

#: The switches this screen may write, each with the shell's own default (`Prefs::default`). The
#: defaults matter on read: a missing file, or one written by an older shell, is these values in force.
SWITCHES: dict[str, bool] = {
    "keep_in_tray": False,
    "call_attention": True,
    "quick_entry": False,
}
DEFAULT_CHORD = "CommandOrControl+Shift+Space"
SIGN_IN_REQUEST = "start_at_sign_in"


class ShellUnavailable(RuntimeError):
    """This backend was not started by the desktop shell, so there are no shell switches to change."""


def _paths(environ: Mapping[str, str]) -> tuple[Path, Path | None] | None:
    prefs = (environ.get(PREFS_ENV) or "").strip()
    if not prefs:
        return None
    state = (environ.get(STATE_ENV) or "").strip()
    return Path(prefs), Path(state) if state else None


def _load_object(path: Path) -> dict[str, Any] | None:
    """The file as a JSON object; ``{}`` when it does not exist; ``None`` when it does not parse."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def read_shell_prefs(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """What is in force, as the shell would read it, plus what the shell last reported."""
    env = os.environ if environ is None else environ
    paths = _paths(env)
    out: dict[str, Any] = {
        "available": paths is not None,
        **SWITCHES,
        "quick_entry_chord": DEFAULT_CHORD,
        "start_at_sign_in": None,
        "sign_in_requested": None,
        "problem": "",
        "unreadable": False,
    }
    if paths is None:
        return out
    prefs_path, state_path = paths
    data = _load_object(prefs_path)
    if data is None:
        # The shell reads a file that does not parse as its defaults, and says so in the tray. Shown
        # the same way here, so the screen does not describe settings the shell is not using.
        out["unreadable"] = True
    else:
        for key, default in SWITCHES.items():
            value = data.get(key, default)
            out[key] = value if isinstance(value, bool) else default
        chord = data.get("quick_entry_chord")
        if isinstance(chord, str) and chord.strip():
            out["quick_entry_chord"] = chord
        request = data.get(SIGN_IN_REQUEST)
        out["sign_in_requested"] = request if isinstance(request, bool) else None
    if state_path is not None:
        state = _load_object(state_path) or {}
        answer = state.get("start_at_sign_in")
        out["start_at_sign_in"] = answer if isinstance(answer, bool) else None
        problem = state.get("problem")
        out["problem"] = problem if isinstance(problem, str) else ""
    return out


def write_shell_prefs(
    changes: Mapping[str, bool], environ: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Save the switches in ``changes`` to the shell's file, keeping every other key it holds.

    Raises :class:`ShellUnavailable` without a shell, and ``ValueError`` for a key outside the
    switches (and the sign-in request) or a file that does not parse. The second is refused rather
    than overwritten: a file that does not parse is someone's hand edit, and the shell itself sets
    such a file aside before writing over it — this side cannot, without racing the shell for it.
    """
    env = os.environ if environ is None else environ
    paths = _paths(env)
    if paths is None:
        raise ShellUnavailable("this server was not started by the desktop app; it has no tray")
    unknown = sorted(set(changes) - set(SWITCHES) - {SIGN_IN_REQUEST})
    if unknown:
        raise ValueError(f"not a shell switch: {', '.join(unknown)}")
    for key, value in changes.items():
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
    prefs_path, _state = paths
    data = _load_object(prefs_path)
    if data is None:
        raise ValueError(
            f"{prefs_path} does not parse; fix or delete it (the tray menu says what is wrong)"
        )
    data.update(changes)
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic, like the shell's own save: the shell may look at the file at any moment, and half a
    # file reads as "does not parse".
    tmp = prefs_path.with_name(prefs_path.name + ".screen.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(prefs_path)
    return read_shell_prefs(env)
