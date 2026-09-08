"""Verification for the autonomous loop.

A ``Verifier`` answers one question: did the attempt succeed? The canonical verifier
runs a command (tests, a build, a linter) and treats exit code 0 as success — the
"executable evidence" gate that lets the agent keep a change instead of reverting it.

The command runs **where the agent's own shell runs** — through the configured sandbox, and behind
the host-exec gate when that sandbox is not isolated. It used to run ``subprocess.run(shell=True)``
on the host, outside the kernel, the taint ledger and the host-exec confirmation, which all wrap
*tools*. A verify string is not always typed by the person starting the run: it is inferred from a
repository's own ``package.json`` / ``Makefile`` / ``pyproject.toml``, read from a cron job file, a
kanban card or a workflow YAML — a shell string bound to a runtime event, which is the exact shape
arXiv 2609.03884 used to compromise seven harnesses. So every constructor names who authored the
string (:data:`VERIFY_SOURCES`), and only the one typed in the same breath as the run is authorised
by construction.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from chimera.telemetry import get_logger

if TYPE_CHECKING:
    from chimera.sandbox.base import Sandbox
    from chimera.sandbox.confirm import HostExecConfirm

_log = get_logger("core.verify")

_MAX_OUTPUT_CHARS = 20_000

#: Who authored the verify string. One literal per call site, and the gate reads it: ``user`` is a
#: command typed on the CLI or sent explicitly in the API request — authorised by construction,
#: because the person asked for the run and the check in the same breath. Every other value names
#: a string that came back out of a file: inferred from the repository's own build files
#: (``inferred``), a cron job (``job``), a kanban card (``card``), a workflow YAML (``workflow``),
#: the spec-test runner over model-written tests (``spec_test``), a crew or lifecycle run whose
#: check was not typed by the person starting it (``crew``, ``lifecycle``), or a benchmark task
#: definition (``eval``).
VERIFY_SOURCES = frozenset(
    {"user", "inferred", "job", "card", "workflow", "spec_test", "crew", "eval", "lifecycle"}
)

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


def module_missing(command: str, output: str) -> bool:
    """True when ``command`` ran a module with ``-m`` and the interpreter said it does not exist.

    :func:`program_missing` asks whether the *binary* is there. This is the same question one level
    in: ``python -m pytest`` on a machine without pytest finds the binary perfectly well, exits **1**
    — indistinguishable from "your tests failed" — and prints ``No module named pytest``. Spec-test
    generation hard-codes exactly that command, and its audience is the person who is not a Python
    developer, so on their machine the generated test "failed", the attempt was reverted, and the
    receipt recorded a test failure that never ran. The same class of bug the 127 handling exists to
    prevent, arriving through the ``-m`` door.

    Matching the message is sound HERE where it was not sound for ``cmd.exe``: this text comes from
    Python's own runpy, not from a localised shell, and it is the same string on every platform.

    Two discriminations keep a real failure from being reinterpreted, which is the dangerous
    direction. The module name must be the one the command actually asked for. And the interpreter's
    unquoted ``No module named pytest`` is required — a traceback from code *under test* failing to
    import says ``No module named 'pytest'``, with quotes, and must stay a failure.
    """
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        return False
    try:
        module = tokens[tokens.index("-m") + 1].strip("\"'")
    except (ValueError, IndexError):
        return False
    if not module:
        return False
    return re.search(rf"No module named {re.escape(module)}(?![\w'\"])", output) is not None



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
    """Runs a shell command; success == exit code 0.

    The command runs through the sandbox the agent's own ``run_shell`` uses — the kernel sandbox
    where the platform has one, docker when configured, the host otherwise — and, when that sandbox
    is not isolated and the string was not typed by the user, behind the same host-exec
    confirmation (``CHIMERA_HOST_EXEC``). A declined command is an **abstention**: we could not
    check it, we do not claim we did, and the loop's other gates take over — the same semantics
    this file already gives a program that is not installed.

    ``source`` is required and keyword-only on purpose. A constructor that could be called without
    it would be one more site where a string from a file reaches a shell with nobody having said
    where it came from. ``sandbox`` and ``confirm`` default to what the shell tool would resolve
    (:func:`chimera.sandbox.get_sandbox`, :func:`chimera.sandbox.confirm.resolve_host_exec_confirm`)
    and exist so a test can hand in a fake of either.
    """

    def __init__(
        self,
        command: str,
        workspace: Path,
        *,
        timeout: int = 120,
        source: str,
        sandbox: Sandbox | None = None,
        confirm: HostExecConfirm | None = None,
    ) -> None:
        if source not in VERIFY_SOURCES:
            raise ValueError(
                f"verify source {source!r} is not one of {sorted(VERIFY_SOURCES)}: the gate needs "
                "to know who authored the command"
            )
        self.command = command
        self.workspace = Path(workspace)
        self.timeout = timeout
        self.source = source
        self._sandbox = sandbox
        self._confirm = confirm

    def _abstain(self, reason: str) -> VerificationResult:
        """Say why the command did not run, where the receipt will show it, and stand aside."""
        _log.warning("verify command not run (%s source): %s", self.source, reason)
        return VerificationResult(True, f"verify command not run: {reason}", abstained=True)

    def _resolve_sandbox(self) -> Sandbox | VerificationResult:
        """The backend this command runs in, or the abstention explaining why there is none.

        ``CHIMERA_VERIFY_NETWORK`` is honoured where it can be: a docker sandbox is rebuilt with the
        network on. The kernel sandboxes deny the network by construction (bubblewrap
        ``--unshare-net``, Seatbelt ``(deny default)``) and cannot be asked for it, so there the
        only way to give the verifier a network is the host — which is allowed for a command the
        user typed, and refused for one that came out of a file.
        """
        from chimera.config import get_settings
        from chimera.sandbox import DockerSandbox, LocalSandbox, get_sandbox
        from chimera.sandbox.confirm import sandbox_is_isolated

        sandbox: Sandbox = self._sandbox if self._sandbox is not None else get_sandbox()
        if not get_settings().verify_network:
            return sandbox
        if isinstance(sandbox, DockerSandbox):
            return DockerSandbox(
                sandbox.image,
                network=True,
                memory=sandbox.memory,
                cpus=sandbox.cpus,
                pids_limit=sandbox.pids_limit,
                runtime=sandbox.runtime,
                fallback=sandbox.fallback,
            )
        if not sandbox_is_isolated(sandbox):
            return sandbox  # the host has the network already
        if self.source == "user":
            _log.warning(
                "CHIMERA_VERIFY_NETWORK is set and the OS sandbox cannot grant a network; the "
                "verify command you typed runs on the host instead"
            )
            return LocalSandbox()
        return self._abstain(
            "CHIMERA_VERIFY_NETWORK asks for the network, the OS sandbox cannot grant it, and a "
            f"command from the {self.source} may not fall back to the host — only one you typed may"
        )

    def _declined(self, sandbox: Sandbox) -> bool:
        """Whether the host-exec gate refused this command. Never consulted for ``user`` or inside
        a genuinely isolated sandbox — the confirmation asks about running on the HOST."""
        from chimera.sandbox.confirm import resolve_host_exec_confirm, sandbox_is_isolated

        if self.source == "user" or sandbox_is_isolated(sandbox):
            return False
        confirm = self._confirm if self._confirm is not None else resolve_host_exec_confirm()
        return confirm is not None and not confirm(self.command)

    def verify(self) -> VerificationResult:
        resolved = self._resolve_sandbox()
        if isinstance(resolved, VerificationResult):
            return resolved
        sandbox = resolved
        if self._declined(sandbox):
            # Recorded as a log line rather than an audit entry: the verifier is built outside the
            # tool registry, so no audit hook reaches it. The abstention itself goes on the receipt.
            return self._abstain(
                f"its source is {self.source!r}, the sandbox is not isolated, and the host-exec "
                "gate (CHIMERA_HOST_EXEC) declined to run it on the host"
            )
        try:
            result = sandbox.run(self.command, timeout=self.timeout, cwd=self.workspace)
        except OSError as exc:
            # e.g. the cwd was removed, or the command binary is missing — report a failed/unverifiable
            # attempt instead of letting it propagate and abort the whole run.
            return VerificationResult(False, f"verification could not run: {exc}")
        if result.timed_out:
            return VerificationResult(False, f"verification timed out after {self.timeout}s")
        output = result.output[:_MAX_OUTPUT_CHARS]
        if result.exit_code in _NO_VERDICT:
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
        if result.exit_code != 0 and module_missing(self.command, output):
            # `python -m <tool>` where the tool is not installed: the binary exists, the exit code is
            # 1, and only the message distinguishes it from a test that failed. Checked after a
            # non-zero exit for the same reason as the Windows arm below — a command that ran and
            # genuinely failed must never be reinterpreted as an abstention.
            return VerificationResult(True, output, abstained=True)
        if result.exit_code != 0 and os.name == "nt" and program_missing(self.command, self.workspace):
            # The Windows half of the same abstention. Checked only after a non-zero exit, so a
            # command that exists and genuinely fails is never reinterpreted, and `which` is not
            # paid on the happy path.
            return VerificationResult(True, output, abstained=True)
        return VerificationResult(result.exit_code == 0, output)


class NullVerifier:
    """Always passes — used when no verification command is configured."""

    def verify(self) -> VerificationResult:
        return VerificationResult(True, "no verification configured")
