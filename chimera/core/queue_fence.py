"""Keep the agent's shell and code tools away from the approval queue.

A question waiting for a person is ``<home>/approvals/<id>.ask.json``; an answer is a file written
beside it, and `governance/pending.py` applies whatever answer file appears. Whoever can write in
that folder can therefore approve, and `pending.py` says it plainly: that includes the local agent
when it has ``run_shell``. #775 closed the folder to the write tools by file identity
(`own_files.py`). This closes it to the three tools that run arbitrary commands — ``run_shell``,
``execute_code`` and ``code_interpreter`` — and to the agent running ``chimera approve`` itself.

Why a fence on the command and not a secret on the answer: every surface the owner answers from —
``chimera approve`` in a terminal, the app's card, the chat bot — runs as the same OS user as the
agent's shell, so any key those surfaces could read, a command could read too, and a secret the
agent cannot reach would be a secret ``chimera approve`` cannot reach either. What does tell the two
apart is WHO issues the command: the owner types ``chimera approve``; the agent asks a tool. So the
tool refuses.

What counts as reaching the queue, decided before anything runs:

- naming the folder by any spelling the file system resolves to it: absolute or relative (to the
  workspace, to the user's home, or to a folder the command itself names, which is how ``cd`` is
  followed), through ``$VAR``, ``${VAR}``, ``%VAR%``, ``$env:VAR`` or ``~``, a glob, a link, and on
  Windows a device or loopback-share prefix, a trailing dot or space, or an 8.3 short name. The
  last ones are not parsed: the path is compared with the folder by file identity, which no
  spelling changes (the method of `own_files.within`, with a cache for long commands);
- naming a question or answer file (``*.ask.json``, ``*.answer.json``);
- calling the app's ``/api/approvals`` route, or importing or calling the queue's own code
  (``pending.answer(``, ``from chimera.governance.pending import``);
- running ``chimera approve`` (or ``chimera project approve``) as the subcommand.

Each of those is read on the command as written AND on the command as a shell joins it once its
quoting is removed: ``appro""vals``, ``appro''vals``, ``appro$''vals``, ``appro`vals``
(PowerShell's escape), ``appro^vals`` (cmd's) and ``approval`` + backslash + ``s`` (a POSIX
escape) are all ``approvals`` to the shell that runs them, and a fence that splits words at a quote
reads two harmless halves. That was a real bypass of this file's first version (study 30, S30-04
review), down to ``.ans""wer.json`` slipping past the file-name test.

The rest of the data folder is NOT fenced here. A deployment can keep the agent's own helper scripts
inside its data folder and run them through the shell all day; refusing the data folder whole would
stop that work to close a door that is only the queue's.

The limit, said once: this REDUCES the ways in; it does not close them. It reads what a command
says, after the quoting above is removed, and nothing a shell computes: a program that assembles
the path at run time from pieces (``"appr" + "ovals"``), a parameter expansion with a default
(``appro${X:-}vals``), brace expansion (``appro{vals,}``), command substitution and ANSI-C escapes
with a code in them are not seen. Those are known gaps, not oversights.

What a refusal costs: a command that only MENTIONS these names is refused too — a grep for
``.answer.json`` over Chimera's own source, ``chimera approve`` inside a quoted argument right
after the launcher. Narrowing the file names to paths that resolve to the folder would reopen
``find ~ -name '*.ask.json'``, which names no folder at all, so a mention stays a refusal; the file
tools read the same source.
"""

from __future__ import annotations

import glob
import os
import re
import stat
from pathlib import Path

#: Question and answer files, by the names `pending.py` gives them.
_QUEUE_FILE = re.compile(r"\.(?:ask|answer)\.json\b", re.IGNORECASE)
#: The app's route that answers a card.
_ROUTE = re.compile(r"/api/approvals\b", re.IGNORECASE)
#: The queue's own code, imported or called — in-process from ``code_interpreter``, or by a
#: ``python -c`` from the shell. An import or a call, not a mention: ``rg answer_with_code`` and
#: ``pytest -k pending.answer`` are how the agent works on Chimera's own repository.
_QUEUE_CODE = re.compile(
    r"\bimport\s+chimera\.governance\.pending\b"
    r"|\bfrom\s+chimera\.governance\.pending\s+import\b"
    r"|\bfrom\s+chimera\.governance\s+import\b[^\n]*\bpending\b"
    r"|\b(?:import_module|__import__)\s*\(\s*['\"]?chimera\.governance\.pending\b"
    r"|\banswer_with_code\s*\(|\bpending\.answer\s*\(",
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
#: read: a cap is a bypass (pad the command with that many words, then name the folder). Measured
#: on Windows: a 57 KB program of 2,400 path-like words is read in about 0.3 s (`_Queue` caches).
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

    Both orders: quotes then escapes for one shell (``approval`` backslash ``s``), escapes then
    quotes for a command quoted for a shell it starts (``bash -c "...appro\\"\\"vals..."``, where
    the outer shell turns each backslash-quote into a quote the inner one removes).
    """

    def unquote(s: str) -> str:
        return _QUOTING.sub("", _EMPTY_DOLLAR_QUOTE.sub("", s))

    def unescape(s: str) -> str:
        return _BACKSLASH_ESCAPE.sub(r"\1", s)

    return list(
        dict.fromkeys([text, unquote(text), unescape(unquote(text)), unquote(unescape(text))])
    )


def _runs_chimera_approve(tokens: list[str]) -> bool:
    """Whether a Chimera launcher in ``tokens`` is given ``approve`` as its SUBCOMMAND.

    Only as the subcommand: ``chimera run "approve the dependency PR"`` asks the agent to do
    something, it answers no question. The CLI takes no option before the subcommand today; options
    are skipped anyway, so one added later does not open a gap.
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
        if words[:1] == ["approve"] or words[:2] == ["project", "approve"]:
            return True
    return False


def _candidates(token: str, bases: list[Path]) -> list[Path]:
    """Where ``token`` could point: itself when absolute, else under each base; globs expanded."""
    token = os.path.expanduser(token)
    if os.sep == "/" and "\\" in token:
        # A backslash is not a separator here, but PowerShell on Linux and `%VAR%\approvals` written
        # for Windows still mean one; reading it as one costs a false refusal at worst.
        token = token.replace("\\", "/")
    path = Path(token)
    raw = [path] if path.is_absolute() else [base / token for base in bases]
    if not _GLOB_CHARS.intersection(token):
        if not _PATHLIKE.search(token):
            # A bare word is a path only where it names something that exists: `approvals` after
            # `cd home`. Checked with one `lexists` per base, so a long program's every word is not
            # resolved and stat'ed up its whole ancestry.
            return [p for p in raw if os.path.lexists(p)]
        return raw
    out: list[Path] = []
    for pattern in raw:
        # Each leading piece of the pattern, so `hom*/appr*/x` finds the folder even though `x`
        # does not exist yet — the file a command is about to create never matches a glob.
        parts = pattern.parts
        for end in range(1, len(parts) + 1):
            piece = str(Path(*parts[:end]))
            if _GLOB_CHARS.intersection(piece):
                out.extend(Path(m) for m in glob.glob(piece)[:_MAX_GLOB_MATCHES])
    return out


class _Queue:
    """The approval folder, and a test of whether a path lands in it that a long command can afford.

    ``own_files.within`` resolves the path and stats its whole ancestry for every call, which is
    right for one path and measured at about 3 ms per word on Windows: 15 s for a command padded
    with five thousand paths. Here the folder's spellings are compared as strings first, and the
    file-system test (identity, which sees 8.3 names, trailing dots and links) walks the ancestry
    through a cache, so the ancestors that every relative path shares are stat'ed once per command.
    """

    def __init__(self, folder: Path) -> None:
        absolute = os.path.normpath(os.path.abspath(folder))
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

    def is_dir(self, candidate: Path) -> bool:
        st = self._stat(os.path.normpath(os.path.abspath(candidate)))
        return st is not None and stat.S_ISDIR(st.st_mode)


def reaches_queue(text: str, *, home: Path | None, cwd: Path) -> str | None:
    """Why ``text`` — a shell command or a program — reaches the approval queue, or None."""
    variants = _as_shells_read_it(text)
    if any(_QUEUE_FILE.search(v) for v in variants):
        return "it names an approval question or answer file"
    if any(_ROUTE.search(v) for v in variants):
        return "it calls the app's approval route"
    if any(_QUEUE_CODE.search(v) for v in variants):
        return "it calls the approval queue's own code"
    # Every variant's words, once each and in order (a word all three share is read once).
    tokens = list(dict.fromkeys(t for v in variants for t in _SEPARATORS.split(_expand(v)) if t))
    if _runs_chimera_approve(tokens):
        return "it runs `chimera approve`"
    if home is None:
        return None
    if _names(tokens, _Queue(Path(home).expanduser() / "approvals"), cwd):
        return "it names Chimera's approval folder"
    return None


def _names(tokens: list[str], target: _Queue, cwd: Path) -> bool:
    """Whether any word of ``tokens`` resolves to ``target`` or below it, by any spelling."""
    bases = [Path(cwd), Path.home()]
    seen: set[tuple[str, int]] = set()
    for token in tokens:
        token = token.strip()
        # Seen with the same set of bases, a word has the same answer; with more bases it may not.
        if not token or token.startswith("-") or (token, len(bases)) in seen:
            continue
        seen.add((token, len(bases)))
        for candidate in _candidates(token, bases):
            if target.holds(candidate):
                return True
            # A folder the command names may be one it changes into (`cd home && cd approvals`), so
            # what follows is also read relative to it.
            if len(bases) < _MAX_BASES and candidate not in bases and target.is_dir(candidate):
                bases.append(candidate)
    return False


#: The owner's hooks file, by the name `chimera/governance/hooks.py` gives it. Kept as a literal
#: rather than imported, so this fence loads nothing from the module it protects.
_HOOKS_FILE = "chimera-hooks.json"
_HOOKS_NAME = re.compile(r"chimera-hooks\.json\b", re.IGNORECASE)
#: The hooks module's own code, imported — `code_interpreter` runs in the server's process, where a
#: loader is one import away. A mention (`rg chimera.governance.hooks`) is how the agent works on
#: Chimera's own repository, so only an import refuses.
_HOOKS_CODE = re.compile(
    r"\bimport\s+chimera\.governance\.hooks\b"
    r"|\bfrom\s+chimera\.governance\.hooks\s+import\b"
    r"|\bfrom\s+chimera\.governance\s+import\b[^\n]*\bhooks\b"
    r"|\b(?:import_module|__import__)\s*\(\s*['\"]?chimera\.governance\.hooks\b",
    re.IGNORECASE,
)


def reaches_hooks_file(text: str, *, home: Path | None, cwd: Path) -> str | None:
    """Why ``text`` — a shell command or a program — reaches the owner's hooks file, or None.

    The hook-update path of `docs/hooks-threat-model.md` (A1): a hook the agent could write is a
    command that fires on every later call with no injection needed any more. The write tools are
    kept out by the data folder they live in (`own_files.py`); this keeps out the three tools that
    run arbitrary text, with the same reading the approval queue gets above — quoting removed,
    variables expanded, the file compared by identity — and the same stated limit: a path the
    command assembles at run time is not seen.
    """
    variants = _as_shells_read_it(text)
    if any(_HOOKS_NAME.search(v) for v in variants):
        return "it names the owner's hooks file"
    if any(_HOOKS_CODE.search(v) for v in variants):
        return "it imports the hooks module"
    if home is None:
        return None
    tokens = list(dict.fromkeys(t for v in variants for t in _SEPARATORS.split(_expand(v)) if t))
    if _names(tokens, _Queue(Path(home).expanduser() / _HOOKS_FILE), cwd):
        return "it names the owner's hooks file"
    return None
