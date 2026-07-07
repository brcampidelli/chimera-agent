"""Paired A/B — the statistical payoff of running two arms from the *identical* forked state (M15-B1).

The unpaired A/B (:mod:`chimera.eval.bench_ab`, Newcombe) treats each arm's trials as independent, so
the variance from *starting conditions* (which task, the stochastic first move) is baked into the
interval. When both arms replay from the SAME forked checkpoint — the LangGraph "fork from a
checkpoint" trick, exposed as :meth:`chimera.core.runstate.RunCheckpointer.fork` — the only thing
that differs is the policy, so the comparison is *paired*: concordant pairs (both pass, both fail)
carry no signal and only the discordant pairs do.

This is McNemar's test with a Wilson interval on the discordant pairs. It is honest and it is
*tighter*: conditioning on the discordant count removes the agreement noise the unpaired interval
still pays for, so a real lift can clear zero at a sample size where Newcombe cannot. Same honesty
rule: "significant" only when the difference CI excludes zero. Pure Python.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from chimera.eval.anytime import wilson_bounds

T = TypeVar("T")


@dataclass
class PairedResult:
    """A paired (McNemar) comparison of a treatment vs a baseline over the same forked states."""

    baseline_name: str
    treatment_name: str
    both_pass: int  # a — concordant, no signal
    baseline_only: int  # b — baseline won this pair
    treatment_only: int  # c — treatment won this pair
    both_fail: int  # d — concordant, no signal

    @property
    def n(self) -> int:
        return self.both_pass + self.baseline_only + self.treatment_only + self.both_fail

    @property
    def discordant(self) -> int:
        """Pairs where the arms disagreed — the only ones that carry signal."""
        return self.baseline_only + self.treatment_only

    @property
    def baseline_rate(self) -> float:
        return (self.both_pass + self.baseline_only) / self.n if self.n else 0.0

    @property
    def treatment_rate(self) -> float:
        return (self.both_pass + self.treatment_only) / self.n if self.n else 0.0

    @property
    def delta(self) -> float:
        """treatment_rate - baseline_rate == (c - b) / n — the paired net lift."""
        return (self.treatment_only - self.baseline_only) / self.n if self.n else 0.0

    @property
    def diff_ci(self) -> tuple[float, float]:
        """95% CI for the paired difference, via a Wilson interval on the discordant pairs.

        Among the ``m`` discordant pairs, the treatment wins a fraction ``q = c/m``; McNemar tests
        ``q == 0.5``. A Wilson CI ``(ql, qu)`` on ``q`` maps to the difference through
        ``delta = (m/n)·(2q − 1)`` (monotonic), which conditions out the concordant agreement —
        that is why it is narrower than the unpaired Newcombe interval on the same data.
        """
        m = self.discordant
        if self.n == 0:
            return (-1.0, 1.0)
        if m == 0:
            return (0.0, 0.0)  # arms agreed on every pair — zero observed difference, no signal
        ql, qu = wilson_bounds(self.treatment_only, m)
        scale = m / self.n
        return (scale * (2 * ql - 1), scale * (2 * qu - 1))

    @property
    def significant(self) -> bool:
        """True when the difference CI excludes zero (the honest bar)."""
        lo, hi = self.diff_ci
        return lo > 0 or hi < 0

    def summary(self) -> dict[str, object]:
        lo, hi = self.diff_ci
        return {
            "n": self.n,
            "baseline_rate": round(self.baseline_rate, 4),
            "treatment_rate": round(self.treatment_rate, 4),
            "delta": round(self.delta, 4),
            "discordant": {"baseline_only": self.baseline_only, "treatment_only": self.treatment_only},
            "diff_ci": [round(lo, 4), round(hi, 4)],
            "significant": self.significant,
        }


def compare_paired(
    baseline: Sequence[bool],
    treatment: Sequence[bool],
    *,
    baseline_name: str = "baseline",
    treatment_name: str = "treatment",
) -> PairedResult:
    """Build a paired result from two aligned pass/fail lists (item i is the SAME forked state)."""
    if len(baseline) != len(treatment):
        raise ValueError(
            f"paired arms must be the same length (got {len(baseline)} vs {len(treatment)}) — "
            "each index must be the same task replayed from the same fork"
        )
    a = b = c = d = 0
    for base, treat in zip(baseline, treatment, strict=True):
        if base and treat:
            a += 1
        elif base and not treat:
            b += 1
        elif not base and treat:
            c += 1
        else:
            d += 1
    return PairedResult(baseline_name, treatment_name, a, b, c, d)


def run_paired_experiment(
    items: Sequence[T],
    *,
    restore: Callable[[T], None],
    baseline: Callable[[T], bool],
    treatment: Callable[[T], bool],
    baseline_name: str = "baseline",
    treatment_name: str = "treatment",
) -> PairedResult:
    """Replay both arms from the identical state per item, then compare them paired.

    For each item, ``restore`` is called **before each arm** so both start from the same forked
    checkpoint/workspace (see :meth:`chimera.core.runstate.RunCheckpointer.fork`); ``baseline`` and
    ``treatment`` each run the item and return pass/fail. This encodes the discipline that makes the
    comparison paired — the only difference between the two runs of an item is the policy. The
    solvers are injected, so the whole experiment is testable without a network or a real workspace.
    """
    base_results: list[bool] = []
    treat_results: list[bool] = []
    for item in items:
        restore(item)
        base_results.append(baseline(item))
        restore(item)
        treat_results.append(treatment(item))
    return compare_paired(
        base_results, treat_results, baseline_name=baseline_name, treatment_name=treatment_name
    )


def format_report(result: PairedResult) -> str:
    """A compact human-readable rendering for the CLI."""
    lo, hi = result.diff_ci
    verdict = "significant (CI excludes 0)" if result.significant else "not significant (CI includes 0)"
    return "\n".join(
        [
            f"{result.baseline_name:<22} {result.baseline_rate:.1%}  ({result.n} paired trials)",
            f"{result.treatment_name:<22} {result.treatment_rate:.1%}",
            f"paired delta (Δ)       {result.delta:+.1%}  95% CI [{lo:+.1%}, {hi:+.1%}]",
            f"discordant pairs       {result.treatment_name} +{result.treatment_only} / "
            f"{result.baseline_name} +{result.baseline_only}  (concordant carry no signal)",
            f"verdict                {verdict}",
        ]
    )
