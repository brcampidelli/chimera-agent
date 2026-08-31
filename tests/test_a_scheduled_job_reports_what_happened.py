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


# --- the receipt says where it ran, and the verdict is real ---------------------------------------
#
# These four properties used to be asserted HERE, against the source text of `chimera/cli/main.py`,
# because `run_job` was a closure inside a CLI command and nothing could call it. That is the
# fallback for a property with no reachable behaviour, and it is a poor one: one of those
# assertions passed with the line COMMENTED OUT, because the substring was still in the file.
#
# `make_run_job` is importable now, so all four are checked by RUNNING it — the verdict it returns,
# the folder it names, the money it logs and the daily cap it honours. See
# `tests/test_the_scheduled_dispatch_can_be_driven.py`. Nothing was dropped; it moved from reading
# the code to driving it, which is the only version of these checks that could have caught the two
# defects that shipped.
