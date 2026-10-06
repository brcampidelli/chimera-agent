"""Keep the agent's shell and code tools away from the audit log.

The audit log is the record the Security screen reads and the one file a run cannot rewrite
without the break :meth:`AuditLog.verify` reports — which is exactly why a run that CAN rewrite it
would: a turn that read an attacker's page, or one that simply prefers a clean record, deletes the
lines that name it and re-chains the rest. The chain only proves the file was not edited; it says
nothing about who did the editing. #775 closed the approval queue to the write tools by file
identity (`own_files.py`), and the queue fence closed it to the three tools that run arbitrary
commands (`queue_fence.py`). The audit log had neither: a shell could ``rm`` it, truncate it, or
rebuild it line by line, and the next ``verify()`` would report a clean chain — the tampering
succeeded, and the evidence of it is gone.

The same limit the queue fence states, said here too: this REDUCES the ways in; it does not close
them. It reads what a command says, after the quoting a shell removes, and nothing a shell
computes — a program that assembles the path at run time from pieces, a parameter expansion with a
default, brace expansion, command substitution and ANSI-C escapes with a code in them are not
seen. Those are known gaps, not oversights. The structural answer (the log outside any path the
agent's tools can reach) is the deployment's to make: put ``CHIMERA_HOME`` where the agent's shell
cannot write, or run the agent in a sandbox whose mounts exclude the data folder. A fence on the
command is a narrowing, not a prevention — the plan says so, and this module does not pretend
otherwise.

What counts as reaching the log, decided before anything runs:

- naming the file by a spelling the file system resolves to it: absolute or relative (to the
  workspace, to the user's home, or to a folder the command itself names, which is how ``cd`` is
  followed), through ``$VAR``, ``${VAR}``, ``%VAR%``, ``$env:VAR`` or ``~``, a glob, a link, and on
  Windows a device or loopback-share prefix, a trailing dot or space, or an 8.3 short name — the
  last ones not parsed: the path is compared with the file by file identity, which no spelling
  changes (the method of `own_files.within`, with a cache for long commands);
- calling the app's audit route (``/api/governance/audit``), or importing or calling the log's own
  code (``from chimera.governance.audit import``, ``AuditLog(``) — in-process from
  ``code_interpreter``, or by a ``python -c`` from the shell;
- running ``chimera audit`` (or ``chimera governance audit``) as the subcommand — the CLI's own
  reader, which can also be pointed at a rewritten file.

Each of those is read on the command as written AND on the command as a shell joins it once its
quoting is removed — the same bypass the queue fence measured (``aud""it.jsonl`` reading as
``audit.jsonl`` to the shell that runs it, and a fence that splits words at a quote reading two
harmless halves).

The rest of the data folder is NOT fenced here, for the same reason the queue fence gives: a
deployment keeps helper scripts in its data folder and runs them through the shell all day;
refusing the data folder whole would stop that work to close a door that is only the log's.
"""

from __future__ import annotations

import glob
import os
import re
import stat
from pathlib import Path

#: The audit log, by the name `governance/audit.py` writes it.
_LOG_NAME = re.compile(r"audit\.jsonl\b", re.IGNORECASE)
#: The app's route that serves the log to the Security screen.
_ROUTE = re.compile(r"/api/governance/audit\b", re.IGNORECASE)
#: The log's own code, imported or called — in-process from ``code_interpreter``, or by a
#: ``python -c`` from the shell. An import or a call, not a mention: ``rg AuditLog`` and
#: ``pytest -k audit`` are how the agent works on Chimera's own repository.
_LOG_CODE = re.compile(
    r"\bimport\s+chimera\.governance\.audit\b"
    r"|\bfrom\s+chimera\.governance\.audit\s+import\b"
    r"|\bfrom\s+chimera\.governance\s+import\b[^\n]*\baudit\b"
    r"|\b(?:import_module|__import__)\s*\(\s*['\"]?chimera\.governance\.audit\b"
    r"|\bAuditLog\s*\(",
    re.IGNORECASE,
)
#: ``$''`` and ``$""`` (ANSI-C and locale quoting): an empty string, once the shell has read it.
_EMPTY_DOLLAR_QUOTE = re.compile(r"\$(?=['\"])")
#: Quoting a shell removes before it runs a word: POSIX quotes, PowerShell's backtick escape, cmd's
#: caret escape.
_QUOTING = re.compile(r"[\"'`^]")
#: A POSIX backslash escape. Read as a variant of its own, because on Windows the same character is
#: the path separator, and removing it there would join a path's parts into one word.
_BACKSLASH_ESCAPE = re.compile(r"\\(.)")
#: ``$env:NAME`` (PowerShell), ``${NAME}``, ``$NAME`` (POSIX shells), ``%NAME%`` (cmd).
_ENV_VAR = re.compile(
    r"\$env:([A-Za-z_][A-Za-z0-9_]*)|\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)"
    r"|%([A-Za-z_][A-Za-z0-9_()]*)%"
)
#: What separates words in a shell line or a short program, for the purpose of finding paths in it.
_SEPARATORS = re.compile(r"[\s;|&<>()`'\",=+\[\]{}]+")
_GLOB_CHARS = frozenset("*?[")
#: A token that is written like a path (has a separator, a drive or a dot-dot), as opposed to a word.
_PATHLIKE = re.compile(r"[\\/:]|^\.\.?$")
_CHIMERA_NAMES = frozenset({"chimera", "chimera.exe", "chimera.cmd", "chimera.bat", "chimera.ps1"})
_CHIMERA_MODULES = frozenset({"chimera", "chimera.cli", "chimera.cli.main", "chimera.__main__"})
#: Bounds on the work one command can cause. There is deliberately no cap on the number of WORDS
#: read: a cap is a bypass (pad the command with that many words, then name the file). The queue
#: fence measured a 57 KB program of 2,400 path-like words at about 0.3 s with the same cache.
_MAX_BASES = 16
_MAX_GLOB_MATCHES = 200


def _expand(text: str) -> str:
    """``text`` with every environment variable it names replaced by its value, when it has one."""

    def value(match: re.Match[str]) -> str:
        name = next(group for group in match.groups() if group)
        return os.environ.get(name, match.group(0))

    return _ENV_VAR.sub(value, text)


def _as_shells_read_it(text: str) -> list[str]:
    """``text`` as written, and as a shell joins it once its quoting and escapes are removed.

    Both orders: quotes then escapes for one shell (``audit`` backslash ``jsonl``), escapes then
    quotes for a command quoted for a shell it starts (``bash -c "...aud\\"it.jsonl..."``, where
    the outer shell turns each backslash-quote into a quote the inner one removes).
    """

    def unquote(s: str) -> str:
        return _QUOTING.sub("", _EMPTY_DOLLAR_QUOTE.sub("", s))

    def unescape(s: str) -> str:
        return _BACKSLASH_ESCAPE.sub(r"\1", s)

    return list(
        dict.fromkeys([text, unquote(text), unescape(unquote(text)), unquote(unescape(text))])
    )


def _runs_chimera_audit(tokens: list[str]) -> bool:
    """Whether a Chimera launcher in ``tokens`` is given ``audit`` as its SUBCOMMAND.

    Only as the subcommand: ``chimera run "audit the dependency tree"`` asks the agent to do
    something, it does not read the log. Options are skipped, so one added later does not open a
    gap; ``governance audit`` is the same reader one level deeper.
    """
    lowered = [t.lower() for t in tokens]
    for i, token in enumerate(lowered):
        name = re.split(r"[\\/]", token)[-1]
        if name in _CHIMERA_NAMES:
            start = i + 1
        elif token == "-m" and i + 1 < len(lowered) and lowered[i + 1] in _CHIMERA_MODULES:
            start = i + 2
        else:
            continue
        words = [w for w in lowered[start : start + 6] if not w.startswith("-")]
        if words[:1] == ["audit"] or words[:2] == ["governance", "audit"]:
            return True
    return False


def _candidates(token: str, bases: list[Path]) -> list[Path]:
    """Where ``token`` could point: itself when absolute, else under each base; globs expanded."""
    token = os.path.expanduser(token)
    if os.sep == "/" and "\\" in token:
        # A backslash is not a separator here, but PowerShell on Linux and `%VAR%\audit.jsonl`
        # written for Windows still mean one; reading it as one costs a false refusal at worst.
        token = token.replace("\\", "/")
    path = Path(token)
    raw = [path] if path.is_absolute() else [base / token for base in bases]
    if not _GLOB_CHARS.intersection(token):
        if not _PATHLIKE.search(token):
            # A bare word is a path only where it names something that exists: `audit.jsonl` after
            # `cd home`. Checked with one `lexists` per base, so a long program's every word is not
            # resolved and stat'ed up its whole ancestry.
            return [p for p in raw if os.path.lexists(p)]
        return raw
    out: list[Path] = []
    for pattern in raw:
        # Each leading piece of the pattern, so `hom*/aud*/x` finds the file even though `x` does
        # not exist yet — the file a command is about to create never matches a glob.
        parts = pattern.parts
        for end in range(1, len(parts) + 1):
            piece = str(Path(*parts[:end]))
            if _GLOB_CHARS.intersection(piece):
                out.extend(Path(m) for m in glob.glob(piece)[:_MAX_GLOB_MATCHES])
    return out


class _Log:
    """The audit file, and a test of whether a path lands on it that a long command can afford.

    Same shape as the queue fence's ``_Queue``: spellings compared as strings first, then the
    file-system test (identity, which sees 8.3 names, trailing dots and links) walking the
    ancestry through a cache, so the ancestors every relative path shares are stat'ed once per
    command.
    """

    def __init__(self, log: Path) -> None:
        absolute = os.path.normpath(os.path.abspath(log))
        self._prefixes = {os.path.normcase(absolute), os.path.normcase(os.path.realpath(absolute))}
        self._stats: dict[str, os.stat_result | None] = {}
        st = self._stat(absolute)
        self._identity = (st.st_dev, st.st_ino) if st is not None and st.st_ino else None

    def _stat(self, path: str) -> os.stat_result | None:
        if path not in self._stats:
            try:
                self._stats[path] = os.stat(path)
            except (OSError, ValueError):
                self._stats[path] = None
        return self._stats[path]

    def holds(self, candidate: Path) -> bool:
        path = os.path.normpath(os.path.abspath(candidate))
        lowered = os.path.normcase(path)
        if any(lowered == p or lowered.startswith(p + os.sep) for p in self._prefixes):
            return True
        if self._identity is None:
            return False
        current = path
        while True:
            st = self._stat(current)
            if st is not None and (st.st_dev, st.st_ino) == self._identity:
                return True
            parent = os.path.dirname(current)
            if parent == current:
                return False
            current = parent


def reaches_log(text: str, *, home: Path | None, cwd: Path) -> str | None:
    """Why ``text`` — a shell command or a program — reaches the audit log, or None."""
    variants = _as_shells_read_it(text)
    if any(_ROUTE.search(v) for v in variants):
        return "it calls the app's audit route"
    if any(_LOG_CODE.search(v) for v in variants):
        return "it calls the audit log's own code"
    # Every variant's words, once each and in order (a word all three share is read once).
    tokens = list(dict.fromkeys(t for v in variants for t in _SEPARATORS.split(_expand(v)) if t))
    # The name counts when it is a PATH (`~/.chimera/audit.jsonl`, `$HOME/x/audit.jsonl`), not when
    # it is a word: `grep -rn audit.jsonl chimera/` is how the agent works on Chimera's own source,
    # and the first version refused every such search. A bare `audit.jsonl` that does name the log
    # (after a `cd`, or in the workspace) is still refused below, by file identity.
    if any(_LOG_NAME.search(t) and _PATHLIKE.search(t) for t in tokens):
        return "it names the audit log"
    if _runs_chimera_audit(tokens):
        return "it runs `chimera audit`"
    if home is None:
        return None
    log = _Log(Path(home).expanduser() / "audit.jsonl")
    bases = [Path(cwd), Path.home()]
    seen: set[tuple[str, int]] = set()
    for token in tokens:
        token = token.strip()
        # Seen with the same set of bases, a word has the same answer; with more bases it may not.
        if not token or token.startswith("-") or (token, len(bases)) in seen:
            continue
        seen.add((token, len(bases)))
        for candidate in _candidates(token, bases):
            if log.holds(candidate):
                return "it names Chimera's audit log"
            # A folder the command names may be one it changes into (`cd home && rm audit.jsonl`),
            # so what follows is also read relative to it.
            st = log._stat(os.path.normpath(os.path.abspath(candidate)))
            if len(bases) < _MAX_BASES and candidate not in bases and st is not None and stat.S_ISDIR(st.st_mode):
                bases.append(candidate)
    return None
