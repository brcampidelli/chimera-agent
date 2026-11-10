"""The secrets the desktop shell starts the backend with, read from stdin instead of the environment.

The shell used to pass the per-launch bearer token (``CHIMERA_SERVER_TOKEN``), the screen credential
(``CHIMERA_SCREEN_TOKEN``) and a service's launcher key (``CHIMERA_SCREEN_LAUNCHER_TOKEN``) in the
child's environment, and this process removed them from ``os.environ`` as it read them. That removal
edits a copy. On Linux ``/proc/<pid>/environ`` goes on showing the environment the process was
*started* with, readable by every process of the same user, and the agent's ``run_shell`` is one: a
``cat /proc/$PPID/environ`` returned the credential that separates the owner's click from everything
else the agent can reach (opening a terminal, switching "Ignore permissions" on). macOS (``ps eww``)
and Windows (Process Explorer's Environment tab, ``NtQueryInformationProcess`` + ``ReadProcessMemory``
on the process parameters) read the same start-up block.

So the shell now writes ONE JSON line to this process's stdin and closes it, and sets
:data:`MARKER_ENV` (a flag, not a secret) so this side knows to look. A pipe leaves nothing behind to
read: no file to protect or to delete after a crash, no command line, no environment block.

* Honoured only under the desktop shell (``CHIMERA_TAURI_SIDECAR=1``): nobody else is entitled to
  choose these values.
* Read once, early (:func:`adopt_from_stdin`, from the frozen entry point, before the settings are
  first built), with a timeout.
* **Fails closed, by exiting.** With the marker set, anything but one valid JSON object on stdin
  (timeout, EOF, bad JSON, no usable fd 0) ends this process with :data:`EXIT_CODE`. Carrying on
  would serve the API with no bearer token, because ``_require_token`` is a no-op without one, which
  is worse than not starting. A valid object that merely lacks a key is legitimate: a service the
  shell starts with no token of its own uses its folder's ``service.token``.
* The screen credential, the launcher key and the bearer token stay in this module's memory. None of
  them is put in ``os.environ``: on Windows ``putenv`` rewrites the live environment block, which a
  same-user process reads through the PEB, so an "internal copy" is not private there. ``Settings``
  gets the bearer token by an in-memory override in ``get_settings`` (:func:`server_token`).
* stdin is pointed at the null device afterwards, so nothing this process starts inherits the pipe.

**The fallback, and its risk.** Without the marker (``chimera app`` in a browser or a terminal, a
test, a shell older than this change) the old environment channel is still read
(``screen_credential`` pops it) and a value there works. That path is exactly as exposed as it
always was: anyone who can read this process's start-up environment reads the value. It is accepted
there because the credential is not a secret from the owner's own session in those modes (``chimera
app`` serves it in the page, ``chimera service`` holds none), and because breaking every older shell
and every script that sets the variable would buy nothing. The desktop shell never takes that path.

**A new shell with an old backend.** An older backend would ignore the marker and run with no
token, and the guard is a no-op without one. The shell therefore probes after it starts or attaches
to a backend: a protected route (``GET /api/approvals``) asked with NO token must answer 401/403, and
a 2xx makes the shell refuse that backend ("the backend does not require a token, reinstall"), ending
the process it started itself (``sidecar_http::token_is_enforced``).
"""

from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any, BinaryIO

#: Set by the shell to say "the secrets are on stdin". Carries no secret itself.
MARKER_ENV = "CHIMERA_LAUNCH_SECRETS"
#: How long the single read may take before it is abandoned.
READ_TIMEOUT_SECONDS = 10.0
#: A line longer than this is not what the shell writes (three 64-character values).
MAX_LINE_BYTES = 16 * 1024
#: The exit status of a backend whose shell did not deliver its secrets (EX_CONFIG).
EXIT_CODE = 78

_SERVER_TOKEN = "server_token"
_SCREEN_TOKEN = "screen_token"
_LAUNCHER_KEY = "launcher_key"

#: What was read and not yet taken (screen credential, launcher key): :func:`take` forgets a value.
_held: dict[str, str] = {}
#: The bearer token the shell delivered. Kept for the life of the process: ``get_settings`` is
#: rebuilt whenever the owner saves a setting, and each rebuild applies it again.
_server_token: str | None = None
#: Whether this process was started with the channel, whatever it delivered. Decides if the
#: environment fallback is consulted at all.
_channel_requested = False

#: How a read that cannot finish ends the process. ``os._exit``, not ``sys.exit``: the reader thread
#: is still blocked inside ``read`` and interpreter shutdown would wait for it. Tests replace it.
_hard_exit = os._exit


def channel_requested() -> bool:
    """Whether the shell said it would deliver on stdin (so an environment value is not wanted)."""
    return _channel_requested


def server_token() -> str | None:
    """The bearer token delivered on stdin, or ``None`` (no channel, or none in the line)."""
    return _server_token


def take(name: str) -> str | None:
    """The value the shell delivered for ``name`` (``screen_token`` or ``launcher_key``), once."""
    return _held.pop(name, None)


def _abort(reason: str, *, hard: bool = False) -> None:
    """End the process: the shell said secrets were coming and they did not arrive intact."""
    try:
        sys.stderr.write(f"chimera: the desktop shell's launch secrets did not arrive ({reason}); exiting\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass
    if hard:
        _hard_exit(EXIT_CODE)
    raise SystemExit(EXIT_CODE)


def _read_line(stream: BinaryIO, out: list[bytes]) -> None:
    try:
        out.append(stream.readline(MAX_LINE_BYTES))
    except (OSError, ValueError):
        out.append(b"")


def _parse(raw: bytes) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _stdin_stream() -> BinaryIO | None:
    """Descriptor 0 itself: ``sys.stdin`` may be ``None`` or already wrapped by something else."""
    try:
        return open(0, "rb", closefd=False)  # noqa: SIM115 - fd 0 is not ours to close
    except OSError:
        return getattr(sys.stdin, "buffer", None)


def _silence_stdin() -> None:
    """Point fd 0 at the null device so no child inherits the pipe. Best effort."""
    try:
        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, 0)
        os.close(devnull)
    except OSError:
        pass


def adopt_from_stdin(stream: BinaryIO | None = None, *, timeout: float = READ_TIMEOUT_SECONDS) -> bool:
    """Read the shell's line, if the shell said there is one. Returns whether the channel was used.

    Call once, first thing, before ``get_settings()`` is first built. Safe to call twice: the marker
    is consumed, so the second call does nothing. When the marker is set and no valid JSON object
    arrives within ``timeout``, the process exits with :data:`EXIT_CODE`.
    """
    global _channel_requested, _server_token
    marker = os.environ.pop(MARKER_ENV, None)
    if marker != "stdin" or os.environ.get("CHIMERA_TAURI_SIDECAR") != "1":
        return False
    _channel_requested = True
    source = stream if stream is not None else _stdin_stream()
    if source is None:
        _abort("no usable stdin")
        return True
    box: list[bytes] = []
    reader = threading.Thread(target=_read_line, args=(source, box), daemon=True)
    reader.start()
    reader.join(timeout)
    if reader.is_alive():
        # Not `_silence_stdin()`: the reader still holds fd 0's buffer, and swapping the descriptor
        # under it is what deadlocked. The process ends instead.
        _abort(f"nothing within {timeout:g} s", hard=True)
        return True
    if stream is None:
        _silence_stdin()
    values = _parse(box[0]) if box else None
    if values is None:
        _abort("not a JSON object")
        return True
    server = values.get(_SERVER_TOKEN)
    if isinstance(server, str) and server.strip():
        _server_token = server.strip()
    for name in (_SCREEN_TOKEN, _LAUNCHER_KEY):
        value = values.get(name)
        if isinstance(value, str) and value.strip():
            _held[name] = value.strip()
    return True


def _forget_for_tests() -> None:
    """Reset module state. Tests only."""
    global _channel_requested, _server_token
    _held.clear()
    _server_token = None
    _channel_requested = False
