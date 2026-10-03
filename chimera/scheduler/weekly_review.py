"""The weekly review: last week's spend, runs, approvals and broken jobs, counted by code.

Study 29, P3.4. Every ingredient already existed and was published somewhere — the Cost screen sums
``usage.jsonl`` and ``runs.jsonl`` (``/api/usage``), "Was it worth it?" groups ``runs.jsonl``
(``/api/code/worth``), ``chimera approve`` prints how the approval questions ended
(``governance/pending.answer_stats``), and ``cron doctor`` names the jobs that keep failing
(``Scheduler.failing``) — and nothing put them side by side once a week, where somebody would read
them.

**The numbers are computed here and nowhere else.** No model writes, rounds or restates a figure:
the owner's standing rule is that numeric data reaches a person through a deterministic script,
because an LLM corrupts numbers (a heartbeat printed "SPY 44.78" for $744.78). The scheduled job
that carries this report is dispatched without a model call at all — see :func:`builtin_of` and
``job_runner.make_run_job``.

**Each total is the published one, over the week.** Every section calls the SAME function its
screen calls, on the rows dated inside the window, so a figure here and the figure on the screen
can only differ by the window. The tests rebuild each source, print the published totals and
compare (the join-finds-the-field lesson: a join that does not find its field returns a plausible
zero, never an error).

**A missing source is said, never zeroed.** No ``runs.jsonl`` is not "0 runs": it is a home where
runs were never recorded, or a different home. Each section is ``None`` when its file is absent and
the text names the file.
"""

from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from chimera.scheduler.models import CreatedBy, CronJob

#: How far back the review looks. A week, because it is delivered once a week: a longer window
#: would count the same run in two consecutive reviews.
WINDOW_DAYS = 7

#: The key in ``CronJob.metadata`` that marks a job dispatched by code instead of by an agent.
BUILTIN_KEY = "builtin"

#: The one builtin there is. A name rather than a flag so a second report does not need a new field.
WEEKLY_REVIEW = "weekly_review"

#: The proposal's name and schedule: Mondays at 09:00, the start of the week the review is read in.
JOB_NAME = "weekly-review"
JOB_SCHEDULE = "0 9 * * 1"
JOB_ACTION = (
    "Weekly review: spend, runs, approvals and failing jobs over the last 7 days, counted by "
    "`chimera report weekly` — no model call."
)

#: How many failing jobs the text names before it says "and N more". A Discord message stops at
#: 2000 characters, and a list that long is read as noise anyway.
MAX_NAMED_JOBS = 5

#: Below this many runs a pass rate is an anecdote — the same threshold "Was it worth it?" uses.
READABLE_RUNS = 10

Lang = Literal["pt", "en"]


@dataclass(frozen=True)
class SpendTotals:
    """What ``/api/usage`` reports as ``totals``, over the rows dated inside the window."""

    usd: float
    """The PRICED rows' sum. Never includes a guess for the unpriced ones."""
    unpriced: int
    """Rows whose price is unknown — when non-zero, ``usd`` is a floor, not the bill."""
    turns: int


@dataclass(frozen=True)
class RunTotals:
    """What ``/api/code/worth`` reports, summed over its groups, for the runs of the window."""

    runs: int
    passed: int
    passed_by_verifier: int
    reverted: int
    unproductive: int
    attempts: int
    usd: float | None
    """The runs' cost, or ``None`` when any run's cost is unknown (the worth view's rule)."""
    usd_known_runs: int

    @property
    def not_passed(self) -> int:
        """Runs that did not pass. Not "failed": a cancelled run is in here too, and is not a failure."""
        return self.runs - self.passed


@dataclass(frozen=True)
class ApprovalTotals:
    """What ``answer_stats`` reports, for the questions asked inside the window."""

    asked: int
    answered: int
    approved: int
    refused: int
    timeouts: int
    answer_rate: float | None
    p50_seconds: float | None

    @property
    def approval_rate(self) -> float | None:
        """Of the questions somebody answered, the share answered yes. ``None`` over none."""
        return (self.approved / self.answered) if self.answered else None


@dataclass(frozen=True)
class FailingJob:
    """A job that is failing NOW — the cron store keeps counters, not a history."""

    id: str
    name: str
    last_status: str
    consecutive_failures: int
    braked: bool


@dataclass(frozen=True)
class WeeklyReview:
    """One week, every section computed, every absent source named."""

    start: datetime
    end: datetime
    spend: SpendTotals | None
    runs: RunTotals | None
    approvals: ApprovalTotals | None
    failing: list[FailingJob] | None
    missing: tuple[str, ...] = ()
    """The source files that were not there, by name, in the order the sections read them."""
    undated: dict[str, int] = field(default_factory=dict)
    """Rows a source held whose date could not be read — left out of the week, and counted so the
    omission is visible rather than silent."""


def _when(value: Any) -> float | None:
    """A row's date as epoch seconds: an ISO string (naive read as UTC) or a number. ``None`` when
    neither — a row that cannot be placed in time is not placed in the week."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


class _Window:
    def __init__(self, start: float, end: float) -> None:
        self.start, self.end = start, end
        self.undated: dict[str, int] = {}

    def keep(self, source: str, value: Any) -> bool:
        stamp = _when(value)
        if stamp is None:
            self.undated[source] = self.undated.get(source, 0) + 1
            return False
        return self.start <= stamp < self.end


def _spend(home: Path, window: _Window) -> SpendTotals:
    """The Cost screen's total, over the week: the same two logs, the same join, the same sum."""
    from chimera.api.usage import _already_counted, load_usage, summarize_usage, usage_from_runs

    turnos = load_usage(home / "usage.jsonl")
    # The join is computed over EVERY row, not the week's: a scheduled run writes its usage row a
    # moment after its receipt, and a pair split by the window's edge must still be one charge.
    linhas = turnos + usage_from_runs(home / "runs.jsonl", already=_already_counted(turnos))
    semana = [r for r in linhas if window.keep("usage", r.ts)]
    totals = summarize_usage(semana)["totals"]
    return SpendTotals(
        usd=round(float(totals["usd"]), 6),
        unpriced=int(totals["unpriced_turns"]),
        turns=int(totals["turns"]),
    )


def _runs(home: Path, window: _Window) -> RunTotals:
    """"Was it worth it?", over the week, summed across its groups."""
    from chimera.api.runs import load_runs
    from chimera.api.worth import summarize_worth

    recibos = [r for r in load_runs(home / "runs.jsonl") if window.keep("runs", r.ts)]
    report = summarize_worth(recibos)
    groups = report.profiles
    # The groups' own rule, applied to their sum: one unknown cost makes the total unknown.
    priced = all(p.usd_total is not None for p in groups)
    return RunTotals(
        runs=report.total_runs,
        passed=sum(p.passed for p in groups),
        passed_by_verifier=sum(p.passed_by_verifier for p in groups),
        reverted=sum(p.reverted for p in groups),
        unproductive=sum(p.unproductive for p in groups),
        attempts=sum(p.attempts_total for p in groups),
        usd=(round(sum(p.usd_total or 0.0 for p in groups), 6) if priced and groups else None),
        usd_known_runs=sum(p.usd_known_runs for p in groups),
    )


def _approvals(home: Path, window: _Window) -> ApprovalTotals:
    """``chimera approve``'s line, over the questions asked this week."""
    from chimera.governance.pending import history, summarize_answers

    stats = summarize_answers([r for r in history(home) if window.keep("approvals", r.get("asked_at"))])
    return ApprovalTotals(
        asked=int(stats["asked"]),
        answered=int(stats["answered"]),
        approved=int(stats["approved"]),
        refused=int(stats["refused"]),
        timeouts=int(stats["timeouts"]),
        answer_rate=stats["answer_rate"],
        p50_seconds=stats["p50_seconds"],
    )


def _failing(jobs_path: Path) -> list[FailingJob]:
    """``cron doctor``'s list of failing jobs, as it stands now."""
    from chimera.scheduler.engine import Scheduler
    from chimera.scheduler.store import CronStore

    return [
        FailingJob(
            id=job.id,
            name=job.name,
            last_status=job.last_status or "",
            consecutive_failures=job.consecutive_failures,
            braked=job.disabled_by == "brake",
        )
        for job in Scheduler(CronStore(jobs_path)).failing(at_least=1)
    ]


def build_weekly_review(home: Path, *, now: float | None = None, days: int = WINDOW_DAYS) -> WeeklyReview:
    """Read every source under ``home`` and count the week ending at ``now``. Reads only."""
    home = Path(home)
    end = time.time() if now is None else float(now)
    start = end - timedelta(days=days).total_seconds()
    window = _Window(start, end)

    usage_log, runs_log = home / "usage.jsonl", home / "runs.jsonl"
    history_log = home / "approvals" / "history.jsonl"
    jobs_file = home / "scheduler" / "jobs.json"
    missing = tuple(
        p.name
        for p in (usage_log, runs_log, history_log, jobs_file)
        if not p.exists()
    )
    return WeeklyReview(
        start=datetime.fromtimestamp(start, UTC),
        end=datetime.fromtimestamp(end, UTC),
        # Spend reads two logs and is known as soon as either exists — the Cost screen's own rule.
        spend=_spend(home, window) if usage_log.exists() or runs_log.exists() else None,
        runs=_runs(home, window) if runs_log.exists() else None,
        approvals=_approvals(home, window) if history_log.exists() else None,
        failing=_failing(jobs_file) if jobs_file.exists() else None,
        missing=missing,
        undated=dict(window.undated),
    )


# --- the text ------------------------------------------------------------------------------------

_TEXT: dict[Lang, dict[str, str]] = {
    "pt": {
        "title": "Revisão semanal — {start} a {end} (UTC)",
        "spend": "Gasto: {usd} em {turns} turno(s).",
        "spend_floor": "Gasto: pelo menos {usd} em {turns} turno(s) — {unpriced} chamada(s) sem preço conhecido.",
        "spend_none": "Gasto: sem registro (nem usage.jsonl nem runs.jsonl).",
        "runs": "Runs: {runs} — {passed} passaram ({verifier} por verificador, {unproductive} sem mudar arquivo), "
        "{not_passed} sem passar, {reverted} com trabalho revertido; {attempts} tentativa(s).",
        "runs_cost": " Custo dos runs: {usd}.",
        "runs_cost_unknown": " Custo dos runs: desconhecido ({known} de {runs} com preço).",
        "runs_few": " Menos de {n} runs: lê-se como episódio, não tendência.",
        "runs_zero": "Runs: nenhum nesta semana.",
        "runs_none": "Runs: sem registro (runs.jsonl ausente).",
        "approvals": "Aprovações: {asked} pergunta(s) — {answered} respondida(s) ({rate}), {approved} aprovada(s), "
        "{refused} recusada(s), {timeouts} sem resposta.",
        "approvals_p50": " Mediana até a resposta: {p50}.",
        "approvals_zero": "Aprovações: nenhuma pergunta nesta semana.",
        "approvals_none": "Aprovações: nenhum histórico (approvals/history.jsonl ausente).",
        "jobs": "Jobs falhando agora: {n} — {names}.",
        "jobs_more": " e mais {n}",
        "jobs_braked": "desligado pelo freio",
        "jobs_zero": "Jobs falhando agora: nenhum.",
        "jobs_none": "Jobs: sem agenda (scheduler/jobs.json ausente).",
        "undated": "Linhas sem data legível, fora da conta: {list}.",
        "footer": "Números contados por `chimera report weekly`, sem modelo.",
    },
    "en": {
        "title": "Weekly review — {start} to {end} (UTC)",
        "spend": "Spend: {usd} over {turns} turn(s).",
        "spend_floor": "Spend: at least {usd} over {turns} turn(s) — {unpriced} call(s) with no known price.",
        "spend_none": "Spend: no record (neither usage.jsonl nor runs.jsonl).",
        "runs": "Runs: {runs} — {passed} passed ({verifier} by a verifier, {unproductive} changed no file), "
        "{not_passed} did not pass, {reverted} had work reverted; {attempts} attempt(s).",
        "runs_cost": " Cost of runs: {usd}.",
        "runs_cost_unknown": " Cost of runs: unknown ({known} of {runs} priced).",
        "runs_few": " Fewer than {n} runs: read it as an anecdote, not a trend.",
        "runs_zero": "Runs: none this week.",
        "runs_none": "Runs: no record (runs.jsonl missing).",
        "approvals": "Approvals: {asked} question(s) — {answered} answered ({rate}), {approved} approved, "
        "{refused} refused, {timeouts} unanswered.",
        "approvals_p50": " Median time to answer: {p50}.",
        "approvals_zero": "Approvals: no question this week.",
        "approvals_none": "Approvals: no history (approvals/history.jsonl missing).",
        "jobs": "Jobs failing now: {n} — {names}.",
        "jobs_more": " and {n} more",
        "jobs_braked": "switched off by the brake",
        "jobs_zero": "Jobs failing now: none.",
        "jobs_none": "Jobs: no schedule (scheduler/jobs.json missing).",
        "undated": "Rows with no readable date, left out: {list}.",
        "footer": "Numbers counted by `chimera report weekly`, no model.",
    },
}


def _usd(value: float, lang: Lang) -> str:
    texto = f"{value:.4f}"
    return f"US$ {texto.replace('.', ',')}" if lang == "pt" else f"${texto}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)}%"


def _seconds(value: float) -> str:
    if value < 120:
        return f"{value:.0f} s"
    if value < 7200:
        return f"{value / 60:.0f} min"
    return f"{value / 3600:.1f} h"


def _runs_line(runs: RunTotals | None, t: dict[str, str], lang: Lang) -> str:
    if runs is None:
        return t["runs_none"]
    if not runs.runs:
        return t["runs_zero"]
    linha = t["runs"].format(
        runs=runs.runs, passed=runs.passed, verifier=runs.passed_by_verifier,
        unproductive=runs.unproductive, not_passed=runs.not_passed, reverted=runs.reverted,
        attempts=runs.attempts,
    )
    if runs.usd is not None:
        linha += t["runs_cost"].format(usd=_usd(runs.usd, lang))
    else:
        linha += t["runs_cost_unknown"].format(known=runs.usd_known_runs, runs=runs.runs)
    if runs.runs < READABLE_RUNS:
        linha += t["runs_few"].format(n=READABLE_RUNS)
    return linha


def _approvals_line(ap: ApprovalTotals | None, t: dict[str, str]) -> str:
    if ap is None:
        return t["approvals_none"]
    if not ap.asked:
        return t["approvals_zero"]
    linha = t["approvals"].format(
        asked=ap.asked, answered=ap.answered, rate=_pct(ap.answer_rate), approved=ap.approved,
        refused=ap.refused, timeouts=ap.timeouts,
    )
    if ap.p50_seconds is not None:
        linha += t["approvals_p50"].format(p50=_seconds(float(ap.p50_seconds)))
    return linha


def _jobs_line(failing: list[FailingJob] | None, t: dict[str, str]) -> str:
    if failing is None:
        return t["jobs_none"]
    if not failing:
        return t["jobs_zero"]
    named = []
    for job in failing[:MAX_NAMED_JOBS]:
        detail = f"{job.last_status or '?'} x{job.consecutive_failures}"
        if job.braked:
            detail = f"{t['jobs_braked']}, {detail}"
        named.append(f"\"{job.name}\" ({detail})")
    names = ", ".join(named)
    if len(failing) > MAX_NAMED_JOBS:
        names += t["jobs_more"].format(n=len(failing) - MAX_NAMED_JOBS)
    return t["jobs"].format(n=len(failing), names=names)


def render_weekly_review(review: WeeklyReview, lang: Lang = "pt") -> str:
    """The review as a short message. Every figure in it was computed above — this only formats."""
    t = _TEXT[lang]
    linhas = [
        t["title"].format(start=review.start.strftime("%d/%m"), end=review.end.strftime("%d/%m"))
        if lang == "pt"
        else t["title"].format(start=review.start.strftime("%Y-%m-%d"), end=review.end.strftime("%Y-%m-%d")),
        "",
    ]
    if review.spend is None:
        linhas.append(t["spend_none"])
    elif review.spend.unpriced:
        linhas.append(
            t["spend_floor"].format(
                usd=_usd(review.spend.usd, lang), turns=review.spend.turns, unpriced=review.spend.unpriced
            )
        )
    else:
        linhas.append(t["spend"].format(usd=_usd(review.spend.usd, lang), turns=review.spend.turns))
    linhas.append(_runs_line(review.runs, t, lang))
    linhas.append(_approvals_line(review.approvals, t))
    linhas.append(_jobs_line(review.failing, t))
    if review.undated:
        linhas.append(t["undated"].format(list=", ".join(f"{k} {v}" for k, v in sorted(review.undated.items()))))
    linhas += ["", t["footer"]]
    return "\n".join(linhas)


# --- the language and the job --------------------------------------------------------------------


def owner_lang(home: Path) -> Lang:
    """Portuguese unless the owner's identity names another language, then English.

    Two templates, not a translation: translating the text around the numbers through a model is
    exactly what this module exists not to do. An identity with no language is read as Portuguese
    (the language this was written for); any language other than Portuguese gets English, the
    nearer of the two for a reader of a third one.
    """
    from chimera.core.instructions import load

    lingua = load(home).language.strip().lower()
    if not lingua or lingua.startswith(("portug", "pt")):
        return "pt"
    return "en"


def builtin_of(job: CronJob) -> str:
    """The builtin a job asks for, or ``""`` for an ordinary agent job."""
    value = job.metadata.get(BUILTIN_KEY) if isinstance(job.metadata, dict) else None
    return value if isinstance(value, str) else ""


def job_lang(job: CronJob, home: Path) -> Lang:
    """The language stored on the job when somebody chose one, else the owner's."""
    stored = job.metadata.get("lang") if isinstance(job.metadata, dict) else None
    if stored == "pt":
        return "pt"
    if stored == "en":
        return "en"
    return owner_lang(home)


def run_builtin(job: CronJob, home: Path, *, now: float | None = None) -> str:
    """The answer of a builtin job. Raises on a name it does not know.

    Raising rather than falling back to the agent: a job whose metadata names a builtin was not
    written for a model, and running its action text as a prompt would hand the model exactly the
    numbers this exists to keep from it. The engine records the error, and the failure notice says so.
    """
    nome = builtin_of(job)
    if nome == WEEKLY_REVIEW:
        return render_weekly_review(build_weekly_review(home, now=now), job_lang(job, home))
    raise ValueError(f"unknown builtin job {nome!r}")


def find_proposal(jobs: list[CronJob]) -> CronJob | None:
    """The weekly-review job already in the store, if there is one."""
    for job in jobs:
        if builtin_of(job) == WEEKLY_REVIEW:
            return job
    return None


def valid_webhook(url: str) -> bool:
    """An http(s) URL with a host — the same check ``cron add --deliver-to`` makes."""
    partes = urllib.parse.urlparse(url)
    return partes.scheme in ("http", "https") and bool(partes.hostname)


def propose(
    scheduler: Any,
    *,
    now: float,
    deliver_to: str | None = None,
    lang: Lang | None = None,
    created_by: CreatedBy = "agent",
) -> tuple[CronJob, bool]:
    """Register the weekly review as a DISABLED job, once. Returns ``(job, created)``.

    Disabled, and ``created_by="agent"``, so it is a proposal in exactly the sense ``cron learn``'s
    are: it does not fire until the owner runs ``chimera cron enable``. ``deliver_to`` is left empty
    unless the owner passes one — where it is posted is theirs to choose, and the URL is a credential.
    Calling it again does not add a second job: it updates the destination or the language when
    one is given, and otherwise returns the existing proposal untouched.

    ``created_by`` is ``"human"`` when the owner asked for it from the Settings screen: the job is
    then theirs, not a proposal, and the provenance says so (it also starts enabled, as every
    human-created job does).
    """
    existing = find_proposal(scheduler.store.list())
    if existing is not None:
        changed = False
        if deliver_to is not None and existing.deliver_to != deliver_to:
            existing.deliver_to = deliver_to
            changed = True
        if lang is not None and existing.metadata.get("lang") != lang:
            existing.metadata["lang"] = lang
            changed = True
        if changed:
            scheduler.store.add(existing)
        return existing, False
    job = scheduler.schedule_cron(
        JOB_NAME, JOB_SCHEDULE, JOB_ACTION, now=now, created_by=created_by, deliver_to=deliver_to,
    )
    job.metadata = {BUILTIN_KEY: WEEKLY_REVIEW, "proposed": True, **({"lang": lang} if lang else {})}
    scheduler.store.add(job)
    return job, True
