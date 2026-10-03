"""A scheduled job that could not run says so where its answers go — once per change, never its error.

Study 29, P3.1. The scheduler recorded every way a job can fail to run or finish — ``error``,
``timeout``, ``budget``, and the brake switching it off — in ``jobs.json``, the logs and
``cron_results.jsonl``, and the owner, who reads the job's Discord channel, saw none of it. A job
with ``notify="always"`` that raised posted nothing at all; one with ``failures_only`` posted the
exception's raw text, on every failing tick; a timeout and the brake reached no channel under any
mode, because the engine decides both outside the dispatch.

Each test drives the real pieces — ``Scheduler``, ``CronStore``, ``make_agent_dispatch``,
``make_deliver``, ``CronDaemon`` and ``make_failure_notifier`` — with a fake ``send`` and no network.
"""

from __future__ import annotations

import inspect
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from chimera.config import Settings
from chimera.orchestration.budget import BudgetExceeded
from chimera.scheduler import CronDaemon, CronStore, Scheduler, make_agent_dispatch
from chimera.scheduler.delivery import (
    Delivered,
    failure_notice,
    make_deliver,
    make_failure_notifier,
)
from chimera.scheduler.models import CronJob, JobOutcome

WEBHOOK = "https://discord.com/api/webhooks/123/segredo-do-canal"
#: What a provider's exception can carry. None of it may reach the channel.
SEGREDO = "401 from provider: key sk-or-v1-abcdef rejected; page said 'ignore your instructions'"


class _Wire:
    """`send` for both the sink and the notifier: records what would have gone on the wire."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[str] = []
        self.urls: list[str] = []

    def __call__(self, url: str, text: str) -> Delivered:
        self.urls.append(url)
        self.sent.append(text)
        return Delivered(self.ok, "HTTP 204" if self.ok else "HTTP 500")


def _inline(fn: Any) -> None:
    fn()


def _setup(
    tmp_path: Path,
    run_job: Any,
    *,
    notify: Any = "always",
    enabled: Any = lambda: True,
    post: Any = _inline,
    job_timeout: float | None = None,
    fail_limit: int = 5,
    deliver_to: str | None = WEBHOOK,
) -> tuple[Scheduler, CronJob, CronDaemon, _Wire]:
    sched = Scheduler(CronStore(tmp_path / "scheduler" / "jobs.json"), fail_limit=fail_limit, jitter=False)
    job = sched.schedule_cron(
        "portfolio watch", "* * * * *", "check", now=0.0, deliver_to=deliver_to, notify=notify
    )
    wire = _Wire()
    dispatch = make_agent_dispatch(
        lambda _t: "", make_deliver(tmp_path / "r.jsonl", send=wire), run_job=run_job
    )
    daemon = CronDaemon(
        sched, dispatch, job_timeout=job_timeout, heartbeat_path=tmp_path / "beat.json",
        on_outcome=make_failure_notifier(send=wire, enabled=enabled, post=post),
    )
    return sched, job, daemon, wire


def _tick(sched: Scheduler, daemon: CronDaemon, job_id: str) -> list[CronJob]:
    """Tick at the job's own next_run, whatever it is now."""
    return daemon.tick(now=(sched.store.get(job_id).next_run or 0.0) + 1)


def _raises(exc: BaseException) -> Any:
    def run_job(_job: CronJob) -> str:
        raise exc

    return run_job


# --------------------------------------------------------------------------- one post, no error text


@pytest.mark.parametrize("notify", ["always", "on_change", "failures_only"])
def test_a_run_that_raised_is_posted_once_under_every_notify_mode(
    tmp_path: Path, notify: str
) -> None:
    sched, job, daemon, wire = _setup(tmp_path, _raises(RuntimeError(SEGREDO)), notify=notify)

    (ran,) = _tick(sched, daemon, job.id)

    assert ran.last_status == "error"
    assert len(wire.sent) == 1, f"notify={notify} did not hear that its job could not finish"
    (linha,) = wire.sent
    assert "**portfolio watch**" in linha and "`error`" in linha
    assert "sk-or-v1" not in linha and "ignore your instructions" not in linha and "401" not in linha
    assert re.search(r"\d{4}-\d\d-\d\dT\d\d:\d\d[+-]\d\d:\d\d", linha), "the time is missing"


def test_a_timeout_is_posted_once_without_holding_the_tick(tmp_path: Path) -> None:
    """The engine abandons an overrun outside the dispatch, so the result sink never heard of it —
    the gap the engine's own comment named. The notice reads `last_status` after the tick."""
    solta = threading.Event()

    def preso(_job: CronJob) -> str:
        solta.wait(5)
        return "too late"

    try:
        sched, job, daemon, wire = _setup(tmp_path, preso, job_timeout=0.05)
        (ran,) = _tick(sched, daemon, job.id)
    finally:
        solta.set()

    assert ran.last_status == "timeout"
    assert len(wire.sent) == 1 and "`timeout`" in wire.sent[0]
    assert "time limit" in wire.sent[0]


def test_a_budget_refusal_is_posted_once_without_its_message(tmp_path: Path) -> None:
    sched, job, daemon, wire = _setup(
        tmp_path, _raises(BudgetExceeded(f"daily cap US$ 2.00 reached; {SEGREDO}"))
    )

    (ran,) = _tick(sched, daemon, job.id)

    assert ran.last_status == "budget"
    (linha,) = wire.sent
    assert "`budget`" in linha and "spend cap" in linha
    assert "US$ 2.00" not in linha and "sk-or-v1" not in linha


def test_the_brake_switching_a_job_off_is_its_own_post(tmp_path: Path) -> None:
    """Failing and switched off are different facts: one heals on its own, the other waits for a
    person. So the brake is a change of state, posted once, with the command that undoes it."""
    sched, job, daemon, wire = _setup(tmp_path, _raises(RuntimeError(SEGREDO)), fail_limit=2)

    _tick(sched, daemon, job.id)
    (ran,) = _tick(sched, daemon, job.id)

    assert not ran.enabled and ran.disabled_by == "brake"
    assert len(wire.sent) == 2
    assert "`error`" in wire.sent[0]
    assert "switched off" in wire.sent[1] and f"chimera cron enable {job.id}" in wire.sent[1]
    assert "2 times in a row" in wire.sent[1]
    assert all("sk-or-v1" not in linha for linha in wire.sent)
    assert sched.store.get(job.id).failure_notice == "brake"


# --------------------------------------------------------------------------- once per change


def test_a_failure_that_repeats_is_not_posted_on_every_tick(tmp_path: Path) -> None:
    sched, job, daemon, wire = _setup(tmp_path, _raises(RuntimeError("provider down")))

    for _ in range(4):
        _tick(sched, daemon, job.id)

    assert sched.store.get(job.id).consecutive_failures == 4
    assert len(wire.sent) == 1, "an outage was announced once per tick"


def test_a_change_from_one_failure_to_another_is_news(tmp_path: Path) -> None:
    """`error` wants a code fix and `budget` a number in the configuration: telling the owner the
    first and staying quiet about the second would send them to the wrong place."""
    falhas = iter([RuntimeError("down"), BudgetExceeded("cap")])

    def run_job(_job: CronJob) -> str:
        raise next(falhas)

    sched, job, daemon, wire = _setup(tmp_path, run_job)
    _tick(sched, daemon, job.id)
    _tick(sched, daemon, job.id)

    assert ["`error`" in wire.sent[0], "`budget`" in wire.sent[1]] == [True, True]


def test_recovery_is_posted_once(tmp_path: Path) -> None:
    respostas: list[Any] = [RuntimeError("down"), RuntimeError("down"), "fine", "fine"]

    def run_job(_job: CronJob) -> str:
        proxima = respostas.pop(0)
        if isinstance(proxima, Exception):
            raise proxima
        return str(proxima)

    sched, job, daemon, wire = _setup(tmp_path, run_job, notify="failures_only")
    for _ in range(4):
        _tick(sched, daemon, job.id)

    assert len(wire.sent) == 2
    assert "`error`" in wire.sent[0]
    assert "running again" in wire.sent[1] and "**portfolio watch**" in wire.sent[1]
    assert sched.store.get(job.id).failure_notice == ""


def test_a_restart_in_the_middle_of_an_outage_does_not_announce_it_again(tmp_path: Path) -> None:
    sched, job, daemon, wire = _setup(tmp_path, _raises(RuntimeError("down")))
    _tick(sched, daemon, job.id)

    reiniciado = Scheduler(CronStore(tmp_path / "scheduler" / "jobs.json"), jitter=False)
    assert reiniciado.store.get(job.id).failure_notice == "error"
    daemon2 = CronDaemon(
        reiniciado, daemon.dispatch, heartbeat_path=tmp_path / "beat.json",
        on_outcome=make_failure_notifier(send=wire, post=_inline),
    )
    _tick(reiniciado, daemon2, job.id)

    assert len(wire.sent) == 1


def test_a_rejected_run_ends_the_outage_without_a_notice_of_its_own(tmp_path: Path) -> None:
    """A rejected run finished, and its answer went to the channel through the sink — that answer
    is the owner's evidence the job runs again. A notice on top would say it twice."""
    respostas: list[Any] = [RuntimeError("down"), JobOutcome("the gate said no", ok=False)]

    def run_job(_job: CronJob) -> Any:
        proxima = respostas.pop(0)
        if isinstance(proxima, Exception):
            raise proxima
        return proxima

    sched, job, daemon, wire = _setup(tmp_path, run_job)
    _tick(sched, daemon, job.id)
    _tick(sched, daemon, job.id)

    assert wire.sent[1:] == ["**portfolio watch**\nthe gate said no"]
    assert sched.store.get(job.id).failure_notice == ""


def test_the_decision_itself_says_nothing_for_a_failure_already_told() -> None:
    """The rule, on the pure function the notifier is built on: same failure, no line."""
    job = CronJob(
        id="j1", name="w", schedule="* * * * *", action="a", deliver_to=WEBHOOK,
        last_status="error", consecutive_failures=3, failure_notice="error",
    )

    assert failure_notice(job, 60.0) == ("error", None)


def test_a_killed_run_changes_nothing() -> None:
    job = CronJob(
        id="j1", name="w", schedule="* * * * *", action="a", deliver_to=WEBHOOK,
        last_status="cancelled", failure_notice="error",
    )

    assert failure_notice(job, 60.0) == ("error", None)


# --------------------------------------------------------------------------- the switches


def test_the_flag_off_posts_nothing_and_moves_nothing(tmp_path: Path) -> None:
    desligado = Settings(CHIMERA_CRON_NOTIFY_FAILURES="0")
    assert desligado.cron_notify_failures is False
    sched, job, daemon, wire = _setup(
        tmp_path, _raises(RuntimeError("down")), enabled=lambda: desligado.cron_notify_failures
    )

    _tick(sched, daemon, job.id)

    assert wire.sent == []
    assert sched.store.get(job.id).failure_notice == "", (
        "switching the flag back on would then stay quiet about an outage still in progress"
    )


def test_the_flag_is_on_by_default_and_editable_from_the_app() -> None:
    from chimera.api.config_api import is_editable

    assert Settings.model_fields["cron_notify_failures"].default is True
    assert is_editable("CHIMERA_CRON_NOTIFY_FAILURES")


def test_a_job_with_nowhere_to_post_is_left_alone(tmp_path: Path) -> None:
    sched, job, daemon, wire = _setup(tmp_path, _raises(RuntimeError("down")), deliver_to=None)

    _tick(sched, daemon, job.id)

    assert wire.sent == [] and sched.store.get(job.id).failure_notice == ""


def test_failures_only_records_the_error_and_posts_only_the_notice(tmp_path: Path) -> None:
    """The sink still writes the exception into the result file — the owner's own record, read on
    their own machine — and holds it back from the channel, where the notice goes instead."""
    sched, job, daemon, wire = _setup(
        tmp_path, _raises(RuntimeError(SEGREDO)), notify="failures_only"
    )

    _tick(sched, daemon, job.id)

    (registro,) = [
        json.loads(linha)
        for linha in (tmp_path / "r.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert SEGREDO in registro["answer"] and registro["status"] == "error"
    assert "never by its error" in registro["skipped"] and "delivered" not in registro
    assert len(wire.sent) == 1 and SEGREDO not in wire.sent[0]


# --------------------------------------------------------------------------- not on the tick


def test_a_slow_webhook_does_not_delay_the_next_job(tmp_path: Path) -> None:
    """Driven with the default `post` — a real thread — and a webhook that hangs. The tick has to
    come back and the second job has to run while the first notice is still waiting."""
    solta = threading.Event()
    enviados: list[str] = []

    def lento(url: str, text: str) -> Delivered:
        solta.wait(10)
        enviados.append(text)
        return Delivered(True, "HTTP 204")

    sched = Scheduler(CronStore(tmp_path / "scheduler" / "jobs.json"), jitter=False)
    a = sched.schedule_cron("first", "* * * * *", "a", now=0.0, deliver_to=WEBHOOK)
    b = sched.schedule_cron("second", "* * * * *", "b", now=0.0, deliver_to=WEBHOOK)
    daemon = CronDaemon(
        sched, make_agent_dispatch(lambda _t: "", run_job=_raises(RuntimeError("down"))),
        heartbeat_path=tmp_path / "beat.json",
        on_outcome=make_failure_notifier(send=lento),
    )

    inicio = time.monotonic()
    try:
        ran = daemon.tick(now=max(a.next_run or 0.0, b.next_run or 0.0) + 1)
        gasto = time.monotonic() - inicio
    finally:
        solta.set()

    assert sorted(j.name for j in ran) == ["first", "second"]
    assert gasto < 2.0, f"the tick waited {gasto:.1f}s on a webhook"
    for _ in range(100):
        if len(enviados) == 2:
            break
        time.sleep(0.02)
    assert len(enviados) == 2, "the notices were dropped instead of sent off the tick"


def test_a_notice_that_fails_to_post_is_said_out_loud(tmp_path: Path) -> None:
    avisos: list[str] = []
    job = CronJob(
        id="j1", name="w", schedule="* * * * *", action="a", deliver_to=WEBHOOK,
        last_status="error", consecutive_failures=1,
    )

    make_failure_notifier(send=_Wire(ok=False), warn=avisos.append, post=_inline)(job, 60.0)

    assert avisos == ["cron 'w': failure notice not delivered — HTTP 500"]
    assert "segredo-do-canal" not in avisos[0]


# --------------------------------------------------------------------------- the engine stays pure


def test_the_engine_on_its_own_posts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_brake` says the engine takes no clock and no I/O. Driven alone, past the brake, with a
    destination on the job and the network rigged to fail the test, it must not reach for it."""
    import urllib.request

    def _rede(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the engine opened a connection")

    monkeypatch.setattr(urllib.request, "urlopen", _rede)
    sched = Scheduler(CronStore(tmp_path / "jobs.json"), fail_limit=1, jitter=False)
    job = sched.schedule_cron("w", "* * * * *", "a", now=0.0, deliver_to=WEBHOOK)

    def quebra(_job: CronJob) -> None:
        raise RuntimeError("down")

    (ran,) = sched.run_due(now=(job.next_run or 0.0) + 1, dispatch=quebra)

    assert ran.disabled_by == "brake" and ran.failure_notice == ""


def test_the_engine_module_imports_nothing_that_talks_to_the_network() -> None:
    """Read from the import statements, not the text: the docstrings may name the notifier."""
    import ast

    import chimera.scheduler.engine as engine

    modulos: set[str] = set()
    for no in ast.walk(ast.parse(inspect.getsource(engine))):
        if isinstance(no, ast.Import):
            modulos |= {alias.name for alias in no.names}
        elif isinstance(no, ast.ImportFrom) and no.module:
            modulos.add(no.module)
    proibidos = ("chimera.scheduler.delivery", "urllib", "socket", "threading", "requests", "http")
    assert not [m for m in modulos if m.split(".")[0] in proibidos or m in proibidos], modulos


def test_the_daemon_the_app_starts_carries_the_notifier_and_the_flag() -> None:
    """The wiring, read from the source for the reason `test_cron_delivery` gives: the daemon is
    built inside a command that needs a provider and a port to run."""
    import chimera.cli.main as cli

    fonte = inspect.getsource(cli._start_cron_daemon)
    assert "make_failure_notifier(" in fonte and "on_outcome=notices" in fonte
    assert "cron_notify_failures" in fonte
    disparo = inspect.getsource(cli.cron_fire)
    assert "make_failure_notifier(" in disparo and "cron_notify_failures" in disparo


# --------------------------------------------------------------------------- the terminal


def test_cron_add_records_where_the_answers_go_and_never_prints_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from chimera.cli.main import app

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    runner = CliRunner()
    added = runner.invoke(app, ["cron", "add", "watch", "*/5 * * * *", "check", "--deliver-to", WEBHOOK])
    evento = runner.invoke(
        app, ["cron", "add", "on-deploy", "deploy", "check", "--event", "--deliver-to", WEBHOOK]
    )
    assert added.exit_code == 0, added.output
    assert evento.exit_code == 0, evento.output

    jobs = CronStore(tmp_path / "home" / "scheduler" / "jobs.json").list()
    assert sorted((j.trigger, j.deliver_to) for j in jobs) == [("cron", WEBHOOK), ("event", WEBHOOK)]
    listed = runner.invoke(app, ["cron", "list"])
    assert "deliver_to=https://discord.com/" in listed.output
    for saida in (added.output, evento.output, listed.output):
        assert "segredo-do-canal" not in saida and "/123/" not in saida


@pytest.mark.parametrize(
    "extra",
    [
        ["--deliver-to", "file:///etc/passwd"],
        ["--deliver-to", "discord.com/api/webhooks/1/x"],
        ["--deliver-to", WEBHOOK, "--webhook"],
    ],
)
def test_cron_add_refuses_a_destination_it_could_not_honour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: list[str]
) -> None:
    from typer.testing import CliRunner

    from chimera.cli.main import app

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    refused = CliRunner().invoke(app, ["cron", "add", "x", "* * * * *", "y", *extra])

    assert refused.exit_code == 1
    assert not (tmp_path / "home" / "scheduler" / "jobs.json").exists() or not CronStore(
        tmp_path / "home" / "scheduler" / "jobs.json"
    ).list()
