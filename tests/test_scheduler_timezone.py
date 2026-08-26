"""Which clock a cron line is read against.

``0 7 * * *`` means seven in the morning where the machine is. The engine used to pin the base to
UTC, so on a desktop four hours west the job fired at 03:00 — found on a real install whose own
record read ``last_run 03:00`` under a schedule of ``0 7``, five nights running.

**No existing test could show this**, and that is the interesting part rather than an aside. Every
scheduler test asserts relative facts (``next_run > now``, ``due(next_run)`` returns the job), which
hold in any time zone under either implementation. And CI runs in UTC, where local time IS UTC and
the two implementations agree exactly — a defect invisible on the machine that tests it and visible
on the machine of every person who uses it.

So these tests fix the zone rather than trusting the host's, and say so when they cannot.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from chimera.scheduler import CronStore, Scheduler

NOW = 1_787_000_000.0  # a fixed instant, so nothing here depends on when it runs

#: Setting TZ only takes effect after ``tzset``, which POSIX has and Windows does not.
sem_tzset = pytest.mark.skipif(
    not hasattr(time, "tzset"), reason="the time zone is only settable at runtime on POSIX"
)


@pytest.fixture
def fuso(monkeypatch: pytest.MonkeyPatch):
    """Run the body in a named zone, and put the host's own back afterwards."""

    def usar(nome: str) -> None:
        monkeypatch.setenv("TZ", nome)
        time.tzset()

    yield usar
    monkeypatch.undo()
    if hasattr(time, "tzset"):
        time.tzset()


def _agendar(tmp_path: Path, expr: str, *, now: float = NOW) -> float:
    sched = Scheduler(CronStore(tmp_path / "jobs.json"))
    job = sched.schedule_cron("resumo", expr, "diga bom dia", now=now)
    assert job.next_run is not None
    return job.next_run


@sem_tzset
def test_a_cron_hour_is_the_hour_on_the_users_clock(tmp_path: Path, fuso) -> None:
    """The whole defect, in one assertion: ask for 7am, get 7am.

    Under the UTC base this lands at 04:00 in São Paulo — the same four-hour slide that put a
    morning job in the middle of the night.
    """
    fuso("America/Sao_Paulo")
    quando = datetime.fromtimestamp(_agendar(tmp_path, "0 7 * * *"))
    assert quando.hour == 7, f"a job scheduled for 07:00 comes due at {quando:%H:%M} local"


@sem_tzset
def test_it_holds_east_of_greenwich_too(tmp_path: Path, fuso) -> None:
    """East as well as west — a fix that only works one side of the meridian is half a fix.

    Tokyo is UTC+9, so a UTC base slides this one the other way, into the evening.
    """
    fuso("Asia/Tokyo")
    quando = datetime.fromtimestamp(_agendar(tmp_path, "30 9 * * *"))
    assert (quando.hour, quando.minute) == (9, 30), f"came due at {quando:%H:%M} local"


@sem_tzset
def test_in_utc_both_readings_agree_which_is_why_this_survived(tmp_path: Path, fuso) -> None:
    """The control, and the explanation.

    On a machine in UTC there is nothing to see: the old base and the new one produce the same
    instant. Servers run in UTC and so does CI, so the fleet this code was written against could
    never have shown the defect. Asserting it here keeps that from being a story — if this ever
    fails, the change did something beyond moving the base.
    """
    fuso("UTC")
    quando = datetime.fromtimestamp(_agendar(tmp_path, "0 7 * * *"), tz=None)
    assert quando.hour == 7
    assert os.environ["TZ"] == "UTC"


def test_a_timestamp_near_the_epoch_does_not_raise(tmp_path: Path) -> None:
    """Small ``now`` values must survive, on every platform — and this one is not hypothetical.

    Reading the clock locally invites a trap that only exists west of Greenwich: a naive
    ``fromtimestamp(60)`` lands in 1969, and asking Windows for the local offset of a pre-1970
    instant raises ``OSError: [Errno 22]``. The scheduler's own tests pass ``now=60``, so the naive
    form took the entire Windows job down while Linux and WSL stayed green — which is how it reached
    CI in the first place.

    No zone fixture and no skip: this one has to run everywhere, because the platform IS the
    variable. On Linux it is a cheap assertion; on Windows it is the regression.
    """
    for quando in (0.0, 60.0, 3600.0):
        assert _agendar(tmp_path, "0 7 * * *", now=quando) > quando


@sem_tzset
def test_a_schedule_that_already_passed_today_lands_tomorrow(tmp_path: Path, fuso) -> None:
    """Local reading must not break the ordinary property: the next run is in the future.

    Worth its own test because a zone shift is exactly the kind of change that can produce a time
    in the past for half the day and go unnoticed until someone's job fires immediately.
    """
    fuso("America/Sao_Paulo")
    for hora in range(0, 24, 3):
        proximo = _agendar(tmp_path, f"0 {hora} * * *")
        assert proximo > NOW, f"'0 {hora} * * *' came due in the past"
        assert proximo - NOW <= 24 * 3600 + 1, f"'0 {hora} * * *' is more than a day out"
