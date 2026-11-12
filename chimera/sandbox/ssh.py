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
* **The remote command, detached.** Each command runs on the remote under a RUN ID (16 hex digits
  made here), started by a fixed launcher script (:data:`LAUNCHER`, POSIX sh, handed to the remote
  login shell as ONE single-quoted word with the operation name, the id and integers after it). The
  launcher makes a private run folder (``${XDG_CACHE_HOME:-$HOME/.cache}/chimera/runs/<id>``, mode 700,
  ``umask 077``), starts the command with ``setsid nohup`` (plain ``nohup`` where there is no
  ``setsid``) so that it outlives the connection, and writes its stdout, its stderr and, last, its
  exit code to three files. The agent's command is never inside any argument: it travels on stdin as
  a script file whose first line is ``cd <remote_path> || exit 1`` (quoted with :func:`shlex.quote`;
  ``cd`` failing ends the command, so a missing folder is an error and not a command that runs in the
  home folder) and the rest is the command exactly as typed, read by the remote's own shell
  (``$SHELL`` when it is sh, bash, dash, zsh, ksh or ash, otherwise sh). Quoting, ``&&``, pipes,
  redirections and ``$(...)`` are that shell's, as before; what differs is that it reads a file and
  not a ``-c`` string, and does not read an interactive profile. The launcher is a multi-line single-quoted word, so the
  account's login shell must be sh-family; a csh/tcsh login shell is detected by its own error and
  reported by name. The launcher refuses a run folder this account does not own, the user's command
  gets the caller's umask back (the launcher's ``umask 077`` is not inherited), and a launch is
  repeated only when its folder has no ``mode`` marker, which is written BEFORE the fork.
  Every launch and poll re-checks the allowed host list; a host removed mid-run stops the run.
* **Following, and reconnecting.** The client follows the run by polling: each ssh call asks the
  launcher for the bytes after the offsets it already has (a long poll of a few seconds, so a short
  command is one connection). If the connection drops, the call is repeated over a new one with
  backoff (:data:`RECONNECT_BACKOFF`, consecutive failures only) and reads from the saved offsets.
  This is what makes reconnecting safe where it used to be unsafe: the run id, not the connection, is
  the command's identity. ``launch`` on an id that already started only reads it (a launch whose
  reply was lost is repeated, never duplicated), and a poll is a read. What is still never retried: a
  refusal waiting cannot fix (host key, credentials) and a remote shell that ran and failed.
* **Stopping.** On a timeout, or when the turn is cancelled, the fixed ``stop`` operation signals the
  run's whole process GROUP (TERM, then KILL) and the partial output is returned. A command that
  finished is never signalled. Where the remote has no ``setsid`` there is no group to signal: the
  wrapper and its direct children are, the reply is "partial", and the client then treats the run as
  NOT stopped (names it, keeps its folder) rather than reporting a stop that did not happen. When the connection is gone and the kill cannot be sent, the result
  says so and names the run, because the command is still running.
* **Cleanup, and a run nobody finished.** The run folder is removed once its result is read, and every
  launch removes folders of finished or dead runs older than :data:`SWEEP_DAYS`. If Chimera itself
  dies mid-command the remote command keeps running; each run is also written to a small journal under
  ``CHIMERA_HOME/ssh_runs`` so that the next start can say, in the "interrupted" notice, which host and
  run id to look at (:func:`take_pending_runs`), and :meth:`SshSandbox.read_run` reads a run by id.
* **Client messages.** The ssh CLIENT's own messages are written to a private temporary file (``-E``),
  apart from the remote command's output. A refusal there (host key, credentials) is explained and
  not retried; ``exit 255`` is the link failing.
* **What the agent is told.** Never the client's raw log: it names the resolved HostName, the
  address, the port and the key files the config points at. The agent gets the remote command's own
  stderr, plus (for exit 255) a one-line explanation, or the client's message with the address and
  port replaced by the alias and any line naming a key file dropped (:func:`sanitized`).

The fences that guard Chimera's own files by path (the approval queue, the audit log, the hooks and
``.env``) protect THIS machine's files and keep reading the command as they always did; they do not,
and cannot, protect a file on the remote machine, and nothing here loosens them. The file tools are
the other half: the files are over there, so a conversation in this environment refuses the local
read/write/edit tools (:mod:`chimera.tools.ssh_files`) instead of editing the wrong machine. The core
ones (``read_file``, ``write_file``, ``edit_file``, ``apply_patch``, ``edit_batch``, ``list_dir``, ``grep``,
``glob``) are replaced there by remote forms that go through :meth:`SshSandbox.run_script`: the same
argv, the same alias re-check and the same sanitizing as a command, but NOT the detached launcher (a
file operation is short and checks itself: a write carries its byte count and sha256 and is dropped on a
mismatch, so repeating one is safe); the rest of the file tools keep the refusal.

``Include`` in ``~/.ssh/config`` is followed only for files that resolve inside ``~/.ssh``; one that
points elsewhere is not read, and its aliases are not offered.

Each ssh call is its own local process with the scrubbed environment of
:class:`~chimera.sandbox.local.LocalSandbox` (reused: ``_child_env``, ``kill_tree``); its output is kept
as bytes because a poll offset is a byte count. Output decoding and the cap on what is kept
(:data:`MAX_OUTPUT_BYTES`) are applied to the whole run, not to a call.
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import posixpath
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.proc.stdio import kill_tree, no_window
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
_PERMISSION = re.compile(r"Permission denied \(", re.IGNORECASE)


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
        exit_code=result.exit_code, stdout=result.stdout, stderr=f"{note}\n{sanitized(err, host)}".rstrip(),
        timed_out=result.timed_out,
    )


def _sleep(seconds: float) -> None:
    """A seam for the tests: the wait between reconnection attempts."""
    time.sleep(seconds)


def _now() -> float:
    """A seam for the tests: the monotonic clock the deadlines are read from."""
    return time.monotonic()


def _read_client_log(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            return handle.read(8192).decode("utf-8", errors="replace")
    except OSError:
        return ""


# -- the detached run ---------------------------------------------------------------------------

#: A run id is 16 hex digits made here; the pattern is what ``read_run`` and the journal accept back.
_RUN_ID = re.compile(r"[0-9a-f]{16}")

#: Where the remote keeps a run, relative to the remote account's cache folder. Shown to the agent so
#: that a later turn can read an orphaned run with an ordinary ``cat``.
RUN_FOLDER_HINT = "${XDG_CACHE_HOME:-$HOME/.cache}/chimera/runs"

#: A finished (or dead) run folder older than this many days is removed when the next run starts.
SWEEP_DAYS = 3

#: How long a launch may take to receive its script (``timeout`` on the remote, where there is one),
#: and how old another attempt's claim must be before a retry may take the folder over. The second is
#: well above the first, and above how long the client keeps retrying, so an attempt that is merely
#: slow is never overtaken; one that is stuck for longer than this is, and it no longer matters what
#: it does when it wakes (every step re-checks that the claim is still its own).
LAUNCH_READ_SECONDS = 60
LAUNCH_STALE_SECONDS = 120

#: The longest a single ssh call may sit on the remote waiting for output to finish, and how long the
#: launching call waits so that a short command is one connection, not two.
POLL_WAIT_SECONDS = 5
LAUNCH_WAIT_SECONDS = 2
_CALL_SLACK_SECONDS = 25
_CHUNK_BYTES = 262144
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_COMMAND_BYTES = 256 * 1024
#: The most time reading an old run's output back may take.
READ_RUN_SECONDS = 60

#: After a call fails at the ssh level (exit 255, or it hung), wait this long before the next try; the
#: number of entries is the number of consecutive failures tolerated before the run is given up on.
#: A call that succeeds starts the count again, so a link that flaps all day still finishes the run.
#: How many times a file operation (:meth:`SshSandbox.run_script`) is repeated after an ssh-level failure.
FILE_OP_RETRIES = 2
RECONNECT_BACKOFF: tuple[float, ...] = (1.5, 3.0, 6.0, 12.0, 15.0, 15.0)

_LAUNCHER_TEMPLATE = r"""
op=$1; id=$2; wait=$3; o=${4:-0}; e=${5:-0}; n=${6:-}
base=${XDG_CACHE_HOME:-$HOME/.cache}/chimera/runs
d=$base/$id
frame() {
  if [ -d "$d" ]; then :; else echo "chimera-poll gone 0 0 0"; return 0; fi
  i=0
  if [ "$wait" -gt 0 ]; then
    if sleep 0.2 2>/dev/null; then ticks=$((wait*5)); step=0.2; else ticks=$wait; step=1; fi
    while [ $i -lt $ticks ]; do
      if [ -f "$d/code" ]; then break; fi
      sleep $step
      i=$((i+1))
    done
  fi
  if [ -f "$d/code" ]; then fin=1; code=$(cat "$d/code"); else fin=0; code=0; fi
  on=$(tail -c +$((o+1)) "$d/out" 2>/dev/null | head -c @CHUNK@ | wc -c | tr -d " ")
  en=$(tail -c +$((e+1)) "$d/err" 2>/dev/null | head -c @CHUNK@ | wc -c | tr -d " ")
  echo "chimera-poll $fin $code $on $en"
  tail -c +$((o+1)) "$d/out" 2>/dev/null | head -c "$on"
  tail -c +$((e+1)) "$d/err" 2>/dev/null | head -c "$en"
}
sweep() {
  for r in "$base"/*; do
    if [ -d "$r" ]; then :; else continue; fi
    if [ -f "$r/code" ]; then f=$r/code; elif [ -f "$r/out" ]; then f=$r/out; elif [ -f "$r/claim" ]; then f=$r/claim; else f=$r; fi
    if [ -n "$(find "$f" -prune -mtime +@DAYS@ 2>/dev/null)" ]; then :; else continue; fi
    if [ -f "$r/code" ]; then :; else
      if [ -f "$r/pid" ] && kill -0 "$(cat "$r/pid")" 2>/dev/null; then continue; fi
    fi
    rm -rf "$r"
  done
}
launch() {
  um=$(umask)
  umask 077
  mkdir -p "$base" 2>/dev/null && chmod 700 "$base" 2>/dev/null
  if [ -O "$base" ]; then :; else echo "chimera-error the run folder is not owned by this account"; return 0; fi
  if [ -d "$d" ]; then
    if [ -f "$d/mode" ]; then frame; return 0; fi
    ctok=; t0=0
    read ctok t0 < "$d/claim" 2>/dev/null
    now=$(date +%s)
    if [ -f "$d/claim" ] && [ "$t0" -gt 0 ] 2>/dev/null && [ $((now-t0)) -ge @STALE@ ]; then rm -rf "$d"; else echo "chimera-error launch in progress"; return 0; fi
  fi
  if mkdir "$d" 2>/dev/null; then :; else echo "chimera-error launch in progress"; return 0; fi
  now=$(date +%s)
  tok=$$-$now
  echo "$tok $now" > "$d/claim"
  tmp=$d/cmd.$tok.tmp
  if command -v timeout >/dev/null 2>&1; then timeout @CATLIMIT@ cat > "$tmp"; else cat > "$tmp"; fi
  got=$(wc -c < "$tmp" 2>/dev/null | tr -d " ")
  if [ "$got" = "$n" ]; then :; else
    release
    echo "chimera-error the command arrived incomplete and was not run"; return 0
  fi
  if mine; then :; else rm -f "$tmp"; return 0; fi
  if ln "$tmp" "$d/cmd.sh" 2>/dev/null; then rm -f "$tmp"; else rm -f "$tmp"; return 0; fi
  if command -v setsid >/dev/null 2>&1; then lmode=setsid; else lmode=plain; fi
  if (set -C; echo "$lmode" > "$d/mode") 2>/dev/null; then :; else return 0; fi
  : > "$d/out"; : > "$d/err"
  sh_bin=${SHELL:-sh}
  case ${sh_bin##*/} in sh|bash|dash|zsh|ksh|ash) ;; *) sh_bin=sh ;; esac
  inner='umask "$3"; ps -o pgid= -p $$ 2>/dev/null | tr -d " " > "$2/pgid"; "$1" "$2/cmd.sh" > "$2/out" 2> "$2/err" < /dev/null; echo $? > "$2/code.tmp"; mv "$2/code.tmp" "$2/code"'
  if [ "$lmode" = setsid ]; then
    setsid nohup sh -c "$inner" chimera-run "$sh_bin" "$d" "$um" < /dev/null > /dev/null 2>&1 &
  else
    nohup sh -c "$inner" chimera-run "$sh_bin" "$d" "$um" < /dev/null > /dev/null 2>&1 &
  fi
  echo $! > "$d/pid"
  sweep
  frame
}
mine() {
  cur=
  read cur _ < "$d/claim" 2>/dev/null
  [ "$cur" = "$tok" ]
}
release() {
  if mine; then rm -f "$d/claim"; fi
  rm -f "$tmp"
  rmdir "$d" 2>/dev/null
  return 0
}
stop() {
  if [ -d "$d" ]; then :; else echo "chimera-stopped gone"; return 0; fi
  if [ -f "$d/code" ]; then echo "chimera-stopped finished"; return 0; fi
  mode=$(cat "$d/mode" 2>/dev/null); pid=$(cat "$d/pid" 2>/dev/null); pg=$(cat "$d/pgid" 2>/dev/null)
  if [ "$mode" = setsid ]; then
    if [ "$pg" -gt 1 ] 2>/dev/null; then :; else pg=$pid; fi
    if [ "$pg" -gt 1 ] 2>/dev/null; then
      kill -s TERM -- "-$pg" 2>/dev/null; sleep 1; kill -s KILL -- "-$pg" 2>/dev/null
      sleep 1
      if kill -s 0 -- "-$pg" 2>/dev/null; then echo "chimera-stopped partial"; return 0; fi
    else
      echo "chimera-stopped partial"; return 0
    fi
  else
    if [ "$pid" -gt 1 ] 2>/dev/null; then
      pkill -TERM -P "$pid" 2>/dev/null; kill -TERM "$pid" 2>/dev/null; sleep 1
      pkill -KILL -P "$pid" 2>/dev/null; kill -KILL "$pid" 2>/dev/null
    fi
    echo "chimera-stopped partial"; return 0
  fi
  echo "chimera-stopped ok"
}
case $op in
  launch) launch ;;
  poll) frame ;;
  stop) stop ;;
  cleanup) if [ -n "$id" ]; then rm -rf "$d"; fi; echo "chimera-cleaned" ;;
esac
"""

#: The remote program: fixed text, POSIX sh, and nothing the agent wrote is ever inside it.
LAUNCHER = (
    _LAUNCHER_TEMPLATE.replace("@CHUNK@", str(_CHUNK_BYTES)).replace("@DAYS@", str(SWEEP_DAYS))
    .replace("@STALE@", str(LAUNCH_STALE_SECONDS))
    .replace("@CATLIMIT@", str(LAUNCH_READ_SECONDS)).strip("\n") + "\n"
)

_FRAME = re.compile(rb"(?:^|\n)chimera-poll (\w+) (\d+) (\d+) (\d+)\r?\n")
_NOTE = re.compile(rb"(?:^|\n)(chimera-error[^\r\n]*)")


def run_script(remote_dir: str, command: str) -> str:
    """What the detached shell reads: :func:`remote_command` and a newline. It travels on stdin."""
    return remote_command(remote_dir, command) + "\n"


def remote_op(op: str, run_id: str, *numbers: int) -> str:
    """The one string the remote login shell is given for an operation on a run.

    Only the fixed launcher (one single-quoted word) and arguments made here: the operation name, a
    hex run id and integers. The agent's command is not in it, so there is nothing to re-quote.
    """
    if not _RUN_ID.fullmatch(run_id):
        raise SshPathError("the run id is not valid")
    return " ".join(["sh", "-c", shlex.quote(LAUNCHER), "chimera-run", op, run_id, *(str(int(n)) for n in numbers)])


@dataclass
class _Call:
    """One ssh process: raw bytes (an offset into a file is a byte count, so nothing is decoded here)."""

    code: int
    out: bytes = b""
    err: bytes = b""
    timed_out: bool = False
    client_log: str = ""


@dataclass
class _Frame:
    """What a poll returned: whether the command finished, its exit code, and the new bytes."""

    done: bool
    code: int
    out: bytes
    err: bytes
    gone: bool = False


def parse_frame(data: bytes) -> _Frame | None:
    """The poll frame in ``data``, or None. Anything the remote login shell printed before it is skipped."""
    match = _FRAME.search(data)
    if match is None:
        return None
    state = match.group(1)
    code, n_out, n_err = int(match.group(2)), int(match.group(3)), int(match.group(4))
    body = data[match.end():]
    if len(body) < n_out + n_err:
        return None  # the connection ended inside the payload: it is read again from the same offset
    return _Frame(
        done=state == b"1", code=code, out=body[:n_out], err=body[n_out:n_out + n_err], gone=state == b"gone"
    )


def _journal_dir() -> Path:
    """Where this side remembers the runs it started. A seam: tests point it at a folder."""
    from chimera.config import get_settings

    return Path(get_settings().home) / "ssh_runs"


def journal_add(host: str, run_id: str, remote_path: str, session: str = "") -> None:
    """Remember a run before it starts, so a later session can find a command this process never
    saw finish (the app closed, the service restarted). Best effort: never costs the command."""
    try:
        folder = _journal_dir()
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        entry = {
            "host": host, "run_id": run_id, "remote_path": remote_path, "session": session,
            "started": time.time(),
        }
        _write_private(folder / f"{run_id}.json", json.dumps(entry))
        _purge_stale_journal(folder)
    except OSError:
        pass


def _write_private(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` readable by this account only (mode 600)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def _purge_stale_journal(folder: Path, now: float | None = None) -> None:
    """Drop journal entries older than the sweep window: the remote's own sweep has removed the run."""
    clock = time.time() if now is None else now
    for path in folder.glob("*.json"):
        try:
            started = float(json.loads(path.read_text(encoding="utf-8")).get("started", 0))
            if clock - started > SWEEP_DAYS * 86400:
                path.unlink()
        except (OSError, ValueError, TypeError, AttributeError):
            continue


def journal_remove(run_id: str) -> None:
    with contextlib.suppress(OSError):
        (_journal_dir() / f"{run_id}.json").unlink()


def take_pending_runs(now: float | None = None) -> list[dict[str, Any]]:
    """Runs the journal holds that were not finished by this side, each handed out once.

    Called when the app starts, where nothing of this process is running yet, so every entry is
    from an earlier one. An entry is marked announced and not offered again; one older than
    :data:`SWEEP_DAYS` is dropped (the remote's own sweep removes the folder by then).
    """
    found: list[dict[str, Any]] = []
    clock = time.time() if now is None else now
    try:
        paths = sorted(_journal_dir().glob("*.json"))
    except OSError:
        return found
    for path in paths:
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            valid = (
                isinstance(entry, dict)
                and valid_alias(entry.get("host"))
                and _RUN_ID.fullmatch(str(entry.get("run_id"))) is not None
            )
            if not valid or clock - float(entry.get("started", 0)) > SWEEP_DAYS * 86400:
                path.unlink()
            elif not entry.get("announced"):
                found.append(entry)
                _write_private(path, json.dumps({**entry, "announced": True}))
        except (OSError, ValueError, TypeError):
            continue
    return found


def interrupted_note(entries: list[dict[str, Any]]) -> str:
    """One sentence per run for the "interrupted" notice: where it is and how to read it."""
    lines = []
    for entry in entries[:5]:
        lines.append(
            f"On {entry['host']} the command of run {entry['run_id']} may still be running or have finished: "
            f"its output is in {RUN_FOLDER_HINT}/{entry['run_id']}/ (out, err, and code once it ends)."
        )
    return " ".join(lines)


def _run_hint(host: str, run_id: str) -> str:
    return (
        f"The command was not stopped: if it started it keeps running on {host} as run {run_id}. "
        f"Its output is in {RUN_FOLDER_HINT}/{run_id}/ (out, err, and code once it ends)."
    )


class _Cancelled(Exception):
    """The turn was stopped while the client was waiting to reconnect."""


#: The launcher's replies that mean "try again", not "not run": another launch of this id is still
#: settling (younger than 30 s), or the command's bytes did not all arrive and nothing was started.
_RETRY_NOTES = (b"launch in progress", b"the command arrived incomplete")


class _Refused(Exception):
    """The host is no longer allowed (``bind_recheck``): the run is stopped, not followed."""


#: What a csh-family login shell says to the single-quoted, multi-line launcher it cannot read.
_CSH_SAYS = re.compile(r"Unmatched|Illegal variable name|Missing }|Badly placed|Invalid null command", re.IGNORECASE)


def _fatal(call: _Call) -> bool:
    """A refusal that no amount of reconnecting changes: an untrusted host key, no usable credential."""
    return bool(_HOST_KEY.search(call.client_log) or _PERMISSION.search(call.client_log))


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
        self._cancelled: Callable[[], bool] | None = None
        self._session = ""

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

    def bind_session(self, session_id: str) -> None:
        """The conversation this sandbox runs for: written to the journal, so that a run nobody
        finished is announced to THAT conversation and no other."""
        self._session = session_id

    def bind_cancel(self, cancelled: Callable[[], bool]) -> None:
        """Stop a running command, remotely, when ``cancelled()`` turns true (checked between polls)."""
        self._cancelled = cancelled

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

    def confirm_when_tainted(self, what: str) -> bool:
        """Whether ``what`` (a write the file tools are about to make) may go ahead: True when the turn
        has read nothing from outside, or when the owner was asked, through the same question a command
        uses, and said yes. False when it was declined or nobody could be asked."""
        return not self._declined_for_taint(what)

    def run_script(
        self, script: str, args: Sequence[str] = (), *, stdin: bytes | None = None, timeout: int = 60
    ) -> SandboxResult:
        """Run a FIXED ``sh`` script on the remote host with ``args`` as its positional parameters.

        For the remote file tools, and deliberately not :meth:`run`'s detached launcher: a file operation
        is short, and it verifies itself (a write is checked against the byte count and sha256 it was
        sent with before it is moved into place), so there is nothing to follow, poll or resume.

        The script is a constant of this app, never built from anything the agent wrote; every variable
        part (a path, a limit) is one ``shlex.quote``d argument, and a file's CONTENT travels on
        ``stdin``, so nothing the agent chose is ever interpolated into the command line. The host is
        re-validated first (and before each retry), the argv is :func:`ssh_argv`'s, and the result is
        :func:`explained`: the remote command's own stderr, never the client's raw log.

        The taint question (``bind_taint``) is NOT asked here: it is a question about a COMMAND, and
        these are the file tools, whose names the governance ledger and the approval policy already
        judge as they judge the local ones. Asking twice would put two cards up for one action.

        A call that fails at the ssh level (exit 255, not a host-key or credential refusal) is repeated
        a couple of times: every script here is safe to run again, because a read and a list change
        nothing, and a write that arrived short is dropped by its own check.
        """
        from chimera.proc.decode import console_text

        command = "sh -c " + shlex.quote(script) + " sh" + "".join(f" {shlex.quote(a)}" for a in args)
        remote = remote_command(self.remote_path, command)
        deadline = _now() + timeout
        attempt = 0
        while True:
            if self._recheck is not None:
                try:
                    self._recheck()
                except ValueError as exc:
                    return SandboxResult(exit_code=1, stderr=f"error: not run: {exc}")
            call = self._exec(remote, stdin, max(1.0, deadline - _now()))
            if call is None:
                return self._failure(None, "", False)
            if call.timed_out:
                return SandboxResult(exit_code=124, stderr=f"command timed out after {timeout}s", timed_out=True)
            if call.code == 255 and not _fatal(call) and attempt < FILE_OP_RETRIES:
                pause = RECONNECT_BACKOFF[attempt]
                if deadline - _now() > pause + 1:
                    attempt += 1
                    _sleep(pause)
                    continue
            seen = SandboxResult(exit_code=call.code, stdout=console_text(call.out), stderr=console_text(call.err))
            return explained(seen, self.host, call.client_log)

    # -- one ssh process --------------------------------------------------------------------

    def _exec(self, remote: str, stdin: bytes | None, timeout: float) -> _Call | None:
        """Run ``ssh alias <remote>`` once and return its raw bytes; None when no log file can be made.

        The argv, the scrubbed environment, the client's private log file and killing the local tree
        on a hang are the same as they always were; only the output is kept as bytes.
        """
        from chimera.sandbox import local as _local

        try:
            handle, log = tempfile.mkstemp(prefix="chimera-ssh-", suffix=".log")
            os.close(handle)
        except OSError:
            return None
        try:
            argv = ssh_argv(self.host, remote, log_path=log)
            proc = subprocess.Popen(
                argv,
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                env=_local._child_env(),
                start_new_session=os.name == "posix",
                **no_window(),
            )
            try:
                raw_out, raw_err = proc.communicate(input=stdin, timeout=max(1.0, timeout))
            except subprocess.TimeoutExpired:
                kill_tree(proc)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.communicate(timeout=5)
                return _Call(code=124, timed_out=True, client_log=_read_client_log(log))
            return _Call(
                code=proc.returncode, out=raw_out or b"", err=raw_err or b"", client_log=_read_client_log(log)
            )
        finally:
            with contextlib.suppress(OSError):
                os.unlink(log)

    def _failure(self, call: _Call | None, run_id: str, started: bool) -> SandboxResult:
        """The agent-facing result for a call that failed at the ssh level: the client's own words,
        explained, never raw (:func:`explained`), and where the command is if it may have started."""
        if call is None:
            return SandboxResult(exit_code=1, stderr="error: not run: could not create the ssh client's log file")
        seen = SandboxResult(exit_code=255, stderr=call.err.decode("utf-8", "replace"))
        stderr = explained(seen, self.host, call.client_log).stderr
        if started and not _fatal(call):
            stderr = f"{stderr}\n{_run_hint(self.host, run_id)}".strip()
        return SandboxResult(exit_code=255, stderr=stderr)

    # -- the run ------------------------------------------------------------------------------

    def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
        if self._recheck is not None:
            try:
                self._recheck()
            except ValueError as exc:
                return SandboxResult(exit_code=1, stderr=f"error: not run: {exc}")
        try:
            where = self._remote_dir(cwd)
            script = run_script(where, command).encode("utf-8")
        except SshPathError as exc:
            return SandboxResult(exit_code=1, stderr=f"error: not run: {exc}")
        if len(script) > MAX_COMMAND_BYTES:
            return SandboxResult(exit_code=1, stderr=f"error: not run: the command is over {MAX_COMMAND_BYTES} bytes")
        if self._declined_for_taint(command):
            return SandboxResult(
                exit_code=1,
                stderr=(
                    f"error: not run: this turn has read content from outside, so a command on {self.host} "
                    "needs the owner's approval, and it was not given (or nobody could be asked here)."
                ),
            )
        run_id = secrets.token_hex(8)
        journal_add(self.host, run_id, where, self._session)
        try:
            return self._follow(run_id, script, timeout)
        except BaseException:
            # The turn was cancelled (or the process is going down): stop the remote group too, so a
            # cancelled command does not keep running on a machine nobody is watching.
            self._stop(run_id)
            raise

    def _follow(self, run_id: str, script: bytes, timeout: int) -> SandboxResult:
        deadline = _now() + timeout
        out, err = bytearray(), bytearray()
        offsets = [0, 0]
        truncated = False
        launched = False
        while True:
            left = deadline - _now()
            if left <= 0:
                return self._timed_out(run_id, timeout, out, err, offsets)
            if self._cancelled is not None and self._cancelled():
                if not launched:  # a stop already set: nothing is started
                    journal_remove(run_id)
                    return SandboxResult(exit_code=130, stderr="command cancelled before it started")
                return self._cancel(run_id, out, err, offsets)
            if launched:
                try:
                    self._require_allowed()
                except _Refused as refused:
                    return self._refused(run_id, str(refused), out, err)
            wait = min(POLL_WAIT_SECONDS if launched else LAUNCH_WAIT_SECONDS, int(left))
            if launched:
                remote, stdin = remote_op("poll", run_id, wait, *offsets), None
            else:
                remote, stdin = remote_op("launch", run_id, wait, 0, 0, len(script)), script
            try:
                frame, failure = self._call_with_reconnect(
                    remote, stdin, wait + _CALL_SLACK_SECONDS, deadline, run_id, launched
                )
            except _Refused as refused:
                return self._refused(run_id, str(refused), out, err)
            except _Cancelled:
                return self._cancel(run_id, out, err, offsets)
            if failure is not None:
                if failure.timed_out:
                    return self._timed_out(run_id, timeout, out, err, offsets)
                if not launched:
                    journal_remove(run_id)
                return failure
            if frame is None:  # unreachable: one of the two is always set
                return SandboxResult(exit_code=1, stderr="error: the ssh helper returned nothing")
            launched = True
            if frame.gone:
                journal_remove(run_id)
                return SandboxResult(exit_code=1, stderr=f"error: the run folder of run {run_id} on {self.host} is gone")
            for buf, data, index in ((out, frame.out, 0), (err, frame.err, 1)):
                offsets[index] += len(data)
                room = MAX_OUTPUT_BYTES - len(buf)
                truncated = truncated or len(data) > room
                if room > 0:
                    buf.extend(data[:room])
            if frame.done and not frame.out and not frame.err:
                return self._finished(run_id, frame.code, out, err, truncated)

    def _require_allowed(self) -> None:
        """Raise :class:`_Refused` when the host is no longer on the owner's lists. Asked before every
        launch retry and every poll; stop and cleanup are never held back by it."""
        if self._recheck is None:
            return
        try:
            self._recheck()
        except ValueError as exc:
            raise _Refused(str(exc)) from exc

    def _refused(self, run_id: str, why: str, out: bytearray, err: bytearray) -> SandboxResult:
        from chimera.proc.decode import console_text

        stopped = self._stop(run_id)  # nothing else is read from a host that is no longer allowed
        text_out, text_err = console_text(bytes(out)), console_text(bytes(err))
        if stopped:
            self._cleanup(run_id)
        tail = "" if stopped else f" It could not be stopped. {_run_hint(self.host, run_id)}"
        return SandboxResult(exit_code=1, stdout=text_out, stderr=f"{text_err}\nerror: stopped: {why}.{tail}".strip())

    def _call_with_reconnect(
        self, remote: str, stdin: bytes | None, timeout: float, deadline: float, run_id: str, started: bool
    ) -> tuple[_Frame | None, SandboxResult | None]:
        """One operation, retried over a fresh connection while the ssh link is what is failing.

        Safe to repeat because the run id, not the connection, is the command's identity: ``launch``
        on an id that already started only reads it, and ``poll`` is a read from an offset. What is
        not retried: a refusal that waiting cannot fix (host key, credentials), and a remote shell
        that ran and failed (an exit other than 255). After :data:`RECONNECT_BACKOFF` consecutive
        failures the run is given up on, left running, with where to find it.
        """
        failures = 0
        while True:
            if failures:
                self._require_allowed()  # a host removed mid-turn is not reconnected to
            call = self._exec(remote, stdin, min(timeout, max(1.0, deadline - _now() + 5)))
            if call is None:
                return None, self._failure(None, run_id, started)
            frame = parse_frame(call.out) if call.code == 0 else None
            if frame is not None:
                return frame, None
            if call.timed_out and _now() >= deadline:
                return None, SandboxResult(exit_code=124, timed_out=True)
            # Only what comes BEFORE any poll header: after it the bytes are the command's own output.
            note = _NOTE.search(call.out.split(b"chimera-poll", 1)[0]) if call.code == 0 else None
            retry_note = note is not None and note.group(1)[len(b"chimera-error "):].startswith(_RETRY_NOTES)
            if note is not None and not retry_note:
                text = note.group(1)[len(b"chimera-error "):].decode("utf-8", "replace")
                return None, SandboxResult(exit_code=1, stderr=f"error: not run: {text}")
            # ssh's own failure (255), a call that hung, or a reply cut inside its payload: the link.
            link = retry_note or call.timed_out or call.code in (0, 255)
            if _fatal(call) or not link:
                if link:
                    return None, self._failure(call, run_id, started)
                err = call.err.decode("utf-8", "replace").strip()
                why = (
                    " The login shell there looks like csh/tcsh, which cannot read the helper: Chimera needs an "
                    "sh-family login shell (sh, bash, zsh, dash) on the account it connects as."
                    if _CSH_SAYS.search(err) else ""
                )
                return None, SandboxResult(
                    exit_code=call.code,
                    stderr=f"error: the remote shell on {self.host} could not run the helper (exit {call.code}): {err}.{why}",
                )
            if failures >= len(RECONNECT_BACKOFF):
                if retry_note and note is not None:
                    said = note.group(1)[len(b"chimera-error "):].decode("utf-8", "replace")
                    return None, SandboxResult(exit_code=255, stderr=f"error: not run: {said}.")
                return None, self._failure(call, run_id, started)
            pause = RECONNECT_BACKOFF[failures]
            failures += 1
            if self._cancelled is not None and self._cancelled():
                raise _Cancelled
            if deadline - _now() <= pause:
                return None, SandboxResult(exit_code=124, timed_out=True)
            _sleep(pause)

    def _finished(self, run_id: str, code: int, out: bytearray, err: bytearray, truncated: bool) -> SandboxResult:
        from chimera.proc.decode import console_text

        self._cleanup(run_id)
        stderr = console_text(bytes(err))
        if truncated:
            stderr = f"{stderr}\n[output over {MAX_OUTPUT_BYTES} bytes was cut]".strip()
        return SandboxResult(exit_code=code, stdout=console_text(bytes(out)), stderr=stderr)

    def _partial(self, run_id: str, out: bytearray, err: bytearray, offsets: list[int]) -> tuple[str, str]:
        """What the command had written when it was stopped: one last read, best effort."""
        from chimera.proc.decode import console_text

        try:
            self._require_allowed()
        except _Refused:  # nothing more is read from a host that is no longer allowed
            return console_text(bytes(out)), console_text(bytes(err))
        call = self._exec(remote_op("poll", run_id, 0, *offsets), None, 20)
        frame = parse_frame(call.out) if call is not None and call.code == 0 else None
        if frame is not None:
            out.extend(frame.out[: max(0, MAX_OUTPUT_BYTES - len(out))])
            err.extend(frame.err[: max(0, MAX_OUTPUT_BYTES - len(err))])
        return console_text(bytes(out)), console_text(bytes(err))

    def _timed_out(self, run_id: str, timeout: int, out: bytearray, err: bytearray, offsets: list[int]) -> SandboxResult:
        stopped = self._stop(run_id)
        text_out, text_err = self._partial(run_id, out, err, offsets)
        message = f"command timed out after {timeout}s"
        if stopped:
            self._cleanup(run_id)
        else:
            message += f" and could not be stopped over ssh. {_run_hint(self.host, run_id)}"
        return SandboxResult(exit_code=124, stdout=text_out, stderr=f"{text_err}\n{message}".strip(), timed_out=True)

    def _cancel(self, run_id: str, out: bytearray, err: bytearray, offsets: list[int]) -> SandboxResult:
        stopped = self._stop(run_id)
        text_out, text_err = self._partial(run_id, out, err, offsets)
        if stopped:
            self._cleanup(run_id)
        message = "command cancelled" + ("" if stopped else f" but not stopped. {_run_hint(self.host, run_id)}")
        return SandboxResult(exit_code=130, stdout=text_out, stderr=f"{text_err}\n{message}".strip())

    def _stop(self, run_id: str) -> bool:
        """Kill the run's process GROUP on the remote with the fixed stop script. True when it answered."""
        for _ in range(2):
            call = self._exec(remote_op("stop", run_id), None, 20)
            if call is not None and call.code == 0 and b"chimera-stopped" in call.out:
                # "partial": the wrapper was signalled but descendants may live (no setsid there, or
                # the process ids were not recorded yet). Not a stop; the run is named and kept.
                return b"chimera-stopped partial" not in call.out
        return False

    def _cleanup(self, run_id: str) -> None:
        """Remove the run folder, once, best effort: the next launch sweeps what this misses."""
        call = self._exec(remote_op("cleanup", run_id), None, 20)
        if call is not None and call.code == 0 and b"chimera-cleaned" in call.out:
            journal_remove(run_id)

    def read_run(self, run_id: str, *, forget: bool = False) -> SandboxResult:
        """The output of a run started earlier (by id), for a turn after the one that lost it.

        ``exit_code`` is the command's once it finished; while it still runs the stderr says so and
        the exit code is 0. ``forget`` removes a finished run's folder after reading it.
        """
        from chimera.proc.decode import console_text

        if self._recheck is not None:
            try:
                self._recheck()
            except ValueError as exc:
                return SandboxResult(exit_code=1, stderr=f"error: not run: {exc}")
        try:
            remote_op("poll", run_id, 0, 0, 0)  # validates the id before anything is sent
        except SshPathError as exc:
            return SandboxResult(exit_code=1, stderr=f"error: {exc}")
        out, err = bytearray(), bytearray()
        offsets = [0, 0]
        truncated = False
        frame: _Frame | None = None
        deadline = _now() + READ_RUN_SECONDS
        while True:  # a frame holds one chunk; read on to the end, up to the cap and the deadline
            call = self._exec(remote_op("poll", run_id, 0, *offsets), None, 40)
            frame = parse_frame(call.out) if call is not None and call.code == 0 else None
            if frame is None:
                return self._failure(call, run_id, False)
            if frame.gone:
                return SandboxResult(
                    exit_code=1,
                    stderr=f"error: run {run_id} is not on {self.host} (finished and cleaned, or never started)",
                )
            for buf, data, index in ((out, frame.out, 0), (err, frame.err, 1)):
                offsets[index] += len(data)
                room = MAX_OUTPUT_BYTES - len(buf)
                truncated = truncated or len(data) > room
                if room > 0:
                    buf.extend(data[:room])
            if not frame.out and not frame.err:
                break
            if len(out) >= MAX_OUTPUT_BYTES or len(err) >= MAX_OUTPUT_BYTES or _now() >= deadline:
                truncated = True
                break
        note = "" if frame.done else f"run {run_id} is still running on {self.host}; this is its output so far"
        if truncated:
            note = f"{note}\n[output over {MAX_OUTPUT_BYTES} bytes was cut]".strip()
        if frame.done and forget:
            self._cleanup(run_id)
        return SandboxResult(
            exit_code=frame.code if frame.done else 0,
            stdout=console_text(bytes(out)),
            stderr=f"{console_text(bytes(err))}\n{note}".strip(),
        )
