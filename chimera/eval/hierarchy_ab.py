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
    """10 read-heavy multi-part tasks, 2-4 docs each, all deterministically gradable."""
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
