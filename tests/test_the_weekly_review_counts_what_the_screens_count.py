"""The weekly review must count what the screens count — and say so when it cannot.

Study 29, P3.4. The review joins four sources that are each already published somewhere: the Cost
screen (``/api/usage``), "Was it worth it?" (``/api/code/worth``), ``chimera approve``'s answer line
(``answer_stats``) and the failing list of ``cron doctor`` (``/api/cron/silence``). A join that does
not find its field returns a plausible zero, never an error — the lesson of a calibration gate whose
end-to-end figure counted every text case as correct because one field was never read. So every
total here is checked against the total its own source publishes, printed beside it, over a home
built so the two must agree; then rows from outside the week are added and the review must not move
while the published figure moves by exactly those rows.

No model call anywhere: the scheduled job this report rides on is dispatched by code, and one test
hands it a backend that fails the run if it is ever called.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.governance.pending import answer_stats, history, summarize_answers
from chimera.scheduler.models import CronJob
from chimera.scheduler.store import CronStore
from chimera.scheduler.weekly_review import (
    BUILTIN_KEY,
    JOB_SCHEDULE,
    WEEKLY_REVIEW,
    build_weekly_review,
    find_proposal,
    owner_lang,
    propose,
    render_weekly_review,
    run_builtin,
)

#: The review's "now": a Monday morning. The week is 2026-09-28 09:00 .. 2026-10-05 09:00 UTC.
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC).timestamp()
DENTRO = "2026-10-01T12:00:00+00:00"
FORA = "2026-09-20T12:00:00+00:00"
DENTRO_EPOCH = datetime(2026, 10, 1, 12, 0, tzinfo=UTC).timestamp()
FORA_EPOCH = datetime(2026, 9, 20, 12, 0, tzinfo=UTC).timestamp()


# --- building a home --------------------------------------------------------------------------


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _usage(ts: str, usd: float | None, session: str = "s1", kind: str | None = None) -> dict[str, Any]:
    return {"ts": ts, "session_id": session, "model": "m", "prompt_tokens": 100,
            "completion_tokens": 10, "usd": usd, "route_kind": kind}


def _attempt(run_id: str, *, usd: float | None, success: bool, reverted: bool = False,
             evidence: str = "none", productive: bool | None = True) -> dict[str, Any]:
    return {"index": 1, "run_id": run_id, "model": "m", "usd": usd, "success": success,
            "verified": success, "reverted": reverted, "evidence": evidence,
            "diff_productive": productive, "prompt_tokens": 50, "completion_tokens": 5}


def _run(ts: str, *attempts: dict[str, Any], success: bool, profile: str | None = None) -> dict[str, Any]:
    # The run's own `usd` as `build_receipt` writes it (`total_usd`): the worth view reads THIS
    # field, not the attempts' — a first draft of this fixture left it out and the published cost
    # read None while the review's spend, which reads the attempts, did not.
    precos = [a["usd"] for a in attempts]
    usd = None if any(p is None for p in precos) else round(sum(precos), 6)
    return {"ts": ts, "task": "t", "success": success, "workspace": "C:/w",
            "profile": profile, "attempts": list(attempts), "usd": usd}


def _question(asked_at: float, outcome: str, seconds: float | None, level: str = "review") -> dict[str, Any]:
    return {"id": f"q{asked_at}{outcome}", "action": "a", "reason": "r", "decision": level,
            "asked_at": asked_at, "resolved_at": asked_at + (seconds or 900.0),
            "seconds_to_answer": seconds, "waited_seconds": seconds or 900.0, "outcome": outcome}


def _week_of_rows(home: Path, ts: str, epoch: float) -> None:
    """One week's worth of every source, all dated ``ts``."""
    _jsonl(home / "usage.jsonl", [
        _usage(ts, 0.0125),
        _usage(ts, 0.0030, session="s2"),
        _usage(ts, None, session="s3"),          # unpriced: a floor, never $0
        _usage(ts, 0.0007, kind="memory"),        # priced, and not a turn
        # A scheduled run writes BOTH a usage row and a receipt; the Cost screen counts it once.
        _usage(ts, 0.0100, session=f"cron:j1:cr-{epoch}"),
    ])
    _jsonl(home / "runs.jsonl", [
        _run(ts, _attempt(f"a-{epoch}", usd=0.02, success=True, evidence="verifier"), success=True),
        _run(ts, _attempt(f"b-{epoch}", usd=0.01, success=False, reverted=True),
             _attempt(f"b2-{epoch}", usd=0.01, success=True, productive=False), success=True,
             profile="max"),
        _run(ts, _attempt(f"c-{epoch}", usd=0.005, success=False, reverted=True), success=False),
        _run(ts, _attempt(f"cr-{epoch}", usd=0.0100, success=True), success=True),
    ])
    _jsonl(home / "approvals" / "history.jsonl", [
        _question(epoch, "approved", 30.0),
        _question(epoch + 1, "approved", 90.0, level="block"),
        _question(epoch + 2, "refused", 12.0),
        _question(epoch + 3, "timeout", None, level="block"),
    ])


def _jobs(home: Path) -> CronStore:
    store = CronStore(home / "scheduler" / "jobs.json")
    base = {"trigger": "cron", "schedule": "0 7 * * *", "action": "x"}
    store.add(CronJob(id="ok1", name="saudavel", **base))
    store.add(CronJob(id="f1", name="resumo", consecutive_failures=3, last_status="error", **base))
    store.add(CronJob(id="f2", name="guardiao", enabled=False, disabled_by="brake",
                      consecutive_failures=5, last_status="timeout", **base))
    # Paused by a person while failing: they know, and the doctor stays quiet about it.
    store.add(CronJob(id="p1", name="pausado", enabled=False, disabled_by="human",
                      consecutive_failures=2, last_status="error", **base))
    return store


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def client(home: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from chimera.api.app import build_api_app
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    return TestClient(
        build_api_app(lambda: None, settings=Settings(CHIMERA_HOME=str(home)))  # type: ignore[arg-type]  # no backend needed
    )


# --- every total is the published total ------------------------------------------------------


def test_every_total_reproduces_the_total_its_screen_publishes(home: Path, client: TestClient) -> None:
    _week_of_rows(home, DENTRO, DENTRO_EPOCH)
    _jobs(home)

    review = build_weekly_review(home, now=NOW)
    usage = client.get("/api/usage").json()["totals"]
    worth = client.get("/api/code/worth").json()
    silence = client.get("/api/cron/silence").json()
    stats = answer_stats(home)

    # Printed side by side, so a failure shows WHICH join lost its field rather than "x != y".
    print("spend   published", usage["usd"], usage["unpriced_turns"], usage["turns"],
          "| review", review.spend)
    print("runs    published", worth["total_runs"], [p["passed"] for p in worth["profiles"]],
          "| review", review.runs)
    print("approve published", stats["asked"], stats["approved"], stats["timeouts"], stats["p50_seconds"],
          "| review", review.approvals)
    print("jobs    published", [j["id"] for j in silence["failing"]], "| review", review.failing)

    assert review.spend is not None
    assert review.spend.usd == pytest.approx(usage["usd"])
    assert review.spend.unpriced == usage["unpriced_turns"] == 1
    assert review.spend.turns == usage["turns"]
    # And the join the Cost screen does: the scheduled run's money is in there once, not twice.
    assert review.spend.usd == pytest.approx(0.0125 + 0.0030 + 0.0007 + 0.0100 + 0.02 + 0.02 + 0.005)

    assert review.runs is not None
    profiles = worth["profiles"]
    assert review.runs.runs == worth["total_runs"] == 4
    assert review.runs.passed == sum(p["passed"] for p in profiles) == 3
    assert review.runs.not_passed == 1
    assert review.runs.passed_by_verifier == sum(p["passed_by_verifier"] for p in profiles) == 1
    assert review.runs.reverted == sum(p["reverted"] for p in profiles) == 2
    assert review.runs.unproductive == sum(p["unproductive"] for p in profiles) == 1
    assert review.runs.attempts == sum(p["attempts_total"] for p in profiles) == 5
    assert review.runs.usd == pytest.approx(sum(p["usd_total"] for p in profiles))
    assert review.runs.usd_known_runs == sum(p["usd_known_runs"] for p in profiles)

    assert review.approvals is not None
    for campo in ("asked", "answered", "approved", "refused", "timeouts", "answer_rate", "p50_seconds"):
        assert getattr(review.approvals, campo) == stats[campo], campo
    assert review.approvals.approval_rate == pytest.approx(2 / 3)

    assert review.failing is not None
    assert [j.id for j in review.failing] == [j["id"] for j in silence["failing"]]
    assert [j.consecutive_failures for j in review.failing] == [
        j["consecutive_failures"] for j in silence["failing"]
    ]
    assert {j.id for j in review.failing} == {"f1", "f2"}
    assert review.missing == ()


def test_rows_from_before_the_week_move_the_screens_and_not_the_review(
    home: Path, client: TestClient
) -> None:
    """The window is the only difference allowed between the review and its sources: a second copy
    of every row, dated two weeks earlier, must double the published totals and leave the review
    exactly where it was."""
    _week_of_rows(home, DENTRO, DENTRO_EPOCH)
    antes = build_weekly_review(home, now=NOW)
    publicado_antes = client.get("/api/usage").json()["totals"]["usd"]

    _week_of_rows(home, FORA, FORA_EPOCH)
    depois = build_weekly_review(home, now=NOW)
    publicado_depois = client.get("/api/usage").json()["totals"]["usd"]

    assert publicado_depois == pytest.approx(2 * publicado_antes)
    assert client.get("/api/code/worth").json()["total_runs"] == 8
    assert answer_stats(home)["asked"] == 8
    assert depois.spend == antes.spend
    assert depois.runs == antes.runs
    assert depois.approvals == antes.approvals


def test_the_habituation_columns_count_only_what_a_person_answered(home: Path) -> None:
    """Study 31, G31-04. The overall approval rate reads a night of timeouts as vigilance; the
    habituation columns count only the questions a PERSON answered, and the fast-approval share is
    the rubber-stamp signature the literature measures (2606.22721: approval rises while reading
    time falls)."""
    _week_of_rows(home, DENTRO, DENTRO_EPOCH)
    # A rubber-stamped night: three yeses under the reading time, and a timeout the system gave.
    _jsonl(home / "approvals" / "history.jsonl", [
        _question(DENTRO_EPOCH + 10, "approved", 4.0),
        _question(DENTRO_EPOCH + 11, "approved", 2.0),
        _question(DENTRO_EPOCH + 12, "approved", 6.0),
        _question(DENTRO_EPOCH + 13, "timeout", None),
    ])

    review = build_weekly_review(home, now=NOW)
    ap = review.approvals
    assert ap is not None
    # The overall line grew by four; the person columns grew by three — the timeout is nobody's.
    assert ap.asked == 8 and ap.answered == 6
    assert ap.person_answered == 6 and ap.person_approved == 5 and ap.person_refused == 1
    assert ap.fast_approvals == 3
    assert ap.fast_approval_rate == pytest.approx(3 / 6)
    # And the text says it, with the threshold named.
    texto = render_weekly_review(review, "pt")
    assert "Por uma pessoa: 6 respondida(s), 5 aprovada(s), 1 recusada(s); 3 em menos de 10 s (50%)" in texto
    assert "carimbo automático" in texto


def test_unanswerable_chat_timeout_count_and_share(home: Path) -> None:
    """Only source surfaces excluded by the chat rule count; answered-via is immaterial to a timeout."""
    from chimera.server.chat_approval import chat_answer_path

    rows = [
        {**_question(DENTRO_EPOCH, "timeout", None), "surface": "app-messaging:whatsapp"},
        {**_question(DENTRO_EPOCH + 1, "timeout", None), "surface": "platform:telegram"},
        {**_question(DENTRO_EPOCH + 2, "timeout", None), "surface": "platform:signal"},
        {**_question(DENTRO_EPOCH + 3, "timeout", None), "surface": "cron"},
        {**_question(DENTRO_EPOCH + 4, "approved", 2), "surface": "app-messaging:whatsapp"},
    ]

    stats = summarize_answers(rows)
    assert stats["timeouts"] == 4
    assert stats["unanswerable_timeouts"] == 3
    assert stats["unanswerable_timeout_rate"] == pytest.approx(3 / 4)
    assert chat_answer_path("platform:whatsapp") is False
    assert chat_answer_path("app-messaging:telegram") is False
    assert chat_answer_path("platform:signal") is False
    assert chat_answer_path("cron") is True
    assert chat_answer_path("cron:telegram") is True


def test_unanswerable_timeout_rate_is_none_without_timeouts() -> None:
    stats = summarize_answers([_question(DENTRO_EPOCH, "approved", 2)])
    assert stats["unanswerable_timeouts"] == 0
    assert stats["unanswerable_timeout_rate"] is None


def test_the_habituation_line_stays_silent_over_zero_person_answers(home: Path) -> None:
    """A week of pure timeouts gets the overall line and nothing more: a 0% fast-approval rate over
    zero person answers would read as a measured vigilance."""
    _jsonl(home / "approvals" / "history.jsonl", [
        _question(DENTRO_EPOCH, "timeout", None),
        _question(DENTRO_EPOCH + 1, "timeout", None),
    ])

    review = build_weekly_review(home, now=NOW)
    ap = review.approvals
    assert ap is not None
    assert ap.asked == 2 and ap.person_answered == 0
    assert ap.fast_approval_rate is None
    texto = render_weekly_review(review, "pt")
    assert "2 pergunta(s)" in texto and "sem resposta" in texto
    assert "Por uma pessoa" not in texto and "carimbo" not in texto
    assert "sem caminho de resposta" not in texto


def test_weekly_review_reports_nonzero_unanswerable_timeouts_in_both_languages(home: Path) -> None:
    _jsonl(home / "approvals" / "history.jsonl", [
        {**_question(DENTRO_EPOCH, "timeout", None), "surface": "app-messaging:whatsapp"},
        {**_question(DENTRO_EPOCH + 1, "timeout", None), "surface": "cron"},
    ])

    review = build_weekly_review(home, now=NOW)
    ap = review.approvals
    assert ap is not None
    assert ap.unanswerable_timeouts == 1
    assert ap.unanswerable_timeout_rate == pytest.approx(1 / 2)
    assert "1 timeout(s) sem caminho de resposta por chat (50%)" in render_weekly_review(review, "pt")
    assert "1 timeout(s) had no chat answer path (50%)" in render_weekly_review(review, "en")


def test_the_text_carries_the_numbers_it_was_given(home: Path) -> None:
    _week_of_rows(home, DENTRO, DENTRO_EPOCH)
    _jobs(home)
    review = build_weekly_review(home, now=NOW)

    pt = render_weekly_review(review, "pt")
    en = render_weekly_review(review, "en")
    print(pt)

    assert "28/09 a 05/10" in pt
    assert "pelo menos US$ 0,0712" in pt and "1 chamada(s) sem preço" in pt
    assert "Runs: 4 — 3 passaram (1 por verificador, 1 sem mudar arquivo), 1 sem passar, 2 com" in pt
    assert "Custo dos runs: US$ 0,0550" in pt
    assert "Menos de 10 runs" in pt
    assert "4 pergunta(s) — 3 respondida(s) (75%), 2 aprovada(s), 1 recusada(s), 1 sem resposta" in pt
    assert "Mediana até a resposta: 30 s" in pt
    assert '"resumo" (error x3)' in pt and '"guardiao" (desligado pelo freio, timeout x5)' in pt
    assert "pausado" not in pt
    assert "at least $0.0712" in en and "3 passed" in en and "Jobs failing now: 2" in en


def test_an_empty_week_is_zero_and_says_so(home: Path) -> None:
    """Every source present, nothing dated inside the week: zeros, from files that exist."""
    _week_of_rows(home, FORA, FORA_EPOCH)
    CronStore(home / "scheduler" / "jobs.json").add(
        CronJob(id="ok1", name="saudavel", trigger="cron", schedule="0 7 * * *", action="x")
    )

    review = build_weekly_review(home, now=NOW)

    assert review.missing == ()
    assert review.spend is not None and review.spend.usd == 0.0 and review.spend.turns == 0
    assert review.spend.unpriced == 0
    assert review.runs is not None and review.runs.runs == 0 and review.runs.usd is None
    assert review.approvals is not None and review.approvals.asked == 0
    assert review.approvals.answer_rate is None and review.approvals.approval_rate is None
    assert review.failing == []
    texto = render_weekly_review(review, "pt")
    assert "Runs: nenhum nesta semana." in texto
    assert "nenhuma pergunta nesta semana" in texto
    assert "Jobs falhando agora: nenhum." in texto


def test_a_missing_source_is_named_and_never_read_as_zero(home: Path) -> None:
    """An empty home is not a quiet week. Each section is None and the text names the file."""
    review = build_weekly_review(home, now=NOW)

    assert review.spend is None and review.runs is None
    assert review.approvals is None and review.failing is None
    assert review.missing == ("usage.jsonl", "runs.jsonl", "history.jsonl", "jobs.json")
    texto = render_weekly_review(review, "pt")
    for nome in ("usage.jsonl", "runs.jsonl", "approvals/history.jsonl", "scheduler/jobs.json"):
        assert nome in texto
    assert " 0 " not in texto and "US$ 0" not in texto


def test_one_missing_source_does_not_hide_the_others(home: Path) -> None:
    """Only the usage log: spend is known (the Cost screen's rule — either log is enough), runs are
    not, and nothing else is invented."""
    _jsonl(home / "usage.jsonl", [_usage(DENTRO, 0.5)])

    review = build_weekly_review(home, now=NOW)

    assert review.spend is not None and review.spend.usd == 0.5
    assert review.runs is None
    assert "runs.jsonl" in review.missing and "usage.jsonl" not in review.missing


def test_a_row_with_no_readable_date_is_left_out_and_counted(home: Path) -> None:
    _jsonl(home / "usage.jsonl", [_usage(DENTRO, 0.5), _usage("ontem", 9.0)])

    review = build_weekly_review(home, now=NOW)

    assert review.spend is not None and review.spend.usd == 0.5
    assert review.undated == {"usage": 1}
    assert "usage 1" in render_weekly_review(review, "pt")


def test_answer_stats_is_the_summary_of_the_whole_history(home: Path) -> None:
    """The refactor the review needed: `answer_stats` is `summarize_answers` over every row, so the
    line `chimera approve` prints and the review's figures are one computation."""
    _week_of_rows(home, DENTRO, DENTRO_EPOCH)
    _week_of_rows(home, FORA, FORA_EPOCH)

    assert answer_stats(home) == summarize_answers(history(home))
    assert answer_stats(home)["asked"] == 8


# --- the job: proposed disabled, dispatched by code ----------------------------------------------


def test_the_proposal_is_disabled_and_has_no_destination_until_the_owner_gives_one(home: Path) -> None:
    from chimera.scheduler import Scheduler

    scheduler = Scheduler(CronStore(home / "scheduler" / "jobs.json"))
    job, created = propose(scheduler, now=NOW)

    assert created is True
    assert job.enabled is False and job.created_by == "agent"
    assert job.deliver_to is None
    assert job.schedule == JOB_SCHEDULE == "0 9 * * 1"
    assert job.metadata[BUILTIN_KEY] == WEEKLY_REVIEW

    again, created_again = propose(scheduler, now=NOW, deliver_to="https://discord.com/api/webhooks/1/x")
    assert created_again is False and again.id == job.id
    assert len(scheduler.store.list()) == 1
    assert CronStore(home / "scheduler" / "jobs.json").get(job.id).deliver_to == (
        "https://discord.com/api/webhooks/1/x"
    )
    assert CronStore(home / "scheduler" / "jobs.json").get(job.id).enabled is False


class _Explodes:
    """A backend that fails the run if anything ever asks it for a completion."""

    def complete(self, *_a: Any, **_k: Any) -> Any:
        raise AssertionError("a model was called to produce the weekly review")


def test_the_scheduled_review_is_dispatched_without_a_model_and_outside_the_cap(home: Path) -> None:
    from chimera.scheduler import Scheduler
    from chimera.scheduler.job_runner import make_run_job

    _week_of_rows(home, DENTRO, DENTRO_EPOCH)
    # Today's money is gone: an agent job would be refused. A free report must not be.
    _jsonl(home / "usage.jsonl", [_usage(datetime.now(UTC).isoformat(), 99.0)])
    job, _ = propose(Scheduler(CronStore(home / "scheduler" / "jobs.json")), now=NOW, lang="pt")
    linhas_antes = (home / "usage.jsonl").read_text(encoding="utf-8").count("\n")

    run_job = make_run_job(
        settings=Settings(CHIMERA_HOME=str(home), CHIMERA_DAILY_USD_CAP="1.0"),
        backend=_Explodes(),
        workspace=home,
        model=None,
        max_steps=3,
        usage_path=home / "usage.jsonl",
    )
    outcome = run_job(job)

    assert outcome.ok is True
    assert outcome.answer.startswith("Revisão semanal")
    assert "Runs:" in outcome.answer
    # No usage row for a run that spent nothing, and no run receipt for a run that was not one.
    assert (home / "usage.jsonl").read_text(encoding="utf-8").count("\n") == linhas_antes
    assert sum(1 for _ in (home / "runs.jsonl").open(encoding="utf-8")) == 4


def test_an_unknown_builtin_is_an_error_not_a_prompt(home: Path) -> None:
    job = CronJob(id="z", name="z", schedule="* * * * *", action="report the numbers",
                  metadata={BUILTIN_KEY: "something_else"})

    with pytest.raises(ValueError, match="unknown builtin"):
        run_builtin(job, home)


def test_the_owner_language_picks_the_template(home: Path) -> None:
    from chimera.core.instructions import AgentIdentity, save

    assert owner_lang(home) == "pt"
    save(home, AgentIdentity(language="English"))
    assert owner_lang(home) == "en"
    save(home, AgentIdentity(language="Português (Brasil)"))
    assert owner_lang(home) == "pt"


def test_the_webhook_server_will_not_hand_a_builtin_to_the_gateway(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.cli.main import _webhook_handler
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    try:
        store = CronStore(home / "scheduler" / "jobs.json")
        store.add(CronJob(id="w1", name="hand-edited", trigger="webhook", schedule="hook",
                          action="the weekly numbers", metadata={BUILTIN_KEY: WEEKLY_REVIEW}))
        received: list[str] = []

        class _Gateway:
            def on_message(self, message: Any) -> str:
                received.append(message.text)
                return "ok"

        _webhook_handler(_Gateway())("hook", {})  # type: ignore[arg-type]  # duck-typed gateway
        assert received == []
    finally:
        get_settings.cache_clear()


# --- the command ---------------------------------------------------------------------------------


@pytest.fixture
def cli_home(home: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    yield home
    get_settings.cache_clear()


def test_report_weekly_print_prints_and_writes_nothing(cli_home: Path) -> None:
    from typer.testing import CliRunner

    from chimera.cli.main import app

    _week_of_rows(cli_home, datetime.now(UTC).isoformat(), datetime.now(UTC).timestamp())
    result = CliRunner().invoke(app, ["report", "weekly", "--print", "--lang", "en"])

    assert result.exit_code == 0, result.output
    assert "Weekly review" in result.output and "Runs: 4" in result.output
    assert not (cli_home / "scheduler" / "jobs.json").exists()


def test_report_weekly_proposes_the_job_once(cli_home: Path) -> None:
    from typer.testing import CliRunner

    from chimera.cli.main import app

    runner = CliRunner()
    first = runner.invoke(app, ["report", "weekly"])
    second = runner.invoke(app, ["report", "weekly", "--deliver-to", "https://discord.com/api/webhooks/9/secret"])

    assert first.exit_code == 0 and second.exit_code == 0, first.output + second.output
    assert "proposed" in first.output and "disabled" in first.output
    assert "already proposed" in second.output
    assert "secret" not in second.output, "the webhook path is a credential and was echoed"
    jobs = CronStore(cli_home / "scheduler" / "jobs.json").list()
    proposta = find_proposal(jobs)
    assert len(jobs) == 1 and proposta is not None and proposta.enabled is False
    assert proposta.deliver_to == "https://discord.com/api/webhooks/9/secret"


def test_report_weekly_refuses_a_bad_destination_and_a_bad_language(cli_home: Path) -> None:
    from typer.testing import CliRunner

    from chimera.cli.main import app

    runner = CliRunner()
    assert runner.invoke(app, ["report", "weekly", "--deliver-to", "ftp://x"]).exit_code == 1
    assert runner.invoke(app, ["report", "weekly", "--lang", "fr"]).exit_code == 1
    assert runner.invoke(
        app, ["report", "weekly", "--print", "--deliver-to", "https://x.test/h"]
    ).exit_code == 1
    assert not (cli_home / "scheduler" / "jobs.json").exists()
