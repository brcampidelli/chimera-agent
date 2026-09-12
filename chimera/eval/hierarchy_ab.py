"""Hierarchy paired A/B (M16-A8): does the orchestrator-worker split beat single-agent —
on quality AND tokens — for read-heavy multi-part tasks?

Design registered before running (see bench/hierarchy/README.md):
- **Arms** on the SAME model family: baseline = one single-agent call that sees ALL
  documents inline + the full multi-part question; treatment = the hierarchy, one
  worker per part, each seeing ONLY its own document (minimal-context scoping),
  top-tier synthesis. Same mid model both sides — the comparison isolates the
  ORCHESTRATION, not model strength.
- **Quality axis**: paired pass/fail -> McNemar/Wilson via
  :mod:`chimera.eval.paired`. "Significant" appears ONLY here.
- **Token axis**: measured totals per arm, reported as totals/medians — no
  significance claim on cost, ever.
- Grading is independent of the solver: every task carries deterministic
  substring checks over the final answer; ALL must pass.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import TYPE_CHECKING, TypeVar

from chimera.eval.paired import PairedResult, compare_paired

if TYPE_CHECKING:
    from chimera.orchestration.spec import TaskSpec

T = TypeVar("T")


@dataclass(frozen=True)
class DocFact:
    """One planted fact inside one document — the unit of grading."""

    needle: str
    """The substring a correct final answer must contain."""


@dataclass(frozen=True)
class HierarchyTask:
    """A read-heavy multi-part task over k documents, deterministically gradable."""

    id: str
    docs: dict[str, str]
    """filename -> content; each doc holds exactly the facts its part needs."""
    question: str
    facts: tuple[DocFact, ...]

    def check(self, answer: str) -> bool:
        low = answer.lower()
        return all(f.needle.lower() in low for f in self.facts)


# Deterministic synthetic corpus: no randomness (workflow/replay safe), realistic
# shape (each doc has its facts buried in filler so a lazy skim fails).
_FILLER = (
    "Background section. This paragraph is deliberately irrelevant context about "
    "process, tooling and history that a careful reader must skip past.\n"
) * 12


def _doc(title: str, facts: list[str]) -> str:
    body = "\n".join(f"- {fact}" for fact in facts)
    return f"# {title}\n\n{_FILLER}\n## Key items\n{body}\n\n{_FILLER}"


def synthetic_tasks() -> list[HierarchyTask]:
    """30 read-heavy multi-part tasks, 2-4 docs each, all deterministically gradable.

    The first ten are the corpus every published number of `bench/hierarchy`,
    `bench/hierarchy_multistep` and `bench/hierarchy_equal_calls` was measured on, in that order;
    the twenty after them were added on 2026-09-12 (`bench/hierarchy_equal_calls` addendum 3)
    because ten tasks at three runs gave a 50% flip rate that no comparison could read through.
    A run that wants the old corpus takes the first ten.
    """
    tasks: list[HierarchyTask] = []
    specs: list[tuple[str, dict[str, list[str]], str]] = [
        (
            "releases",
            {
                "alpha.md": ["Alpha 3.1 requires Python 3.12", "Alpha 3.1 drops the sync client"],
                "beta.md": ["Beta 2.0 requires Node 22", "Beta 2.0 renames init to setup"],
            },
            "Read alpha.md and beta.md. What does each release require, and what breaking "
            "change does each introduce? Answer for both.",
        ),
        (
            "vendors",
            {
                "acme.md": ["Acme charges 14 dollars per seat"],
                "globex.md": ["Globex charges 11 dollars per seat"],
                "initech.md": ["Initech charges 19 dollars per seat"],
            },
            "Read acme.md, globex.md and initech.md and report each vendor's per-seat price.",
        ),
        (
            "incidents",
            {
                "jan.md": ["January outage lasted 42 minutes", "January root cause was a bad certificate"],
                "feb.md": ["February outage lasted 8 minutes", "February root cause was a full disk"],
            },
            "Summarize the January and February incident reports: duration and root cause of each.",
        ),
        (
            "teams",
            {
                "core.md": ["Core team owns the scheduler module"],
                "infra.md": ["Infra team owns the deployment pipeline"],
                "data.md": ["Data team owns the metrics warehouse"],
            },
            "From core.md, infra.md and data.md: which team owns what?",
        ),
        (
            "limits",
            {
                "api.md": ["The API rate limit is 600 requests per minute"],
                "batch.md": ["The batch job ceiling is 250 concurrent jobs"],
            },
            "Read api.md and batch.md and state the rate limit and the batch ceiling.",
        ),
        (
            "contracts",
            {
                "north.md": ["North contract renews on March 15"],
                "south.md": ["South contract renews on August 3"],
                "east.md": ["East contract renews on November 20"],
            },
            "When does each of the North, South and East contracts renew?",
        ),
        (
            "benchmarks",
            {
                "cpu.md": ["CPU suite improved by 12 percent"],
                "gpu.md": ["GPU suite regressed by 4 percent"],
            },
            "Compare the CPU and GPU benchmark reports: what changed in each?",
        ),
        (
            "policies",
            {
                "security.md": ["Security policy mandates rotation every 90 days"],
                "privacy.md": ["Privacy policy mandates deletion within 30 days"],
                "access.md": ["Access policy mandates review every 180 days"],
            },
            "Extract the mandated interval from each of security.md, privacy.md and access.md.",
        ),
        (
            "dependencies",
            {
                "web.md": ["The web app pins framework version 5.2"],
                "worker.md": ["The worker pins queue library version 8.4"],
            },
            "Which versions are pinned in web.md and worker.md?",
        ),
        (
            "capacity",
            {
                "eu.md": ["EU region has 320 spare cores"],
                "us.md": ["US region has 75 spare cores"],
                "apac.md": ["APAC region has 140 spare cores"],
            },
            "Report the spare-core capacity for the EU, US and APAC regions.",
        ),
        # --- the twenty added on 2026-09-12 (bench/hierarchy_equal_calls addendum 3): the ten above
        # gave a 50% flip rate that no comparison could read through. Same shape, different figures;
        # no figure repeats across a task's documents, so a value from the wrong document never
        # passes for the right one.
        (
            "budgets",
            {
                "marketing.md": ["Marketing budget is 480 thousand dollars"],
                "engineering.md": ["Engineering budget is 1250 thousand dollars"],
                "support.md": ["Support budget is 210 thousand dollars"],
            },
            "Read marketing.md, engineering.md and support.md and report each department's budget.",
        ),
        (
            "slas",
            {
                "gold.md": ["Gold tier promises 99.95 percent uptime", "Gold tier response time is 25 minutes"],
                "silver.md": ["Silver tier promises 99.5 percent uptime", "Silver tier response time is 12 hours"],
            },
            "From gold.md and silver.md: what uptime does each tier promise, and what is each tier's "
            "response time?",
        ),
        (
            "storage",
            {
                "hot.md": ["Hot storage holds 18 terabytes"],
                "warm.md": ["Warm storage holds 96 terabytes"],
                "cold.md": ["Cold storage holds 640 terabytes"],
                "archive.md": ["Archive storage holds 2100 terabytes"],
            },
            "Read hot.md, warm.md, cold.md and archive.md and state how much each storage class holds.",
        ),
        (
            "migrations",
            {
                "orders.md": ["Orders migration takes 35 minutes", "Orders migration locks the ledger table"],
                "users.md": ["Users migration takes 16 minutes", "Users migration locks the sessions table"],
            },
            "Read orders.md and users.md: how long does each migration take, and which table does "
            "each one lock?",
        ),
        (
            "headcount",
            {
                "berlin.md": ["Berlin office has 46 engineers"],
                "lisbon.md": ["Lisbon office has 23 engineers"],
                "toronto.md": ["Toronto office has 71 engineers"],
            },
            "From berlin.md, lisbon.md and toronto.md: how many engineers does each office have?",
        ),
        (
            "latency",
            {
                "search.md": ["Search ninety-fifth percentile latency is 340 milliseconds"],
                "checkout.md": ["Checkout ninety-fifth percentile latency is 910 milliseconds"],
            },
            "Read search.md and checkout.md and report the ninety-fifth percentile latency of each service.",
        ),
        (
            "retention",
            {
                "logs.md": ["Logs are retained for 45 days"],
                "metrics.md": ["Metrics are retained for 400 days"],
                "traces.md": ["Traces are retained for 14 days"],
            },
            "From logs.md, metrics.md and traces.md: how long is each kind of data retained?",
        ),
        (
            "licences",
            {
                "editor.md": ["Editor licence costs 29 dollars per month", "Editor licence covers 12 devices"],
                "viewer.md": ["Viewer licence costs 18 dollars per month", "Viewer licence covers 40 devices"],
            },
            "Read editor.md and viewer.md: what does each licence cost, and how many devices does "
            "each cover?",
        ),
        (
            "regions",
            {
                "frankfurt.md": ["Frankfurt region opened in 2019"],
                "osaka.md": ["Osaka region opened in 2022"],
                "saopaulo.md": ["Sao Paulo region opened in 2016"],
                "sydney.md": ["Sydney region opened in 2021"],
            },
            "From frankfurt.md, osaka.md, saopaulo.md and sydney.md: in which year did each region open?",
        ),
        (
            "audits",
            {
                "soc2.md": ["The SOC audit found 13 exceptions"],
                "iso.md": ["ISO audit found 42 exceptions"],
            },
            "Read soc2.md and iso.md and report how many exceptions the SOC audit and the ISO audit found.",
        ),
        (
            "tickets",
            {
                "billing.md": ["Billing queue has 128 open tickets", "Billing oldest ticket is 19 days old"],
                "login.md": ["Login queue has 54 open tickets", "Login oldest ticket is 16 days old"],
            },
            "From billing.md and login.md: how many tickets are open in each queue, and how old is "
            "the oldest ticket in each?",
        ),
        (
            "quotas",
            {
                "free.md": ["Free plan allows 750 requests per day"],
                "team.md": ["Team plan allows 20000 requests per day"],
                "business.md": ["Business plan allows 250000 requests per day"],
            },
            "Read free.md, team.md and business.md and state the daily request quota of each plan.",
        ),
        (
            "backups",
            {
                "nightly.md": ["Nightly backup completes in 52 minutes"],
                "weekly.md": ["Weekly backup completes in 11 hours"],
            },
            "From nightly.md and weekly.md: how long does each backup take to complete?",
        ),
        (
            "throughput",
            {
                "ingest.md": ["Ingest pipeline handles 8500 events per second"],
                "export.md": ["Export pipeline handles 1200 events per second"],
                "replay.md": ["Replay pipeline handles 300 events per second"],
            },
            "Read ingest.md, export.md and replay.md and report each pipeline's events per second.",
        ),
        (
            "pricing",
            {
                "starter.md": ["Starter tier costs 49 dollars per month"],
                "growth.md": ["Growth tier costs 199 dollars per month"],
                "scale.md": ["Scale tier costs 890 dollars per month"],
                "enterprise.md": ["Enterprise tier costs 4200 dollars per month"],
            },
            "From starter.md, growth.md, scale.md and enterprise.md: what does each tier cost per month?",
        ),
        (
            "caches",
            {
                "edge.md": ["Edge cache hit rate is 93 percent", "Edge cache TTL is 60 seconds"],
                "origin.md": ["Origin cache hit rate is 61 percent", "Origin cache TTL is 900 seconds"],
            },
            "Read edge.md and origin.md: what is each cache's hit rate, and what TTL does each use?",
        ),
        (
            "queues",
            {
                "email.md": ["Email queue depth peaked at 7400 messages"],
                "webhook.md": ["Webhook queue depth peaked at 260 messages"],
                "sms.md": ["SMS queue depth peaked at 1900 messages"],
            },
            "From email.md, webhook.md and sms.md: what was the peak depth of each queue?",
        ),
        (
            "endpoints",
            {
                "v1.md": ["The first API version retires on 30 June"],
                "v2.md": ["The second API version retires on 15 December"],
            },
            "Read v1.md and v2.md and state the retirement date of each API version.",
        ),
        (
            "uptime",
            {
                "q1.md": ["First quarter uptime was 99.91 percent", "First quarter had 12 major incidents"],
                "q2.md": ["Second quarter uptime was 99.72 percent", "Second quarter had 25 major incidents"],
                "q3.md": ["Third quarter uptime was 99.98 percent", "Third quarter had 17 major incidents"],
            },
            "From q1.md, q2.md and q3.md: what was the uptime each quarter, and how many major "
            "incidents did each quarter have?",
        ),
        (
            "certificates",
            {
                "public.md": ["Public certificate expires on 19 April"],
                "internal.md": ["Internal certificate expires on 27 October"],
                "vpn.md": ["VPN certificate expires on 14 January"],
            },
            "Read public.md, internal.md and vpn.md and report when each certificate expires.",
        ),
    ]
    for task_id, docs, question in specs:
        facts = tuple(
            DocFact(needle=_needle(fact)) for content in docs.values() for fact in content
        )
        tasks.append(
            HierarchyTask(
                id=task_id,
                docs={name: _doc(name, fact_list) for name, fact_list in docs.items()},
                question=question,
                facts=facts,
            )
        )
    return tasks


def _needle(fact: str) -> str:
    """The gradable core of a planted fact: its trailing specific token(s).

    'Acme charges 14 dollars per seat' -> '14 dollars'; keeps grading robust to
    paraphrase while still requiring the exact figure/name to appear.
    """
    words = fact.split()
    for i, word in enumerate(words):
        if any(ch.isdigit() for ch in word):
            return " ".join(words[i : i + 2]).rstrip(".,")
    return " ".join(words[-3:]).rstrip(".,")


def baseline_prompt(task: HierarchyTask) -> str:
    """Baseline arm: ONE prompt carrying ALL documents inline + the full question."""
    docs = "\n\n".join(f"### {name}\n{content}" for name, content in task.docs.items())
    return f"{task.question}\n\n{docs}"


def make_specs(task: HierarchyTask, *, max_tokens: int = 8_000) -> list[TaskSpec]:
    """Treatment arm: one contract per document, each worker sees ONLY its own doc.

    This is the registered mechanism under test — minimal-context scoping. The
    question travels with every spec; the context is the single document.
    """
    from chimera.orchestration.spec import EffortBudget, TaskSpec

    budget = EffortBudget(max_tokens=max_tokens)
    return [
        TaskSpec(
            task_id=f"{task.id}-{i + 1}",
            objective=(
                "From the document below, extract exactly what this question needs "
                f"about it (verbatim figures/names included): {task.question}"
            ),
            output_format="The relevant facts as short bullets, exact figures verbatim.",
            boundaries="Use ONLY the provided document. Do not guess about other documents.",
            context=f"### {name}\n{content}",
            effort=budget,
        )
        for i, (name, content) in enumerate(task.docs.items())
    ]


@dataclass
class ArmOutcome:
    """One arm's result on one task: pass/fail + measured tokens (None = unknown)."""

    passed: bool
    tokens: int | None = None


@dataclass
class HierarchyABReport:
    """Quality (paired, significance-capable) + tokens (totals only, no significance)."""

    paired: PairedResult
    baseline_tokens: list[int | None] = field(default_factory=list)
    treatment_tokens: list[int | None] = field(default_factory=list)
    counterfactual_tokens: list[int | None] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        out = dict(self.paired.summary())
        base = [t for t in self.baseline_tokens if t is not None]
        treat = [t for t in self.treatment_tokens if t is not None]
        out["baseline_total_tokens"] = sum(base) if base else None
        out["treatment_total_tokens"] = sum(treat) if treat else None
        out["baseline_median_tokens"] = median(base) if base else None
        out["treatment_median_tokens"] = median(treat) if treat else None
        if base and treat and sum(base):
            out["token_reduction"] = round(1 - (sum(treat) / sum(base)), 4)
        cf = [t for t in self.counterfactual_tokens if t is not None]
        if cf:
            out["counterfactual_total_tokens"] = sum(cf)
        return out


def run_hierarchy_ab(
    items: Sequence[T],
    *,
    restore: Callable[[T], None],
    baseline: Callable[[T], ArmOutcome],
    treatment: Callable[[T], ArmOutcome],
    baseline_name: str = "single-agent",
    treatment_name: str = "hierarchy",
) -> HierarchyABReport:
    """Paired discipline (restore before EACH arm) + symmetric token metering."""
    base_pass: list[bool] = []
    treat_pass: list[bool] = []
    base_tokens: list[int | None] = []
    treat_tokens: list[int | None] = []
    for item in items:
        restore(item)
        outcome = baseline(item)
        base_pass.append(outcome.passed)
        base_tokens.append(outcome.tokens)
        restore(item)
        outcome = treatment(item)
        treat_pass.append(outcome.passed)
        treat_tokens.append(outcome.tokens)
    paired = compare_paired(
        base_pass, treat_pass, baseline_name=baseline_name, treatment_name=treatment_name
    )
    return HierarchyABReport(
        paired=paired, baseline_tokens=base_tokens, treatment_tokens=treat_tokens
    )


def format_token_report(report: HierarchyABReport) -> str:
    """The cost table — explicitly WITHOUT a significance verdict (that word is
    reserved for the quality axis)."""
    summary = report.summary()
    lines = [
        f"tokens (measured):  {report.paired.baseline_name}={summary.get('baseline_total_tokens')}"
        f"  {report.paired.treatment_name}={summary.get('treatment_total_tokens')}",
        f"medians:            {summary.get('baseline_median_tokens')} vs "
        f"{summary.get('treatment_median_tokens')}",
    ]
    reduction = summary.get("token_reduction")
    if isinstance(reduction, float):
        lines.append(f"token reduction:    {reduction:+.1%} (totals; no significance claimed on cost)")
    return "\n".join(lines)
