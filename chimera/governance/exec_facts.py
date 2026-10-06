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

A folder change earlier in the same command is followed when it is plain: ``cd sub && git commit``
names ``sub``'s ``git`` (cmd.exe looks in the NEW folder first) and ``sub``'s hooks. A ``cd`` it
cannot follow — to ``$VAR``, ``-``, nowhere, inside ``( … )`` or a pipe, or a ``popd`` — is said,
and after it the card never says "no repository hook will run".

The hooks folder is read the way git reads ``core.hooksPath``: ``-c`` on the command, the
worktree's ``config.worktree`` (when ``extensions.worktreeConfig`` is on), the repository's config,
``~/.gitconfig``, ``~/.config/git/config``, then the usual system files (``/etc/gitconfig``; on
Windows the Git install under Program Files). A plain ``[include]`` is followed. What is NOT
evaluated, and is said on the card instead of a hooks line: an ``[includeIf]`` whose file could set
the hooks folder, and ``GIT_CONFIG_*``/``GIT_DIR``-family variables in the environment or on the
command line. A system config a git build keeps somewhere else is not read.

The reader fails closed: what it cannot read exactly as git does is said, never guessed. That is a
value with a comment, quotes inside or escapes, a key on a section line (``[core] hooksPath = …``),
a value continued onto the next line, ``-c include.*``, and ``--config-env``, ``--git-dir``,
``--work-tree``, ``--exec-path``, ``--bare`` or ``--namespace`` on the command. A second review found
each of the first five letting git run a ``pre-commit`` the card said would not run.
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


#: Builtins that move the shell to another folder for the rest of the command.
_CHANGES_FOLDER = frozenset({"cd", "pushd", "popd", "chdir"})
#: The operators kept when splitting, so a segment knows whether a pipe put it in a subshell.
_SPLIT_KEEP = re.compile(r"(&&|\|\||[;&|\n])")


def _commands(command: str) -> list[tuple[list[str], bool]]:
    """Each simple command's words from its program on, and whether it runs in a subshell.

    A segment on either side of a single ``|``, or opened with ``(``, runs in a subshell, so a
    ``cd`` there does not move the rest of the command; it is reported, not followed.
    """
    parts = _SPLIT_KEEP.split(command)
    out: list[tuple[list[str], bool]] = []
    for i in range(0, len(parts), 2):
        segment = parts[i].strip()
        before = parts[i - 1] if i > 0 else ""
        after = parts[i + 1] if i + 1 < len(parts) else ""
        subshell = "|" in (before, after) or segment.startswith("(")
        words = _words(segment.lstrip("(").rstrip(")").strip())
        while words and (_ASSIGNMENT.match(words[0]) or words[0] in _LAUNCHERS):
            words = words[1:]
        if words:
            out.append((words, subshell))
    return out


def _cd_target(words: list[str]) -> str | None:
    """The folder a ``cd``/``pushd`` goes to when it is a plain path, else None (not followed)."""
    args = [w for w in words[1:] if w.lower() not in ("/d", "-l", "-p", "--")]
    if len(args) != 1:
        return None  # `cd` alone goes home (POSIX) or prints the folder (cmd.exe)
    target = args[0]
    if target == "-" or any(mark in target for mark in ("$", "`", "%", "*", "?", "{")):
        return None
    return target


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


class _Unsure(Exception):
    """The hooks folder cannot be read off the config files without evaluating what git evaluates."""


def _config_hooks_path(config: Path, depth: int = 0) -> str | None:
    """``core.hooksPath`` in one git config file, or None. A small reader: sections and keys.

    A plain ``[include] path = …`` is followed (relative to the including file, as git does). An
    ``[includeIf "…"]`` is not evaluated — its condition is git's to decide — so when the file it
    names could set ``core.hooksPath`` (it says ``hooksPath`` or includes further), this raises
    :class:`_Unsure` rather than letting the card say "no repository hook will run" for a folder
    git may not be using. Review of S30-30: a repo config including a file that set
    ``hooksPath = evil-hooks`` made the card name ``.git/hooks`` while git ran ``evil-hooks``.
    """
    try:
        lines = config.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if depth > 5:
        raise _Unsure(f"includes nested deeper than this reader follows ({config})")
    section = ""
    value: str | None = None
    for raw in lines:
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if _CONTINUES.search(raw):
            # A trailing backslash joins the next line to this value, so the next line is not the
            # key it looks like (or a key this reader skipped is one git reads). Not followed.
            raise _Unsure(f"{config} continues a value onto the next line")
        if line.startswith("["):
            name, closed, after = line[1:].partition("]")
            if not closed or (after.strip() and after.strip()[0] not in "#;"):
                # `[core] hooksPath = x` on one line is a key git reads; taking the whole line as
                # the section name missed it (review of S30-30, verified with git 2.53).
                raise _Unsure(f"{config} has a key on a section line ({line[:60]})")
            section = name.strip().lower()
            continue
        key, eq, rest = line.partition("=")
        if not eq:
            continue
        key = key.strip().lower()
        if section == "core" and key == "hookspath":
            value = _plain_value(rest, config)
        elif section.startswith("include") and key == "path" and rest.strip():
            rest = _plain_value(rest, config)
            target = Path(os.path.expanduser(rest))
            target = target if target.is_absolute() else config.parent / target
            if section == "include":
                included = _config_hooks_path(target, depth + 1)
                if included is not None:
                    value = included
            elif _may_set_hooks_path(target):
                raise _Unsure(f"{config} includes {target} under a condition git evaluates")
    return value


#: A line whose value goes on to the next one: an odd number of backslashes at its end.
_CONTINUES = re.compile(r"(?<!\\)(?:\\\\)*\\\s*$")


def _plain_value(rest: str, config: Path) -> str:
    """A value this reader can read exactly as git does, or :class:`_Unsure`.

    Git ends an unquoted value at ``;`` or ``#`` (a comment), drops quotes wherever they are and
    reads backslash escapes. ``hooksPath = <dir>/evil/hooks ; note`` is ``<dir>/evil/hooks`` to git
    and was ``<dir>/evil/hooks ; note`` here — an empty folder, so the card said no hook would run
    while git ran ``evil/hooks/pre-commit``. Only a bare value, or one wrapped whole in quotes with
    nothing special inside, is taken; anything else is said instead of guessed.
    """
    text = rest.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1]
    if any(mark in text for mark in ('"', "\\", ";", "#")):
        raise _Unsure(f"{config} writes the value with quotes, escapes or a comment ({rest.strip()[:60]})")
    return text


def _may_set_hooks_path(config: Path) -> bool:
    try:
        text = config.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False
    return "hookspath" in text or "[include" in text


#: Environment variables that change which config git reads, or which repository it is in. Set in
#: this process's environment (which the command inherits) or on the command line, they make the
#: config files this module reads not the ones git reads.
_GIT_ENV = ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
            "GIT_CONFIG", "GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE")
_GIT_ENV_IN_COMMAND = re.compile(r"\bGIT_(?:CONFIG\w*|DIR|COMMON_DIR|WORK_TREE)\b")


def _worktree_config(gitdir: Path, common: Path) -> list[Path]:
    """``config.worktree``, which git reads only with ``extensions.worktreeConfig`` on."""
    extra = gitdir / "config.worktree"
    if not extra.is_file():
        return []
    try:
        shared = re.sub(r"\s+", "", (common / "config").read_text(encoding="utf-8", errors="replace"))
    except OSError:
        shared = ""
    if "worktreeconfig=true" in shared.lower():
        return [extra]
    if _may_set_hooks_path(extra):
        # Read or ignored depending on an extension this small reader does not fully evaluate.
        raise _Unsure(f"{extra} may set the hooks folder")
    return []


def _system_configs() -> list[Path]:
    """Where git's system config usually is. Lowest precedence; a build that keeps it elsewhere
    is a limit the module docstring names."""
    found = [Path("/etc/gitconfig")]
    if sys.platform == "win32":
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMW6432")):
            if base:
                found += [Path(base) / "Git" / "etc" / "gitconfig",
                          Path(base) / "Git" / "mingw64" / "etc" / "gitconfig"]
    return found


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
            key = key.strip().lower()
            if key == "core.hookspath":
                override = value
            elif key.startswith("include"):
                raise _Unsure(f"the command includes a config file ({words[i + 1][:60]})")
            i += 2
            continue
        flag = word.split("=", 1)[0]
        if flag in _ELSEWHERE_FLAGS:
            # Each makes git read another repository's hooks or a config this reader never opens;
            # skipped as a generic flag, the card named the cwd repository's hooks instead.
            raise _Unsure(f"the command points git elsewhere ({flag})")
        if word.startswith("-"):
            i += 1
            continue
        break
    found = _git_dirs(where)
    if found is None:
        return None, None
    worktree, gitdir, common = found
    configured = override
    if configured is None:
        # Highest precedence first, as git applies them: the worktree's own config, the
        # repository's, the user's (~/.gitconfig over the XDG file), then the system's.
        for config in (
            *_worktree_config(gitdir, common),
            common / "config",
            Path.home() / ".gitconfig",
            Path.home() / ".config" / "git" / "config",
            *_system_configs(),
        ):
            configured = _config_hooks_path(config)
            if configured is not None:
                break
    if configured is None:
        return common / "hooks", worktree
    if configured == "":
        return None, worktree
    folder = Path(os.path.expanduser(configured))
    return (folder if folder.is_absolute() else worktree / folder), worktree


#: git's global options that point it at another repository, config or program folder.
_ELSEWHERE_FLAGS = frozenset({"--git-dir", "--work-tree", "--config-env", "--exec-path", "--bare",
                              "--namespace"})
#: git's global options whose value may be the next word (`--git-dir ../x commit`).
_TAKES_NEXT = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--config-env", "--namespace",
                         "--super-prefix"})


def _subcommand(words: list[str]) -> tuple[str, list[str]]:
    i = 1
    while i < len(words):
        if words[i] in _TAKES_NEXT and i + 1 < len(words):
            i += 2
            continue
        if words[i].startswith("-"):
            i += 1
            continue
        return words[i].lower(), words[i + 1 :]
    return "", []


def _unsure_line(sub: str, why: str) -> str:
    return (
        f"git {sub}: which repository hooks run could NOT be determined ({why}); "
        "they may run and can do more than the command says"
    )


def _hook_line(words: list[str], cwd: Path, *, command: str = "", lost: str = "") -> str | None:
    """The hooks fact for one git invocation. ``lost`` says why the folder it runs in is unknown."""
    sub, rest = _subcommand(words)
    names = _HOOKS_BY_SUBCOMMAND.get(sub)
    if not names:
        return None
    if lost:
        # Never "no repository hook will run" about a folder the command may not be in.
        return _unsure_line(sub, lost)
    env_set = [name for name in _GIT_ENV if os.environ.get(name)]
    if env_set or _GIT_ENV_IN_COMMAND.search(command):
        return _unsure_line(sub, f"git's config or repository is set by the environment "
                                 f"({', '.join(env_set) or 'on the command line'})")
    try:
        folder, worktree = _hooks_dir(words, cwd)
    except _Unsure as exc:
        return _unsure_line(sub, str(exc))
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
    seen: set[tuple[str, Path]] = set()
    hook_lines: list[str] = []
    here = cwd
    lost = ""  # why the folder the rest of the command runs in is not known, once it is not
    for words, piped in _commands(command):
        name = words[0]
        if name.lower() in _CHANGES_FOLDER:
            # `cd sub && git commit` runs `sub`'s git (cmd.exe looks in the NEW folder first) and
            # `sub`'s hooks. Followed when the target is a plain path; anything else is said.
            target = _cd_target(words)
            if lost or piped or target is None or name.lower() == "popd":
                lost = lost or f"it changes folder ({' '.join(words)[:60]}) in a way this card does not follow"
            else:
                moved = Path(os.path.expanduser(target))
                here = moved if moved.is_absolute() else here / moved
            continue
        base = os.path.basename(name).lower()
        if base in ("git", "git.exe", "git.cmd", "git.bat"):
            line = _hook_line(words, here, command=command, lost=lost)
            if line is not None and line not in hook_lines:
                hook_lines.append(line)
        if lost or (name, here) in seen or len(seen) >= _MAX_PROGRAMS:
            continue
        seen.add((name, here))
        path = resolve(name, here)
        if path is None:
            continue  # a builtin (`echo`), a function, or not installed: nothing to name
        note = ""
        if workspace is not None and _inside(path, workspace):
            note = "  (INSIDE the project folder, where the agent can write)"
        if here != cwd:
            note += f"  (run from {here}, after the command's cd)"
        lines.append(f"{name} -> {path}{note}")
    if _SETS_PATH.search(command):
        lines.append(
            "this command changes PATH itself, so the programs above may not be the ones that run"
        )
    if lost:
        lines.append(
            f"{lost}, so the programs and hooks after it may not be the ones named here"
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
