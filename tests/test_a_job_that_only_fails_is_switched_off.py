"""A job that fails on every tick was rescheduled, identically, forever.

The counter was already right. `_record` increments `consecutive_failures` on every bad outcome and
deliberately excludes a budget refusal — the job never ran, so nothing about it failed, and letting
that climb would make a spending decision look like forty broken dispatches. That precision was
built and then read by nobody: the only consumers are `failing()`, the `cron doctor` table and the
JSON on `/api/features`, all of which report. `disable()` had exactly two callers, and both are a
person — an HTTP route someone hits and a command someone types.

So the instrument was built, calibrated, and never wired to an actuator. On a deployment with ~39
agent-jobs and a paid key, a job that started failing at 02:00 kept firing every tick until somebody
read a table. The daily spend cap bounds the money, but it is the wrong instrument: it stops *after
paying*, not *on detecting*.

Switching the job off is the conservative half of this. It stops the bleeding and leaves the
evidence — `last_error`, `last_status` and the counter all survive, and `cron enable` puts it back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.orchestration.budget import BudgetExceeded
from chimera.scheduler.engine import FAIL_LIMIT, Scheduler
from chimera.scheduler.store import CronStore


def _sched(tmp_path: Path, **kwargs: object) -> Scheduler:
    return Scheduler(CronStore(tmp_path / "jobs.json"), **kwargs)  # type: ignore[arg-type]


def _tick_until(sch: Scheduler, dispatch: object, *, times: int) -> None:
    """Advance the clock past each `next_run` so every tick actually dispatches.

    Reads `next_run` rather than assuming it. Written with a hardcoded 61 for the first tick first,
    and that was wrong the moment schedules gained an offset: a `* * * * *` job is due at 60 plus a
    few seconds, so the first tick dispatched nothing and every count in this file was off by one.
    """
    for _ in range(times):
        job = sch.store.list()[0]
        sch.run_due((job.next_run or 0) + 1, dispatch)  # type: ignore[arg-type]


def _explode(_job: object) -> None:
    raise RuntimeError("o provedor recusou")


def test_a_job_that_keeps_failing_stops_firing(tmp_path: Path) -> None:
    """The whole point: the fifth failure is the last dispatch, not the fifth of forty."""
    sch = _sched(tmp_path)
    sch.schedule_cron("relatório", "* * * * *", "escreva o relatório", now=0)

    _tick_until(sch, _explode, times=FAIL_LIMIT)

    assert sch.store.list()[0].enabled is False


def test_it_does_not_switch_off_early(tmp_path: Path) -> None:
    """A transient provider hiccup is not a broken job, and the limit is the whole judgement."""
    sch = _sched(tmp_path)
    sch.schedule_cron("relatório", "* * * * *", "escreva o relatório", now=0)

    _tick_until(sch, _explode, times=FAIL_LIMIT - 1)

    assert sch.store.list()[0].enabled is True


def test_one_success_clears_the_count(tmp_path: Path) -> None:
    """The failure mode of a counter that only goes up: a job that fails on Mondays for two months
    is switched off by arithmetic, having worked fine on fifty-eight days."""
    sch = _sched(tmp_path)
    sch.schedule_cron("relatório", "* * * * *", "escreva o relatório", now=0)
    estado = {"n": 0}

    def às_vezes(_job: object) -> None:
        estado["n"] += 1
        if estado["n"] != 3:
            raise RuntimeError("falhou")

    _tick_until(sch, às_vezes, times=FAIL_LIMIT + 2)

    assert sch.store.list()[0].enabled is True


def test_a_budget_refusal_never_switches_a_job_off(tmp_path: Path) -> None:
    """`_record` already refuses to count this, and the reason is written there: the job never ran,
    so nothing about it failed. Pinned here because that precision now decides whether a working
    job keeps running — a spending decision must not read as forty broken dispatches."""
    sch = _sched(tmp_path)
    sch.schedule_cron("relatório", "* * * * *", "escreva o relatório", now=0)

    def sem_saldo(_job: object) -> None:
        raise BudgetExceeded("daily cap reached")

    _tick_until(sch, sem_saldo, times=FAIL_LIMIT + 3)

    assert sch.store.list()[0].enabled is True


def test_the_reason_survives_being_switched_off(tmp_path: Path) -> None:
    """A job that is off and does not say why sends its owner to the server log.

    The evidence has to outlive the decision: the counter, the last status and the last error are
    what turn "why did this stop" into an answer instead of an investigation.
    """
    sch = _sched(tmp_path)
    sch.schedule_cron("relatório", "* * * * *", "escreva o relatório", now=0)

    _tick_until(sch, _explode, times=FAIL_LIMIT)
    job = sch.store.list()[0]

    assert job.consecutive_failures >= FAIL_LIMIT
    assert job.last_status == "error"
    assert job.last_error and "o provedor recusou" in job.last_error


def test_a_rejected_job_counts(tmp_path: Path) -> None:
    """`rejected` means the job ran and its own verify command threw the work away. Repeating that
    forever is exactly the state `_record`'s comment says made a nightly job look healthy while
    producing nothing for a month."""
    sch = _sched(tmp_path)
    sch.schedule_cron("relatório", "* * * * *", "escreva o relatório", now=0)

    _tick_until(sch, lambda _job: "rejected", times=FAIL_LIMIT)

    assert sch.store.list()[0].enabled is False


def test_the_limit_is_configurable(tmp_path: Path) -> None:
    """A fixed constant would make this untestable at any other value, and undeployable for someone
    whose provider is flakier than ours."""
    sch = _sched(tmp_path, fail_limit=2)
    sch.schedule_cron("relatório", "* * * * *", "escreva o relatório", now=0)

    _tick_until(sch, _explode, times=2)

    assert sch.store.list()[0].enabled is False


def test_a_switched_off_job_can_be_switched_back_on(tmp_path: Path) -> None:
    """This is a brake, not a delete. Whoever fixes the cause needs one command, not a rebuild."""
    sch = _sched(tmp_path)
    job = sch.schedule_cron("relatório", "* * * * *", "escreva o relatório", now=0)
    _tick_until(sch, _explode, times=FAIL_LIMIT)

    voltou = sch.enable(job.id, now=1000.0)

    assert voltou.enabled is True
    assert voltou.next_run and voltou.next_run > 1000.0
    # The counter goes with it. Leaving it at the limit turns the brake into a one-strike rule for
    # anything it has ever caught: the next single failure switches the job straight back off, and
    # whoever fixed the cause sees it die again for a reason that is no longer true.
    assert voltou.consecutive_failures == 0


def test_a_re_enabled_job_gets_the_full_limit_again(tmp_path: Path) -> None:
    """The assertion the counter reset exists for, stated as behaviour rather than as a field."""
    sch = _sched(tmp_path)
    job = sch.schedule_cron("relatório", "* * * * *", "escreva o relatório", now=0)
    _tick_until(sch, _explode, times=FAIL_LIMIT)
    sch.enable(job.id, now=1000.0)

    _tick_until(sch, _explode, times=FAIL_LIMIT - 1)

    assert sch.store.list()[0].enabled is True


def test_a_job_someone_paused_stays_out_of_the_failing_report(tmp_path: Path) -> None:
    """The other half of `disabled_by`, and the reason it is not just a boolean.

    A job the brake stopped must be reported — it is the most broken job on the machine. A job a
    PERSON paused must not: they know, and a report that keeps naming it trains its reader to skip
    the report. Same `enabled = False`, opposite facts.
    """
    sch = _sched(tmp_path)
    job = sch.schedule_cron("relatório", "* * * * *", "escreva o relatório", now=0)
    _tick_until(sch, _explode, times=FAIL_LIMIT - 1)  # failing, still on
    sch.disable(job.id)

    assert sch.failing() == []
    assert sch.store.get(job.id).consecutive_failures == FAIL_LIMIT - 1


@pytest.mark.parametrize("limite", [0, -1])
def test_a_limit_of_zero_is_refused(tmp_path: Path, limite: int) -> None:
    """A limit of zero switches off a job that has never failed — read as "the scheduler is broken"
    by everyone. Refusing at construction is louder than a job that vanishes on its first tick."""
    with pytest.raises(ValueError):
        _sched(tmp_path, fail_limit=limite)
