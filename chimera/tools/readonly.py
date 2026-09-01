"""Commands that can be PROVED to only read, so the gate stops asking about `git status`.

`CHIMERA_HOST_EXEC=ask` asks about every command equally: `ls`, `git status` and `rm -rf /` all get
the same prompt. In a working session that is dozens of prompts an hour for commands that cannot
change anything, and the pressure it creates has one outlet — `CHIMERA_HOST_EXEC=allow`, which
removes the gate entirely. A gate people turn off protects nothing, so the cost of asking too often
is measured in the questions it stops anyone from answering.

**This is an allowlist, and it says no by default.** It is not a denylist made safer: `policy.py`
keeps that job and keeps its own regexes. The two answer different questions — that one asks "is
this obviously destructive", this one asks "can I be certain this changes nothing" — and only the
second is allowed to skip a prompt. Anything not proved is `False`.

The shape of a wrong answer here is a command that runs unasked, so every rule below refuses on
doubt rather than resolving it:

- the line must tokenise cleanly with `shlex`, and carry no shell metacharacter — one `;`, `|`, a
  backtick, a redirect, and the string is more than one command;
- the base command must be in a short table;
- **every** flag must be in that command's own table. Not a prefix, not a heuristic: a flag nobody
  listed is a flag nobody checked.

The last rule is the one that matters, and it comes from a hard-won observation elsewhere: an
argument parser and a shell disagree in ways nobody predicts — `-A20` glued together, bundled short
flags, a tool that ignores `--`, an option that swallows the next token in one implementation and
not another. This module does not try to win those arguments. It refuses whenever it is not certain,
which makes every disagreement a prompt rather than an execution.
"""

from __future__ import annotations

import shlex

#: Anything that makes a line more than one command, or that writes. Present in the raw string is
#: enough — no attempt is made to decide whether a given `>` is "really" a redirect, because that
#: decision is exactly the class of judgement this module refuses to make.
_METACHARACTERS = (";", "|", "&", "`", "$(", ">", "<", "\n", "\r")

#: Per command, the flags that are certain to keep it read-only.
#:
#: Empty tuple means "this command takes no flags we have checked", not "any flag is fine". A flag
#: outside its command's tuple refuses the whole line.
_READONLY: dict[str, frozenset[str]] = {
    "pwd": frozenset(),
    "whoami": frozenset(),
    "date": frozenset(),
    "uname": frozenset({"-a", "-s", "-r", "-m"}),
    "ls": frozenset({"-l", "-a", "-la", "-al", "-h", "-lh", "-lah", "-1", "-t", "-r", "-R", "--color"}),
    "cat": frozenset({"-n"}),
    "head": frozenset({"-n"}),
    "tail": frozenset({"-n"}),
    "wc": frozenset({"-l", "-w", "-c"}),
    "file": frozenset(),
    "stat": frozenset(),
    "du": frozenset({"-h", "-s", "-sh"}),
    "df": frozenset({"-h"}),
    "which": frozenset(),
    # `echo` is deliberately ABSENT. It reads nothing and writes nothing, so it would be safe — and
    # it buys nothing, because nobody is worn down by prompts for `echo`. It is also the stand-in
    # every test in this repository uses for "some command", so allowlisting it would silently
    # approve the sample in a dozen suites whose subject is whether the gate refuses.
    # `rg`/`grep` write nothing, but their flag surface is wide and some of it executes — so only
    # the handful actually used for reading is listed.
    "rg": frozenset({"-n", "-i", "-l", "-c", "-w", "--no-heading", "--color", "-A", "-B", "-C"}),
    "grep": frozenset({"-n", "-i", "-l", "-c", "-w", "-r", "-R", "-E", "-F", "-v", "-A", "-B", "-C"}),
    "find": frozenset({"-name", "-type", "-maxdepth", "-iname", "-path"}),
}

#: Flags that take a VALUE, per command. A bundle of short flags is only safe when none of its
#: letters expects one: `-rn` is two booleans and reads exactly like `-r -n`, while `-An` would be
#: `-A n` in one parser and a bundle in another — and that disagreement is the thing this module
#: exists to refuse rather than resolve.
_TAKES_VALUE: dict[str, frozenset[str]] = {
    "head": frozenset({"-n"}),
    "tail": frozenset({"-n"}),
    "rg": frozenset({"-A", "-B", "-C"}),
    "grep": frozenset({"-A", "-B", "-C"}),
    "find": frozenset({"-name", "-iname", "-type", "-maxdepth", "-path"}),
}

#: Any git flag that names a file to write. `git diff --output=x` writes `x` — a read-only
#: subcommand with a writing flag, which is why the subcommand alone cannot be the whole answer.
_GIT_WRITES_A_FILE = ("--output", "-o=")

#: Read-only git subcommands, listed rather than derived. `git` is one binary with a hundred
#: behaviours, and "starts with git" says nothing at all about whether it writes.
_GIT_READONLY = frozenset({"status", "log", "diff", "show", "branch", "remote", "config", "blame",
                           "describe", "rev-parse", "ls-files", "shortlog", "stash"})

def is_provably_readonly(command: str) -> bool:
    """True only when this command certainly changes nothing on the machine.

    False is the answer for anything not proved, including anything this module has never heard of.
    A caller may use True to skip a confirmation prompt; it must never read False as "dangerous".
    """
    if not command or not command.strip():
        return False
    if any(marca in command for marca in _METACHARACTERS):
        return False
    try:
        partes = shlex.split(command)
    except ValueError:
        # An unbalanced quote. The shell and this parser would disagree about where the arguments
        # end, and a disagreement is exactly what must not be resolved by guessing.
        return False
    if not partes:
        return False

    base, resto = partes[0], partes[1:]
    if base == "git":
        return _git_is_readonly(resto)
    permitidas = _READONLY.get(base)
    if permitidas is None:
        return False
    com_valor = _TAKES_VALUE.get(base, frozenset())
    return all(_flag_ok(arg, permitidas, com_valor) for arg in resto)


def _flag_ok(arg: str, permitidas: frozenset[str], com_valor: frozenset[str]) -> bool:
    """Whether one argument is safe: a listed flag, or a plain (non-flag) operand.

    Three shapes are accepted and each has a reason.

    A listed flag, obviously. A NUMBER glued to a listed flag (`-A20`, `-n5`), because that is how
    these tools are actually typed and an allowlist that refuses it covers a vocabulary nobody uses.
    And a BUNDLE of short flags (`-rn`) when every letter is listed AND none of them takes a value —
    `-rn` reads exactly like `-r -n` in every parser, while `-An` is `-A n` in one and a bundle in
    another, which is the disagreement this module refuses rather than resolves.

    Anything else, including a single letter nobody listed, is False.
    """
    if not arg.startswith("-"):
        return True
    if arg in permitidas:
        return True
    # A long flag is either listed verbatim above or unknown, and the bundle rule below already
    # refuses it: `--x` leaves `-x` in `corpo`, whose first "letter" is `-`, and `--` is in nobody's
    # table. Written as an explicit early return first, and a sabotage that deleted it changed no
    # result — an unreachable guard reads as protection and is not, so it is gone.
    corpo = arg[1:]
    if not corpo:
        return False
    for i, ch in enumerate(corpo):
        if ch.isdigit():
            return corpo[i:].isdigit() and f"-{corpo[:i]}" in permitidas
    # A bundle: every letter must be listed on its own, and none may expect a value.
    return all(f"-{ch}" in permitidas and f"-{ch}" not in com_valor for ch in corpo)


def _git_is_readonly(args: list[str]) -> bool:
    """`git` is one binary with a hundred behaviours, so the subcommand decides and flags can veto."""
    sub = next((a for a in args if not a.startswith("-")), "")
    if sub not in _GIT_READONLY:
        return False
    # A read-only subcommand with a writing flag. `git diff --output=x` writes `x`, which is why
    # the subcommand alone cannot be the whole answer even for the ones that only ever read.
    if any(a.startswith(_GIT_WRITES_A_FILE) for a in args):
        return False
    resto = args[args.index(sub) + 1 :]
    if sub == "stash":
        # `git stash` with no argument SAVES. Only the listing forms read.
        return bool(resto) and resto[0] in {"list", "show"}
    if sub == "config":
        # `git config a.b` reads; `git config a.b value` writes. `--get`/`--list` are explicit.
        operandos = [a for a in resto if not a.startswith("-")]
        return len(operandos) <= 1 and not any(
            a in {"--add", "--unset", "--unset-all", "--replace-all", "--edit", "-e"} for a in resto
        )
    if sub in {"branch", "remote"}:
        # Listing only. Any flag at all is refused here rather than enumerated: `-d`, `-D`, `-m`,
        # `--set-upstream-to` all write, and the list of ways to write is longer than the list of
        # ways to read.
        return not resto or resto == ["-v"] or resto == ["--list"]
    return True
