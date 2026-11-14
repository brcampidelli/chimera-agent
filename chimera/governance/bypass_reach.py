"""(d) of the bypass floor: a recursive delete or write that lands outside the workspace and the home.

Measured in the audit of 2026-11 (a07-04): on Windows, where no OS sandbox exists, with "Ignore
permissions" on, ``python -c "import shutil; shutil.rmtree('C:/Users')"`` and
``Remove-Item -Recurse -Force C:/Users/x`` were released without a question. The kernel's BLOCK rules
are fixed signatures (``rm -rf /``, ``mkfs``) and neither command matches one, so nothing between the
switch and the disk asked. This module turns those into a question again, and nothing else.

**What it is, said plainly: a LEXICAL rule.** It reads the words of a command. It does not follow a
variable, a script file, a program the command starts, or Python that builds its path at run time
(``shutil.rmtree(p)`` with ``p`` computed earlier is not seen). The repository measured a lexical
denylist at 30% macro coverage outside its own sample (``bench/denylist_bypass/RESULTS.md``); there is
no reason to think this one does better against someone trying. It REDUCES the reach of an accident
or an injected instruction while the switch is on; it does not close it. The only thing that closes it
is a sandbox that isolates, which Windows does not have here.

What counts as "outside": a target that is neither inside the workspace (the workspace itself
included) nor strictly inside the home folder. The home itself is outside, so is any ancestor of
either. When the path exists on disk (or its nearest existing ancestor does) it is placed by its
``realpath``, so a link inside the workspace that leads out of it counts as outside. A target this
rule cannot place counts as outside too: an unknown place is asked about rather than assumed safe.
That covers a variable (``$X``, ``%X%``, ``$(...)``), a brace expansion (``{a,b}``), a drive-relative
path (``C:x``) and a relative path that climbs out with ``..``. A relative path is resolved against
the command's ``cwd`` when it has one (an absolute ``cwd`` as is, a relative one joined to the
workspace, as the shell tool runs it), else the workspace; with no workspace known, a relative path
that does not climb out with ``..`` is taken as inside, since that is where the agent's commands run.

**Directory changes inside one command.** ``cd``, ``chdir``, ``pushd``, ``Set-Location`` and ``sl``
move the directory the later relative paths are placed against. The rule keeps EVERY directory the
command may be in (the one it started in plus each one a ``cd`` reached) and a relative target that
is outside under any of them is outside. A ``cd`` to a place outside the workspace, to a variable, to
``-`` or to nothing (the home) makes the relative paths after it count as outside. It is
deliberately blind to subshells, ``popd`` and branches (``a || cd /``): the effect of that is only
ever more questions, never fewer.

What it reads:

* ``rm`` with ``-r``/``-R``/``--recursive``; ``rmdir``/``rd``/``del``/``erase`` with ``/s``; ``find PATH
  ... -delete``.
* PowerShell ``Remove-Item`` (and its aliases ``ri``, ``rm``, ``del``, ``erase``, ``rmdir``, ``rd``)
  with ``-Recurse`` (any prefix, ``-r``, ``-Recurse:$true``); a comma list (``a,C:/x``) is checked item
  by item; ``Copy-Item`` / ``Move-Item`` destinations (``-Recurse`` for the copy).
* ``cp -r``/``-R``/``-a``, ``mv``, ``robocopy``, ``xcopy /s|/e``, and a local ``rsync -r|-a``
  destination.
* Python literals: ``rmtree('...')`` / ``rmtree(path='...')``, ``removedirs``, ``copytree(src, '...')``
  and ``shutil.move('...', '...')``, with ``r``/``b``/``u`` prefixes, single, double or triple quotes
  and a space before the parenthesis, wherever they appear in the command or in code handed to a code
  tool.

Commands wrapped in ``bash``/``sh``/``zsh`` (``-c`` in any position among the flags, ``-lc``),
``cmd /c``, ``powershell``/``pwsh`` (``-Command`` or any prefix of it, ``/c``, or no flag at all, where
the first argument is the command), and ``eval`` are read as the script they run, as the rest of the
floor does. ``sudo``, ``doas``, ``env`` (with ``VAR=x`` words), ``nohup``, ``command``, ``exec``,
``time``, ``nice``, ``timeout`` and the shell keywords ``if``/``then``/``do``/``else`` in front of a
command are skipped to find it. A ``(`` or ``{`` stuck to the command and a trailing ``)`` or ``}``
are taken off, a standalone ``{`` or ``}`` ends a command, and a lone carriage return is a line end.

**What it still does NOT read** (each is a way past it, listed so nobody assumes otherwise):

* a path that comes by a pipe or as the output of another command (``ls | xargs rm -rf``,
  ``find ... -exec rm -r {} +``), and anything inside ``$(...)`` or backticks;
* the program a script file or another program starts (``./clean.sh``, ``make clean``, ``npm run``),
  an ``-File`` of PowerShell, ``-EncodedCommand`` (the floor refuses that one on its own);
* a variable assigned earlier and used as a Python or shell path; the Python call ``os.rename``,
  ``os.remove`` loops and ``pathlib`` (``Path(...).rmdir()``), ``shutil.rmtree(pathlib.Path(...))``;
* a wrapper this module does not name (``wsl``, ``ssh``, ``docker exec``, ``xargs``, ``su -c``);
* quoting that splits the word (``r""m``), which the kernel's own RuleSet has as a separate, open
  finding;
* the shell's own expansion of a glob: ``rm -rf *`` is read as the text ``*`` inside the directory
  it runs in, not as the files it would match, and a glob is NOT treated as an unknown place;
* single-file writes and deletes without ``-r``: an ordinary file is released by design.
"""

from __future__ import annotations

import os
import posixpath
import re
import shlex
from collections.abc import Callable, Iterator
from pathlib import Path

_SEPARATORS = frozenset({";", "&", "&&", "|", "||", "\n"})
_POSIX_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_MAX_NESTING = 4
_MAX_DIRECTORIES = 24
#: How many wrapper readings one command may cost, and how deep they may nest, before it is given up on.
_MAX_UNWRAPS = 64
#: A command longer than this is asked about without being read: reading it is not worth the time.
MAX_COMMAND_CHARS = 16_000
_MAX_UNWRAP_DEPTH = 8

_PS_REMOVE = frozenset({"remove-item", "ri", "rm", "del", "erase", "rmdir", "rd"})
_PS_COPY = frozenset({"copy-item", "cpi", "copy", "cp"})
_PS_MOVE = frozenset({"move-item", "mi", "move"})
_PS_PATH_FLAGS = frozenset({"-path", "-literalpath", "-lp", "-pspath"})
_PS_DEST_FLAGS = frozenset({"-destination", "-dest", "-d"})
#: PowerShell switches that take a value, so the value is not a positional path.
_PS_VALUE_FLAGS = frozenset({
    "-filter", "-include", "-exclude", "-credential", "-stream", "-erroraction", "-ea",
    "-warningaction", "-wa", "-confirm", "-whatif",
})
#: ``powershell`` parameters that take a value, so the value is not mistaken for the command.
_PS_HOST_VALUE_PARAMS = (
    "executionpolicy", "inputformat", "outputformat", "windowstyle", "configurationname", "version",
    "workingdirectory", "psconsolefile", "settingsfile", "custompipename",
)
_PS_HOST_VALUE_ALIASES = frozenset({"ep", "w", "wd", "wo", "v"})

_CD = frozenset({"cd", "chdir", "set-location", "sl", "pushd", "push-location"})

#: A wrapper in front of a command: ``(valued, plain)``, the options that take an argument and the
#: ones that do not. An option a wrapper is not known to have makes the command unplaceable.
_Spec = tuple[frozenset[str], frozenset[str]]


def _spec(valued: str, plain: str) -> _Spec:
    return frozenset(valued.split()), frozenset(plain.split())


_KEYWORD: _Spec = _spec("", "")
_PREFIXES: dict[str, _Spec] = {
    "sudo": _spec(
        "-u -g -C -D -h -p -r -t -U -T --user --group --close-from --chdir --host --prompt --role "
        "--type --other-user --command-timeout",
        "-A -b -E -e -H -i -K -k -l -n -P -S -s -V -v -B --askpass --background --preserve-env "
        "--edit --set-home --login --reset-timestamp --remove-timestamp --non-interactive "
        "--preserve-groups --stdin --shell --bell --version --validate --list --",
    ),
    "doas": _spec("-u -C", "-n -s"),
    "env": _spec(
        "-u -C -S -P -a --unset --chdir --split-string --argv0",
        "-i -0 -v --ignore-environment --null --debug",
    ),
    "nohup": _spec("", ""),
    "command": _spec("", "-p -v -V"),
    "exec": _spec("-a", "-c -l"),
    "time": _spec("-f -o --format --output", "-p -v -a --portability --verbose --append"),
    "nice": _spec("-n --adjustment", ""),
    "timeout": _spec("-s -k --signal --kill-after", "-f -p -v --foreground --preserve-status --verbose"),
    "builtin": _spec("", ""),
    "setsid": _spec("", "-c -f -w --ctty --fork --wait"),
    "stdbuf": _spec("-i -o -e --input --output --error", ""),
    "then": _KEYWORD, "do": _KEYWORD, "else": _KEYWORD, "if": _KEYWORD, "elif": _KEYWORD,
    "while": _KEYWORD, "until": _KEYWORD, "!": _KEYWORD,
}
#: What a wrapper's option does besides take a value: change the directory, or hand over a script.
_WRAPPER_CHDIR = {"sudo": frozenset({"-D", "--chdir"}), "env": frozenset({"-C", "--chdir"})}
_WRAPPER_SCRIPT = {"env": frozenset({"-S", "--split-string"})}
#: A directory no text names: ``cd`` to it makes every relative path after it outside.
_NOWHERE = "$unresolvable"
#: A command that deletes a place no text names: what a reading gives when it is given up on.
_GIVEN_UP = ["rm", "-r", _NOWHERE]
_ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")

_LITERAL = re.compile(r"""(?:[rRbBuUfF]{1,2})?('''|\"\"\"|'|")(.*?)\1""", re.DOTALL)
_PY_CALL = re.compile(r"""\b(rmtree|removedirs|copytree|shutil\.move)\s*\(""")

_HOME_VARIABLES = re.compile(
    r"^(?:\$\{?home\}?|\$env:home|\$env:userprofile|%userprofile%|%homedrive%%homepath%)(?=$|[\\/])",
    re.IGNORECASE,
)
#: A place this rule cannot follow: a variable, a command substitution, a brace expansion.
_UNRESOLVABLE = re.compile(r"\$|%[^%]+%|[{}]")
_DRIVE = re.compile(r"^[a-z]:(?:[\\/]|$)", re.IGNORECASE)
#: ``C:Windows`` is relative to the current directory ON drive C, which the text cannot place.
_DRIVE_RELATIVE = re.compile(r"^[a-z]:(?![\\/]|$)", re.IGNORECASE)
#: A cmd.exe switch (``/s``, ``/q``, ``/mir``), as opposed to a POSIX path that starts with a slash.
_CMD_SWITCH = re.compile(r"^/[a-z?][a-z0-9:]*$", re.IGNORECASE)
_POSIX_C_FLAG = re.compile(r"^-[A-Za-z]*c[A-Za-z]*$")

#: A directory a relative path is placed against: ``(base, prefix)`` as :func:`_base` gives, or
#: ``None`` for a directory this rule cannot name (every relative path from it is outside).
_Cwd = tuple[str, str] | None


def reaches_outside(
    command: str,
    *,
    workspace: Path | str | None,
    home: Path | str | None = None,
    cwd: Path | str | None = None,
) -> bool:
    """Whether ``command`` deletes or writes recursively outside the workspace and the home."""
    if len(command) > MAX_COMMAND_CHARS:
        return True
    try:
        return _reaches_outside(command, workspace=workspace, home=home, cwd=cwd)
    except Exception:  # fail closed: a command this rule cannot read is asked about
        return True


def _reaches_outside(
    command: str,
    *,
    workspace: Path | str | None,
    home: Path | str | None,
    cwd: Path | str | None,
) -> bool:
    home_path = str(home) if home is not None else str(Path.home())

    def step(directories: list[_Cwd], args: list[str]) -> list[_Cwd]:
        return _after_cd(directories, args, workspace=workspace, home=home_path)

    return any(
        _outside(target, workspace=workspace, home=home_path, cwd=directory)
        for target, directories in _scan(command, [_base(workspace, cwd)], step)
        for directory in directories
    )


def _base(workspace: Path | str | None, cwd: Path | str | None) -> tuple[str, str]:
    """Where a relative target is placed, as ``(base, prefix)``.

    The shell tool's ``cwd`` is RELATIVE to the workspace (``ShellTool._resolve_cwd`` joins it
    there), so a relative ``cwd`` is joined to the workspace too. Read raw, ``cwd='src'`` made
    ``rm -rf build`` look like a path outside everything and every cleanup run with a ``cwd`` came
    back as a question. With no workspace known, the relative ``cwd`` becomes a ``prefix`` of the
    target, so only a path that climbs out with ``..`` counts as outside.
    """
    cwd_text = str(cwd) if cwd else ""
    if cwd_text and _looks_absolute(cwd_text.strip()):
        return cwd_text, ""
    if workspace:
        ws = str(workspace).rstrip("/\\")
        return (ws + "/" + cwd_text if cwd_text else ws), ""
    return "", cwd_text


def recursive_targets(command: str) -> list[str]:
    """Every path a recursive delete or write in ``command`` names, as written."""
    found: list[str] = []
    for target, _directories in _scan(command, [None], lambda directories, _args: directories):
        if target not in found:
            found.append(target)
    return found


def _scan(
    command: str, start: list[_Cwd], step: Callable[[list[_Cwd], list[str]], list[_Cwd]],
) -> list[tuple[str, tuple[_Cwd, ...]]]:
    """Each target with every directory the command may have been in when it got there.

    ``step`` takes the directories the command may be in and the arguments of a ``cd`` and returns the
    ones it may be in after it. A target is placed against EVERY directory the command has been in so
    far, the start included (so ``cd build; cd ..`` is back where it began and ``(cd /); rm -rf x``,
    a subshell the rule cannot see, still asks). A Python literal has no position in the shell
    script, so it gets every directory seen.
    """
    found: list[tuple[str, tuple[_Cwd, ...]]] = []
    seen: list[_Cwd] = list(start)
    for reading in _readings(command):
        frontier = list(start)
        visited = list(start)
        for tokens in _segments(reading):
            if _exe(tokens[0]) in _CD:
                frontier = step(frontier, tokens[1:])
                visited.extend(d for d in frontier if d not in visited)
                seen.extend(d for d in frontier if d not in seen)
                continue
            found.extend((t, tuple(visited)) for t in _shell_targets(tokens))
    found.extend((t, tuple(seen)) for t in _python_targets(command))
    return found


# --- reading the command ------------------------------------------------------------------------


def _readings(command: str) -> list[str]:
    r"""The command read with its line continuations joined, and read with them as line breaks.

    A backslash or a backtick before a line break continues the line in bash and PowerShell, but
    a Windows path may also end in a backslash (``cd C:\work\`` then a delete on the next line).
    Joining always would hide that delete behind ``cd``; never joining would cut
    ``rm -rf \`` / ``/opt/x`` in two. Both readings are taken and a target found in either one
    counts: an unclear command is asked about, not assumed safe. In the second reading the marker
    itself is dropped, so a lone ``\`` left at the end of a line is not read as the drive's root.
    """
    joined = _CONTINUATION.sub(" ", command)
    if joined == command:
        return [command]
    return [joined, _CONTINUATION.sub("\n", command)]


def _segments(command: str, depth: int = 0) -> Iterator[list[str]]:
    """Each simple command, quotes taken off, with the scripts of shell wrappers read in turn."""
    for segment in _split(command):
        yield segment
        nested = nested_script(segment)
        if not nested:
            continue
        if depth >= _MAX_NESTING:
            yield _GIVEN_UP  # too deep to read: given up on, and that is "outside", never "fine"
            continue
        yield from _segments(nested, depth + 1)


#: A line continuation: a backslash (POSIX shells) or a backtick (PowerShell) right before the break.
_CONTINUATION = re.compile(r"[\\`][ \t]*(?:\r\n|\r|\n)")
#: What separates two simple commands: the shell operators and a line break (``\r`` alone is one too).
_BREAKS = ";&|\n\r"
_CLOSER = {"(": ")", "{": "}"}
_OPENER = {")": "(", "}": "{"}


def _split(command: str) -> list[list[str]]:
    """The simple commands in ``command``, one token list each.

    A line break separates commands, as ``;`` does. With ``whitespace_split`` the lexer used to eat
    the break as plain whitespace, so ``cd build`` on one line and ``Remove-Item -Recurse -Force
    C:/Users/x`` on the next became ONE command whose executable was ``cd``, and the delete was
    released: a multi-line script, the usual way an agent writes to ``run_shell``, slipped past the
    rule that the same command on one line did not. The break is therefore taken out of the
    lexer's whitespace and made punctuation; inside quotes it stays part of the quoted word. Line
    continuations are handled by :func:`_readings`, before this.
    """
    lexer = shlex.shlex(command, posix=False, punctuation_chars=_BREAKS)
    lexer.whitespace = " \t"
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        raw = list(lexer)
    except ValueError:
        raw = [t for line in command.splitlines() for t in (*line.split(), "\n")]
    segments: list[list[str]] = [[]]
    for token in _glue_quoted_values(raw):
        if _is_separator(token):
            segments.append([])
            continue
        splits, text = _peel(token)
        if splits:
            segments.append([])
        if not text:
            if "{" in token or "}" in token:
                segments.append([])
            continue
        segments[-1].append(_unquote(text))
    budget = [_MAX_UNWRAPS]
    return [piece for segment in segments if segment for piece in _expand(segment, budget, 0)]


_LONG_OPTION_QUOTE = re.compile(r"^(?:--[\w-]+=|-[A-Za-z])(['\"])(.*)$", re.DOTALL)


def _is_separator(token: str) -> bool:
    return token in _SEPARATORS or set(token) <= set(_BREAKS)


def _glue_quoted_values(raw: list[str]) -> list[str]:
    """Join ``--opt='a b'`` back into one word: the lexer opens a quote only at the start of a word.

    Only a token that is EXACTLY a long option, ``=``, and a quote that does not close in the same
    token (``--split-string='rm``) is joined, to the first later token holding the same quote, and the
    join never crosses a separator (``;``, ``&&``, ``|``, ``&``, a line break). Counting quotes across
    a command line was the first version, and ``echo "x='"; rm -r /srv/x; echo "'"`` made it swallow
    the delete into one word. If the quote does not close before a separator, nothing is joined.
    """
    out: list[str] = []
    index = 0
    while index < len(raw):
        token = raw[index]
        index += 1
        match = _LONG_OPTION_QUOTE.match(token)
        if match and match.group(1) not in match.group(2):
            quote = match.group(1)
            joined, scan = token, index
            while scan < len(raw) and not _is_separator(raw[scan]):
                joined += " " + raw[scan]
                scan += 1
                if quote in raw[scan - 1]:
                    token, index = joined, scan
                    break
        out.append(token)
    return out


def _peel(token: str) -> tuple[bool, str]:
    """Take off the grouping a token carries that is not part of the word.

    ``(rm``, ``{Remove-Item`` and ``/etc)`` are the words ``rm``, ``Remove-Item`` and ``/etc`` with
    punctuation stuck to them. Only an UNBALANCED bracket is taken off, so ``{a,b}`` and ``$(pwd)``
    stay whole. A leading ``{`` starts a new command (the block's body); a ``(`` does not, because
    ``Remove-Item (Join-Path a b) -Recurse`` is one command whose argument is parenthesised.
    """
    splits = False
    while token and token[0] in "({" and token.count(token[0]) > token.count(_CLOSER[token[0]]):
        splits = splits or token[0] == "{"
        token = token[1:]
    while token and token[-1] in ")}" and token.count(token[-1]) > token.count(_OPENER[token[-1]]):
        token = token[:-1]
    return splits, token


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def _classify(flag: str, spec: _Spec, name: str) -> tuple[str, str, str | None]:
    """``(kind, head, attached)`` of a wrapper option.

    ``kind`` is ``valued``, ``plain`` or ``unknown``; ``head`` is the option itself; ``attached`` is the
    value written in the same word, or ``None`` when it is the next word. ``--signal=TERM`` is
    ``--signal`` with ``TERM`` attached. A cluster of one-letter options reads left to right: plain
    letters, then at most one that takes a value, and what follows that letter IS its value
    (``-Srm``, ``-Cdir``, ``-uroot``, ``-s9``, ``-k5``).
    """
    valued, plain = spec
    head, equals, inline = flag.partition("=")
    if head in valued:
        return "valued", head, inline if equals else None
    if head in plain:
        return "plain", head, None
    if name == "nice" and re.fullmatch(r"-\d+", flag):
        return "plain", head, None
    if not flag.startswith("--") and len(flag) > 2:
        for position, letter in enumerate(flag[1:], 1):
            key = "-" + letter
            if key in valued:
                return "valued", key, flag[position + 1:] or None
            if key not in plain:
                return "unknown", head, None
        return "plain", flag, None
    return "unknown", head, None


def _unwrap(tokens: list[str]) -> tuple[list[str], list[list[str]]]:
    """The command inside ``sudo``/``env``/``timeout``/``then`` words, and what those words do.

    The second part is commands of their own, in the order they happen: a ``cd`` for ``env -C DIR``
    and ``sudo -D DIR`` (the command runs there), an ``eval`` for ``env -S 'script'`` (the script is
    the command), and, when a wrapper has an option this rule does not know, a ``cd`` to nowhere plus
    every tail of the words after it as a possible command: where the command starts is then unknown,
    so its relative paths count as outside and each later word is tried as the executable.
    """
    extra: list[list[str]] = []
    index = 0
    while index < len(tokens):
        if _ASSIGNMENT.match(tokens[index]):
            index += 1
            continue
        name = _exe(tokens[index])
        spec = _PREFIXES.get(name)
        if spec is None:
            break
        index += 1
        if spec is _KEYWORD:
            continue
        while index < len(tokens) and tokens[index].startswith("-") and len(tokens[index]) > 1:
            flag = tokens[index]
            index += 1
            if flag == "--":
                break
            kind, head, attached = _classify(flag, spec, name)
            if kind == "unknown":
                extra.append(["cd", _NOWHERE])
                if len(tokens) - index > _MAX_UNWRAPS // 2:
                    extra.append(_GIVEN_UP)  # too many places the command could start: given up on
                else:
                    extra.extend(tokens[later:] for later in range(index, len(tokens)))
                return [], extra
            if kind == "plain":
                continue
            if attached is not None:
                value = _unquote(attached)
            else:
                value = tokens[index] if index < len(tokens) else ""
                index += 1
            if head in _WRAPPER_CHDIR.get(name, ()):
                extra.append(["cd", value or _NOWHERE])
            elif head in _WRAPPER_SCRIPT.get(name, ()):
                # `env -S 'rm -r' /srv/x` runs `rm -r /srv/x`: the words after the string are appended
                # to what it splits into, so they are part of the script, not a command of their own.
                # A space written as `\ ` inside the word is a space of the script, not a word break.
                extra.append(["eval", " ".join((value, *tokens[index:])).replace("\\ ", " ")])
                return [], extra
        if name == "timeout" and index < len(tokens):
            index += 1  # the duration
    return tokens[index:], extra


def _expand(tokens: list[str], budget: list[int], depth: int) -> list[list[str]]:
    """``tokens`` as the commands it runs, wrappers unwrapped (see :func:`_unwrap`).

    Reading is capped (:data:`_MAX_UNWRAPS` readings, :data:`_MAX_UNWRAP_DEPTH` deep): past the cap the
    command is given up on and the answer is "outside" (a delete of a place no text names), never an
    exception and never a release.
    """
    budget[0] -= 1
    if budget[0] < 0 or depth > _MAX_UNWRAP_DEPTH:
        return [_GIVEN_UP]
    command, extra = _unwrap(tokens)
    out = [piece for segment in extra for piece in _expand(segment, budget, depth + 1)]
    if command:
        out.append(command)
    return out


def is_wrapper_word(token: str) -> bool:
    """Whether ``token`` is a word like ``sudo``/``env``/``timeout`` that runs the command after it."""
    return _exe(token) in _PREFIXES


def may_wrap(token: str) -> bool:
    """Whether :func:`nested_script` could find a script after ``token``: a cheap test, so a caller
    walking every token of a long command does not slice the rest of it at each one."""
    exe = _exe(token)
    return exe in _PREFIXES or exe in _POSIX_SHELLS or exe in {"eval", "cmd", "powershell", "pwsh"}


def nested_script(tokens: list[str]) -> str:
    """The script a shell wrapper at ``tokens[0]`` runs, or ''.

    Shared with the rest of the floor (``bypass.py``), so a force push, a secret or an encoded command
    inside ``bash --noprofile -c '...'``, ``powershell -Comm "..."`` or ``eval '...'`` is read the same way.
    """
    exe, rest = _exe(tokens[0]), tokens[1:]
    if exe in _PREFIXES:
        command, extra = _unwrap(tokens)
        script = next((" ".join(seg[1:]) for seg in extra if seg[0] == "eval" and len(seg) > 1), "")
        return script or (nested_script(command) if command else "")
    if exe == "eval":
        return " ".join(rest)
    if exe in _POSIX_SHELLS:
        for index, flag in enumerate(rest):
            if _POSIX_C_FLAG.match(flag):
                return " ".join(rest[index + 1:])
        return ""
    if exe == "cmd":
        for index, flag in enumerate(rest):
            if flag.casefold() in {"/c", "/k"}:
                return " ".join(rest[index + 1:])
        return ""
    if exe in {"powershell", "pwsh"}:
        return _powershell_script(rest)
    return ""


def _powershell_script(rest: list[str]) -> str:
    """What ``powershell`` runs: after ``-Command`` (or a prefix, or ``/c``), else its first argument.

    PowerShell accepts any unambiguous prefix of a parameter name, so ``-Co``/``-Comm`` are ``-Command``,
    and a first argument that is not a parameter is the command with no flag at all, the common spelling
    ``powershell "Remove-Item ..."``. Parameters that take a value (``-ExecutionPolicy Bypass``) are
    skipped with it; ``-File`` runs a script this rule cannot read.
    """
    index = 0
    while index < len(rest):
        arg = rest[index]
        if len(arg) > 1 and arg[0] in "-/":
            name, colon, inline = arg[1:].partition(":")
            lowered = name.casefold()
            if lowered and "command".startswith(lowered):
                head = [inline] if colon and inline else []
                return " ".join([*head, *rest[index + 1:]])
            if lowered and "file".startswith(lowered):
                return ""
            takes_value = lowered in _PS_HOST_VALUE_ALIASES or (
                bool(lowered) and any(p.startswith(lowered) for p in _PS_HOST_VALUE_PARAMS)
            )
            index += 2 if takes_value and not colon else 1
            continue
        return " ".join(rest[index:])
    return ""


def _exe(token: str) -> str:
    return re.split(r"[/\\]", token.casefold())[-1].removesuffix(".exe")


def _shell_targets(tokens: list[str]) -> list[str]:
    return [piece for target in _targets_of(tokens) for piece in _comma_pieces(target)]


def _comma_pieces(target: str) -> list[str]:
    """``a,C:/x`` is one word to bash but a list to PowerShell: each piece is checked."""
    if "," not in target or target.startswith("{"):
        return [target]
    return [piece for piece in target.split(",") if piece]


def _targets_of(tokens: list[str]) -> list[str]:
    exe, args = _exe(tokens[0]), tokens[1:]
    lowered = [a.casefold() for a in args]
    if exe == "rm" and _posix_recursive(args):
        return _positional(args)
    if exe in {"rmdir", "rd", "del", "erase"} and "/s" in lowered:
        return [a for a in args if not _CMD_SWITCH.match(a)]
    if exe == "find" and "-delete" in lowered:
        first_expression = next((i for i, a in enumerate(args) if a.startswith("-")), len(args))
        return args[:first_expression]
    if exe == "cp" and _posix_recursive(args, archive=True):
        return _destination(args)
    if exe == "mv":
        sources = [a for a in _positional(args) if not _UNRESOLVABLE.search(a)]
        return [*sources, *_destination(args)]
    if exe in {"robocopy", "xcopy"}:
        switches = {a for a in lowered if _CMD_SWITCH.match(a)}
        if exe == "robocopy" or switches & {"/s", "/e"}:
            return [a for a in args if not _CMD_SWITCH.match(a)][1:2]
    if exe == "rsync" and _posix_recursive(args, archive=True):
        dest = _positional(args)[-1:]
        return [d for d in dest if ":" not in d or _DRIVE.match(d)]
    return _powershell_targets(exe, args)


def _destination(args: list[str]) -> list[str]:
    """Where ``cp``/``mv`` write: ``-t DIR``, ``-tDIR``, ``--target-directory[=]DIR``, else the last word."""
    positional = _positional(args)
    for index, arg in enumerate(args):
        if arg == "--":
            break
        value: str | None = None
        if arg.startswith("--target-directory"):
            _, equals, inline = arg.partition("=")
            if equals:
                value = inline
            elif arg == "--target-directory" and index + 1 < len(args):
                value = args[index + 1]
        elif arg.startswith("-") and not arg.startswith("--") and "t" in arg[1:]:
            tail = arg[arg.index("t") + 1:]
            value = tail or (args[index + 1] if index + 1 < len(args) else "")
        if value is not None:
            return [value]
    return positional[-1:]


def _powershell_targets(exe: str, args: list[str]) -> list[str]:
    recurse = _ps_recursive(args)
    if exe in _PS_REMOVE and recurse:
        return _ps_values(args, _PS_PATH_FLAGS) or _ps_positional(args)
    if exe in _PS_COPY and recurse:
        dest = _ps_values(args, _PS_DEST_FLAGS)
        return dest or _ps_positional(args)[1:2]
    if exe in _PS_MOVE:
        dest = _ps_values(args, _PS_DEST_FLAGS)
        return (_ps_values(args, _PS_PATH_FLAGS) or _ps_positional(args)[:1]) + (dest or _ps_positional(args)[1:2])
    return []


def _ps_recursive(args: list[str]) -> bool:
    """``-Recurse`` in any prefix PowerShell accepts, with or without a value (``-Recurse:$true``)."""
    for arg in args:
        name, colon, value = arg.partition(":")
        lowered = name.casefold()
        if len(lowered) > 1 and "-recurse".startswith(lowered):
            if colon and value.casefold() in {"$false", "false", "0"}:
                continue  # an explicit off switch is a non-recursive call
            return True
    return False


def _posix_recursive(args: list[str], *, archive: bool = False) -> bool:
    for arg in args:
        if arg == "--":
            break
        if arg.startswith("--"):
            if arg in {"--recursive", "--archive"} and (archive or arg == "--recursive"):
                return True
            continue
        if arg.startswith("-") and len(arg) > 1 and ("r" in arg[1:] or "R" in arg[1:] or archive and "a" in arg[1:]):
            return True
    return False


def _positional(args: list[str]) -> list[str]:
    out: list[str] = []
    rest = False
    for arg in args:
        if rest or not arg.startswith("-") or arg == "-":
            out.append(arg)
        elif arg == "--":
            rest = True
    return out


def _ps_list(args: list[str], start: int, first: str | None = None) -> tuple[list[str], int]:
    """A PowerShell list from ``start``: ``a,b``, ``a, b`` and ``a ,b`` are all the list ``a b``.

    ``first`` is a value already read from the same word (``-Path:a,``). Returns the items and the
    index of the first word after the list.
    """
    items: list[str] = []
    index = start
    piece = first
    while True:
        if piece is None:
            if index >= len(args):
                break
            piece = args[index]
            index += 1
        items.extend(p for p in piece.split(",") if p)
        more = piece.endswith(",") or (index < len(args) and args[index].startswith(","))
        piece = None
        if not more or index >= len(args):
            break
    return items, index


def _ps_values(args: list[str], flags: frozenset[str]) -> list[str]:
    out: list[str] = []
    for index, arg in enumerate(args):
        flag, sep, value = arg.partition(":")
        if flag.casefold() in flags:
            if sep and value:
                out.extend(_ps_list(args, index + 1, value)[0])
            elif index + 1 < len(args):
                out.extend(_ps_list(args, index + 1)[0])
    return out


def _ps_positional(args: list[str]) -> list[str]:
    """The words that are neither a switch nor a switch's value; a list split by spaces is one word."""
    out: list[str] = []
    index = 0
    joined = False
    while index < len(args):
        arg = args[index]
        index += 1
        if arg.startswith("-") and len(arg) > 1:
            joined = False
            name = arg.partition(":")[0].casefold()
            if name in _PS_VALUE_FLAGS | _PS_PATH_FLAGS | _PS_DEST_FLAGS and ":" not in arg:
                index = _ps_list(args, index)[1]
            continue
        if joined or arg.startswith(","):
            out[-1] += arg
        else:
            out.append(arg)
        joined = arg.endswith(",")
    return out


# --- Python literals ----------------------------------------------------------------------------


def _python_targets(text: str) -> list[str]:
    """The string literals in the argument list of each ``rmtree``/``copytree``/``move`` call.

    Every literal inside the parentheses counts, so ``rmtree(path='x')`` and
    ``rmtree('x', ignore_errors=True)`` are read alike. ``copytree`` writes only its destination, so
    a literal that is PROVABLY the source is left out: the one named ``src=``, or the first argument
    of the call when it is a bare literal followed by a comma. Anything less certain is checked.
    """
    found: list[str] = []
    for call in _PY_CALL.finditer(text):
        literals = _call_literals(text, call.end())
        if call.group(1) == "copytree":
            literals = [lit for lit in literals if not lit.is_source]
        found.extend(lit.text for lit in literals if lit.text not in found)
    return found


class _Literal:
    """A string literal in a call: its text, and whether it is surely ``copytree``'s source."""

    __slots__ = ("is_source", "text")

    def __init__(self, text: str, *, is_source: bool) -> None:
        self.text = text
        self.is_source = is_source


_KEYWORD_BEFORE = re.compile(r"(\w+)\s*=\s*$")
_COMMA_AFTER = re.compile(r"\s*,")


def _call_literals(text: str, start: int) -> list[_Literal]:
    """The string literals between ``start`` (just after an opening parenthesis) and its closing one."""
    literals: list[_Literal] = []
    depth, position = 1, start
    first_argument = True
    while position < len(text) and depth:
        literal = _LITERAL.match(text, position)
        if literal and (position == start or not text[position - 1].isalnum()):
            keyword = _KEYWORD_BEFORE.search(text, start, position)
            bare_first = (
                depth == 1 and first_argument and keyword is None
                and _COMMA_AFTER.match(text, literal.end()) is not None
            )
            literals.append(_Literal(
                literal.group(2),
                is_source=bool(depth == 1 and keyword and keyword.group(1) == "src") or bare_first,
            ))
            first_argument = False
            position = literal.end()
            continue
        if not text[position].isspace():
            first_argument = False
        depth += {"(": 1, ")": -1}.get(text[position], 0)
        position += 1
    return literals


# --- placing a target ---------------------------------------------------------------------------


def _outside(
    target: str, *, workspace: Path | str | None, home: str, cwd: _Cwd,
) -> bool:
    resolved = _resolve(target, home=home, cwd=cwd)
    if resolved is None:
        return True  # a place this rule cannot follow: asked about, never assumed inside
    if resolved == "":
        return False  # relative, no base known, does not climb out: where the agent runs
    real = _place(resolved)
    if workspace and _within(real, _place(str(workspace))):
        return False
    home_norm = _place(home)
    return not (real != home_norm and _within(real, home_norm))


def _resolve(target: str, *, home: str, cwd: _Cwd) -> str | None:
    text = target.strip()
    if not text:
        return ""
    if text == "~" or text.startswith(("~/", "~\\")):
        text = home + text[1:]
    else:
        text = _HOME_VARIABLES.sub(lambda _m: home.replace("\\", "/"), text, count=1)
    if _UNRESOLVABLE.search(text) or _DRIVE_RELATIVE.match(text):
        return None
    if text.startswith("~"):
        return None  # `~bob/x`: another user's home, which the text does not say where it is
    if _looks_absolute(text):
        return text
    if cwd is None:
        return None  # relative to a directory this rule lost track of
    base, prefix = cwd
    if base:
        return base.rstrip("/\\") + "/" + text
    if prefix:
        text = prefix.rstrip("/\\") + "/" + text
    climbed = posixpath.normpath(text.replace("\\", "/"))
    return None if climbed == ".." or climbed.startswith("../") else ""


def _after_cd(
    directories: list[_Cwd], args: list[str], *, workspace: Path | str | None, home: str,
) -> list[_Cwd]:
    """Where a command may be once a ``cd`` has run from any of ``directories``."""
    target = _cd_target(args)
    reached: list[_Cwd] = []
    for directory in directories:
        moved = _cd_to(directory, target, workspace=workspace, home=home)
        if moved not in reached:
            reached.append(moved)
    return reached if len(reached) <= _MAX_DIRECTORIES else [None]


def _cd_target(args: list[str]) -> str | None:
    values = _ps_values(args, _PS_PATH_FLAGS)
    if values:
        return values[0]
    rest = [a for a in _positional(args) if a.casefold() != "/d"]
    return rest[0] if rest else None


def _cd_to(
    directory: _Cwd, target: str | None, *, workspace: Path | str | None, home: str,
) -> _Cwd:
    """Where a ``cd`` leads: a directory inside the workspace, or ``None``, the unknown place.

    A ``cd`` to anywhere outside the workspace is not followed: every relative path after it is
    asked about, which is what a command running from there deserves. So is ``cd -``, a bare ``cd``
    (the home) and a destination built from a variable.
    """
    if directory is None or target is None or target.strip() in {"", "-"}:
        return None
    resolved = _resolve(target, home=home, cwd=directory)
    if resolved is None:
        return None
    if resolved == "":  # no workspace known and the path stays under the relative prefix
        joined = (directory[1].rstrip("/\\") + "/" if directory[1] else "") + target.replace("\\", "/")
        return "", posixpath.normpath(joined)
    real = _place(resolved)
    if workspace:
        inside = _within(real, _place(str(workspace)))
    else:
        home_norm = _place(home)
        inside = real != home_norm and _within(real, home_norm)
    return (real, "") if inside else None


def _looks_absolute(text: str) -> bool:
    return text.startswith(("/", "\\")) or bool(_DRIVE.match(text))


def _norm(path: str) -> str:
    # `normpath` keeps exactly two leading slashes, so a UNC path stays one.
    text = posixpath.normpath(path.replace("\\", "/"))
    if _DRIVE.match(text) or os.name == "nt":
        text = text.casefold()
    return text.rstrip("/") or "/"


def _place(path: str) -> str:
    """Where ``path`` lands: walked one component at a time, links followed AS THEY ARE MET.

    ``..`` is applied to the directory the walk has really reached, not to the text before it: with
    ``proj/linked -> /srv/tree``, ``proj/linked/../victim`` is ``/srv/victim`` (the parent of the link's
    target), and collapsing the ``..`` first read it as ``proj/victim``. A component that does not exist
    is kept as written (a destination about to be created); an error resolving one leaves it as written.
    """
    text = path.replace("\\", "/")
    match = re.match(r"^([A-Za-z]:)?(/*)", text)
    assert match is not None
    drive, slashes = match.group(1) or "", match.group(2)
    root = drive + ("//" if len(slashes) == 2 and not drive else "/" if slashes else "")
    current = root
    for part in text[match.end():].split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            parent = posixpath.dirname(current.rstrip("/"))
            current = parent if parent and parent not in (drive, current.rstrip("/")) else root
            continue
        current = _follow(current.rstrip("/") + "/" + part)
    return _norm(current)


def _follow(candidate: str) -> str:
    if not os.path.lexists(candidate):
        return candidate
    try:
        return os.path.realpath(candidate).replace("\\", "/")
    except (OSError, ValueError):
        return candidate


def _within(path: str, root: str) -> bool:
    root = root.rstrip("/") or "/"
    return path == root or path.startswith(root if root.endswith("/") else root + "/")
