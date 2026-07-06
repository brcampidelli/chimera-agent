"""Honest A/B over a benchmark — the measuring stick for the whole M14 "with proof" discipline.

Every M14 feature must justify itself by moving a number, and the only credible way to report
that (given the HAL finding that a model swings ~7pts from scaffolding alone) is a controlled
A/B: fix the task subset and the model, make the *only* variable the scaffolding, and report the
delta with a confidence interval — not a bare "it got better".

This aggregates per-task pass/fail for two arms (baseline vs treatment) across any number of
seeds (each task×seed is one Bernoulli trial), then reports each arm's Wilson-bounded pass rate
and the Newcombe CI on the difference (from :mod:`chimera.eval.anytime`). The verdict is honest:
"significant" only when the difference CI excludes zero. Pure Python — no benchmark runner here;
feed it the pass/fail lists a terminal-bench (or any) run produces.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from chimera.eval.anytime import proportion_diff_ci, wilson_bounds


@dataclass
class Arm:
    """One experimental arm: a name and the flat pass/fail list of all its trials."""

    name: str
    passed: list[bool] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.passed)

    @property
    def successes(self) -> int:
        return sum(1 for p in self.passed if p)

    @property
    def rate(self) -> float:
        return self.successes / self.n if self.n else 0.0

    @property
    def wilson(self) -> tuple[float, float]:
        return wilson_bounds(self.successes, self.n)


@dataclass
class ABResult:
    """The comparison of a treatment arm against a baseline arm."""

    baseline: Arm
    treatment: Arm

    @property
    def delta(self) -> float:
        """treatment.rate - baseline.rate (the headline number)."""
        return self.treatment.rate - self.baseline.rate

    @property
    def diff_ci(self) -> tuple[float, float]:
        """Newcombe CI for (treatment - baseline). Lower bound > 0 == treatment wins."""
        return proportion_diff_ci(
            self.treatment.successes, self.treatment.n, self.baseline.successes, self.baseline.n
        )

    @property
    def significant(self) -> bool:
        """True only when the difference is confidently positive (CI excludes zero)."""
        return self.diff_ci[0] > 0.0

    def summary(self) -> dict[str, float | bool | int]:
        low, high = self.diff_ci
        return {
            "baseline_rate": round(self.baseline.rate, 4),
            "treatment_rate": round(self.treatment.rate, 4),
            "delta": round(self.delta, 4),
            "ci_low": round(low, 4),
            "ci_high": round(high, 4),
            "significant": self.significant,
            "n_baseline": self.baseline.n,
            "n_treatment": self.treatment.n,
        }


def compare(
    baseline_passed: list[bool],
    treatment_passed: list[bool],
    *,
    baseline_name: str = "baseline",
    treatment_name: str = "treatment",
) -> ABResult:
    """Build an :class:`ABResult` from two arms' pass/fail trial lists."""
    return ABResult(Arm(baseline_name, list(baseline_passed)), Arm(treatment_name, list(treatment_passed)))


def format_report(result: ABResult) -> str:
    """A compact, honest text report — pass rates, delta, CI, and the verdict."""
    b, t = result.baseline, result.treatment
    bl, bh = b.wilson
    tl, th = t.wilson
    low, high = result.diff_ci
    verdict = (
        "SIGNIFICANT — treatment beats baseline" if result.significant
        else "not significant (CI includes 0)"
    )
    return (
        f"{b.name:<22} {b.rate:6.1%}  [{bl:.1%}, {bh:.1%}]  ({b.successes}/{b.n})\n"
        f"{t.name:<22} {t.rate:6.1%}  [{tl:.1%}, {th:.1%}]  ({t.successes}/{t.n})\n"
        f"{'delta (Δ)':<22} {result.delta:+6.1%}  95% CI [{low:+.1%}, {high:+.1%}]\n"
        f"{'verdict':<22} {verdict}"
    )
