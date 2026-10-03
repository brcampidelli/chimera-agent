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
from chimera.scheduler.models import CronJob, JobOutcome, kill_flag_path
from chimera.scheduler.surface import scheduled_run_note
from chimera.tools.builtin import default_registry


def _job_allow(job: CronJob, settings: Any, warn: Callable[[str], None]) -> str | None:
    """The ``allow`` string for this job's governance, or None when the job names no tools.

    Narrowed by the deployment's own allowlist when there is one: an owner who fenced the whole
    install to five tools did not mean a job could reach a sixth by naming it.
    """
    if job.tools is None:
        return None
    wanted = [name.strip() for name in job.tools if name.strip()]
    fence = set(settings.tool_allowlist or ())
    if fence:
        outside = [name for name in wanted if name not in fence]
        if outside:
            warn(
                f"cron '{job.name}': tools {', '.join(outside)} are outside the deployment's "
                "allowlist and were not granted"
            )
        wanted = [name for name in wanted if name in fence]
    # An empty string is an explicit allowlist that grants nothing, which is what `tools: []` says.
    return ",".join(wanted)


def _warn_missing_tools(job: CronJob, registry: Any, warn: Callable[[str], None]) -> None:
    """Say which of the job's tools it did not get.

    A job that needed a tool it does not have fails quietly otherwise — the only trace is a worse
    answer. Read off the registry the run actually holds, after governance, so a misspelt name and
    one the deployment denies are both caught.
    """
    if job.tools is None:
        return
    held = set(registry.names()) if hasattr(registry, "names") else set()
    missing = sorted({name.strip() for name in job.tools if name.strip()} - held)
    if missing:
        warn(f"cron '{job.name}': runs without {', '.join(missing)} (no such tool, or not allowed)")


def make_run_job(
    *,
    settings: Any,
    backend: Any,
    workspace: Path,
    model: str | None,
    max_steps: int,
    usage_path: Path,
    warn: Callable[[str], None] = lambda _linha: None,
    daily_cap: Callable[[], float | None] | None = None,
) -> Callable[[CronJob], JobOutcome]:
    """Build the dispatch for one serve loop. Everything it used to close over is a parameter now.

    ``daily_cap`` is asked per dispatch when given. A serve loop runs for weeks on the ``settings``
    it was built with, and the cap is now set from a screen (``PATCH /api/config``): read off that
    snapshot, a cap saved at noon braked nothing until the app was relaunched, while the screen
    said it was set. Omitted, the cap is read from ``settings`` as before — right for a one-shot
    command that builds its settings and exits.
    """

    def run_job(job: CronJob) -> JobOutcome:
        """One dispatch, inside whatever the money allows.

        Three things happen here that did not before. The DAILY cap is checked before the job is
        allowed to spend anything; the job's own cap is handed to the loop; and what the job spent
        is written to the usage log — which is what makes the daily figure include cron at all. It
        did not: the log was written only by the chat turn, so a daily cap read from it would have
        been blind to exactly the spend it exists to bound.
        """
        cap = daily_cap() if daily_cap is not None else settings.daily_usd_cap
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
        #
        # `on_ledger` hands back the TaintLedger this call built, so it can reach the autonomous
        # loop as `taint=`. Until this line the ledger existed on the cron path only as a tool
        # wrapper: it narrowed dangerous calls after untrusted input, but the LOOP never heard of
        # it — so a tainted run's memory fact was stored `clean`, its answer was never stripped of
        # leaked control tokens before delivery, and a pause-on-taint was impossible. The three
        # defences every other surface gets from provenance were unreachable from the one surface
        # that runs unattended, every day, for months.
        job_ledger: Any = None

        def _take_ledger(ledger: Any) -> None:
            nonlocal job_ledger
            job_ledger = ledger

        job_registry, job_approvals = governed_profile(
            default_registry(job_root),
            settings=settings,
            home=settings.home,
            # The job's own tool list (`CronJob.tools`), when it has one. `None` passes `None`, and
            # governed_profile then applies the deployment's allowlist exactly as before.
            allow=_job_allow(job, settings, warn),
            surface=f"cron:{job.name}",
            # The job's own action is the person's instruction: a page or a file it names is a
            # fetch the person asked for, and the ledger records it as such.
            instruction=job.action,
            workspace=job_root,
            on_ledger=_take_ledger,
        )
        _warn_missing_tools(job, job_registry, warn)
        # The operator's stop switch, read at dispatch time. `chimera cron kill <id>` writes the
        # flag; the dispatch it actually stops deletes it on the way out — so a kill aimed at a
        # running job takes it down at the next attempt boundary, and a kill aimed at a job
        # between runs cannot leak into the run after the next one. The poller keeps watching the
        # file: the kill may arrive mid-run, and one `exists()` per step is the same cost the
        # trace log already pays.
        kill_path = kill_flag_path(settings.home, job.id)
        should_stop: Callable[[], bool] = lambda: kill_path.exists()  # noqa: E731
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
                turn_context=True,
                # What this run is: unattended, its answer delivered as it stands, nobody to answer
                # a question, and a fixed reply for "nothing new" (study 28, P3). In the turn
                # context rather than the system prompt, which stays the same bytes for a cache.
                turn_notes=scheduled_run_note(job),
                # And the one nudge that fits a run nobody attends: a run that answered with
                # questions is told to assume and act. Not `insist_on_action` — that also pushes
                # back a prose answer with no tool call, which for a report job is the job done.
                assume_on_questions=True,
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
            # The string lives in `jobs.json`, written long before this dispatch: `source="job"`
            # keeps the host-exec gate in front of it on a host without an isolated sandbox.
            verifier=CommandVerifier(job.verify, job_root, source="job") if gated else None,
            guard=WorkspaceGuard(job_root) if gated else None,
            config=AutonomousConfig(
                max_attempts=max(1, job.max_attempts),
                use_planner=False,
                # Unchanged: cron has never had a reviewer, and adding one would spend a model call
                # per dispatch to grade prose nobody asked to be graded.
                use_manager=False,
            ),
            # Provenance, finally reaching the loop (see `on_ledger` above): with the ledger here,
            # a tainted run's memory fact is stored `tainted`, its delivered answer is stripped of
            # leaked control tokens, and `pause_on_taint` below has something to read. None keeps
            # the old behaviour exactly — governance `off` builds no ledger at all.
            taint=job_ledger,
            # The operator's stop switch (cron kill): polled per step inside the worker and
            # between attempts by the loop itself.
            should_stop=should_stop,
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
        # The flag is consumed by the dispatch it stopped, never left behind: a kill that arrived
        # mid-run is spent here, and one that arrived between runs is spent by the run it stopped
        # at the top. Either way the NEXT dispatch starts with no flag — a stop is a request about
        # one run, not a permanent state of the job. Whether THIS run actually stopped is what the
        # run itself reports (`ending="cancelled"`); a kill that lands a moment after the answer
        # is complete does not rewrite a finished run into a cancelled one.
        kill_path.unlink(missing_ok=True)
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
            return JobOutcome(result.answer, cancelled=result.ending == "cancelled")
        return JobOutcome(
            result.answer,
            ok=bool(result.success),
            cancelled=result.ending == "cancelled",
        )

    return run_job
