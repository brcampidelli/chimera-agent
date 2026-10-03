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
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar

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
CHORD = "quick_entry_chord"
SIGN_IN_REQUEST = "start_at_sign_in"
_KNOWN = (*SWITCHES, CHORD, SIGN_IN_REQUEST)

#: How often a read or a replace that Windows refused is tried again, and the pause before each try.
#: The shell replaces the file by rename, and a rename over a file another process has open — or an
#: open of a file being renamed over — fails with "access denied" for the moment it takes. The shell
#: retries its side (`retry_denied` in prefs.rs); this is the same courtesy from this side.
_DENIED_TRIES = 8
_DENIED_PAUSE = 0.015

#: One writer at a time inside this process: two saves from the screen read-modify-write the same
#: file, and interleaved they would each keep the other's old value.
_WRITE_LOCK = threading.Lock()

_T = TypeVar("_T")

BUSY = "the desktop app's preferences file is in use by another program; try again in a moment"


class ShellUnavailable(RuntimeError):
    """This backend was not started by the desktop shell, so there are no shell switches to change."""


class ShellPrefsUnreadable(RuntimeError):
    """The shell's file is one the shell itself would refuse whole, so it is on its defaults."""


class ShellPrefsBusy(RuntimeError):
    """The file stayed locked by another process through every retry."""


def _paths(environ: Mapping[str, str]) -> tuple[Path, Path | None] | None:
    prefs = (environ.get(PREFS_ENV) or "").strip()
    if not prefs:
        return None
    state = (environ.get(STATE_ENV) or "").strip()
    return Path(prefs), Path(state) if state else None


def _patiently(op: Callable[[], _T]) -> _T:
    """``op()``, tried again while Windows answers "access denied" (``PermissionError``)."""
    for attempt in range(1, _DENIED_TRIES + 1):
        try:
            return op()
        except PermissionError:
            if attempt == _DENIED_TRIES:
                raise
            time.sleep(_DENIED_PAUSE * attempt)
    raise AssertionError("unreachable")  # the loop returns or raises


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


class _Object(dict[str, Any]):
    """A JSON object that remembers whether it named one of the shell's fields twice."""

    repeats_a_field = False


def _note_repeats(pairs: list[tuple[str, Any]]) -> _Object:
    """serde refuses a struct field named twice; ``json`` would silently keep the last one.

    Noted, not raised: the hook runs for nested objects too, and serde skips the content of a key it
    does not know without looking inside it — only the top level's repeats count.
    """
    obj = _Object(pairs)
    names = [key for key, _value in pairs if key in _KNOWN]
    obj.repeats_a_field = len(names) != len(set(names))
    return obj


def parse_prefs(text: str) -> dict[str, Any] | None:
    """The file as the shell's serde reads it: the object, or ``None`` when serde refuses it whole.

    The shell does not read key by key: ONE wrong-typed known key — ``"keep_in_tray": "yes"``, a
    null chord, a quoted request — and serde rejects the whole file, so the shell runs on its
    defaults and says so in the tray. Reading key by key here showed the file's values as in force
    while the shell ignored all of them. This mirrors serde rule for rule (an object only — see
    ``parse`` in prefs.rs —, the types, a known key twice, NaN, a byte-order mark), and the rules are
    pinned to the shell's by cases both test suites read: ``tests/fixtures/shell_prefs_cases.json``.
    """
    try:
        data = json.loads(text, object_pairs_hook=_note_repeats, parse_constant=_no_constant)
    except ValueError:
        return None
    if not isinstance(data, _Object) or data.repeats_a_field:
        return None
    for key in SWITCHES:
        if key in data and not isinstance(data[key], bool):
            return None
    if CHORD in data and not isinstance(data[CHORD], str):
        return None
    if data.get(SIGN_IN_REQUEST) is not None and not isinstance(data[SIGN_IN_REQUEST], bool):
        return None
    return dict(data)


def _read_text(path: Path) -> str | None:
    """The file's text; ``None`` when there is no file.

    Retried while Windows refuses the open, and :class:`ShellPrefsBusy` when it never stops: "the
    file is locked" is not "the file is broken", and reading it as unreadable would show the shell's
    defaults for settings that are in force.
    """
    try:
        return _patiently(lambda: path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except PermissionError as exc:
        raise ShellPrefsBusy(BUSY) from exc


def _load(path: Path) -> dict[str, Any] | None:
    """The file as serde reads it; ``{}`` for no file; ``None`` for one the shell refuses whole."""
    try:
        text = _read_text(path)
    except ShellPrefsBusy:
        raise
    except (OSError, UnicodeDecodeError):
        # The shell's `read_to_string` fails on the same files, and runs on its defaults.
        return None
    if text is None:
        return {}
    return parse_prefs(text)


def _load_state(path: Path) -> dict[str, Any]:
    try:
        text = _read_text(path)
        data = json.loads(text) if text else {}
    except (OSError, ValueError, ShellPrefsBusy):
        return {}
    return data if isinstance(data, dict) else {}


def read_shell_prefs(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """What is in force, as the shell would read it, plus what the shell last reported."""
    env = os.environ if environ is None else environ
    paths = _paths(env)
    out: dict[str, Any] = {
        "available": paths is not None,
        **SWITCHES,
        CHORD: DEFAULT_CHORD,
        "start_at_sign_in": None,
        "sign_in_requested": None,
        "problem": "",
        "unreadable": False,
    }
    if paths is None:
        return out
    prefs_path, state_path = paths
    data = _load(prefs_path)
    if data is None:
        # The shell runs on its defaults and says so in the tray; shown the same way here, so the
        # screen does not describe settings the shell is not using.
        out["unreadable"] = True
    else:
        for key, default in SWITCHES.items():
            out[key] = data.get(key, default)
        out[CHORD] = data.get(CHORD, DEFAULT_CHORD)
        out["sign_in_requested"] = data.get(SIGN_IN_REQUEST)
    if state_path is not None:
        state = _load_state(state_path)
        answer = state.get("start_at_sign_in")
        out["start_at_sign_in"] = answer if isinstance(answer, bool) else None
        problem = state.get("problem")
        out["problem"] = problem if isinstance(problem, str) else ""
    return out


def _replace(path: Path, body: str) -> None:
    """Write ``body`` to ``path`` atomically, through a temporary file only this call uses.

    A fixed temporary name was shared by every save, so two at once wrote into one file and one of
    them replaced the other's half. ``mkstemp`` in the same directory (a rename across volumes is a
    copy) gives each its own. A replace Windows keeps refusing is :class:`ShellPrefsBusy`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
        try:
            _patiently(lambda: os.replace(tmp, path))
        except PermissionError as exc:
            raise ShellPrefsBusy(BUSY) from exc
    finally:
        tmp.unlink(missing_ok=True)


def write_shell_prefs(
    changes: Mapping[str, bool], environ: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Save the switches in ``changes`` to the shell's file, keeping every other key it holds.

    Raises :class:`ShellUnavailable` without a shell, :class:`ShellPrefsUnreadable` for a file the
    shell refuses whole, :class:`ShellPrefsBusy` for one that stayed locked, and ``ValueError`` for
    a key outside the switches (and the sign-in request) or a value that is not a boolean.

    A file the shell refuses is refused here rather than overwritten: it is someone's hand edit, and
    the shell sets such a file aside itself before writing over it — this side cannot, without
    racing the shell for it.
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
    with _WRITE_LOCK:
        text = _read_text(prefs_path)
        data = {} if text is None else parse_prefs(text)
        if data is None:
            raise ShellPrefsUnreadable(
                "the desktop app's preferences file (shell-prefs.json) has a value it cannot read, "
                "so the app is using its defaults; fix or delete the file — the tray menu says "
                "what is wrong"
            )
        data.update(changes)
        _replace(prefs_path, json.dumps(data, indent=2))
    return read_shell_prefs(env)
