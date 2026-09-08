"""Replicated runs — the reporting protocol that makes a single agent number into a measurement.

A single run of an agent benchmark is a sample, not a measurement. Measured here: on LoopsBench the
identical configuration run twice resolved **one task each time and a different task each time** —
25% of outcomes flipped with nothing changed (`bench/loopsbench/RESULTS.md`). Measured elsewhere:
identical agent + identical input gives 2.3–4.2 distinct action sequences per 10 runs
(arXiv 2602.11619); configuration-equivalent protocols differ by [−3, +18] pp across two seeds and a
+18 pp p=0.012 result vanished at the second seed (2606.20695); 23 identical reruns of one tool
benchmark spread 57.9–76.8% (2607.02577). Seven of ten recent multi-agent architectures report
headline effects below their own noise floor (2606.20695). Ours included, until this module.

Three things a replicated report carries that a single run cannot:

1. **``pass^k``** (2601.06112, 2608.14711): the fraction of tasks that pass in *every* one of ``k``
   runs. Strict on purpose — it is the number a user experiences, and it falls as ``k`` grows for
   any task whose outcome is a coin flip.
2. **The flip rate and ICC(1)** (2512.06710): how much of the variance is *between tasks* (a task
   property) versus *within a task across runs* (noise). A negative or near-zero ICC says which task
   passes is not a property of the task, and then no per-task comparison can be read.
3. **Mechanism-active scoring** (2606.20695): score only the trials where the mechanism under test
   was *logically active*. A retry policy measured on runs that never retried is measuring nothing
   — §2r of the project's lessons file, "a intervenção reporta quanto ela agiu", as a protocol.

The paired statistic itself is unchanged: :func:`chimera.eval.paired.compare_paired` stays the
ruler, applied to per-task ``pass^k`` outcomes, so twenty existing callers keep their numbers and
their meaning. What this module adds is the denominator those numbers were missing.

The seeds rule, encoded rather than remembered: **one run is a sample, two alert, three decide**
(§2x). ``seeds_verdict`` says which one a report is, in the report.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from chimera.eval.paired import PairedResult, compare_paired

__all__ = [
    "ReplicatedArm",
    "ReplicatedResult",
    "compare_replicated",
    "format_replicated_report",
    "icc1",
    "seeds_verdict",
]


def seeds_verdict(k: int) -> str:
    """One run is a sample, two alert, three decide. Said in the report, not in a memory file."""
    if k <= 1:
        return "a sample — one run cannot separate an effect from a reseed"
    if k == 2:
        return "an alert — two runs produce a difference, not a variance estimate"
    return "decides — three or more runs bound the noise the comparison is read against"


def icc1(runs: Sequence[Sequence[bool]]) -> tuple[float | None, str]:
    """ICC(1) of a task × run grid of pass/fail, or ``None`` with the reason it cannot be computed.

    One-way random effects: how much of the total variance is between tasks. With binary outcomes
    it is the standard approximation (2512.06710 reports 0.30–0.77 on agentic suites and treats an
    improvement as trustworthy only if ICC improves with it). It can be slightly negative when the
    within-task spread exceeds the between-task spread — that is a real reading ("which task passes
    is not a property of the task"), not an error, so it is returned as computed.

    ``None`` is returned, never ``0.0``, when the grid cannot support the statistic: fewer than two
    tasks or two runs (nothing to partition), or no variance anywhere (every trial identical).
    """
    n = len(runs)
    if n < 2:
        return None, "needs at least two tasks"
    k = len(runs[0])
    if k < 2:
        return None, "needs at least two runs per task"
    total = n * k
    grand = sum(1 for row in runs for v in row if v) / total
    means = [sum(1 for v in row if v) / k for row in runs]
    msb = k * sum((m - grand) ** 2 for m in means) / (n - 1)
    msw = sum(((1.0 if v else 0.0) - m) ** 2 for row, m in zip(runs, means, strict=True) for v in row)
    msw /= n * (k - 1)
    denom = msb + (k - 1) * msw
    if denom == 0:
        return None, "no variance at all — every trial identical"
    return (msb - msw) / denom, ""


@dataclass
class ReplicatedArm:
    """One arm run ``k`` times per task: ``runs[task][run]`` is pass/fail.

    ``active``, same shape, marks the trials where the mechanism this arm exists to test actually
    acted — the retry fired, the compaction ran, the delegation happened. Scores over the active
    subset are what 2606.20695 calls mechanism-active, and a trial count of zero there is reported
    as *not measured*, never as 0%.
    """

    name: str
    runs: list[list[bool]]
    active: list[list[bool]] | None = None

    def __post_init__(self) -> None:
        if not self.runs:
            raise ValueError(f"arm {self.name!r} has no tasks")
        k = len(self.runs[0])
        if k == 0:
            raise ValueError(f"arm {self.name!r} has tasks with zero runs")
        if any(len(row) != k for row in self.runs):
            raise ValueError(
                f"arm {self.name!r} is not rectangular — every task must have the same k, "
                "or pass^k means different things on different rows"
            )
        if self.active is not None and (
            len(self.active) != len(self.runs) or any(len(a) != k for a in self.active)
        ):
            raise ValueError(
                f"arm {self.name!r}: `active` must have the same task × run shape as `runs`"
            )

    @property
    def n(self) -> int:
        return len(self.runs)

    @property
    def k(self) -> int:
        return len(self.runs[0])

    @property
    def per_task_rate(self) -> list[float]:
        return [sum(1 for v in row if v) / self.k for row in self.runs]

    @property
    def pass_at_1(self) -> float:
        """Mean pass rate over every trial — the number a single run pretends to be."""
        return sum(1 for row in self.runs for v in row if v) / (self.n * self.k)

    @property
    def pass_pow_k(self) -> float:
        """Fraction of tasks that passed in EVERY run. Strict: it is what a user experiences."""
        return sum(1 for row in self.runs if all(row)) / self.n

    @property
    def per_task_pow_k(self) -> list[bool]:
        return [all(row) for row in self.runs]

    @property
    def flip_rate(self) -> float:
        """Fraction of tasks whose outcome differed between runs of the SAME arm — the noise floor."""
        return sum(1 for row in self.runs if any(row) and not all(row)) / self.n

    @property
    def icc(self) -> float | None:
        return icc1(self.runs)[0]

    @property
    def icc_reason(self) -> str:
        return icc1(self.runs)[1]

    @property
    def active_trials(self) -> int | None:
        if self.active is None:
            return None
        return sum(1 for row in self.active for v in row if v)

    @property
    def active_pass_rate(self) -> float | None:
        """Pass rate over the trials where the mechanism acted; ``None`` when nothing was marked.

        ``None`` for an absent mask AND for a mask with zero active trials: a mechanism that never
        fired has not been measured, and 0% would say it fired and lost every time.
        """
        if self.active is None:
            return None
        hits = total = 0
        for row, act in zip(self.runs, self.active, strict=True):
            for v, a in zip(row, act, strict=True):
                if a:
                    total += 1
                    hits += 1 if v else 0
        return hits / total if total else None

    def summary(self) -> dict[str, object]:
        return {
            "name": self.name,
            "n": self.n,
            "k": self.k,
            "pass_at_1": round(self.pass_at_1, 4),
            "pass_pow_k": round(self.pass_pow_k, 4),
            "flip_rate": round(self.flip_rate, 4),
            "icc": None if self.icc is None else round(self.icc, 4),
            "icc_reason": self.icc_reason,
            "active_trials": self.active_trials,
            "active_pass_rate": (
                None if self.active_pass_rate is None else round(self.active_pass_rate, 4)
            ),
        }


@dataclass
class ReplicatedResult:
    """Two replicated arms compared paired on per-task ``pass^k``, with the noise floor beside it."""

    baseline: ReplicatedArm
    treatment: ReplicatedArm
    paired: PairedResult = field(init=False)

    def __post_init__(self) -> None:
        if self.baseline.n != self.treatment.n:
            raise ValueError(
                f"arms must cover the same tasks (got {self.baseline.n} vs {self.treatment.n})"
            )
        self.paired = compare_paired(
            self.baseline.per_task_pow_k,
            self.treatment.per_task_pow_k,
            baseline_name=self.baseline.name,
            treatment_name=self.treatment.name,
        )

    @property
    def k(self) -> int:
        return min(self.baseline.k, self.treatment.k)

    @property
    def noise_floor(self) -> float:
        """The larger flip rate of the two arms: how much a task moves with nothing changed."""
        return max(self.baseline.flip_rate, self.treatment.flip_rate)

    @property
    def inside_noise_floor(self) -> bool:
        """True when the paired delta is no larger than the arms' own run-to-run movement.

        Reported BESIDE ``paired.significant``, never instead of it. A CI that excludes zero on a
        delta smaller than the floor is what 2606.20695 found in seven of ten published systems.
        """
        return abs(self.paired.delta) <= self.noise_floor

    def summary(self) -> dict[str, object]:
        return {
            "k": self.k,
            "seeds": seeds_verdict(self.k),
            "baseline": self.baseline.summary(),
            "treatment": self.treatment.summary(),
            "paired_pow_k": self.paired.summary(),
            "noise_floor": round(self.noise_floor, 4),
            "inside_noise_floor": self.inside_noise_floor,
        }


def compare_replicated(baseline: ReplicatedArm, treatment: ReplicatedArm) -> ReplicatedResult:
    """Compare two replicated arms. Task ``i`` must be the same task in both."""
    return ReplicatedResult(baseline, treatment)


def _fmt_icc(arm: ReplicatedArm) -> str:
    if arm.icc is None:
        return f"n/a ({arm.icc_reason})"
    return f"{arm.icc:+.2f}"


def _fmt_active(arm: ReplicatedArm) -> str:
    if arm.active is None:
        return "not marked"
    if arm.active_trials == 0:
        return "0 trials — NOT MEASURED (the mechanism never fired)"
    assert arm.active_pass_rate is not None
    return f"{arm.active_pass_rate:.1%} over {arm.active_trials} active trials"


def format_replicated_report(result: ReplicatedResult) -> str:
    """A compact rendering that puts the denominator next to every rate."""
    b, t, p = result.baseline, result.treatment, result.paired
    lo, hi = p.diff_ci
    verdict = "significant (CI excludes 0)" if p.significant else "not significant (CI includes 0)"
    floor = (
        "INSIDE the noise floor — a task moves this much with nothing changed"
        if result.inside_noise_floor
        else "outside the noise floor"
    )
    width = max(len(b.name), len(t.name), 8)
    lines = [
        f"{'arm':<{width}}  pass@1   pass^{result.k}   flip   ICC(1)   mechanism-active",
        f"{b.name:<{width}}  {b.pass_at_1:>5.1%}   {b.pass_pow_k:>5.1%}   {b.flip_rate:>4.0%}   "
        f"{_fmt_icc(b):<7}  {_fmt_active(b)}",
        f"{t.name:<{width}}  {t.pass_at_1:>5.1%}   {t.pass_pow_k:>5.1%}   {t.flip_rate:>4.0%}   "
        f"{_fmt_icc(t):<7}  {_fmt_active(t)}",
        "",
        f"paired on pass^{result.k}   Δ {p.delta:+.1%}  95% CI [{lo:+.1%}, {hi:+.1%}]  "
        f"discordant {t.name} +{p.treatment_only} / {b.name} +{p.baseline_only}",
        f"verdict             {verdict}; |Δ| {abs(p.delta):.1%} vs floor {result.noise_floor:.1%}: {floor}",
        f"runs per task       k={result.k}: {seeds_verdict(result.k)}",
    ]
    return "\n".join(lines)
