"""Every hourly job was due at exactly the same instant, and dispatch is sequential.

`_next_after` returns the cron expression's exact boundary, so fifteen jobs written `0 * * * *` are
all due on the same tick. `run_due` walks them one at a time with a 30-minute ceiling each, and the
daemon ticks every 30 seconds — so a pile-up at `:00` does not merely run slowly, it delays every
LATER tick behind it. Fifteen jobs at two minutes each is half an hour in which nothing else on the
schedule can happen.

The offset is derived from the job's id, not drawn at random, and that is the whole design: a random
offset is redrawn on every restart, so the spread a deployment converged on is lost each time the
container comes up — and two jobs that happened to collide keep colliding on a new pair of numbers.
A hash of the id gives the same job the same slot forever, and different jobs different slots,
without storing anything.

Bounded twice: to a share of the interval, so a job that runs every minute is never pushed past the
next minute, and to an absolute ceiling, so a daily job is late by minutes rather than by hours.
"""

from __future__ import annotations

from pathlib import Path

from chimera.scheduler.engine import JITTER_CAP_S, Scheduler, _jitter, _next_after
from chimera.scheduler.store import CronStore

HORA = 3600.0
DIA = 86400.0


def _sched(tmp_path: Path, **kwargs: object) -> Scheduler:
    return Scheduler(CronStore(tmp_path / "jobs.json"), **kwargs)  # type: ignore[arg-type]


# ------------------------------------------------------------------ the spread


def test_two_hourly_jobs_are_not_due_at_the_same_instant(tmp_path: Path) -> None:
    """The whole point, stated as the thing that was false."""
    sch = _sched(tmp_path)
    a = sch.schedule_cron("a", "0 * * * *", "faça a", now=0)
    b = sch.schedule_cron("b", "0 * * * *", "faça b", now=0)

    assert a.next_run != b.next_run


def test_a_pile_of_jobs_spreads_over_minutes(tmp_path: Path) -> None:
    """Two differing is luck; the property is that a realistic pile actually thins out.

    Fifteen is the number from the deployment this came from. The assertion is deliberately weak on
    HOW they spread — a hash gives no guarantee of uniformity — and strong on the only thing that
    matters: no tick carries the whole pile.
    """
    sch = _sched(tmp_path)
    horarios = {
        sch.schedule_cron(f"job{i}", "0 * * * *", "trabalhe", now=0).next_run for i in range(15)
    }

    assert len(horarios) >= 12


def test_the_offset_is_the_same_after_a_restart(tmp_path: Path) -> None:
    """What a random offset cannot do, and the reason this is a hash.

    A fresh Scheduler over the same store — which is what a container restart is — must put the job
    back in the slot it already had, or the spread a deployment converged on is redrawn every time
    the process comes up.
    """
    sch = _sched(tmp_path)
    job = sch.schedule_cron("relatório", "0 * * * *", "trabalhe", now=0)
    antes = job.next_run

    sch.mark_ran(job, 0.0)

    assert job.next_run == antes


def test_two_installs_of_the_same_schedule_do_not_collide(tmp_path: Path) -> None:
    """Ids differ per install, so the same crontab on two machines lands on different seconds —
    which is what stops a shared provider being hit by everyone at `:00`."""
    a = _next_after("0 * * * *", 0.0, jitter_key="aaaaaaaa")
    b = _next_after("0 * * * *", 0.0, jitter_key="bbbbbbbb")

    assert a != b


# ------------------------------------------------------------------ the bounds


def test_a_minute_job_never_slides_into_the_next_minute() -> None:
    """The invariant that keeps this from being a bug: an offset must be smaller than the interval.

    A job written `* * * * *` that is pushed 90 seconds is not late — it has silently become a
    two-minute job, and its owner has no way to see that from the schedule they wrote.
    """
    base = _next_after("* * * * *", 0.0)
    for chave in ("a", "b", "c", "d", "e", "f", "0", "zzzzzzzz"):
        atrasado = _next_after("* * * * *", 0.0, jitter_key=chave)
        assert base <= atrasado < base + 60.0


def test_a_daily_job_is_late_by_minutes_not_hours() -> None:
    """The other bound. A tenth of a day is two and a half hours, and "every morning · 7h" firing
    at half past nine is a different promise from the one the screen made."""
    base = _next_after("0 7 * * *", 0.0)
    for chave in ("a", "b", "c", "zzzzzzzz"):
        assert base <= _next_after("0 7 * * *", 0.0, jitter_key=chave) <= base + JITTER_CAP_S


def test_the_job_is_never_pulled_earlier() -> None:
    """Jitter only ever delays. An offset that could go negative would fire a job before the time
    its owner wrote, which is the one direction a schedule may not move."""
    for expr in ("* * * * *", "0 * * * *", "0 7 * * *", "*/5 * * * *"):
        base = _next_after(expr, 1000.0)
        assert _next_after(expr, 1000.0, jitter_key="qualquer") >= base


def test_no_key_means_no_offset() -> None:
    """The escape hatch, and the reason every existing caller's arithmetic is unchanged."""
    assert _next_after("0 * * * *", 0.0) == _next_after("0 * * * *", 0.0, jitter_key="")


def test_an_empty_key_produces_no_offset_at_the_source() -> None:
    """Asserted on the helper, not through `_next_after`, and that is the point.

    The version above passes either way: `_next_after` returns early on an empty key, so the guard
    inside `_jitter` is never reached and an inert guard reads as a working one. A sabotage that
    removed it went undetected — which is exactly what an untested guard is for.
    """
    assert _jitter("", 3600.0) == 0.0
    assert _jitter("qualquer", 0.0) == 0.0
    assert _jitter("qualquer", -1.0) == 0.0


def test_it_can_be_turned_off(tmp_path: Path) -> None:
    """Someone whose job must fire at the exact boundary — a market open, a report due at midnight —
    needs the boundary, and a spread they cannot switch off is a spread they will work around."""
    sch = _sched(tmp_path, jitter=False)
    a = sch.schedule_cron("a", "0 * * * *", "faça a", now=0)
    b = sch.schedule_cron("b", "0 * * * *", "faça b", now=0)

    assert a.next_run == b.next_run == _next_after("0 * * * *", 0.0)


# ------------------------------------------------------------------ it still fires


def test_a_jittered_job_still_becomes_due(tmp_path: Path) -> None:
    """The failure a spread can quietly introduce: a job that is always a little in the future.

    `due()` compares against `next_run`, and `mark_ran` recomputes it — so an offset applied at the
    wrong end could push the boundary forward on every tick and the job would never run at all.
    """
    sch = _sched(tmp_path)
    job = sch.schedule_cron("relatório", "* * * * *", "trabalhe", now=0)
    corridos: list[str] = []

    sch.run_due((job.next_run or 0) + 1, lambda j: corridos.append(j.name))  # type: ignore[arg-type,func-returns-value]

    assert corridos == ["relatório"]
