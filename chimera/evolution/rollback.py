"""Auto-rollback (M19-A6): retract the most recent learned artifact when evolution REGRESSED.

The self-evolution engine measures its own health with :class:`~chimera.eval.continuous.EvolutionReport`
(first-half vs second-half pass rate, with a confidence interval, plus a cost-drift signal). This
closes the loop: when a run **degraded significantly** — the CI lower bound on degradation is > 0, not
a bare point subtraction — or cost drifted past a tolerance, retract the most recently adopted skill so
the next run reverts to the prior, healthier state.

Two honesty rails, both load-bearing:
- It keys on ``degraded_significantly()`` (a CI), never on the point ``degradation`` — a 0.2 drop on a
  short chain is usually noise, and rolling back on noise thrashes healthy artifacts. A sample too
  small to say (``None``) is treated as "no rollback", never a false positive.
- Retraction is a **retire** (proposed-with-review), not a delete — reversible via
  ``SkillStore.approve`` — so an over-eager rollback costs nothing permanent.

The decision is pure (testable without a model); ``apply_rollback`` performs the reversible retire.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from chimera.eval.continuous import EvolutionReport
    from chimera.evolution.skill_store import SkillStore


@dataclass
class RollbackDecision:
    """Whether to retract the most recent artifact, and which one."""

    should_rollback: bool
    reason: str
    target: str | None  # the skill name to retire, or None when nothing should be rolled back


def assess_rollback(
    report: EvolutionReport,
    *,
    recent_artifacts: Sequence[str],
    cost_drift_tol: float | None = None,
) -> RollbackDecision:
    """Decide whether to retract the most recent artifact, from the run's degradation report.

    Rolls back ONLY on a statistically significant accuracy degradation (CI lower bound > 0) or, when
    ``cost_drift_tol`` is given, a cost drift beyond it. A point degradation, or a sample too small for
    the CI to speak, never triggers a rollback. ``recent_artifacts`` is newest-last; the last one is
    the retract target.
    """
    target = recent_artifacts[-1] if recent_artifacts else None
    significant = report.degraded_significantly()
    if significant is True:
        return RollbackDecision(
            should_rollback=target is not None,
            reason=(
                f"accuracy degraded significantly (degradation Δ={report.degradation:+.1%}, CI low > 0)"
                + ("" if target else " — but no artifact to retract")
            ),
            target=target,
        )
    drift = report.cost_drift()
    if cost_drift_tol is not None and drift is not None and drift > cost_drift_tol:
        return RollbackDecision(
            should_rollback=target is not None,
            reason=(
                f"cost drift {drift:+.0f} tokens exceeds tolerance {cost_drift_tol:+.0f}"
                + ("" if target else " — but no artifact to retract")
            ),
            target=target,
        )
    if significant is None:
        return RollbackDecision(
            False, "sample too small for the degradation CI to speak — no rollback", None
        )
    return RollbackDecision(
        False, "no significant degradation and cost within tolerance — evolution is healthy", None
    )


def apply_rollback(store: SkillStore, decision: RollbackDecision) -> bool:
    """Perform the rollback: retire the target skill (reversible via ``store.approve``).

    Returns True when a skill was retired. A no-op (and False) when the decision says not to roll
    back or names no target.
    """
    if not decision.should_rollback or decision.target is None:
        return False
    return store.retire(decision.target)
