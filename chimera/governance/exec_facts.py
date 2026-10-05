"""What a shell command will actually run, said on the card that asks about it (study 30, S30-30).

A person approving ``git commit -m "fix"`` approves what the words say. Two things the words do not
say decide what runs:

- **Which program the name resolves to.** ``git`` is whatever ``PATH`` finds first — on Windows,
  ``cmd.exe`` looks in the working folder BEFORE ``PATH`` — so a ``git.bat`` the agent left in the
  project, or a folder put ahead on ``PATH``, runs instead of Git. The study's sweep measured this
  shadowing at 1.00: every approval of the plain name was an approval of whatever the name found.
- **Which repository hooks git will run.** An approved ``git commit`` runs the repository's
  ``pre-commit``, ``commit-msg``, ``post-commit``…, which are files in ``.git/hooks`` (or wherever
  ``core.hooksPath`` points) that the agent can write. The sweep laundered an argument class 9/20
  through a ``pre-commit`` that ran ``git add -A``: the commit the person approved swept in files
  nobody named. `core/pull_request.py` already pushes with ``--no-verify`` and an empty
  ``hooksPath`` for that reason; a command the agent wrote cannot be rewritten that way without
  changing what the person was asked about, so the card SAYS which hooks will run instead.

Read without running anything. Resolving ``git`` by running ``git rev-parse`` would execute the very
program whose identity is in question, before anybody approved it; the repository and its hooks
folder are found by reading files, as git itself would.

What this does not see, said once: a program the command itself writes or a hook it installs before
the program runs (``echo … > .git/hooks/pre-commit && git commit``) — the command text shows that,
the folder did not hold it yet; names behind a shell function, an alias or ``$(…)``; and a ``PATH``
the command changes (``PATH=./bin:$PATH git …``, ``export PATH=…``), which is flagged rather than
followed.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
from pathlib import Path

#: The header of the block appended to a card's action. `approval._facts_of` reads the lines under
#: it back as the record's ``programs`` fact, so the receipt carries what the card showed.
HEADER = "[what this runs, as this machine resolves it now]"

#: Prefix words that run the NEXT word as the program.
_LAUNCHERS = frozenset({"sudo", "env", "exec", "nohup", "time", "command", "builtin", "nice", "xargs"})
#: Shell operators that start a new simple command.
_SPLIT = re.compile(r"&&|\|\||[;&|\n]")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SETS_PATH = re.compile(r"(?:^|[\s;&|])(?:export\s+|set\s+|\$env:)?PATH\s*[+]?=", re.IGNORECASE)

#: The git subcommands that run repository hooks, and which of their hooks ``--no-verify`` skips.
_HOOKS_BY_SUBCOMMAND: dict[str, tuple[str, ...]] = {
    "commit": ("pre-commit", "prepare-commit-msg", "commit-msg", "post-commit", "post-rewrite"),
    "merge": ("pre-merge-commit", "prepare-commit-msg", "commit-msg", "post-merge"),
    "pull": ("pre-merge-commit", "prepare-commit-msg", "commit-msg", "post-merge", "post-rewrite"),
    "push": ("pre-push", "reference-transaction"),
    "rebase": ("pre-rebase", "post-rewrite", "post-checkout"),
    "am": ("applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit"),
    "checkout": ("post-checkout",),
    "switch": ("post-checkout",),
    "cherry-pick": ("prepare-commit-msg", "commit-msg", "post-commit"),
    "revert": ("prepare-commit-msg", "commit-msg", "post-commit"),
}
_SKIPPED_BY_NO_VERIFY = frozenset({"pre-commit", "commit-msg", "pre-push", "pre-merge-commit"})
_MAX_PROGRAMS = 8


def _words(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _program_words(command: str) -> list[list[str]]:
    """Each simple command's words from its program on, assignments and launchers skipped."""
    out: list[list[str]] = []
    for segment in _SPLIT.split(command):
        words = _words(segment.strip())
        while words and (_ASSIGNMENT.match(words[0]) or words[0] in _LAUNCHERS):
            words = words[1:]
        if words:
            out.append(words)
    return out


def resolve(name: str, cwd: Path) -> str | None:
    """Where ``name`` resolves for a shell started in ``cwd`` with this process's ``PATH``."""
    if os.sep in name or (os.altsep and os.altsep in name):
        candidate = Path(name) if Path(name).is_absolute() else cwd / name
        return str(candidate.resolve()) if candidate.exists() else None
    search = os.environ.get("PATH", "")
    if sys.platform == "win32":
        # `cmd.exe` searches the working folder before PATH (unless the owner set
        # NoDefaultCurrentDirectoryInExePath), and this process's folder is not the command's.
        if "NoDefaultCurrentDirectoryInExePath" not in os.environ:
            search = os.pathsep.join([str(cwd), search])
    found = shutil.which(name, path=search)
    return str(Path(found).resolve()) if found else None


def _inside(path: str, folder: Path) -> bool:
    try:
        return Path(path).resolve().is_relative_to(folder.resolve())
    except (OSError, ValueError):
        return False


def _git_dirs(start: Path) -> tuple[Path, Path, Path] | None:
    """``(worktree, gitdir, commondir)`` of the repository holding ``start``, read from files."""
    for folder in [start, *start.parents]:
        dot = folder / ".git"
        if dot.is_dir():
            return folder, dot, dot
        if dot.is_file():
            try:
                text = dot.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                return None
            if not text.startswith("gitdir:"):
                return None
            gitdir = Path(text.split(":", 1)[1].strip())
            gitdir = gitdir if gitdir.is_absolute() else (folder / gitdir)
            common = gitdir
            try:
                pointer = (gitdir / "commondir").read_text(encoding="utf-8").strip()
                common = Path(pointer) if Path(pointer).is_absolute() else gitdir / pointer
            except OSError:
                pass
            return folder, gitdir, common
    return None


def _config_hooks_path(config: Path) -> str | None:
    """``core.hooksPath`` in one git config file, or None. A small reader: sections and keys."""
    try:
        lines = config.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    section = ""
    value: str | None = None
    for raw in lines:
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("["):
            section = line.strip("[]").strip().lower()
            continue
        if section == "core" and "=" in line:
            key, _, rest = line.partition("=")
            if key.strip().lower() == "hookspath":
                value = rest.strip().strip('"')
    return value


def _hooks_dir(words: list[str], cwd: Path) -> tuple[Path | None, Path | None]:
    """``(hooks folder, worktree)`` for a git invocation, honouring ``-C`` and ``-c core.hooksPath``."""
    where = cwd
    override: str | None = None
    i = 1
    while i < len(words):
        word = words[i]
        if word == "-C" and i + 1 < len(words):
            target = Path(os.path.expanduser(words[i + 1]))
            where = target if target.is_absolute() else where / target
            i += 2
            continue
        if word == "-c" and i + 1 < len(words):
            key, _, value = words[i + 1].partition("=")
            if key.strip().lower() == "core.hookspath":
                override = value
            i += 2
            continue
        if word.startswith("-"):
            i += 1
            continue
        break
    found = _git_dirs(where)
    if found is None:
        return None, None
    worktree, _gitdir, common = found
    configured = override
    if configured is None:
        configured = _config_hooks_path(common / "config")
    if configured is None:
        for config in (Path.home() / ".gitconfig", Path.home() / ".config" / "git" / "config"):
            configured = _config_hooks_path(config)
            if configured is not None:
                break
    if configured is None:
        return common / "hooks", worktree
    if configured == "":
        return None, worktree
    folder = Path(os.path.expanduser(configured))
    return (folder if folder.is_absolute() else worktree / folder), worktree


def _subcommand(words: list[str]) -> tuple[str, list[str]]:
    i = 1
    while i < len(words):
        if words[i] in ("-C", "-c") and i + 1 < len(words):
            i += 2
            continue
        if words[i].startswith("-"):
            i += 1
            continue
        return words[i].lower(), words[i + 1 :]
    return "", []


def _hook_line(words: list[str], cwd: Path) -> str | None:
    sub, rest = _subcommand(words)
    names = _HOOKS_BY_SUBCOMMAND.get(sub)
    if not names:
        return None
    folder, worktree = _hooks_dir(words, cwd)
    if worktree is None:
        return None
    if folder is None:
        return f"git {sub}: repository hooks are switched off for this command (empty core.hooksPath)"
    present = []
    for hook in names:
        path = folder / hook
        if path.is_file() and (sys.platform == "win32" or os.access(path, os.X_OK)):
            present.append(hook)
    if "--no-verify" in rest or (sub == "commit" and "-n" in rest):
        present = [h for h in present if h not in _SKIPPED_BY_NO_VERIFY]
    if not present:
        return f"git {sub}: no repository hook will run ({folder})"
    return (
        f"git {sub}: these repository hooks WILL RUN, and can do more than the command says — "
        f"{', '.join(present)} (in {folder})"
    )


def describe(command: str, cwd: Path, workspace: Path | None = None) -> list[str]:
    """One line per fact the card should carry about ``command`` run in ``cwd``; empty if none."""
    lines: list[str] = []
    seen: set[str] = set()
    hook_lines: list[str] = []
    for words in _program_words(command):
        name = words[0]
        base = os.path.basename(name).lower()
        if base in ("git", "git.exe", "git.cmd", "git.bat"):
            line = _hook_line(words, cwd)
            if line is not None and line not in hook_lines:
                hook_lines.append(line)
        if name in seen or len(seen) >= _MAX_PROGRAMS:
            continue
        seen.add(name)
        path = resolve(name, cwd)
        if path is None:
            continue  # a builtin (`cd`, `echo`), a function, or not installed: nothing to name
        note = ""
        if workspace is not None and _inside(path, workspace):
            note = "  (INSIDE the project folder, where the agent can write)"
        lines.append(f"{name} -> {path}{note}")
    if _SETS_PATH.search(command):
        lines.append(
            "this command changes PATH itself, so the programs above may not be the ones that run"
        )
    return lines + hook_lines


def block(command: str, cwd: Path, workspace: Path | None = None) -> str:
    """The text appended to a card's action, or "" when there is nothing to say."""
    try:
        lines = describe(command, cwd, workspace)
    except Exception:  # noqa: BLE001 — a card without this note is the card it was before
        return ""
    if not lines:
        return ""
    return "\n\n" + HEADER + "\n" + "\n".join(f"- {line}" for line in lines)


def programs_of(action: str) -> list[str]:
    """The fact lines of a card's action (the inverse of :func:`block`), for the record."""
    head, sep, tail = action.partition("\n" + HEADER + "\n")
    if not sep:
        return []
    return [line[2:] for line in tail.splitlines() if line.startswith("- ")]
