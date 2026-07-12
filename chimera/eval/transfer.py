"""Transfer-gated promotion — does a learned change GENERALIZE, or did it memorize the eval?

The honest A/B (:mod:`chimera.eval.paired`) proves a self-evolution change (GEPA prompt,
ACE playbook delta, a distilled skill) raised the pass rate *on the slice it was tuned
against*. But EvoAgentBench (arXiv 2607.05202) measured that automatic experience-encoding
methods frequently produce **negative transfer** — a change that helps its own tuned tasks
yet REGRESSES on other tasks that share the same capability. GEPA specifically regressed
−12.3 in that study. That is exactly the failure Chimera's diff-gate guards against, and
this module makes the guard sharper: promote only when the change

1. **helps the tuned slice** (paired Δ > 0, optionally significant), AND
2. **does not regress a disjoint, same-capability HOLDOUT slice** (tasks it was not tuned
   on) beyond a tolerance.

The holdout is the EvoAgentBench "ability-aware split" discipline, minus the benchmark
infra: hold out tasks that exercise the same capability but were not the ones the change
was fit to, and require non-regression there. If no holdout is supplied the decision falls
back to the tuned slice alone but flags that transfer was NOT measured — an honest
"promoted without a transfer check", never a silent pass.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from chimera.eval.paired import PairedResult, compare_paired


@dataclass(frozen=True)
class TransferDecision:
    """Whether to promote a learned change, with the paired evidence behind it."""

    promote: bool
    reason: str
    tuned: PairedResult
    holdout: PairedResult | None
    transfer_measured: bool

    def summary(self) -> dict[str, object]:
        out: dict[str, object] = {
            "promote": self.promote,
            "reason": self.reason,
            "transfer_measured": self.transfer_measured,
            "tuned": self.tuned.summary(),
        }
        if self.holdout is not None:
            out["holdout"] = self.holdout.summary()
        return out


def transfer_gated_promotion(
    *,
    tuned_baseline: Sequence[bool],
    tuned_treatment: Sequence[bool],
    holdout_baseline: Sequence[bool] | None = None,
    holdout_treatment: Sequence[bool] | None = None,
    require_tuned_significant: bool = False,
    holdout_regression_tol: float = 0.0,
) -> TransferDecision:
    """Promote a change only if it helps its tuned slice AND doesn't regress a same-capability holdout.

    Parameters
    ----------
    tuned_baseline / tuned_treatment:
        Aligned pass/fail lists on the slice the change was fit to (item i = same forked state).
    holdout_baseline / holdout_treatment:
        Aligned pass/fail on a DISJOINT slice sharing the capability but not tuned on. Omit both
        to fall back to the tuned slice alone (``transfer_measured=False``).
    require_tuned_significant:
        If True, the tuned gain must clear the honest bar (paired CI excludes 0), not just Δ > 0.
    holdout_regression_tol:
        Max tolerated drop on the holdout: promotion is blocked if
        ``holdout.treatment_rate < holdout.baseline_rate - tol``. Default 0.0 = no regression allowed.
    """
    tuned = compare_paired(tuned_baseline, tuned_treatment, treatment_name="candidate")

    tuned_helps = (tuned.significant and tuned.delta > 0) if require_tuned_significant else tuned.delta > 0
    if not tuned_helps:
        bar = "not significant" if require_tuned_significant else f"Δ={tuned.delta:+.1%} <= 0"
        return TransferDecision(
            promote=False, reason=f"tuned slice did not improve ({bar})",
            tuned=tuned, holdout=None, transfer_measured=False,
        )

    # Falsy = None OR empty: an EMPTY holdout is not a measured non-regression — compare_paired([], [])
    # would give n=0, Δ=0.0, "no regression", falsely asserting transfer was measured on zero tasks.
    if not holdout_baseline or not holdout_treatment:
        return TransferDecision(
            promote=True,
            reason=f"tuned slice improved (Δ={tuned.delta:+.1%}); TRANSFER NOT MEASURED (no holdout)",
            tuned=tuned, holdout=None, transfer_measured=False,
        )

    holdout = compare_paired(holdout_baseline, holdout_treatment, treatment_name="candidate")
    regressed = holdout.treatment_rate < holdout.baseline_rate - holdout_regression_tol
    if regressed:
        return TransferDecision(
            promote=False,
            reason=(
                f"NEGATIVE TRANSFER: helped tuned (Δ={tuned.delta:+.1%}) but regressed the "
                f"same-capability holdout (Δ={holdout.delta:+.1%}, tol={holdout_regression_tol:+.1%})"
            ),
            tuned=tuned, holdout=holdout, transfer_measured=True,
        )
    return TransferDecision(
        promote=True,
        reason=f"generalizes: tuned Δ={tuned.delta:+.1%}, holdout Δ={holdout.delta:+.1%} (no regression)",
        tuned=tuned, holdout=holdout, transfer_measured=True,
    )
