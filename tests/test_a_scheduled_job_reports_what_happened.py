"""A scheduled job has to say where it ran and whether its work survived.

Both found by letting a real job fire in the shipped app, with a gate written to reject everything:

* the receipt was written with an **empty workspace**, so the run was invisible in the project it
  ran in — a receipt with no workspace is deliberately excluded from every filtered list, which is
  the rule that makes an unattributable receipt unfindable rather than misattributed;
* the job reported **``last_status: "ok"``** with ``consecutive_failures: 0`` after its gate had
  rejected both attempts and reverted every file, because the dispatch returned only the answer
  string and the run's ``success`` was dropped on the floor.

The second is the worse one: the gate WORKS — it ran, it judged, it reverted — and a gate whose
firing cannot be seen is not a signal. The silence alarm reads `consecutive_failures`, so it never
saw it either.

Free: no model call, no network.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from chimera.scheduler import CronJob, Scheduler, make_agent_dispatch
from chimera.scheduler.models import JobOutcome


def _scheduler(tmp_path: Path, **kw: Any) -> Scheduler:
    """A scheduler holding one job that is due at t=60.

    Scheduled through `schedule_cron` rather than constructed and stored by hand: that is what
    computes `next_run`, and a job whose `next_run` is None is never due — so a hand-built one
    would leave `last_status` as None and every assertion here would report the state before the
    tick, failing for a reason that has nothing to do with the outcome.
    """
    from chimera.scheduler import CronStore

    sched = Scheduler(CronStore(tmp_path / "jobs.json"))
    sched.schedule_cron("nightly", "* * * * *", "fix the tests", now=0, workspace="/w", **kw)
    return sched


def _depois(sched: Scheduler, dispatch: Any) -> CronJob:
    """Run the tick and return the job AS THE SCHEDULER LEFT IT.

    Read back rather than kept: the scheduler works on instances it loads from the store, so a
    local `CronJob` is a different object and would still hold the state from before the tick.
    """
    ran = sched.run_due(60.0, dispatch)
    assert ran, "the job was not due, so nothing here is measuring a dispatch"
    return sched.store.list()[0]


# --- the verdict reaches the schedule ----------------------------------------------------------


def test_a_job_whose_gate_rejected_the_work_is_not_ok(tmp_path: Path) -> None:
    """The measured case: two attempts, both reverted, and a green row on the Automation screen."""
    sched = _scheduler(tmp_path)
    dispatch = make_agent_dispatch(
        lambda task: "unused", run_job=lambda j: JobOutcome("gave up", ok=False)
    )

    job = _depois(sched, dispatch)

    assert job.last_status == "rejected"
    assert job.consecutive_failures == 1, "the silence alarm reads this and would never fire"
    assert job.last_error and "rejected" in job.last_error


def test_a_job_that_kept_its_work_is_still_ok(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path)
    dispatch = make_agent_dispatch(lambda task: "unused", run_job=lambda j: JobOutcome("done"))

    job = _depois(sched, dispatch)

    assert job.last_status == "ok"
    assert job.consecutive_failures == 0


def test_a_dispatch_with_no_verdict_still_means_ok(tmp_path: Path) -> None:
    """Every dispatch written before this returns a bare string, and must keep its meaning: a job
    that declared no gate has nothing that could reject it, and absence of a verdict is not a
    failure — the same rule the reviewer and the verifier follow when they abstain."""
    sched = _scheduler(tmp_path)

    job = _depois(sched, make_agent_dispatch(lambda task: "ran"))

    assert job.last_status == "ok"


def test_a_rejection_still_delivers_the_answer(tmp_path: Path) -> None:
    """The work was thrown away; the report of it was not. Someone reading a webhook has to learn
    the job produced nothing, which is exactly the message they need most."""
    entregue: dict[str, str] = {}
    sched = _scheduler(tmp_path)
    dispatch = make_agent_dispatch(
        lambda task: "unused",
        on_result=lambda j, ans: entregue.__setitem__(j.name, ans),
        run_job=lambda j: JobOutcome("the gate rejected every attempt", ok=False),
    )

    job = _depois(sched, dispatch)

    assert entregue["nightly"] == "the gate rejected every attempt"
    assert job.last_status == "rejected", "delivery succeeded, so the WORK was reported as fine"


def test_a_failed_delivery_does_not_erase_the_rejection(tmp_path: Path) -> None:
    """Two independent facts. A webhook that is down must not turn a rejected job into a fine one,
    nor the reverse."""
    sched = _scheduler(tmp_path)

    def sempre_falha(j: CronJob, ans: str) -> None:
        raise RuntimeError("webhook down")

    dispatch = make_agent_dispatch(
        lambda task: "unused", on_result=sempre_falha, delivery_retries=0,
        run_job=lambda j: JobOutcome("gave up", ok=False),
    )
    job = _depois(sched, dispatch)

    assert job.last_status == "rejected"


def test_an_exception_is_still_an_error_not_a_rejection(tmp_path: Path) -> None:
    """The two are different claims: `error` means something broke, `rejected` means the job worked
    and its own gate threw the result away. Collapsing them loses the distinction that makes the
    new one worth having."""
    sched = _scheduler(tmp_path)

    def explode(j: CronJob) -> JobOutcome:
        raise RuntimeError("boom")

    job = _depois(sched, make_agent_dispatch(lambda t: "x", run_job=explode))

    assert job.last_status == "error"


# --- the receipt says where it ran --------------------------------------------------------------


def test_the_scheduled_loop_is_given_the_folder_it_runs_in() -> None:
    """A wiring assertion, because the defect was wiring: the guard and the tools were rooted in the
    job's folder and the LOOP was not, so `_persist_receipt` wrote an empty workspace.

    Asserted on the source because the closure lives inside a CLI command and cannot be reached
    from a test — and asserting nothing was how it shipped.
    """
    fonte = Path("chimera/cli/main.py").read_text(encoding="utf-8")
    bloco = re.search(
        r"run_log=settings\.home / \"runs\.jsonl\",(.{0,900}?)\)\n", fonte, re.S
    )

    assert bloco, "the scheduled loop no longer names its run log — this test is looking at nothing"
    # An ARGUMENT, not the substring. Commenting the line out leaves `workspace=job_root` in the
    # file, and a substring check passes over it — measured: that sabotage walked straight through
    # the first version of this assertion.
    linhas = [linha.strip() for linha in bloco.group(1).splitlines()]

    assert "workspace=job_root," in linhas, (
        "the scheduled receipt is written without a workspace, so it is unfindable from the "
        "project it ran in"
    )


def _corpo_do_run_job() -> str:
    """The source of the closure that actually runs a scheduled job.

    Bounded to the function rather than searched across the file: `return result.answer` appears
    elsewhere for a different command, and a whole-file search would find that one and report the
    wiring as present while the scheduled path returned a bare string.
    """
    fonte = Path("chimera/cli/main.py").read_text(encoding="utf-8")
    inicio = fonte.index("def run_job(job: CronJob)")
    return fonte[inicio : fonte.index("def run_task(", inicio)]


def test_the_scheduled_job_actually_reports_its_verdict() -> None:
    """The half that shipped missing, and the reason it shipped missing.

    ``JobOutcome``, the ``rejected`` status and the engine branch that records it were all added
    together — and the one line that FEEDS them was not. A bare string is read as ``ok``, so a job
    whose gate rejected every attempt and reverted every file still showed a green row. Measured
    twice on a real install: once before the outcome type existed, and once after.

    The tests that were supposed to cover it injected a ``JobOutcome`` straight into the dispatch,
    which is every part of the path except the only place that produces one — a guard outside the
    flow, wearing the name of the thing it was not checking.
    """
    corpo = _corpo_do_run_job()
    linhas = [linha.strip() for linha in corpo.splitlines()]

    assert "return result.answer" not in linhas, (
        "the scheduled path returns a bare answer, which the dispatch reads as ok — the verdict "
        "never reaches the schedule"
    )
    assert "return JobOutcome(result.answer, ok=bool(result.success))" in linhas


def test_an_ungated_job_reports_no_verdict_at_all() -> None:
    """Without a gate there is nothing that could reject the work, and ``success`` then reflects
    gates this path does not run — reporting it would turn every ungated job into a failure."""
    corpo = _corpo_do_run_job()

    assert "if not gated:" in corpo
    assert "return JobOutcome(result.answer)" in [linha.strip() for linha in corpo.splitlines()]


def test_the_folder_comes_from_the_job_not_the_process() -> None:
    """`job_root` is the job's own folder falling back to the process root. Passing `workspace`
    (the process one) instead would attribute every scheduled run to whatever folder the daemon
    happened to start in — misattribution, which is worse than the empty string it replaces."""
    fonte = Path("chimera/cli/main.py").read_text(encoding="utf-8")

    assert "job_root = Path(job.workspace).expanduser() if job.workspace else workspace" in fonte
