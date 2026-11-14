"""The remote half of the SSH file tools: where a path may point, and the fixed scripts that act on it.

:mod:`chimera.tools.ssh_files` holds the tools the agent sees; this module is what they stand on, kept
apart so the part that decides "may this path be touched" can be read, and tested, without any tool
in the way.

**One road.** Every remote action is one of the :data:`SCRIPTS` below, handed to
:meth:`chimera.sandbox.ssh.SshSandbox.run_script`: the same argv as a command (``BatchMode``,
``StrictHostKeyChecking=yes``, forwarding off, ``--`` before the alias), the same alias re-check at each
call, the same sanitizing of what ssh says. No second ssh client, no ``shell=True`` here.

**Fixed scripts, quoted arguments, content on stdin.** A script is a constant of this app. A path or a
limit is one ``shlex.quote``d positional argument; a file's CONTENT is the process's stdin (writing) or
base64 on its stdout (reading), so what the agent chose is never part of a command line, where the remote
shell would read it as syntax.

**Where a path may point.** Three checks, because each stops what the others cannot:

1. *Lexically*, here: the path is joined to ``remote_path`` and normalised with ``posixpath``, and one
   that leaves the folder (``..``, an absolute path elsewhere) is refused before ssh is called.
2. *On the host*, with ``realpath``: a symlink inside the folder that points outside it would pass the
   first check, so the deepest EXISTING ancestor of the target is resolved there and must still be
   inside the real folder. The resolved path, not the typed one, is then what the action works on.
3. *The denylist*, on both the typed path and the resolved one, so a link named ``notes.txt`` that points
   at ``.ssh/id_rsa`` is as refused as the key itself.

The action script re-checks containment (step 2) on the resolved path, which narrows, but cannot close,
the window between the two ssh calls: the host is the owner's own machine and the account is the owner's
own login, so this is a guard against the agent's mistakes and a model's whims, not isolation (the sandbox
module says the same of commands).
"""

from __future__ import annotations

import base64
import hashlib
import posixpath
import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from chimera.core.credential_files import (
    CREDENTIAL_NAME_GLOBS,
    CREDENTIAL_PATH_SEQUENCES,
    credential_sequence_part,
    is_credential_name,
)
from chimera.sandbox.base import SandboxResult

#: A file bigger than this is not read over ssh (it would have to be base64'd through one pipe).
MAX_READ_BYTES = 10 * 1024 * 1024
MAX_PATH = 1024
#: What `find` may list before it is cut: the cap is on the host's side, not on the answer.
MAX_FIND_LINES = 200_000

#: File names that hold credentials: :data:`chimera.core.credential_files.CREDENTIAL_NAME_GLOBS`, the
#: one list the bridge and the permission switch read too (audit 2026-11, a07-02: this module had its
#: own copy, and ``service.token`` was refused by the bridge and not here, ``.kube`` the other way
#: round). As globs, so that the ``grep``/``find`` run on the host can be told to skip them too and a
#: secret is never even read. A folder named here is refused whole, because a glob names one path
#: COMPONENT. Lower case: names are compared lower-cased, here and on the host.
SECRET_NAME_GLOBS: tuple[str, ...] = CREDENTIAL_NAME_GLOBS
assert all(re.fullmatch(r"[a-z0-9._*-]+", g) for g in SECRET_NAME_GLOBS)  # safe to put in a `case`


def case_insensitive(glob: str) -> str:
    """``glob`` with every letter as ``[xX]``: ``grep --exclude`` has no case-insensitive form."""
    return "".join(f"[{c}{c.upper()}]" if c.isalpha() else c for c in glob)

#: Printed by every script once its guard has passed, and (but for a write) when it has finished. The
#: login shell of the host may print a banner or an rc file's output on the same stdout; what is
#: between the markers is the script's, whatever surrounds it.
MARK_BEGIN = "@@chimera-begin@@\n"
MARK_END = "@@chimera-end@@\n"

#: Directories no search descends into (the local ``grep``/``glob`` skip the same ones).
IGNORE_DIRS: tuple[str, ...] = (
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea", ".tox", "site-packages",
)

# Exit codes the scripts use for "refused", mapped to a sentence below.
_NO_ROOT, _ESCAPES, _NO_REALPATH, _NOT_THERE, _TOO_BIG, _NO_DIR, _NO_TMP, _NO_WRITE, _NO_MOVE, _IS_DIR, _BAD_REGEX = (
    90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100,
)
_SHORT, _DENIED_HOST, _NO_READ = 101, 102, 103
_DENY_CASE = "|".join(SECRET_NAME_GLOBS)
#: The runs of components (``gh/hosts.yml``) as one ``case`` over the whole lower-cased path, each
#: component quoted so a name with a space stays one literal.
_DENY_SEQUENCE_CASE = "|".join(
    "*/" + "/".join(shlex.quote(part) for part in sequence) + "/*" for sequence in CREDENTIAL_PATH_SEQUENCES
)


class RemoteFileError(Exception):
    """A refusal or a failure, carrying the sentence the tool returns to the agent."""


class ScriptRunner(Protocol):
    """What the file tools need of the SSH sandbox (a seam: the tests stand in a fake host)."""

    host: str
    remote_path: str

    def confirm_when_tainted(self, what: str) -> bool: ...

    def run_script(
        self, script: str, args: Sequence[str] = (), *, stdin: bytes | None = None, timeout: int = 60
    ) -> SandboxResult: ...


def is_secret_name(name: str) -> bool:
    """Whether one path component names a credential file. Case-insensitive; trailing dots and spaces
    are dropped, as Windows opens ``.env `` as ``.env`` (a remote host may be a Windows OpenSSH too)."""
    return is_credential_name(name)


def secret_component(path: str) -> str | None:
    """The first component of ``path``, or run of components (``gh/hosts.yml``), that names a credential."""
    return next(
        (part for part in path.split("/") if part and is_secret_name(part)), None
    ) or credential_sequence_part(path)


def secret_in(rel: str, absolute: str) -> str | None:
    """What names a credential in a target, read on the ``absolute`` path as well as on ``rel``.

    The absolute path, because the configured folder is not a place where the list stops applying:
    with ``remote_path`` set to ``/home/me/.kube`` the relative path of the cluster token is just
    ``config``, and reading only ``rel`` handed it over (review of the P0-01/02 fix). A folder that is
    itself a credential folder refuses everything under it, which is what it holds.
    """
    return secret_component(rel) or secret_component(absolute)


def within(root: str, path: str) -> bool:
    """Whether the POSIX ``path`` is ``root`` or below it (both already normalised)."""
    return root == "/" or path == root or path.startswith(root.rstrip("/") + "/")


def lexical_target(remote_path: str, requested: str) -> str:
    """``requested`` as an absolute, normalised path inside ``remote_path``, or :class:`RemoteFileError`."""
    if len(requested) > MAX_PATH:
        raise RemoteFileError(f"error: the path is longer than {MAX_PATH} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in requested):
        raise RemoteFileError("error: the path has a control character")
    joined = requested if requested.startswith("/") else posixpath.join(remote_path, requested or ".")
    normal = posixpath.normpath(joined)
    if normal.startswith("//"):  # normpath keeps exactly two leading slashes
        normal = "/" + normal.lstrip("/")
    if not within(remote_path, normal):
        raise RemoteFileError(
            f"error: path {requested!r} escapes {remote_path} on the remote host. Only that folder "
            "is reachable with this tool; use run_shell for anything else. Do not retry."
        )
    if (secret := secret_in(posixpath.relpath(normal, remote_path), normal)) is not None:
        raise RemoteFileError(_denied(requested, secret))
    return normal


#: Files a shell, git, cron or systemd reads and RUNS on its own later: writing one in a turn that has read
#: content from outside is how an injected instruction becomes code that fires at the next login, commit
#: or tick. By component of the path below the folder, plus the system folders by absolute path.
_STARTUP_NAMES = frozenset({
    ".bashrc", ".bash_profile", ".bash_login", ".bash_logout", ".profile", ".zshrc", ".zprofile",
    ".zshenv", ".zlogin", ".zlogout", ".kshrc", ".xprofile", ".xinitrc", "config.fish", "crontab",
    ".gitconfig", ".bash_aliases", ".inputrc",
})
_SCHEDULER_PARTS = frozenset({
    "cron.d", "cron.daily", "cron.hourly", "cron.weekly", "cron.monthly", "systemd", "autostart",
    "init.d", "rc.d", "profile.d", ".husky",
})
_SYSTEM_PREFIXES = (
    "/etc/cron", "/etc/systemd", "/var/spool/cron", "/etc/sudoers", "/etc/profile", "/etc/rc",
    "/etc/init.d", "/etc/environment", "/etc/crontab",
)


def startup_reason(real: str, rel: str) -> str | None:
    """Why a write to this path is code that runs later (a sentence), or None for an ordinary file."""
    parts = [p.lower() for p in rel.split("/") if p]
    if ".git" in parts:
        tail = parts[parts.index(".git") + 1:]
        if "hooks" in tail or tail[-1:] == ["config"]:
            return "a git hook or git config, which git runs"
    if parts[-2:] == ["git", "config"]:  # ~/.config/git/config: core.sshCommand, aliases that start with `!`
        return "a git config, which git runs"
    if parts and parts[-1] in _STARTUP_NAMES:
        return "a shell startup file, which runs at the next login"
    if any(p in _SCHEDULER_PARTS for p in parts) or real.lower().startswith(_SYSTEM_PREFIXES):
        return "a cron, systemd or startup location, which the system runs"
    return None


def _denied(path: str, component: str) -> str:
    return (
        f"error: {path!r} names {component!r}, a credential file; no file tool reads or writes those "
        "on a remote host either. Do not retry."
    )


# -- the fixed scripts -------------------------------------------------------------------------------

#: Shared by every action. $1 is the real folder and $2 the real target as :data:`RESOLVE` returned them;
#: both are checked again on the host, because a link could have been swapped in between the two calls,
#: and so is the credential denylist, on every component of the whole real path, folder included (case-insensitively).
#: ``rootp`` drops a trailing slash so the root folder ``/`` still has a prefix to match.
_GUARD = f"""\
root=$(cd -- "$1" 2>/dev/null && pwd -P) || exit {_NO_ROOT}
rootp=${{root%/}}
t=$2
case "$t" in "$root"|"$rootp"/*) ;; *) exit {_ESCAPES};; esac
a=$t
while [ ! -e "$a" ] && [ ! -L "$a" ]; do
  q=$(dirname -- "$a"); [ "$q" = "$a" ] && break; a=$q
done
r=$(realpath -- "$a" 2>/dev/null || readlink -f -- "$a" 2>/dev/null) || exit {_NO_REALPATH}
case "$r" in "$root"|"$rootp"/*) ;; *) exit {_ESCAPES};; esac
chk() {{
  oi=$IFS; IFS=/; set -f
  for c in $(printf '%s' "$1" | tr 'A-Z' 'a-z'); do
    case "$c" in {_DENY_CASE}) IFS=$oi; set +f; return 1;; esac
  done
  IFS=$oi; set +f
  case "$(printf '%s' "$1" | tr 'A-Z' 'a-z')/" in {_DENY_SEQUENCE_CASE}) return 1;; esac
  return 0
}}
chk "$t" && chk "$r" || exit {_DENIED_HOST}
printf '@@chimera-begin@@\\n'
"""

#: $1 the folder as configured, $2 the lexical target. Answers (NUL-separated): OK, the real folder, the
#: deepest existing ancestor of the target, that ancestor's real path, and its kind (d / f / o).
RESOLVE = """\
printf '@@chimera-begin@@\n'
root=$(cd -- "$1" 2>/dev/null && pwd -P) || { printf 'NOROOT\\0@@chimera-end@@\n'; exit 0; }
p=$2
while [ ! -e "$p" ] && [ ! -L "$p" ]; do
  q=$(dirname -- "$p"); [ "$q" = "$p" ] && break; p=$q
done
real=$(realpath -- "$p" 2>/dev/null || readlink -f -- "$p" 2>/dev/null)
[ -n "$real" ] || { printf 'NOREALPATH\\0@@chimera-end@@\n'; exit 0; }
if [ -d "$p" ]; then k=d; elif [ -f "$p" ]; then k=f; else k=o; fi
printf 'OK\\0%s\\0%s\\0%s\\0%s@@chimera-end@@\n' "$root" "$p" "$real" "$k"
"""

#: $3 the size limit. The file is base64'd so that any bytes survive the pipe and a UTF-8 check is done
#: HERE, on the exact content, rather than on text the transport may have re-decoded.
READ = _GUARD + f"""\
[ -f "$t" ] || exit {_NOT_THERE}
size=$(wc -c < "$t" | tr -d ' ') || exit {_NOT_THERE}
[ "$size" -le "$3" ] || exit {_TOO_BIG}
enc=$(base64 < "$t") || exit {_NO_READ}
printf '%s\\n%s\\n' "$size" "$enc"
printf '@@chimera-end@@\\n'
"""

#: $3 the byte count and $4 the sha256 the content must have: ssh dying mid-upload closes stdin, ``cat``
#: sees a clean EOF and exits 0, and without this check the truncated file would be moved into place.
#: (Where the host has no sha256 tool only the count is checked.) Content is the stdin. The temp file lives in the target's own folder so that ``mv`` is a rename on
#: one filesystem, i.e. atomic: a reader sees the old file or the new one, never half of one. ``mkdir -p``
#: runs only after the guard has checked the deepest EXISTING ancestor, so it creates folders below a
#: folder known to be inside the root. The mode of a file that exists is kept; a new file gets the umask's.
WRITE = _GUARD + f"""\
[ -d "$t" ] && exit {_IS_DIR}
dir=$(dirname -- "$t")
mkdir -p -- "$dir" || exit {_NO_DIR}
tmp=$(mktemp "$dir/.chimera-tmp.XXXXXX") || exit {_NO_TMP}
cat > "$tmp" || {{ rm -f -- "$tmp"; exit {_NO_WRITE}; }}
n=$(wc -c < "$tmp" | tr -d ' ')
h=$( (sha256sum "$tmp" 2>/dev/null || shasum -a 256 "$tmp" 2>/dev/null) | cut -d' ' -f1)
if [ "$n" != "$3" ] || {{ [ -n "$h" ] && [ "$h" != "$4" ]; }}; then rm -f -- "$tmp"; exit {_SHORT}; fi
if [ -f "$t" ]; then
  chmod --reference="$t" "$tmp" 2>/dev/null || chmod "$(stat -c %a "$t" 2>/dev/null || stat -f %Lp "$t")" "$tmp" 2>/dev/null
else
  m=$(umask); chmod "$(printf '%o' $((0666 & ~m)))" "$tmp" 2>/dev/null
fi
mv -f -- "$tmp" "$t" || {{ rm -f -- "$tmp"; exit {_NO_MOVE}; }}
"""

LIST = _GUARD + f"""\
[ -d "$t" ] || exit {_NOT_THERE}
ls -1Ap -- "$t"
printf '@@chimera-end@@\\n'
"""

#: $3 the pattern, $4 the match limit, $5 an optional ``--include`` glob. Perl-compatible when the host's
#: grep has ``-P`` (models write ``\\d``), POSIX extended otherwise. The pattern is checked first, on empty
#: input, so a bad one is an error with grep's own words and not a silent "no matches". The credential
#: names and the ignored folders are passed as ``--exclude``/``--exclude-dir``: a secret is never READ.
#: ``--include`` goes first: grep keeps a file that matches no option unless the FIRST option is an include.
#: It runs from inside the searched folder and searches ``.``: ``--exclude-dir`` also applies to a folder
#: named on the command line, so a project that lives in ``/srv/build`` would otherwise search nothing.
#: Paths come back relative to that folder (or the bare file name, for a single file).
GREP = (
    _GUARD
    + f"""\
[ -e "$t" ] || exit {_NOT_THERE}
if [ -d "$t" ]; then d=$t; s=.; else d=$(dirname -- "$t"); s=$(basename -- "$t"); fi
cd -- "$d" || exit {_NOT_THERE}
mode=-E
printf '' | grep -P -e '' >/dev/null 2>&1; [ "$?" -ne 2 ] && mode=-P
err=$(printf '' | grep "$mode" -e "$3" 2>&1 >/dev/null); rc=$?
[ "$rc" -ne 2 ] || {{ printf '%s\\n' "$err" >&2; exit {_BAD_REGEX}; }}
pat=$3; lim=$4; inc=$5
set -- -r -I -H -n -Z -s "$mode" -e "$pat"
[ -z "$inc" ] || set -- "$@" "--include=$inc"
set -- "$@" """
    + " ".join(
        [
            f"--exclude={shlex.quote(case_insensitive(g))} --exclude-dir={shlex.quote(case_insensitive(g))}"
            for g in SECRET_NAME_GLOBS
        ]
        + [f"--exclude-dir={shlex.quote(d)}" for d in IGNORE_DIRS]
    )
    + """
grep "$@" -- "$s" | head -n "$lim"
printf '@@chimera-end@@\\n'
exit 0
"""
)

#: $3 the line limit. Lists files only, without descending into the ignored folders; links are not
#: followed (``-type f``), so nothing is listed through one. Paths are relative to the folder ($t), for
#: the same reason as in :data:`GREP`.
FIND = (
    _GUARD
    + f"""\
[ -d "$t" ] || exit {_NOT_THERE}
cd -- "$t" || exit {_NOT_THERE}
find . \\( """
    + " -o ".join(f"-name {shlex.quote(d)}" for d in IGNORE_DIRS)
    + """ \\) -prune -o -type f -print | head -n "$3"
printf '@@chimera-end@@\\n'
exit 0
"""
)

SCRIPTS: frozenset[str] = frozenset({RESOLVE, READ, WRITE, LIST, GREP, FIND})

_EXIT_SENTENCES = {
    _NO_ROOT: "the remote folder cannot be entered",
    _ESCAPES: "the path leaves the remote folder (a link points outside it). Do not retry",
    _NO_REALPATH: "the host has neither `realpath` nor `readlink -f`, so a path cannot be checked and nothing was touched",
    _NOT_THERE: "no such file or folder",
    _TOO_BIG: f"the file is over {MAX_READ_BYTES // (1024 * 1024)} MB; read a part of it with run_shell",
    _NO_DIR: "could not create the folder",
    _NO_TMP: "could not create a temporary file next to the target (permissions?)",
    _NO_WRITE: "could not write the content",
    _NO_MOVE: "could not move the new file into place; the old one is untouched",
    _IS_DIR: "that path is a folder",
    _NO_READ: "the host could not read the file",
    _SHORT: "the upload arrived incomplete (ssh dropped?); the old file is untouched",
    _DENIED_HOST: "that path names a credential file; no file tool reads or writes those. Do not retry",
}


@dataclass(frozen=True)
class Resolved:
    """A path as the host sees it: absolute, symlinks followed, and known to be inside the folder."""

    root: str
    real: str
    kind: str  # "file", "dir" or "none" (it does not exist yet, or it is something else)

    @property
    def rel(self) -> str:
        return "." if self.real == self.root else posixpath.relpath(self.real, self.root)


def _framed(stdout: str, *, require_end: bool = False) -> str:
    """What the script printed between its markers (the host's login shell may print around them).

    ``require_end``: a script whose answer is DATA (a file) must also have reached its end marker; an
    answer cut short by a dropped connection is not a smaller file."""
    start = stdout.find(MARK_BEGIN)
    if start < 0:
        raise RemoteFileError("error: the host's answer was not recognised (a login banner only?); nothing was touched")
    body = stdout[start + len(MARK_BEGIN):]
    end = body.rfind(MARK_END)
    if end < 0 and require_end:
        raise RemoteFileError("error: the host's answer was cut short; nothing was read")
    return body if end < 0 else body[:end]


def clamp_limit(value: Any, default: int, ceiling: int = 1000) -> int:
    """A ``max_results`` as a number between 1 and ``ceiling``; :class:`RemoteFileError` if it is not one."""
    if value in (None, "", 0):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:  # a list, "lots", 1e999 (infinity)
        raise RemoteFileError("error: max_results must be a whole number") from exc
    return max(1, min(number, ceiling))


def refuse_nul(**named: str) -> None:
    """A NUL byte cannot be part of an argument; say so instead of letting the process start fail."""
    for name, value in named.items():
        if "\0" in value:
            raise RemoteFileError(f"error: {name} has a NUL byte, which cannot be sent to the host")


class RemoteFiles:
    """Read, write and list on the host of an SSH sandbox, inside its ``remote_path``."""

    def __init__(self, sandbox: ScriptRunner) -> None:
        self.sandbox = sandbox

    def _run(
        self, script: str, args: Sequence[str], *, stdin: bytes | None = None, timeout: int = 60,
        framed: bool = True, require_end: bool = False,
    ) -> str:
        result = self.sandbox.run_script(script, args, stdin=stdin, timeout=timeout)
        if result.exit_code == 0:
            return _framed(result.stdout, require_end=require_end) if framed else ""
        stderr = (result.stderr or "").strip()
        if result.exit_code == _BAD_REGEX:
            raise RemoteFileError(f"error: invalid regex: {stderr}")
        if result.timed_out:
            raise RemoteFileError(f"error: the remote command timed out after {timeout}s")
        if (sentence := _EXIT_SENTENCES.get(result.exit_code)) is not None:
            raise RemoteFileError(f"error: {sentence}" + (f" ({stderr})" if stderr else ""))
        raise RemoteFileError(
            stderr if stderr.startswith("error:") else f"error: {stderr or f'ssh exited {result.exit_code}'}"
        )

    def resolve(self, requested: str) -> Resolved:
        """``requested`` resolved on the host; refused when it leaves the folder or names a secret."""
        root_cfg = self.sandbox.remote_path
        lexical = lexical_target(root_cfg, requested)
        parts = self._run(RESOLVE, [root_cfg, lexical]).split("\0")
        if parts[0] != "OK" or len(parts) < 5:
            raise RemoteFileError(
                "error: the remote folder cannot be entered"
                if parts[0] == "NOROOT"
                else "error: the host has neither `realpath` nor `readlink -f`, so a path cannot be "
                "checked and nothing was touched"
            )
        root, ancestor, real, kind = parts[1], parts[2], parts[3], parts[4]
        if not (lexical == ancestor or within(ancestor, lexical)) or not (root and real):
            raise RemoteFileError("error: the host's answer did not make sense; nothing was touched")
        exists = ancestor == lexical
        dest = real if exists else posixpath.normpath(posixpath.join(real, posixpath.relpath(lexical, ancestor)))
        if not within(root, dest):
            raise RemoteFileError(
                f"error: path {requested!r} resolves outside {root_cfg} on the remote host (a link "
                "leaves it). Do not retry."
            )
        if (secret := secret_in(posixpath.relpath(dest, root), dest)) is not None:
            raise RemoteFileError(_denied(requested, secret))
        shown = {"d": "dir", "f": "file"}.get(kind, "none") if exists else "none"
        return Resolved(root=root, real=dest, kind=shown)

    def read_bytes(self, target: Resolved) -> bytes:
        out = self._run(READ, [target.root, target.real, str(MAX_READ_BYTES)], require_end=True)
        size_text, _, encoded = out.partition("\n")
        try:
            data = base64.b64decode("".join(encoded.split()), validate=True)  # strict: nothing else in it
            if not size_text.strip().isdigit() or len(data) != int(size_text):
                raise ValueError("size")
        except ValueError as exc:
            raise RemoteFileError("error: the host sent a file that could not be decoded in full") from exc
        return data

    def write_bytes(self, target: Resolved, data: bytes) -> None:
        # The count and the hash are what the script checks before it moves the file into place. The
        # timeout grows with the size: at least ~256 KB/s must get through, on top of the connection.
        args = [target.root, target.real, str(len(data)), hashlib.sha256(data).hexdigest()]
        self._run(WRITE, args, stdin=data, timeout=60 + len(data) // (256 * 1024), framed=False)

    def listing(self, target: Resolved) -> list[str]:
        return [ln for ln in self._run(LIST, [target.root, target.real]).split("\n") if ln]

    def grep(self, target: Resolved, pattern: str, limit: int, include: str) -> list[tuple[str, int, str]]:
        """``(real path, line number, line)`` for each match, at most ``limit`` of them."""
        out = self._run(GREP, [target.root, target.real, pattern, str(limit), include], timeout=120)
        # The script searched from inside the folder: names are relative to it (or the bare file name).
        base = target.real if target.kind == "dir" else posixpath.dirname(target.real)
        hits: list[tuple[str, int, str]] = []
        for raw in out.split("\n"):
            path, sep, rest = raw.partition("\0")
            number, colon, text = rest.partition(":")
            if sep and colon and number.isdigit():
                hits.append((posixpath.normpath(posixpath.join(base, path)), int(number), text))
        hits.sort(key=lambda hit: (hit[0], hit[1]))  # grep walks in directory order; the local tool sorts
        return hits

    def files(self, target: Resolved) -> tuple[list[str], bool]:
        """Every file below ``target`` (real paths), and whether the host's listing was cut."""
        out = self._run(FIND, [target.root, target.real, str(MAX_FIND_LINES)], timeout=120)
        paths = [posixpath.normpath(posixpath.join(target.real, ln)) for ln in out.split("\n") if ln]
        return paths, len(paths) >= MAX_FIND_LINES


def glob_regex(pattern: str) -> re.Pattern[str]:
    """``pattern`` as ``pathlib`` reads it (``*``, ``?``, ``[..]`` inside a name; ``**`` for any depth).

    The host cannot be asked to match it (``find`` has no ``**``), so the file list is matched here.
    A pattern that is only ``**`` matches directories in ``pathlib`` and so no file; it never matches.
    Raises :class:`RemoteFileError` for an absolute pattern or one that climbs with ``..``.
    """
    segments = pattern.split("/")
    if not pattern or pattern.startswith("/") or ".." in segments:
        raise RemoteFileError(
            "error: invalid glob pattern: it must be relative to the remote folder and not climb out of it"
        )
    segments = [s for s in segments if s not in ("", ".")]
    if not segments:
        raise RemoteFileError("error: invalid glob pattern: empty")
    out = ""
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == "**":
            if last:
                return re.compile(r"(?!)")
            out += r"(?:[^/]+/)*"
            continue
        out += _segment_regex(segment) + ("" if last else "/")
    return re.compile(out)


def _segment_regex(segment: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(segment):
        ch = segment[i]
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "[" and (close := segment.find("]", i + 2)) != -1:
            body = segment[i + 1:close]
            negate = body[:1] in ("!", "^")
            out.append("[" + ("^" if negate else "") + re.escape(body[1:] if negate else body).replace(r"\-", "-") + "]")
            i = close
        else:
            out.append(re.escape(ch))
        i += 1
    return "".join(out)
