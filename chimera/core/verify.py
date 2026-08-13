"""Verification for the autonomous loop.

A ``Verifier`` answers one question: did the attempt succeed? The canonical verifier
runs a command (tests, a build, a linter) and treats exit code 0 as success — the
"executable evidence" gate that lets the agent keep a change instead of reverting it.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_MAX_OUTPUT_CHARS = 20_000

#: Exit codes that mean "this command reached no verdict", not "the work is bad".
#:
#: 127 — the shell could not find the command at all.
#: 5   — pytest's "no tests collected". Deliberately included even though it is one tool's
#:       convention: pytest is what the inference reaches for most often, and a repository whose
#:       tests live outside the inferred path would otherwise have every change reverted by a
#:       verifier that ran nothing.
_NO_VERDICT = frozenset({5, 127})

#: `cmd.exe` builtins, which have no executable on disk.
#:
#: `shutil.which` cannot find them, so without this list a builtin that exits non-zero would be
#: mistaken for a command that does not exist — and the verifier would abstain on a real failure,
#: which is the more dangerous direction of this bug.
_CMD_BUILTINS = frozenset(
    {
        "assoc", "break", "call", "cd", "chdir", "cls", "color", "copy", "date", "del",
        "dir", "echo", "endlocal", "erase", "exit", "for", "ftype", "goto", "if", "md",
        "mkdir", "mklink", "move", "path", "pause", "popd", "prompt", "pushd", "rd",
        "rem", "ren", "rename", "rmdir", "set", "setlocal", "shift", "start", "time",
        "title", "type", "ver", "verify", "vol",
    }
)


def _exists_with_pathext(candidate: Path) -> bool:
    """Whether ``candidate`` names a file the shell could start, extension included or implied.

    On Windows `check` and `check.cmd` are the same command, so a verify command written without
    the extension must not be read as absent — that reading abstains, and abstention keeps work
    that nothing verified.
    """
    if candidate.exists():
        return True
    if os.name != "nt":
        return False
    for ext in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep):
        if ext and candidate.with_name(candidate.name + ext).exists():
            return True
    return False


def program_missing(command: str, workspace: Path | str | None = None) -> bool:
    """True when the first token of ``command`` names a program that is nowhere to be found.

    ``workspace`` is the directory the command will RUN in, and leaving it out was a real defect in
    the first version of this function. The verifier runs with ``cwd=workspace``; this predicate
    resolved relative paths against the *server process's* cwd — which for a packaged desktop build
    is wherever the launcher points, typically `C:\\Program Files\\Chimera`. So a project whose
    verify command is `scripts\\test.cmd` had a program that cmd.exe finds and this function calls
    missing, and a REAL test failure was recorded as an abstention. Abstention keeps the work, so
    that is the dangerous direction — a change nothing verified, kept, with a receipt saying the
    verification could not run.

    This exists because **Windows has no exit code for "command not found"**. `cmd.exe` answers 1 —
    the same code every test runner uses for "your tests failed" — so the 127 convention below is
    Unix-only, and on Windows a missing verify command was reported as a FAILED verification. That
    reverts the attempt and writes a test failure into the receipt that never happened, which is
    precisely the outcome the 127 handling was added to prevent.

    Matching the shell's error message is not an option: `cmd.exe` localises it. On the machine
    where this was found it read "não é reconhecido como um comando interno ou externo", so an
    English pattern would have been a defence that works everywhere except where you are.

    Asking whether the program exists is the only signal that is neither exit-code nor locale
    dependent. Only the first token is examined: in a pipeline or an `&&` chain it is the one the
    shell reports as missing, and it is the one the caller typed.
    """
    try:
        # posix=False keeps Windows path quoting (`"C:\\Program Files\\..."`) in one piece; the
        # posix lexer would eat the backslashes.
        tokens = shlex.split(command, posix=False)
    except ValueError:
        # Unbalanced quotes. Not our question to answer — let the shell's own verdict stand.
        return False
    if not tokens:
        return False

    program = tokens[0].strip('"').strip("'")
    if not program:
        return False
    if program.lower() in _CMD_BUILTINS:
        return False

    root = Path(workspace) if workspace is not None else Path.cwd()
    if os.sep in program or (os.altsep and os.altsep in program):
        # An explicit path: existence is a direct question, asked from the directory the command
        # will actually run in.
        #
        # Spelled out rather than delegated to `shutil.which`, whose handling of a path WITH a
        # directory differs between Python versions on Windows — 3.11 checks the exact name and
        # never tries PATHEXT. Depending on that would make this gate's verdict a function of the
        # interpreter, which is not a property a safety check should have.
        candidate = Path(program)
        if not candidate.is_absolute():
            candidate = root / candidate
        return not _exists_with_pathext(candidate)
    # A bare name: PATH, and then the workspace itself, because `cmd.exe` searches its current
    # directory too (unless `NoDefaultCurrentDirectoryInExePath` is set). If the file is sitting
    # right there, the shell can run it and we must not claim otherwise.
    return shutil.which(program) is None and shutil.which(program, path=str(root)) is None



@dataclass
class VerificationResult:
    """Outcome of a verification."""

    passed: bool
    output: str = ""
    abstained: bool = False
    """True = the verifier had nothing runnable to check (e.g. spec-test generation produced no
    tests). A ``passed=True, abstained=True`` result is NOT positive evidence — the caller must fall
    back to its other gates (Manager review, coverage checklist) rather than accept on it."""


class Verifier(Protocol):
    """Anything that can verify the current workspace state."""

    def verify(self) -> VerificationResult: ...


class CommandVerifier:
    """Runs a shell command; success == exit code 0."""

    def __init__(self, command: str, workspace: Path, *, timeout: int = 120) -> None:
        self.command = command
        self.workspace = Path(workspace)
        self.timeout = timeout

    def verify(self) -> VerificationResult:
        try:
            proc = subprocess.run(
                self.command,
                shell=True,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return VerificationResult(False, f"verification timed out after {self.timeout}s")
        except OSError as exc:
            # e.g. the cwd was removed, or the command binary is missing — report a failed/unverifiable
            # attempt instead of letting it propagate and abort the whole run.
            return VerificationResult(False, f"verification could not run: {exc}")
        output = ((proc.stdout or "") + (proc.stderr or ""))[:_MAX_OUTPUT_CHARS]
        if proc.returncode in _NO_VERDICT:
            # The command produced no verdict, which is NOT the same as a verdict of "bad".
            #
            # 127 is the shell saying the command does not exist; 5 is pytest saying it collected no
            # tests. Both used to be reported as a failed verification, so the attempt was reverted
            # and the receipt recorded a test failure that never happened. That was survivable while
            # a human typed the command and could see the mistake. It stops being survivable when the
            # command is INFERRED from the project, because then a repository whose tests live
            # somewhere the inference did not look would have every change silently thrown away.
            #
            # Abstaining hands the decision back to the other gates (the Manager review and the diff
            # gate) exactly as if no verifier had been configured — `verifier_active` in the
            # autonomous loop already demotes an abstention to the no-verifier path, so `evidence`
            # correctly stops being "verifier". We could not check it; we do not claim we did, and we
            # do not punish the work for our own inability.
            return VerificationResult(True, output, abstained=True)
        if proc.returncode != 0 and os.name == "nt" and program_missing(self.command, self.workspace):
            # The Windows half of the same abstention. Checked only after a non-zero exit, so a
            # command that exists and genuinely fails is never reinterpreted, and `which` is not
            # paid on the happy path.
            return VerificationResult(True, output, abstained=True)
        return VerificationResult(proc.returncode == 0, output)


class NullVerifier:
    """Always passes — used when no verification command is configured."""

    def verify(self) -> VerificationResult:
        return VerificationResult(True, "no verification configured")
