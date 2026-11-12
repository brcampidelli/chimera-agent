"""The SSH execution environment: a conversation's commands run on another machine, over OpenSSH.

**This is not isolation, and nothing here pretends it is.** The command runs as the owner's own
login on a machine the owner named; whatever that account can do there, the agent can do. So
:meth:`SshSandbox.is_isolated` is False, the host-exec gate (``CHIMERA_HOST_EXEC``), Manual,
Accept edits and the "only reads" classifier treat it as the host, and the environment never loosens
anything the local one would not (:func:`chimera.core.environments.sandbox_for`).

What this module owns:

* **The alias, never the connection.** An environment names a ``Host`` alias that exists in the
  owner's ``~/.ssh/config`` (:func:`config_aliases`); the user, the address, the key and the
  jump host stay in that file, and the key stays in ``ssh-agent``. No user name, IP, password or key
  is ever typed into Chimera, kept in the environment file, written to a conversation or logged.
  Which aliases may be used at all is the owner's second list (``ssh_hosts.json``, edited only on the
  app's own screen), so a ``Host`` line added to the config by anything else does not become a place
  the agent can run commands.
* **The argv** (:func:`ssh_argv`): the system ``ssh``, always a list, never ``shell=True`` locally.
  ``BatchMode=yes`` (no prompt can block a turn that has nobody to answer it) and
  ``StrictHostKeyChecking=yes`` (a host key that is not already in ``known_hosts`` is an error, never
  accepted on the agent's behalf, and a changed one is a refusal). Agent, port and X11 forwarding are
  switched off, so a command cannot borrow the owner's keys to reach a machine that is not on the
  list; ``RemoteCommand=none`` and ``RequestTTY=no`` keep a ``Host`` block of the config from
  replacing the agent's command or opening a terminal. ``--`` ends the options before the alias.
* **The remote command.** ssh hands ONE string to the remote account's login shell, which interprets
  it exactly as the local shell would interpret the same string: quoting, ``&&``, pipes, redirections
  and ``$(...)`` are the REMOTE shell's. The working folder is the environment's ``remote_path``,
  quoted for that shell with :func:`shlex.quote`; ``cd`` failing ends the command, so a missing
  folder is an error and not a command that runs in the home folder.
* **Reconnecting, narrowly.** The ssh CLIENT's own messages are written to a private temporary file
  (``-E``), apart from the remote command's stderr, which is the only thing that comes back on the
  process's stderr. A connection that fails BEFORE the remote shell started (the client's own
  ``connect to host ... Connection refused/timed out`` line in that file, exit 255, nothing on stdout
  or stderr) is retried once or twice after a short wait. What the remote command prints, however
  it looks, is never evidence of a failed connection, so a script that forges that line, or whose
  last step is a nested ``ssh`` that fails, is not run again. Anything else is never re-run: a
  command that may have started is not started again, because a command is not idempotent.
* **What the agent is told.** Never the client's raw log: it names the resolved HostName, the
  address, the port and the key files the config points at. The agent gets the remote command's own
  stderr, plus (for exit 255) a one-line explanation, or the client's message with the address and
  port replaced by the alias and any line naming a key file dropped (:func:`sanitized`).

The fences that guard Chimera's own files by path (the approval queue, the audit log, the hooks and
``.env``) protect THIS machine's files and keep reading the command as they always did; they do not,
and cannot, protect a file on the remote machine, and nothing here loosens them. The file tools are
the other half: the files are over there, so a conversation in this environment refuses the local
read/write/edit tools (:mod:`chimera.tools.ssh_files`) instead of editing the wrong machine. A remote
file layer is a separate piece of work.

``Include`` in ``~/.ssh/config`` is followed only for files that resolve inside ``~/.ssh``; one that
points elsewhere is not read, and its aliases are not offered.

The process itself (timeout, killing the tree, the scrubbed environment, the decoding) is
:class:`~chimera.sandbox.local.LocalSandbox`'s, reused. Killing the local ``ssh`` on a timeout ends
this side; what the remote shell does with the command it was running is the remote's (it normally
gets a hangup when the channel closes, but a program that ignores it keeps running).
"""

from __future__ import annotations

import contextlib
import glob
import os
import posixpath
import re
import shlex
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from chimera.sandbox.base import SandboxResult

SSH_PROGRAM = "ssh"

#: A ``Host`` alias as this app accepts it. The leading character cannot be ``-``: the alias is an
#: argv element and a value that looked like an option would be read as one (``--`` guards it too).
_ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

MAX_REMOTE_PATH = 1024
MAX_ALIASES = 500
_MAX_CONFIG_FILES = 20
_MAX_CONFIG_BYTES = 256 * 1024
_MAX_INCLUDE_DEPTH = 4

#: Retries after a connection that failed before the remote shell started, and the wait before each.
CONNECT_RETRIES = 2
RETRY_WAIT_SECONDS = 1.5

#: The only options this app puts on the command line. Nothing here is read from a setting.
_SECURITY_OPTIONS: tuple[str, ...] = (
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3",
    "-o", "ConnectTimeout=10",
    "-o", "ForwardAgent=no",
    "-o", "ClearAllForwardings=yes",
    "-o", "ForwardX11=no",
    "-o", "PermitLocalCommand=no",
    "-o", "RemoteCommand=none",
    "-o", "RequestTTY=no",
)


class SshPathError(ValueError):
    """A remote folder or working directory that cannot be used."""


def ssh_supported() -> bool:
    """Whether an ``ssh`` program is on the PATH. A seam for the tests."""
    return shutil.which(SSH_PROGRAM) is not None


def valid_alias(name: object) -> bool:
    """Whether ``name`` has the shape of a host alias. Existence in the config is checked separately."""
    return isinstance(name, str) and bool(_ALIAS.fullmatch(name))


def clean_remote_path(path: object) -> str:
    """``path`` as an absolute POSIX folder with no ``..``, or :class:`SshPathError` with a sentence."""
    if not isinstance(path, str):
        raise SshPathError("remote_path must be text")
    text = path.strip()
    if not text or len(text) > MAX_REMOTE_PATH:
        raise SshPathError(f"remote_path is required and at most {MAX_REMOTE_PATH} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise SshPathError("remote_path has a control character")
    if "\\" in text:
        raise SshPathError("remote_path is a POSIX path: it has no backslash")
    if not text.startswith("/"):
        raise SshPathError("remote_path must be an absolute path on the remote machine (it starts with /)")
    if ".." in text.split("/"):
        raise SshPathError("remote_path may not contain '..'")
    cleaned = re.sub(r"/{2,}", "/", text)
    return cleaned if cleaned == "/" else cleaned.rstrip("/")


# -- ~/.ssh/config ------------------------------------------------------------------------------


def ssh_dir() -> Path:
    """The owner's ``~/.ssh``. A seam: tests point it at a folder."""
    return Path.home() / ".ssh"


def config_aliases(config: Path | None = None) -> list[str]:
    """The concrete ``Host`` aliases of the owner's ssh config, in file order, without duplicates.

    Patterns are not aliases and are left out: ``Host *``, ``Host *.corp``, ``Host web?`` and any
    ``!negated`` entry (a wildcard is not a place to run commands, and offering one would let the
    owner name any machine that matches). ``Match`` blocks are ignored. ``Include`` is followed for
    files that resolve inside ``~/.ssh`` only, a few levels deep. A file that cannot be read yields no
    aliases rather than an error: the screen shows an empty list and the environment is not created.
    """
    root = ssh_dir()
    start = config if config is not None else root / "config"
    found: list[str] = []
    _read_config(start, root, found, set(), 0)
    return found


def _read_config(path: Path, root: Path, found: list[str], seen: set[Path], depth: int) -> None:
    try:
        real = path.resolve(strict=True)
        if real in seen or len(seen) >= _MAX_CONFIG_FILES or not real.is_file():
            return
        if real.stat().st_size > _MAX_CONFIG_BYTES:
            return
        text = real.read_text(encoding="utf-8", errors="ignore")
    except (OSError, ValueError, RuntimeError):
        return
    seen.add(real)
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        match = re.match(r"(?i)(host|include)(?:\s*=\s*|\s+)(.+)$", line)
        if match is None:
            continue
        if match.group(1).lower() == "host":
            try:
                words = shlex.split(match.group(2))  # a quoted pattern is ONE word, as ssh reads it
            except ValueError:
                continue
            for word in words:
                if valid_alias(word) and word not in found and len(found) < MAX_ALIASES:
                    found.append(word)
        elif depth < _MAX_INCLUDE_DEPTH:
            # Not shlex: a Windows path has backslashes the POSIX rules would eat.
            words = [w.strip("\"'") for w in match.group(2).split()]
            for target in _include_targets(words, root):
                _read_config(target, root, found, seen, depth + 1)


def _include_targets(patterns: list[str], root: Path) -> list[Path]:
    """The files an ``Include`` names that resolve inside ``root``; anything else is not followed."""
    try:
        inside = root.resolve(strict=False)
    except (OSError, ValueError, RuntimeError):
        return []
    out: list[Path] = []
    for pattern in patterns:
        expanded = os.path.expanduser(pattern)
        full = expanded if os.path.isabs(expanded) else str(root / expanded)
        for hit in sorted(glob.glob(full)):
            try:
                resolved = Path(hit).resolve(strict=True)
            except (OSError, ValueError, RuntimeError):
                continue
            if resolved.is_relative_to(inside):
                out.append(resolved)
    return out


# -- the command --------------------------------------------------------------------------------


def remote_command(remote_dir: str, command: str) -> str:
    """The one string the remote login shell is given: ``cd <dir>``, then ``command`` as typed.

    ``||`` rather than ``&&`` so that a ``cd`` that fails ends the command: with ``cd d && a; b`` the
    ``b`` would still run, in the home folder, which is a different folder than the one the owner
    named. The directory is quoted with :func:`shlex.quote`; the command is NOT quoted or rewritten:
    it is the agent's command and the remote shell reads it.
    """
    return f"cd {shlex.quote(remote_dir)} || exit 1; {command}"


def ssh_argv(
    alias: str, remote: str, program: str = SSH_PROGRAM, log_path: str | None = None
) -> list[str]:
    """The argument vector for one command: a list, the options above, ``--``, the alias, the command.

    ``log_path`` sends the client's own messages to that file (``-E``) instead of stderr.
    """
    if not valid_alias(alias):
        raise SshPathError("the host alias is not valid")
    logging = ["-E", log_path] if log_path else []
    return [program, *_SECURITY_OPTIONS, *logging, "--", alias, remote]


_HOST_KEY = re.compile(
    r"Host key verification failed|REMOTE HOST IDENTIFICATION HAS CHANGED|No (?:\w+ )?host key is known",
    re.IGNORECASE,
)
#: ssh's OWN words for a connection that never reached the remote shell. Anchored to its prefixes so
#: that a remote program that happens to print "Connection refused" and exit 255 is not re-run.
_NEVER_CONNECTED = re.compile(
    r"^(?:ssh: connect to host \S+ port \d+: "
    r"(?:Connection refused|Connection timed out|Operation timed out|No route to host|"
    r"Network is unreachable|Connection reset by peer)"
    r"|kex_exchange_identification|ssh_exchange_identification"
    r"|Connection timed out during banner exchange)",
    re.IGNORECASE,
)
_PERMISSION = re.compile(r"Permission denied \(", re.IGNORECASE)


def failed_before_connecting(result: SandboxResult, client_log: str) -> bool:
    """A connection that provably failed before the remote shell started.

    Decided by the CLIENT's own log, which the remote command cannot write to: exit 255, nothing on
    stdout or stderr (a shell that started would have said something, or the command's own stderr
    would be here), and the client's own "could not connect" line in its log.
    """
    if result.exit_code != 255 or result.timed_out:
        return False
    if (result.stdout or "").strip() or (result.stderr or "").strip():
        return False
    lines = [ln.strip() for ln in client_log.splitlines() if ln.strip()]
    return 0 < len(lines) <= 4 and any(_NEVER_CONNECTED.match(ln) for ln in lines)


_KEY_LINE = re.compile(
    r"identity file|bad owner or permissions|load key|key_load|identityfile|\.pem|id_[a-z0-9]+|known_hosts"
    r"|\.ssh|^debug\d|/home/|/Users/|/root/|[A-Za-z]:\\",
    re.IGNORECASE,
)
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_IPV6 = re.compile(r"(?<![0-9a-z])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![0-9a-z])", re.IGNORECASE)


def sanitized(client_log: str, host: str) -> str:
    """The client's message with what the config resolved removed: the alias, never the HostName.

    The address, the port and any key path are the owner's connection details; the agent needs to
    know that the connection failed and why, not where the config points.
    """
    out: list[str] = []
    for raw in client_log.splitlines():
        line = raw.strip()
        if not line or _KEY_LINE.search(line):
            continue
        line = re.sub(r"connect to host \S+ port \d+", f"connect to {host}", line)
        line = re.sub(r"Could not resolve hostname \S+", f"Could not resolve the address of {host}", line)
        line = _IPV6.sub("<address>", _IPV4.sub("<address>", line))
        line = re.sub(r"\bport \d+", "port <port>", line)
        out.append(line)
    return "\n".join(out)


def explained(result: SandboxResult, host: str, client_log: str = "") -> SandboxResult:
    """``result`` as the agent sees it: the remote command's stderr, plus a sentence when ssh itself failed.

    ssh's own failure (exit 255 with a client log) is turned into a sentence the owner can act on
    (trust, credentials) or the sanitized client message. The raw client log is never passed on.
    """
    err = result.stderr or ""
    if result.exit_code == 255 and not client_log.strip():
        # No client log, yet exit 255: a ProxyJump / ProxyCommand hop's own ssh writes to THIS stderr
        # (not to the -E file), naming the resolved address, port and key files. Same cleaning.
        if not err.strip():
            return result
        return SandboxResult(
            exit_code=result.exit_code, stdout=result.stdout, stderr=sanitized(err, host),
            timed_out=result.timed_out,
        )
    if result.exit_code != 255:
        return result
    if _HOST_KEY.search(client_log):
        note = (
            f"error: ssh refused {host}: its host key is not trusted (it is not in known_hosts, or it "
            "changed). Chimera never accepts a new host key on its own. Connect once from a terminal "
            f"(`ssh {host}`), check the fingerprint, then try again. Not retried."
        )
    elif _PERMISSION.search(client_log):
        note = (
            f"error: ssh could not authenticate to {host} without asking anything: it never prompts "
            "here. Load the key into ssh-agent (or fix the host's IdentityFile) and try again. Not retried."
        )
    else:
        note = f"error: ssh could not run the command on {host}: {sanitized(client_log, host)}".rstrip(": ")
    return SandboxResult(
        exit_code=result.exit_code, stdout=result.stdout, stderr=f"{note}\n{err}".rstrip(),
        timed_out=result.timed_out,
    )


def _sleep(seconds: float) -> None:
    """A seam for the tests: the wait between connection retries."""
    time.sleep(seconds)


def _read_client_log(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            return handle.read(8192).decode("utf-8", errors="replace")
    except OSError:
        return ""


class SshSandbox:
    """Runs a command on a host from the owner's ssh config, in the environment's remote folder.

    Deliberately **not** a :class:`~chimera.sandbox.local.LocalSandbox`: every place that treats that
    class as "a process on this host I can adopt, wrap or kill as a job" would be wrong about this one.
    Background jobs are therefore not offered.
    """

    def __init__(self, host: str, remote_path: str, workspace: Path | str | None = None) -> None:
        if not valid_alias(host):
            raise SshPathError("the host alias is not valid")
        self.host = host
        self.remote_path = clean_remote_path(remote_path)
        self.workspace = Path(workspace) if workspace is not None else None
        self._tainted: Callable[[], bool] | None = None
        self._ask: Callable[[str], bool] | None = None
        self._recheck: Callable[[], None] | None = None

    @staticmethod
    def is_isolated() -> bool:
        """Never: a remote shell is the owner's login on that machine."""
        return False

    def bind_recheck(self, recheck: Callable[[], None]) -> None:
        """Re-validate the host (in the config AND on the allowed list) at each command.

        ``recheck`` raises ``ValueError`` with a sentence. It closes the window between the turn's
        start and a later command: a host removed from either file mid-turn stops the next command.
        """
        self._recheck = recheck

    def bind_taint(self, tainted: Callable[[], bool], ask: Callable[[str], bool] | None) -> None:
        """Make a command after untrusted input ask the owner first.

        ``tainted`` answers "has this turn read content from outside" (the turn's taint ledger) and
        is asked at each command. ``ask`` puts the command on the owner's screen; None is "nobody can
        be asked here", and then a tainted turn is refused. A turn that never reads anything outside
        is not slowed down by this at all.
        """
        self._tainted = tainted
        self._ask = ask

    def _remote_dir(self, cwd: Path | None) -> str:
        """The remote folder for ``cwd``: the environment's, plus the part below the local workspace."""
        if cwd is None or self.workspace is None:
            return self.remote_path
        try:
            below = Path(cwd).resolve(strict=False).relative_to(self.workspace.resolve(strict=False))
        except (OSError, ValueError, RuntimeError) as exc:
            raise SshPathError("the working directory is outside the workspace") from exc
        return posixpath.join(self.remote_path, *below.parts) if below.parts else self.remote_path

    def _declined_for_taint(self, command: str) -> bool:
        if self._tainted is None or not self._tainted():
            return False
        return self._ask is None or not self._ask(command)

    def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
        from chimera.sandbox.local import LocalSandbox

        if self._recheck is not None:
            try:
                self._recheck()
            except ValueError as exc:
                return SandboxResult(exit_code=1, stderr=f"error: not run: {exc}")
        try:
            where = self._remote_dir(cwd)
            remote = remote_command(where, command)
        except SshPathError as exc:
            return SandboxResult(exit_code=1, stderr=f"error: not run: {exc}")
        if self._declined_for_taint(command):
            return SandboxResult(
                exit_code=1,
                stderr=(
                    f"error: not run: this turn has read content from outside, so a command on {self.host} "
                    "needs the owner's approval, and it was not given (or nobody could be asked here)."
                ),
            )
        try:
            handle, log = tempfile.mkstemp(prefix="chimera-ssh-", suffix=".log")
            os.close(handle)
        except OSError as exc:
            return SandboxResult(
                exit_code=1, stderr=f"error: not run: could not create the ssh client's log file ({exc.strerror or 'OSError'})"
            )
        try:
            argv = ssh_argv(self.host, remote, log_path=log)
            return self._run_with_retries(LocalSandbox(), argv, log, timeout)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(log)

    def _run_with_retries(self, runner: Any, argv: list[str], log: str, timeout: int) -> SandboxResult:
        deadline = time.monotonic() + timeout
        attempt = 0
        while True:
            with open(log, "wb"):  # the client's log starts empty for each attempt
                pass
            left = max(1, int(deadline - time.monotonic()))
            result = runner._run_process(argv, timeout=left, cwd=None, use_shell=False, on_timeout=None)
            client = _read_client_log(log)
            if (
                failed_before_connecting(result, client)
                and attempt < CONNECT_RETRIES
                and deadline - time.monotonic() > RETRY_WAIT_SECONDS + 1
            ):
                attempt += 1
                _sleep(RETRY_WAIT_SECONDS)
                continue
            return explained(result, self.host, client)
