"""Shell execution tool.

Powerful by design: it runs commands in the workspace directory. For now safety
is limited to a timeout and the workspace cwd; the governance kernel (M5) will gate
it (allow/warn/block/review) and the sandbox layer (M3/M5) will isolate it.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.sandbox.confirm import sandbox_is_isolated
from chimera.sandbox.docker import docker_refusal
from chimera.tools.base import Tool
from chimera.tools.clip import clip_output, keep_tail_enabled
from chimera.tools.suite_gate import SUITE_SLOTS, SuiteJob, SuiteSlots, SuiteTicket
from chimera.tools.workspace import audit_refusal, queue_refusal

if TYPE_CHECKING:
    from chimera.core.jobs import JobRegistry
    from chimera.sandbox.base import Sandbox
    from chimera.sandbox.confirm import HostExecConfirm

_MAX_OUTPUT_CHARS = 20_000
_DEFAULT_TIMEOUT = 60
_MAX_TIMEOUT = 3600  # cap: long ops (backups, builds) are fine; runaway ones are not
_CMD_HEREDOC_ERROR = (
    "error: the shell is cmd.exe, which has no heredoc syntax. Save the script to a file with "
    "write_file and run `python script.py`, or use `powershell -Command`."
)
_HEREDOC_DELIMITER = re.compile(
    r"<<\s*-?\s*(?:'[^'\r\n]+'|\"[^\"\r\n]+\"|[A-Za-z_][A-Za-z0-9_.-]*)"
)


def _cmd_heredoc_refusal(command: str, *, shell_is_cmd: bool) -> str | None:
    """Refuse only an unquoted heredoc operator when the selected interpreter is cmd.exe.

    A model can compose a valid bash heredoc while the desktop's Windows host runs cmd.exe. Sending
    it through approval wastes the owner's time and then produces a syntax error. This scanner is
    intentionally not a general shell validator: cmd operators such as ``&&`` and ``>`` are valid,
    and PowerShell's ``<<`` can be a legitimate expression inside its quoted command argument.
    """
    if not shell_is_cmd:
        return None
    quote: str | None = None
    index = 0
    while index < len(command):
        char = command[index]
        if quote is not None:
            if char == quote:
                # Shell command text commonly doubles a quote to include it inside a quoted region.
                if index + 1 < len(command) and command[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in ("'", '\"'):
            quote = char
        elif command.startswith("<<", index) and _HEREDOC_DELIMITER.match(command, index):
            return _CMD_HEREDOC_ERROR
        index += 1
    return None


#: Told to the model, in the turn context, on a turn whose folder has commands off. The agent used to
#: learn it by trying: it ran `npm run build`, was told "tsc not found", and reported an environment
#: fault when the truth was a permission (study 34, item 15).
COMMANDS_OFF_NOTE = (
    "Commands are off for this folder: run_shell, and the builds, test runs and installs that need "
    "it, cannot run in this turn. Do not try them, and do not report a missing tool such as tsc as "
    "an environment fault. Read and edit files, say which command you would have run, and ask the "
    "owner to allow commands for this folder."
)

#: Told on a Code turn whose folder has commands on. Measured 2026-10-29 to 31: the agent ran the whole
#: suite unfiltered, started several suites at once in one folder, and sent their output to the screen.
TEST_RUN_NOTE = (
    "To run tests: start with the tests of the files you changed, then the wider suite. Run one "
    "suite at a time per folder: a second one is refused while the first runs. A background run is "
    "followed with job_status, and a run started in this app must finish before the next starts. "
    "Send the output to a file (for example `> out.txt`) and read the file. If the suite is vitest, "
    "pass --maxWorkers=2."
)

#: What `run_shell` answers when the owner has not allowed commands for the folder.
COMMANDS_OFF_REFUSAL = (
    "error: commands are off for this folder — ask the owner to allow them. Nothing was run."
)

HOST_EXEC_ALREADY_DECLINED = (
    "Host execution was already declined in this turn; it will not be asked again. "
    "Continue without running commands, and say what you could not verify."
)


class HostExecTurnLimiter:
    """Share a single host-execution refusal across the tools in one turn.

    A change to the folder's recorded grant starts a new capability state. The object is created
    with the turn's registry, never at module scope, so a later turn starts clean.
    """

    def __init__(self, confirm: HostExecConfirm, granted: Callable[[], bool]) -> None:
        self._confirm = confirm
        self._granted = granted
        self._grant_state = self._read_grant()
        self._declined = False
        self._confirming = False
        self._condition = threading.Condition()

    def _read_grant(self) -> bool:
        try:
            return bool(self._granted())
        except Exception:  # a failed status read must not grant more capability
            return False

    def refresh(self) -> None:
        current = self._read_grant()
        with self._condition:
            if current != self._grant_state:
                was_granted = self._grant_state
                self._grant_state = current
                # Revoking a live grant is a hard stop, not permission to ask again and run.
                # A later explicit grant may start a fresh capability state.
                self._declined = True if was_granted and not current else self._declined
                if current and not was_granted:
                    self._declined = False
                self._condition.notify_all()

    def refuse(self) -> None:
        """Latch a refusal made by the turn's separate owner-approval path."""
        self.refresh()
        with self._condition:
            self._declined = True
            self._condition.notify_all()

    @property
    def declined(self) -> bool:
        self.refresh()
        with self._condition:
            return self._declined

    def __call__(self, action: str) -> bool:
        while True:
            self.refresh()
            with self._condition:
                if self._declined:
                    return False
                if not self._confirming:
                    self._confirming = True
                    grant_state = self._grant_state
                    break
                self._condition.wait()

        try:
            approved = self._confirm(action)
        finally:
            # If the callback raises, release queued callers too; preserve the callback's error.
            with self._condition:
                if "approved" not in locals():
                    self._confirming = False
                    self._condition.notify_all()
        self.refresh()
        with self._condition:
            self._confirming = False
            if not approved:
                self._declined = True
            # A revocation during the prompt is a hard stop even if the owner approved it.
            current_grant = self._grant_state
            self._condition.notify_all()
            return approved and not self._declined and current_grant == grant_state


class RunShellTool(Tool):
    name = "run_shell"
    #: Bounds its own output: _MAX_OUTPUT_CHARS via clip_output. The central ceiling in `ToolRegistry.run` leaves it alone.
    clips_own_output = True
    # The shell is named because the model cannot see it: measured 2026-10-06 on Windows, an agent
    # lost four calls in one turn to `;`-chained commands, `pwd` and PowerShell `if (...)` sent to
    # cmd.exe. Static on purpose — the desktop's Capabilities screen pins this text word for word.
    description = (
        "Run a shell command in the workspace directory and return its output. "
        "Use with care: this can modify the system. On Windows the command runs in cmd.exe, not "
        "PowerShell or bash: chain with &&, and run PowerShell as powershell -Command \"...\". "
        "In a container, on Linux and on macOS it runs in sh."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run."},
            "timeout": {
                "type": "integer",
                "description": (
                    "Timeout in seconds (default 60). A command that may take longer than a few "
                    "minutes (a benchmark, a long build or test run) should be started with "
                    "background=true instead of a long timeout."
                ),
            },
            "cwd": {
                "type": "string",
                "description": "Working directory, relative to the workspace (default: workspace root).",
            },
            "background": {
                "type": "boolean",
                "description": (
                    "Start the command as a background job and return at once with its job id, "
                    "instead of waiting for it. For long work (downloads, builds, batch "
                    "processing) that should not hold the conversation. The job keeps running "
                    "after this turn; check it with job_status, stop it with job_cancel."
                ),
            },
        },
        "required": ["command"],
    }

    def __init__(
        self,
        workspace: Path | None = None,
        sandbox: Sandbox | None = None,
        *,
        default_timeout: int = _DEFAULT_TIMEOUT,
        max_timeout: int = _MAX_TIMEOUT,
        confirm: HostExecConfirm | None = None,
        jobs: JobRegistry | None = None,
        slots: SuiteSlots | None = None,
    ) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self._sandbox = sandbox
        self.default_timeout = default_timeout
        self.max_timeout = max_timeout
        # Optional gate consulted before running on the host; None = run as before (isolated sandbox,
        # explicit allow, or a caller that opts out). See chimera.sandbox.confirm.
        self._confirm = confirm
        # Where `background: true` puts a command. None = the parameter is refused with a sentence,
        # which is how a registry built without a home (a bench, a bare `RunShellTool()`) behaves.
        self._jobs = jobs
        # The process-wide register of suites in progress; a test passes its own.
        self._slots = SUITE_SLOTS if slots is None else slots
        self.turn_job_ids: set[str] = set()
        #: What a declined host execution answers, when the caller knows WHY it is declined (the
        #: folder has no command grant). None: the generic CHIMERA_HOST_EXEC sentence.
        self.declined_message: str | None = None

    _sandbox_is_isolated = staticmethod(sandbox_is_isolated)  # shared with code.py; see confirm.py

    def _start_job(
        self, command: str, cwd: Path, sandbox: Any, ticket: SuiteTicket | None = None
    ) -> str:
        """The `background: true` path, reached only AFTER every gate the foreground path passes:
        the kernel and the taint ledger saw this exact call one wrapper out, and the host-exec
        confirm above said yes. What differs is only that nobody waits."""
        from chimera.core.jobs import JobLimitError
        from chimera.sandbox import LocalSandbox
        from chimera.sandbox.local import _child_env
        from chimera.sandbox.wsl import WslSandbox

        if isinstance(sandbox, WslSandbox):
            return (
                "error: background jobs are not available in the WSL environment: the process would "
                "live in the distro, where this app cannot list or stop it. Run the command in the "
                "foreground."
            )
        from chimera.sandbox.ssh import SshSandbox

        if isinstance(sandbox, SshSandbox):
            return (
                "error: background jobs are not available in an SSH environment: the process would "
                "live on the remote host, where this app cannot list or stop it. Run the command in "
                "the foreground."
            )
        if self._jobs is None:
            return (
                "error: background jobs are not available here — this registry has no job store. "
                "Run the command in the foreground."
            )
        # The local family only: the host, or a kernel sandbox (bubblewrap, Seatbelt) that is the
        # same process wrapped in an argv — `_command_argv` below applies that wrapper to the job
        # exactly as `run` applies it to a foreground command. A container is another thing: it runs
        # a command to completion inside itself, and there is no detached form of that here.
        if not isinstance(sandbox, LocalSandbox):
            return (
                "error: background jobs run on the host sandbox only — an isolated container runs a "
                "command to completion inside itself and has no detached form. Run it in the "
                "foreground, or set CHIMERA_SANDBOX=local."
            )
        argv, use_shell = sandbox._command_argv(command, cwd)
        # Said BEFORE the start, while the count is still the one the person would want to know: a
        # third job running is fine, and the tenth is worth a sentence, but neither is a refusal.
        over = self._jobs.over_advisory_limit()
        try:
            job = self._jobs.start(command, cwd=cwd, env=_child_env(), argv=argv, shell=use_shell)
        except JobLimitError as exc:
            running = "; ".join(f"{j.id}: {j.command[:80]}" for j in exc.running)
            return (
                f"error: {len(exc.running)} background jobs are already running, the most this app "
                f"runs at once ({exc.limit}; CHIMERA_JOBS_MAX_RUNNING). Not started. Wait for one to "
                f"finish (job_status) or stop one (job_cancel). Running: {running}"
            )
        except OSError as exc:
            return f"error: could not start the background job: {exc}"
        if ticket is not None:
            self._slots.attach(ticket, job.id)
        self.turn_job_ids.add(job.id)
        limit = f" It is stopped after {job.max_runtime:.0f}s at most." if job.max_runtime else ""
        if over is not None:
            limit += (
                f" Note: {over[0]} other background jobs were already running (more than the usual "
                f"{over[1]}); that is a suggestion, not a limit, and it started anyway."
            )
        return (
            f"job {job.id} started in the background (pid {job.pid}); it keeps running after this "
            f"turn and is NOT stopped by cancelling the turn.{limit} Check it with "
            f"job_status(job_id={job.id!r}) — it shows the state, the exit code and the last lines "
            f"of output; stop it with job_cancel(job_id={job.id!r}). "
            "Do not report the work as done until job_status says it finished. To wait for it, "
            "call job_status once with wait_seconds (up to 120) rather than again and again."
        )

    @staticmethod
    def _uses_cmd_shell(sandbox: Any, command: str, cwd: Path) -> bool:
        """Whether this command would reach ``cmd.exe``: a host sandbox on Windows.

        Decided from the sandbox's type and the platform, never by calling ``_command_argv``: that
        method has effects (the OS sandbox claims its once-per-process notice and builds its
        wrapper), and asking it here ran them a second time for every command — measured on the
        Windows CI, where a kernel-sandbox job was wrapped twice. On Windows neither ``LocalSandbox``
        nor ``OsSandbox`` (no Seatbelt or bubblewrap there) wraps the command: both hand it to the
        shell, which is ``cmd.exe``.
        """
        from chimera.sandbox import LocalSandbox

        return os.name == "nt" and isinstance(sandbox, LocalSandbox)

    def _running_suites(self) -> list[SuiteJob]:
        """The background jobs still running, as the suite rule reads them. Empty without a registry."""
        if self._jobs is None:
            return []
        return [
            SuiteJob(job.id, job.command, job.cwd)
            for job in self._jobs.all()
            if job.state == "running"
        ]

    def _job_running(self, job_id: str) -> bool:
        """Whether a background job still runs; a slot follows its job and is freed when it ends."""
        if self._jobs is None:
            return False
        job = self._jobs.get(job_id)
        return job is not None and job.state == "running"

    def _resolve_cwd(self, rel: str | None) -> Path | str:
        """Resolve a per-call ``cwd`` under the workspace, or an ``error:`` string if it escapes."""
        if not rel:
            return self.workspace
        candidate = (self.workspace / rel).resolve()
        if candidate != self.workspace and not candidate.is_relative_to(self.workspace):
            return f"error: cwd '{rel}' escapes the workspace"
        return candidate

    def run(self, **kwargs: Any) -> str:
        from chimera.sandbox import LocalSandbox

        command = str(kwargs["command"])
        timeout = int(kwargs.get("timeout") or self.default_timeout)
        timeout = max(1, min(timeout, self.max_timeout))
        cwd = self._resolve_cwd(kwargs.get("cwd"))
        if isinstance(cwd, str):  # escape error
            return cwd
        # Before the host-exec question and before anything runs, in every sandbox: an isolated
        # container may still mount the data folder, and a command that answers a question must not
        # become a question the person is asked about instead. The audit log is held by the same
        # two fences: a command that rewrites the record must not run either.
        from chimera.sandbox.wsl import WslSandbox

        in_wsl = isinstance(self._sandbox, WslSandbox)
        fenced = queue_refusal(self.name, command, cwd, wsl=in_wsl)
        if fenced is not None:
            return fenced
        fenced = audit_refusal(self.name, command, cwd, wsl=in_wsl)
        if fenced is not None:
            return fenced
        sandbox = self._sandbox or LocalSandbox()
        refused = docker_refusal(sandbox)
        if refused is not None:
            return refused
        refused = _cmd_heredoc_refusal(
            command, shell_is_cmd=self._uses_cmd_shell(sandbox, command, cwd)
        )
        if refused is not None:
            return refused
        # A suite that would collide is refused before the owner is asked, so nothing is asked for it.
        # This only peeks: the folder is marked after approval, in `_run_gated`, not here.
        early = self._slots.peek(
            command, cwd, is_running=self._job_running, running_jobs=self._running_suites
        )
        if early is not None:
            return early
        return self._run_gated(command, timeout, cwd, sandbox, kwargs)

    def _run_gated(
        self,
        command: str,
        timeout: int,
        cwd: Path,
        sandbox: Any,
        kwargs: dict[str, Any],
    ) -> str:
        """The approval, then the claim, then the run. The mark is taken after the owner says yes."""
        if (
            isinstance(self._confirm, HostExecTurnLimiter)
            and self._confirm.declined
            and not self._sandbox_is_isolated(sandbox)
        ):
            return HOST_EXEC_ALREADY_DECLINED
        if self._confirm is not None and not self._sandbox_is_isolated(sandbox):
            # The prompt shows what the command resolves to here (S30-30), as the governance cards
            # do; the callback itself still gets exactly the command, which is what it classifies.
            from chimera.governance.exec_facts import describe, offered_with
            from chimera.sandbox.ssh import SshSandbox
            from chimera.sandbox.wsl import WslSandbox

            try:
                # Programs and git hooks resolve inside the distro there, which this machine cannot
                # see: naming the Windows `PATH`'s answer would describe a run that does not happen.
                if isinstance(sandbox, SshSandbox):
                    lines = [
                        f"runs on {sandbox.host} over ssh, in {sandbox.remote_path}: its programs and "
                        "files are that machine's, not this one's"
                    ]
                elif isinstance(sandbox, WslSandbox):
                    lines = ["runs in the WSL environment: its programs and git hooks resolve in the distro, not here"]
                else:
                    lines = describe(command, cwd, self.workspace)
            except Exception:  # a prompt without the note is the prompt it was
                lines = []
            with offered_with(command, lines):
                confirmed = self._confirm(command)
            if not confirmed:
                return self.declined_message or "error: host execution declined (CHIMERA_HOST_EXEC). Not run."
        # Taken only now, right before the process starts. The owner may have taken two minutes to
        # decide; a suite that was refused while waiting must not be refused by a mark nothing holds.
        claim = self._slots.claim(
            command, cwd, is_running=self._job_running, running_jobs=self._running_suites
        )
        if claim.refusal is not None:
            return claim.refusal
        if claim.ticket is None:
            return self._dispatch(command, timeout, cwd, sandbox, kwargs, None)
        try:
            return self._dispatch(command, timeout, cwd, sandbox, kwargs, claim.ticket)
        finally:
            self._slots.finish(claim.ticket)

    def _dispatch(
        self,
        command: str,
        timeout: int,
        cwd: Path,
        sandbox: Any,
        kwargs: dict[str, Any],
        ticket: SuiteTicket | None,
    ) -> str:
        """The run itself, after approval and the claim: a background job, or a foreground command."""
        from chimera.sandbox import LocalSandbox

        if bool(kwargs.get("background", False)):
            return self._start_job(command, cwd, sandbox, ticket)
        if self._jobs is not None and isinstance(sandbox, LocalSandbox):
            jobs = self._jobs

            def adopt(proc: Any) -> str | None:
                job = jobs.adopt(proc, command, cwd=cwd)
                return job.id if job is not None else None

            result = sandbox.run(command, timeout=timeout, cwd=cwd, on_timeout=adopt)
        else:
            result = sandbox.run(command, timeout=timeout, cwd=cwd)
        if ticket is not None and result.adopted:
            # A foreground suite that outlived its timeout is now a background job: its slot follows it.
            self._slots.attach(ticket, result.adopted)
        if result.adopted:
            return (
                f"job {result.adopted} is still running after {timeout}s and was NOT stopped: it "
                f"continues in the background and keeps running after this turn. Its output appears "
                f"when it finishes; check it with job_status(job_id={result.adopted!r}), stop it with "
                f"job_cancel(job_id={result.adopted!r}). Do not report the work as done until "
                "job_status says it finished."
            )
        if result.timed_out:
            if self._jobs is not None and isinstance(sandbox, LocalSandbox):
                return (
                    f"error: command timed out after {timeout}s and was stopped. If it needs longer, "
                    "run it again with background=true: it keeps running after this turn, and "
                    "job_status shows how it is going."
                )
            return f"error: command timed out after {timeout}s"
        # The verdict of a command is at its end (pytest's FAILED summary, a traceback's last line);
        # keeping it is opt-in until measured — see chimera.tools.clip.
        out = clip_output(result.output, _MAX_OUTPUT_CHARS, keep_tail=keep_tail_enabled())
        return f"[exit {result.exit_code}]\n{out}".rstrip()
