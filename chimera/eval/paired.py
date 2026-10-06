"""Paired A/B — the statistical payoff of running two arms from the *identical* forked state (M15-B1).

The unpaired A/B (:mod:`chimera.eval.bench_ab`, Newcombe) treats each arm's trials as independent, so
the variance from *starting conditions* (which task, the stochastic first move) is baked into the
interval. When both arms replay from the SAME forked checkpoint — the LangGraph "fork from a
checkpoint" trick, exposed as :meth:`chimera.core.runstate.RunCheckpointer.fork` — the only thing
that differs is the policy, so the comparison is *paired*: concordant pairs (both pass, both fail)
carry no signal and only the discordant pairs do.

The interval is Bonett-Price's adjusted Wald interval for a paired difference
(:func:`chimera.eval.proportions.bonett_price_paired`), and it is tighter than the unpaired one for
the honest reason: the concordant pairs move both arms together, so their agreement is not counted
as noise twice. "Significant" needs two things to agree: the difference CI excludes zero AND the
exact McNemar test (:func:`chimera.eval.proportions.mcnemar_exact`, the test PROTOCOL §11 names for
the paired p) is at or under 0.05. Bonett-Price alone is an interval, not a test, and on small
one-sided tables it is anti-conservative: four discordant pairs to none at n = 4-6, or five to none
at n = 20-23, print an interval clear of zero while the exact p is 0.125 and 0.0625.

⚠️ Until study 30 (S30-34) the interval here was a Wilson interval on the share of discordant pairs
the treatment won, scaled by the observed ``m/n`` as if that share were known. It was narrower than
it had any right to be: 41-88% coverage of a real difference at a nominal 95%
(`tests/test_the_paired_interval_covers_the_difference.py`), a zero-width interval whenever the arms
agreed on every pair, and "significant" at four discordant pairs to none, where the exact McNemar
p-value is 0.125. Bonett-Price alone did not close that last one at every n (see above), which is why
the verdict also asks the exact test. Published intervals computed the old way, where they were
committed as a ``PairedResult.summary()`` JSON or are named in its RESULTS, are re-read in
`bench/interval_reread`; that bench lists the readers it did not re-read.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from chimera.eval.proportions import bonett_price_paired, mcnemar_exact

#: The level the exact McNemar test must reach for a paired difference to be called significant.
SIGNIFICANCE_LEVEL = 0.05

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
        """95% CI for ``treatment − baseline``, Bonett-Price on the paired table.

        It keeps the uncertainty in how many pairs disagree, which the conditional interval this
        replaced did not (see the module docstring), so arms that agreed on every pair still get an
        interval of honest width instead of ``(0.0, 0.0)``. ``(-1.0, 1.0)`` with no pairs.
        """
        return bonett_price_paired(self.baseline_only, self.treatment_only, self.n)

    @property
    def significant(self) -> bool:
        """True when the difference CI excludes zero AND the exact McNemar p is at most 0.05.

        Either alone can say yes where the other says no on a small table; the verdict that gates a
        flip (skill cards, memory-graph slices, the CLI) is the conservative one of the two.
        """
        lo, hi = self.diff_ci
        if not (lo > 0 or hi < 0):
            return False
        return mcnemar_exact(self.baseline_only, self.treatment_only) <= SIGNIFICANCE_LEVEL

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


def verdict_text(result: PairedResult) -> str:
    """The one-line verdict, naming which of the two checks said no when the CI alone would say yes."""
    if result.significant:
        return "significant (CI excludes 0)"
    lo, hi = result.diff_ci
    if lo > 0 or hi < 0:
        p = mcnemar_exact(result.baseline_only, result.treatment_only)
        return f"not significant (CI excludes 0, but exact McNemar p = {p:.3g} > {SIGNIFICANCE_LEVEL})"
    return "not significant (CI includes 0)"


def format_report(result: PairedResult) -> str:
    """A compact human-readable rendering for the CLI."""
    lo, hi = result.diff_ci
    verdict = verdict_text(result)
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
