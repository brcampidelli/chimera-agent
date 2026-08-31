"""The one scheduled dispatch, as a function a test can drive.

It lived as a closure inside a CLI command, which is the reason two defects reached users: nothing
could call it, so nothing tested the only place that produces a verdict or names a workspace. The
tests that existed handed a `JobOutcome` straight to the part that RECORDS one — every step of the
path except the step that was broken.

`make_run_job` takes what the closure used to capture. `warn` replaces the console for the same
reason the delivery sink takes one: a module that runs unattended must not own a terminal, and rich
markup is the caller's business.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chimera.api.usage import UsageRecord, append_usage, spent_today
from chimera.core.agent import Agent, AgentConfig
from chimera.core.instructions import load as load_identity
from chimera.core.instructions import render as render_identity
from chimera.governance import governed_profile
from chimera.orchestration.budget import BudgetExceeded
from chimera.scheduler.models import CronJob, JobOutcome
from chimera.tools.builtin import default_registry


def make_run_job(
    *,
    settings: Any,
    backend: Any,
    workspace: Path,
    model: str | None,
    max_steps: int,
    usage_path: Path,
    warn: Callable[[str], None] = lambda _linha: None,
) -> Callable[[CronJob], JobOutcome]:
    """Build the dispatch for one serve loop. Everything it used to close over is a parameter now."""

    def run_job(job: CronJob) -> JobOutcome:
        """One dispatch, inside whatever the money allows.

        Three things happen here that did not before. The DAILY cap is checked before the job is
        allowed to spend anything; the job's own cap is handed to the loop; and what the job spent
        is written to the usage log — which is what makes the daily figure include cron at all. It
        did not: the log was written only by the chat turn, so a daily cap read from it would have
        been blind to exactly the spend it exists to bound.
        """
        cap = settings.daily_usd_cap
        if cap and not job.critical:
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            spent, unpriced = spent_today(usage_path, today=today)
            if unpriced:
                raise BudgetExceeded(
                    f"today's spend cannot be known (an unpriced model ran); {job.name} refused. "
                    "Price the model, or mark this job critical if it must run regardless"
                )
            if spent >= cap:
                raise BudgetExceeded(f"daily cap reached: ${spent:.4f} of ${cap:.4f}")

        # The folder THIS job was written against, falling back to the process root. A schedule
        # fires for months: "wherever the app was pointing when it went off" is not a root anybody
        # chose, and on a packaged build the process root is the install directory.
        job_root = Path(job.workspace).expanduser() if job.workspace else workspace

        # Governance on the path that runs unattended. In `observe` this refuses nothing and
        # records what enforcement would have cost; the count is reported below, per job, which is
        # the whole point of having a middle state.
        job_registry, job_approvals = governed_profile(
            default_registry(job_root),
            settings=settings,
            home=settings.home,
            surface=f"cron:{job.name}",
        )
        agent = Agent(
            backend,
            job_registry,
            AgentConfig(
                model=model,
                max_steps=max_steps,
                max_usd=job.max_usd,
                # The same workspace the job's tools are rooted in. A scheduled job is the surface
                # LEAST able to be told the conventions any other way — nobody is at a terminal to
                # restate them — and it was the one reading none.
                project_root=job_root,
                # And the owner's own instructions, which every other surface that answers a
                # person already loads. A cron job reports to a person too — into Discord, into a
                # log, into the app — and this was the second surface answering in English to an
                # owner who had configured Portuguese, because the same rendered block carries the
                # "always answer in {language}" line.
                instructions=render_identity(load_identity(settings.home)),
                # The path that runs the most was the one with no step-level record at all. Without
                # it there is no success-versus-context curve, no replay of a job that went wrong,
                # and no reliability bench for the 24/7 loop — every one of those reads this file.
                trace_path=settings.home / "scheduler" / "cron_traces.jsonl",
            ),
        )
        # THE HARNESS, on the surface that runs most often and had none of it.
        #
        # `chimera solve`, the Code screen and the run endpoint all wrap the worker in the
        # autonomous loop — snapshot, verify, revert, retry, receipt. Cron called `agent.run`
        # directly, so the path that fires unattended every day, for months, was the only one with
        # no gate, no rollback, no retry and no entry in `runs.jsonl`. Nothing was watching the
        # thing that runs when nobody is watching.
        #
        # The guard and the verifier are armed ONLY when the job declares a `verify`, and that is
        # the whole design rather than a caution. `unverified_and_unchanged` fails an attempt that
        # changed no file, and most scheduled jobs are reports that change no files — arming it for
        # everyone would fail every report job with "this task requires editing code", which is a
        # defect this project has already measured on its own release. With no guard the diff gate
        # cannot fire (`diff_productive` stays None), so a report job behaves exactly as it did and
        # gains only the receipt.
        from chimera.core import AutonomousAgent, AutonomousConfig, WorkspaceGuard
        from chimera.core.verify import CommandVerifier

        gated = bool(job.verify.strip())
        loop = AutonomousAgent(
            agent,
            planner=None,
            manager=None,
            verifier=CommandVerifier(job.verify, job_root) if gated else None,
            guard=WorkspaceGuard(job_root) if gated else None,
            config=AutonomousConfig(
                max_attempts=max(1, job.max_attempts),
                use_planner=False,
                # Unchanged: cron has never had a reviewer, and adding one would spend a model call
                # per dispatch to grade prose nobody asked to be graded.
                use_manager=False,
            ),
            # The receipt. `runs.jsonl` is what the Runs screen reads, and a job that has fired
            # nightly for a month left nothing there to read.
            run_log=settings.home / "runs.jsonl",
            # And the folder it happened in, or the receipt is unreadable from the only screen
            # that would look for it. The guard and the tools were rooted here already; the LOOP
            # was not, so every scheduled receipt was written with an empty workspace — and a
            # receipt with no workspace is deliberately excluded from every filtered list, which
            # made each one invisible in the project it ran in. The fix above went half the
            # distance: the receipt existed and could not be found.
            workspace=job_root,
        )
        result = loop.run(job.action)
        # Summed across attempts rather than read off one: with `max_attempts > 1` a dispatch can
        # pay for several, and reporting the last one would understate the cost of exactly the
        # configuration that costs most. `usd` follows the all-or-nothing rule used everywhere else
        # — one unpriced attempt makes the total unknown, never smaller.
        attempts = result.attempts or []
        usd_values = [a.usd for a in attempts]
        append_usage(
            usage_path,
            UsageRecord(
                ts=datetime.now(UTC).isoformat(),
                # `run_id` in the session field is what joins this row to the trace line and to the
                # job: three records, one run, one key.
                session_id=f"cron:{job.id}:{attempts[-1].run_id if attempts else ''}",
                # The model that ANSWERED, falling back to the one asked for. `model` is optional on
                # this path (None = the settings default), so the empty string is the last resort —
                # a usage row with no model is still a usage row, and inventing a name would be
                # worse than leaving the field blank.
                model=(attempts[-1].model if attempts else None) or model or "",
                prompt_tokens=sum(int(a.prompt_tokens or 0) for a in attempts),
                completion_tokens=sum(int(a.completion_tokens or 0) for a in attempts),
                usd=None if any(v is None for v in usd_values) else sum(v or 0.0 for v in usd_values),
            ),
        )
        # Said out loud, on the job, every time. A governance decision that only exists inside an
        # observation string is one nobody counts — and on this path "nobody counted" reads as a
        # green tick over work that never happened.
        if job_approvals.granted or job_approvals.refused:
            touched = len(job_approvals.granted) + len(job_approvals.refused)
            detail = job_approvals.summary() or (
                f"{len(job_approvals.granted)} would be refused under enforce"
            )
            warn(
                f"cron '{job.name}': governance touched {touched} action(s) — "
                f"{detail}"
            )
        # The VERDICT, not just the answer. Returning a bare string is read as `ok` by the
        # dispatch, and that is how a job whose gate rejected every attempt and reverted every
        # file showed a green row with zero failures — measured twice, once before the outcome
        # type existed and once after, because the type was added and this line was not changed.
        #
        # Only when the job declared a gate. Without one there is nothing that could reject the
        # work, `success` then reflects gates this path does not run, and reporting it would turn
        # every ungated job into a failure. Absence of a verdict is not a failure.
        if not gated:
            return JobOutcome(result.answer)
        return JobOutcome(result.answer, ok=bool(result.success))

    return run_job
