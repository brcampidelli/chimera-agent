"""One test suite at a time per folder.

Measured 2026-10-29 to 31: pytest and vitest were started in the same folder at once. They shared a
database and build outputs and timed out for each other. The rule here is narrow: two commands that
are both test suites, running in the same effective folder, cannot both run. A build next to a
suite, a suite in another folder, and installing or asking for a version of a test runner are not
affected.

Three parts, kept apart so each is testable on its own:

- ``suite_label`` decides whether a command runs a suite, and which one.
- ``suite_folder_key`` gives the folder a suite runs in: a leading ``cd X &&`` and a ``--prefix`` /
  ``-C`` / ``--dir`` move it. When that cannot be resolved (an environment variable in the path), the
  key falls back to the normalised text, so two identical unresolvable commands still collide.
- ``SuiteSlots`` is the process-wide register of suites in progress. The check and the mark happen
  under one lock, so two conversations that start at the same moment cannot both pass. A foreground
  suite holds its slot until it returns; a background job holds it until the job leaves ``running``.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

#: Programs that are a test suite whatever arguments they get.
_SUITE_PROGRAMS = frozenset({"pytest", "py.test", "vitest", "jest", "unittest"})
#: Package managers whose exact `test` script is a suite. `test:e2e` is an arbitrary name, not one.
_PACKAGE_MANAGERS = frozenset({"npm", "pnpm", "yarn", "bun"})
#: `<program> <subcommand>` pairs that run a suite: `cargo test`, `npx playwright test`, `dotnet test`.
_SUBCOMMAND_SUITES = {"cargo": "test", "go": "test", "dotnet": "test", "playwright": "test"}
#: A word before the suite word that makes the command a set-up, not a run: `pip install pytest`.
_SETUP_WORDS = frozenset({"install", "i", "add", "remove", "uninstall", "rm"})
#: A word after the suite word that asks about the tool rather than running it: `pytest --help`.
_QUERY_FLAGS = frozenset({"--version", "--help", "-h", "-V"})
#: Flags that take the next word as their value, so that value is not read as the program.
_VALUE_FLAGS = frozenset({"--prefix", "-C", "--dir", "-w", "--workspace", "--filter"})
#: The flags that move the folder a package manager runs in. `--dir` is one only for pnpm; for vitest
#: it filters files, so `npx vitest run --dir a` and `--dir b` are the same folder.
_FOLDER_FLAGS = frozenset({"--prefix", "-C"})
_FOLDER_FLAGS_BY_PROGRAM = {"pnpm": _FOLDER_FLAGS | {"--dir"}}
#: A command whose first word is one of these reads or prints; a suite word after it is an argument.
_NON_RUNNERS = frozenset(
    {
        "echo", "cat", "type", "grep", "rg", "findstr", "head", "tail", "less", "more", "sed",
        "awk", "git", "ls", "dir", "where", "which", "select-string", "sls", "printf", "rm",
        "del", "cp", "mv", "tee", "cd", "set", "export",
    }
)
_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;&|\n]")
_LEADING_CD = re.compile(
    r"""\s*\(?\s*(?:cd|pushd)(?:\s+/d)?\s+("[^"]*"|'[^']*'|[^\s&;)]+)\s*(?:&&|;)\s*""",
    re.IGNORECASE,
)
#: Text a path cannot be resolved from: an environment variable, a home shortcut, a dash.
_UNRESOLVABLE = ("$", "%", "~", "`")


class SuiteJob(NamedTuple):
    """A background job, as far as the suite rule needs to know it."""

    job_id: str
    command: str
    cwd: str


def _bare(word: str) -> str:
    """A word without the quotes and parentheses a shell wraps around it: `(cd`, `pytest)`."""
    return word.strip("\"'()")


def _basename(word: str) -> str:
    """``.venv/Scripts/pytest.exe`` and ``"pytest"`` both read as ``pytest``."""
    name = re.split(r"[\\/]", _bare(word))[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _npm_runs_test(words_after: list[str]) -> bool:
    """Whether the words after `npm` name the exact `test` script: `test`, or `run test`."""
    words: list[str] = []
    skip_value = False
    for word in words_after:
        if skip_value:
            skip_value = False
            continue
        if word in _VALUE_FLAGS:
            skip_value = True
            continue
        if word.startswith("-"):
            continue
        words.append(_bare(word).lower())
    if not words:
        return False
    if words[0] == "test":
        return True
    return words[0] == "run" and len(words) > 1 and words[1] == "test"


def _segment_suite(words: list[str]) -> tuple[str, int] | None:
    """The suite label and the index of its program word in one command segment, or None."""
    for index, word in enumerate(words):
        before = words[:index]
        if any(_basename(earlier) in _NON_RUNNERS for earlier in before):
            return None
        program = _basename(word)
        after = words[index + 1 :]
        if program in _SUITE_PROGRAMS:
            label = program
        elif program in _PACKAGE_MANAGERS and _npm_runs_test(after):
            label = f"{program} test"
        elif program in _SUBCOMMAND_SUITES and after and _bare(after[0]).lower() == _SUBCOMMAND_SUITES[program]:
            label = f"{program} {_SUBCOMMAND_SUITES[program]}"
        else:
            continue
        if any(_bare(earlier).lower() in _SETUP_WORDS for earlier in before):
            return None
        if any(_bare(later) in _QUERY_FLAGS for later in after):
            return None
        return label, index
    return None


def suite_label(command: str) -> str | None:
    """The name of the test suite a shell command runs, or None when it runs no suite."""
    for segment in _SEGMENT_SPLIT.split(command):
        found = _segment_suite(segment.split())
        if found is not None:
            return found[0]
    return None


def _split_leading_cd(command: str) -> tuple[str | None, str]:
    """`cd X && rest` -> (``X``, ``rest``); anything else -> (None, command)."""
    match = _LEADING_CD.match(command)
    if match is None:
        return None, command
    return _bare(match.group(1)), command[match.end() :]


def _flag_folder(words_after: list[str], program: str) -> str | None:
    """The value of the first folder flag the program honours (`--prefix X`, `-C X`, `--dir X` in pnpm)."""
    flags = _FOLDER_FLAGS_BY_PROGRAM.get(program, _FOLDER_FLAGS)
    for index, word in enumerate(words_after):
        if word in flags and index + 1 < len(words_after):
            return _bare(words_after[index + 1])
        for flag in flags:
            if word.startswith(flag + "="):
                return _bare(word[len(flag) + 1 :])
    return None


def _folder_parts(command: str) -> tuple[str | None, str | None]:
    """The leading `cd` target and the suite's folder flag, as written in the command."""
    lead, rest = _split_leading_cd(command.strip())
    for segment in _SEGMENT_SPLIT.split(rest):
        words = segment.split()
        found = _segment_suite(words)
        if found is not None:
            program = _basename(words[found[1]])
            return lead, _flag_folder(words[found[1] + 1 :], program)
    return lead, None


def _resolve(base: Path, text: str) -> Path | None:
    """``text`` relative to ``base``, or None when it cannot be resolved from here."""
    if not text or text == "-" or any(mark in text for mark in _UNRESOLVABLE):
        return None
    try:
        path = Path(text)
        return (path if path.is_absolute() else base / path).resolve()
    except (OSError, ValueError):
        return None


def _text(value: str) -> str:
    return re.sub(r"[\\/]+", "/", value).rstrip("/").lower()


def suite_folder_key(command: str, cwd: str | Path) -> str:
    """The folder a suite command runs in, as a key two commands can be compared by.

    The leading ``cd X &&`` is resolved against the call's ``cwd``; the folder flag is resolved
    against the folder the ``cd`` reached. When a step cannot be resolved, the key is the normalised
    text of the folder parts and of the call's ``cwd``, so the comparison still says something.
    """
    lead, flag = _folder_parts(command)
    base = Path(cwd)
    for part in (lead, flag):
        if part is None:
            continue
        resolved = _resolve(base, part)
        if resolved is None:
            return "text:" + _text(str(cwd)) + "|" + _text(lead or "") + "|" + _text(flag or "")
        base = resolved
    try:
        final = base.resolve()
    except (OSError, ValueError):
        return "text:" + _text(str(cwd)) + "|" + _text(lead or "") + "|" + _text(flag or "")
    return "path:" + os.path.normcase(str(final))


class SuiteClaim(NamedTuple):
    """What a claim gave: a ticket to hold the folder, or the refusal to return instead."""

    ticket: SuiteTicket | None
    refusal: str | None


@dataclass(eq=False)
class SuiteTicket:
    """A suite that holds a folder. ``job_id`` is set when the run became a background job."""

    key: str
    label: str
    command: str
    job_id: str | None = None


def _held_refusal(ticket: SuiteTicket) -> str:
    if ticket.job_id is not None:
        return (
            f"error: a test suite is already running in this folder: job {ticket.job_id} "
            f"({ticket.label}). Not started: one suite at a time per folder. Wait for it with "
            f"job_status(job_id={ticket.job_id!r}, wait_seconds=120), or stop it with "
            f"job_cancel(job_id={ticket.job_id!r}) if it is not the run you need. Do not start another."
        )
    return (
        f"error: a test suite is already running in this folder: a {ticket.label} run started in "
        "this app is still going. Not started: one suite at a time per folder. Wait until it "
        "finishes and then run this one; do not start another in the meantime."
    )


class SuiteSlots:
    """The suites in progress in this process, by effective folder, under one lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: dict[str, SuiteTicket] = {}

    def peek(
        self,
        command: str,
        cwd: str | Path,
        *,
        is_running: Callable[[str], bool],
        running_jobs: Callable[[], Iterable[SuiteJob]],
    ) -> str | None:
        """The refusal a claim would get now, without marking anything. None when it would pass."""
        if suite_label(command) is None:
            return None
        key = suite_folder_key(command, cwd)
        with self._lock:
            return self._collision_locked(key, is_running, running_jobs)

    def claim(
        self,
        command: str,
        cwd: str | Path,
        *,
        is_running: Callable[[str], bool],
        running_jobs: Callable[[], Iterable[SuiteJob]],
    ) -> SuiteClaim:
        """Take the folder for this suite, or say why not. A non-suite claims nothing.

        ``is_running`` says whether a held background job still runs; a slot whose job has ended is
        released here. ``running_jobs`` lists the registry's running jobs, which covers a suite
        started by an earlier process. The check and the mark are one step under the lock.
        """
        label = suite_label(command)
        if label is None:
            return SuiteClaim(None, None)
        key = suite_folder_key(command, cwd)
        with self._lock:
            refusal = self._collision_locked(key, is_running, running_jobs)
            if refusal is not None:
                return SuiteClaim(None, refusal)
            ticket = SuiteTicket(key, label, command)
            self._held[key] = ticket
            return SuiteClaim(ticket, None)

    def is_held(self, command: str, cwd: str | Path) -> bool:
        """Whether a suite in this command's folder currently holds the slot."""
        key = suite_folder_key(command, cwd)
        with self._lock:
            return key in self._held

    def _collision_locked(
        self,
        key: str,
        is_running: Callable[[str], bool],
        running_jobs: Callable[[], Iterable[SuiteJob]],
    ) -> str | None:
        """The refusal for a suite in ``key``, or None. The caller holds the lock."""
        for held_key, ticket in list(self._held.items()):
            if ticket.job_id is not None and not is_running(ticket.job_id):
                del self._held[held_key]
        held = self._held.get(key)
        if held is not None:
            return _held_refusal(held)
        for job in running_jobs():
            job_label = suite_label(job.command)
            if job_label is None or suite_folder_key(job.command, job.cwd) != key:
                continue
            return _held_refusal(SuiteTicket(key, job_label, job.command, job.job_id))
        return None

    def attach(self, ticket: SuiteTicket, job_id: str) -> None:
        """The run became (or was adopted as) background job ``job_id``: the slot follows the job."""
        with self._lock:
            ticket.job_id = job_id

    def finish(self, ticket: SuiteTicket) -> None:
        """A foreground run returned: release its slot. A background run keeps it until its job ends."""
        with self._lock:
            if ticket.job_id is None and self._held.get(ticket.key) is ticket:
                del self._held[ticket.key]


#: The register every `RunShellTool` in this process shares; a test passes its own.
SUITE_SLOTS = SuiteSlots()
