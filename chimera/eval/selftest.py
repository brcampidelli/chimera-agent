"""Prove the apparatus discriminates, before spending a cent on the phenomenon.

A benchmark task is a claim: *this test fails now, and passes once the work is done*. The second half
is measured on every run. The first half — that the test fails **before** anyone touches anything —
is assumed, and an assumption is exactly the shape of the failure this repository has already paid
for twice.

A task whose test passes against the starting workspace measures nothing. It scores a hit for every
arm, including a baseline that did nothing, and it does it silently: the run completes, the numbers
look plausible, and the suite's pass rate quietly carries a task that could never have failed. Three
of this project's own learning-lift runs closed with a control above 88%, and a check like this one
would have named the reason before the spend rather than after it.

The rule is stated in `bench/learning_lift/tasks_recurring.py`, in
`skills/chimera-prove-the-test-discriminates/`, and in the card about writing the check before the
code. It was never in the execution path. That is the whole point of this module: a guard that lives
in prose guards nothing, which is the lesson from the Bee pre-training — the assertion was written,
committed, and never called.

The verify command runs **where the agent's own shell runs** — through the configured sandbox, and
behind the host-exec gate when that sandbox is not isolated — because the string is the task file's,
not the developer's: a shell string read from a file, the shape :mod:`chimera.core.verify` gates for
a cron job, a kanban card or an inferred build command. This was the one such string that still
reached ``subprocess.run(shell=True)`` on its own. A command the gate declines is *not runnable*: the
guard could not prove the apparatus, and says so, rather than certifying it or crashing.

Two failures, kept apart on purpose:

* **not discriminating** — the verify command SUCCEEDS on the untouched workspace. The task is
  vacuous and the suite is measuring air.
* **not runnable** — the command could not be executed at all: no pytest, a timeout, a broken
  interpreter. That is an environment problem, not a vacuous task, and calling it the same thing
  would train everyone to ignore both.
"""

from __future__ import annotations

import os
import shlex
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.sandbox.base import Sandbox

__all__ = ["TaskCheck", "Verdict", "check_discriminates", "run_selftest", "assert_discriminating"]

#: Long enough for a pytest collect + a slow import; short enough that a hung task does not stall a
#: guard whose entire selling point is being cheap. A task that needs longer than this to FAIL is
#: reporting something worth knowing on its own.
DEFAULT_TIMEOUT = 120

#: Who authored the verify string, in the vocabulary of :data:`chimera.core.verify.VERIFY_SOURCES`:
#: a benchmark task definition. The same word the verifier uses for the same file, so the gate's
#: refusal reads the same whichever of the two was asked to run the command.
_SOURCE = "eval"


@dataclass(frozen=True)
class Verdict:
    """What the untouched workspace did when the task's own verify command ran against it."""

    task_id: str
    discriminates: bool
    """True when the command failed, which is the only healthy answer before any work is done."""

    runnable: bool = True
    """False when the command could not be executed at all — a different problem, said differently."""

    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.discriminates and self.runnable


@dataclass(frozen=True)
class TaskCheck:
    """One task to prove: how to build its starting workspace, and how it is verified.

    ``setup`` is a callable rather than a path because every runner already has its own
    workspace-building function, and re-deriving it here would be a second copy of the thing under
    test — the copy that drifts.
    """

    task_id: str
    setup: Callable[[], Path]
    verify: str


def _executable_missing(command: str) -> str:
    """The command's own program, when it is not on PATH — else "".

    Asked BEFORE running, because after running it is unanswerable: a POSIX shell reports a missing
    command as exit 127, but `cmd.exe` reports it as exit 1 with a message in the system's language,
    and exit 1 is exactly what a failing test looks like. A guard that cannot tell "the test failed"
    from "nothing ran" on Windows is a guard that passes an empty apparatus, which is what this whole
    module exists to refuse.
    """
    try:
        parts = shlex.split(command, posix=os.name != "nt")
    except ValueError:  # unbalanced quotes — let the shell report it in its own words
        return ""
    if not parts:
        return ""
    program = parts[0].strip('"')
    return "" if shutil.which(program) else program


def _declined_on_the_host(sandbox: Sandbox, command: str) -> bool:
    """Whether the host-exec gate refused ``command`` — ``CommandVerifier._declined``'s rule.

    The string came out of a task file, never off a keyboard, so it is gated the way the verifier
    gates ``job``, ``card`` and ``inferred``: inside a sandbox that genuinely isolates the question
    does not arise, and on the host ``CHIMERA_HOST_EXEC`` decides, resolved the way the shell tool
    resolves it. No is no.
    """
    from chimera.sandbox.confirm import resolve_host_exec_confirm, sandbox_is_isolated

    if sandbox_is_isolated(sandbox):
        return False
    confirm = resolve_host_exec_confirm()
    return confirm is not None and not confirm(command)


def check_discriminates(
    check: TaskCheck, *, timeout: int = DEFAULT_TIMEOUT, sandbox: Sandbox | None = None
) -> Verdict:
    """Run one task's verify command against its untouched workspace and report what happened.

    ``sandbox`` defaults to what the shell tool would resolve (:func:`chimera.sandbox.get_sandbox`)
    and exists so a test can hand in a fake — the verifier's own seam.
    """
    try:
        workspace = check.setup()
    except Exception as exc:  # a workspace that cannot be built is not a vacuous task either
        return Verdict(check.task_id, discriminates=False, runnable=False, detail=f"setup failed: {exc}")

    missing = _executable_missing(check.verify)
    if missing:
        return Verdict(
            check.task_id,
            discriminates=False,
            runnable=False,
            detail=f"verify command not found on PATH: {missing}",
        )

    from chimera.sandbox import get_sandbox

    backend = sandbox if sandbox is not None else get_sandbox()
    if _declined_on_the_host(backend, check.verify):
        # An abstention, in the verifier's words: we could not run it, we do not claim we did.
        # `assert_discriminating` reports it as BROKEN and refuses the run — an apparatus nobody
        # could execute is not one anybody has proved.
        return Verdict(
            check.task_id,
            discriminates=False,
            runnable=False,
            detail=(
                f"verify command not run: its source is {_SOURCE!r} (the task file), the sandbox "
                "is not isolated, and the host-exec gate (CHIMERA_HOST_EXEC) declined to run it "
                "on the host"
            ),
        )

    try:
        done = backend.run(check.verify, timeout=timeout, cwd=workspace)
    except OSError as exc:
        return Verdict(check.task_id, discriminates=False, runnable=False, detail=str(exc))
    if done.timed_out:
        return Verdict(
            check.task_id,
            discriminates=False,
            runnable=False,
            detail=f"verify command did not finish in {timeout}s",
        )

    # A command that never ran exits non-zero too, and non-zero is the answer this guard calls
    # healthy — so an environment with no pytest would otherwise report every task as discriminating
    # and certify an apparatus that measured nothing: the exact failure this exists to prevent, one
    # level up. The exit codes below are POSIX shells (127 not found, 126 not executable) and cost
    # nothing to keep; Windows is why they are not the only check, because `cmd` answers a missing
    # command with plain exit 1 — indistinguishable from a failing test — and says so in the system
    # language, which is why `_executable_missing` runs first. "No module named" comes from Python
    # itself and is English on every platform, and it is the case that actually happens.
    if done.exit_code in (126, 127) or "No module named" in (done.stderr or ""):
        return Verdict(
            check.task_id,
            discriminates=False,
            runnable=False,
            detail=(
                f"verify command could not run (exit {done.exit_code}): "
                f"{(done.stderr or done.stdout or '').strip()[:200]}"
            ),
        )

    if done.exit_code == 0:
        # The one that matters. Say what was run and where, because the fix is always in the task.
        return Verdict(
            check.task_id,
            discriminates=False,
            detail=(
                "the test PASSES against the untouched workspace — this task scores a hit for an arm "
                "that does nothing, so it measures nothing"
            ),
        )

    # A non-zero exit is the healthy case: the test fails, so there is something for the work to fix.
    return Verdict(check.task_id, discriminates=True, detail=f"exit {done.exit_code}")


def run_selftest(checks: Iterable[TaskCheck], *, timeout: int = DEFAULT_TIMEOUT) -> list[Verdict]:
    """Every task, in order. Nothing is short-circuited: one vacuous task rarely travels alone, and a
    run that aborts on the first would hide the other four behind a second and third rerun."""
    return [check_discriminates(check, timeout=timeout) for check in checks]


def assert_discriminating(
    checks: Iterable[TaskCheck],
    *,
    timeout: int = DEFAULT_TIMEOUT,
    report: Callable[[str], None] = print,
) -> list[Verdict]:
    """Prove the suite before spending on it, or refuse to run.

    Raises ``SystemExit`` rather than returning a flag, and that is the design: a guard whose result
    the caller may ignore is the guard this project already wrote three times and never called. The
    exception carries the offending task ids, because "some task is broken" is not actionable.
    """
    verdicts = run_selftest(checks, timeout=timeout)
    vacuous = [v for v in verdicts if v.runnable and not v.discriminates]
    unrunnable = [v for v in verdicts if not v.runnable]

    if not vacuous and not unrunnable:
        report(f"[selftest] {len(verdicts)} tasks discriminate — the suite can measure something")
        return verdicts

    for verdict in vacuous:
        report(f"[selftest] VACUOUS  {verdict.task_id}: {verdict.detail}")
    for verdict in unrunnable:
        report(f"[selftest] BROKEN   {verdict.task_id}: {verdict.detail}")

    raise SystemExit(
        f"selftest failed before any model was called: "
        f"{len(vacuous)} vacuous, {len(unrunnable)} unrunnable, of {len(verdicts)} tasks. "
        "A task whose test passes on the untouched workspace scores a hit for every arm."
    )
