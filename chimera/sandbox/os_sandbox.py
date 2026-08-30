"""OS-level sandbox — the kernel enforces the boundary, not a prompt and not a promise.

Until now the shipped default was ``local``: the agent's commands ran on the host with a timeout
and a working directory, and the only thing between a chosen command and the machine was the
governance kernel plus a confirmation the user had to answer. ``SECURITY.md`` said so out loud.
Both comparable products enforce a kernel boundary **per command** with the network off by default,
and one of them refuses to run at all rather than degrade to unsandboxed.

This module is that boundary where the platform offers one:

* **macOS** — Seatbelt (``/usr/bin/sandbox-exec``) with a ``(deny default)`` profile: read anywhere,
  write only under the declared roots, no network.
* **Linux** — bubblewrap: read-only bind of ``/``, read-write bind of the declared roots,
  ``--unshare-net``, ``--unshare-pid``, ``--unshare-ipc``, ``--cap-drop ALL``, ``--die-with-parent``.
* **Windows** — **nothing**. The mechanism Codex uses there is a restricted token plus WFP filters,
  which is native work this cannot honestly approximate, and approximating it is worse than not
  having it: a boundary that is believed and absent is more dangerous than one known to be absent.

That last point is the design rule for the whole module: :meth:`OsSandbox.is_isolated` answers True
**only** when the wrapper really applied. It is what the host-exec gate consults, so a machine
without a usable sandbox keeps its confirmation prompt and a machine with one may skip it — the
claim and the enforcement move together, and neither can drift from the other.

Availability is **probed, not guessed**. `bwrap` on PATH is not the question; whether this kernel
lets it unshare a user namespace is. Containers, hardened distributions and WSL1 all ship the binary
and refuse the syscall.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Literal

from chimera.sandbox.local import LocalSandbox
from chimera.telemetry import get_logger

_log = get_logger("sandbox.os")

#: How long the availability probe may take. A probe that hangs is a probe that answers "no".
_PROBE_TIMEOUT = 10

_SEATBELT = "/usr/bin/sandbox-exec"


def _probe(argv: list[str], what: str) -> bool:
    """Run ``argv`` and report whether it succeeded, treating any failure to launch as a no.

    The probe runs the **same argv builder** the real command will use, differing only in the
    program at the end. Probing a simpler invocation than the one that ships is how a machine ends
    up reporting an available sandbox and then failing every command with a flag error — the check
    would pass and the thing it certified would not exist.
    """
    try:
        result = subprocess.run(argv, capture_output=True, timeout=_PROBE_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        _log.debug("%s probe could not run: %s", what, exc)
        return False
    if result.returncode != 0:
        _log.debug("%s present but unusable: %s", what, (result.stderr or b"")[:300])
    return result.returncode == 0


@lru_cache(maxsize=1)
def seatbelt_available() -> bool:
    """macOS with a sandbox binary that accepts the profile this module actually generates.

    The absolute path is deliberate, and it is the same reasoning Chrome and Codex use: resolving
    ``sandbox-exec`` through PATH would let anything earlier on PATH become the thing that decides
    whether the sandbox exists.
    """
    if platform.system() != "Darwin" or not Path(_SEATBELT).is_file():
        return False
    profile = _seatbelt_profile(_writable_roots(None))
    return _probe([_SEATBELT, "-p", profile, "/usr/bin/true"], "seatbelt")


@lru_cache(maxsize=1)
def bubblewrap_available() -> bool:
    """Linux with a `bwrap` that this kernel lets run the exact flag set used below.

    Probed rather than assumed. Unprivileged user namespaces are disabled by default on several
    distributions and inside many container runtimes, and WSL1 has no support at all — in every one
    of those the binary is present and the call fails. Asking `shutil.which` would report a sandbox
    that cannot start.
    """
    if platform.system() != "Linux" or shutil.which("bwrap") is None:
        return False
    return _probe(_bwrap_argv(_writable_roots(None), None) + ["--", "/bin/true"], "bubblewrap")


def os_sandbox_available() -> bool:
    """Whether this machine can enforce a kernel boundary for the agent's commands."""
    return seatbelt_available() or bubblewrap_available()


#: Machine-readable causes, so a screen can say this in the reader's language instead of relaying
#: an English sentence from a server. Same shape and same reason as ``PostureFacts.fell_back_reason``:
#: the app is translated into ten languages and the Security screen was printing this one in English
#: beside its own translated prose, which reads as the panel not knowing what it is looking at.
UnavailableCode = Literal[
    "", "windows", "bwrap_missing", "userns_refused", "seatbelt_missing", "unsupported_os"
]


def unavailable_cause() -> tuple[UnavailableCode, str]:
    """Why there is no OS sandbox, as ``(code, sentence)``. ``("", "")`` when one IS available.

    One function returns both so they cannot drift: a code and a sentence maintained separately
    eventually disagree, and the disagreement surfaces as a screen confidently explaining the wrong
    cause. The sentence stays the fallback for a reader whose client does not know the code yet.
    """
    if os_sandbox_available():
        return "", ""
    system = platform.system()
    if system == "Windows":
        return "windows", (
            "Windows has no OS sandbox in Chimera: the mechanism there is a restricted token plus "
            "network filters, which is native work this does not attempt. Use CHIMERA_SANDBOX=docker "
            "for a real boundary."
        )
    if system == "Linux":
        if shutil.which("bwrap") is None:
            return "bwrap_missing", (
                "bubblewrap is not installed (apt install bubblewrap), so commands run on the host."
            )
        return "userns_refused", (
            "bubblewrap is installed but this kernel refuses to unshare a user namespace "
            "(common in containers and on hardened kernels), so commands run on the host."
        )
    if system == "Darwin":
        return "seatbelt_missing", f"{_SEATBELT} is missing, so commands run on the host."
    return "unsupported_os", f"no OS sandbox is implemented for {system!r}, so commands run on the host."


def unavailable_reason() -> str:
    """Why not, in one sentence a person can act on. Empty when a sandbox IS available."""
    return unavailable_cause()[1]


def _writable_roots(cwd: Path | None) -> list[Path]:
    """Where the command may write: the directory it runs in, plus the temp dir it will reach for.

    Temp is not a convenience. Compilers, package managers and test runners all write there, and a
    sandbox that forbids it turns into a sandbox nobody leaves switched on.
    """
    roots: list[Path] = []
    if cwd is not None:
        roots.append(Path(cwd))
    tmp = os.environ.get("TMPDIR") or "/tmp"
    roots.append(Path(tmp))
    resolved: list[Path] = []
    for root in roots:
        try:
            candidate = root.resolve()
        except OSError:
            continue
        if candidate.is_dir() and candidate not in resolved:
            resolved.append(candidate)
    return resolved


def _seatbelt_profile(roots: list[Path]) -> str:
    """A closed-by-default SBPL profile: read anywhere, write only under ``roots``, no network.

    ``(deny default)`` is what makes the omissions safe — network is denied because nothing allows
    it, not because a rule turned it off, so a rule that fails to render cannot open it by accident.
    """
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec)",
        "(allow process-fork)",
        "(allow signal (target same-sandbox))",
        "(allow sysctl-read)",
        "(allow file-read*)",
        # /dev/null must stay writable or almost every shell pipeline fails on its own redirection.
        '(allow file-write-data (require-all (path "/dev/null") (vnode-type CHARACTER-DEVICE)))',
    ]
    for root in roots:
        lines.append(f'(allow file-write* (subpath "{root.as_posix()}"))')
    return "\n".join(lines) + "\n"


def _bwrap_argv(roots: list[Path], cwd: Path | None) -> list[str]:
    argv = ["bwrap", "--ro-bind", "/", "/"]
    for root in roots:
        argv += ["--bind", str(root), str(root)]
    argv += [
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--unshare-net",
        "--unshare-pid",
        "--unshare-ipc",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
    ]
    if cwd is not None:
        argv += ["--chdir", str(cwd)]
    return argv


class OsSandbox(LocalSandbox):
    """Runs the command under the platform's kernel sandbox.

    Subclasses :class:`LocalSandbox` for its process handling rather than copying it: the timeout
    that kills the whole process tree, the secret-scrubbed environment and the non-interactive
    overrides are all correctness fixes with their own history, and a second copy of them is a
    second place for that history to be forgotten. Only the argv changes.
    """

    @staticmethod
    def is_isolated() -> bool:
        """True only where the wrapper genuinely applies. This is the claim the host-exec gate
        reads, so it must never be more optimistic than the enforcement."""
        return os_sandbox_available()

    def _command_argv(self, command: str, cwd: Path | None) -> tuple[list[str] | str, bool]:
        roots = _writable_roots(cwd)
        if seatbelt_available():
            return ([_SEATBELT, "-p", _seatbelt_profile(roots), "/bin/sh", "-c", command], False)
        if bubblewrap_available():
            return (_bwrap_argv(roots, cwd) + ["--", "/bin/sh", "-c", command], False)
        # No boundary to apply. Falling through to the host would be the silent degradation this
        # module exists to prevent, so say it and run gated instead — `is_isolated` is already False,
        # which keeps the host-exec confirmation in front of this command.
        _log.warning("os sandbox unavailable, running on the host: %s", unavailable_reason())
        return (command, True)
