"""GEPA refine bridge (M19-A5): mine verified trajectories → refine a skill → transfer-gate it.

GEPA (``chimera/evolution/gepa.py``) evolves a skill's prompt template against a set of graded
``TaskInstance``s, but nothing fed it from a live run — it was offline-only. This bridge closes that
gap: it turns **verified** solve trajectories (``outcome == "success"`` and NOT a hollow diff) into
task instances whose scorer rewards reproducing the verified answer, runs GEPA, and then — crucially —
does NOT adopt the winner on the tuned slice alone. Promotion goes through
:func:`~chimera.eval.transfer.transfer_gated_promotion`: the refined template must help its tuned
slice AND not regress a disjoint, same-capability **holdout** slice (the EvoAgentBench negative-
transfer guard, which measured GEPA itself regressing −12.3 without it). No holdout ⇒ **dry-run**: the
refinement is reported but never persisted, because transfer was not measured.

Both model-touching seams (executor, reflector) are injected, so mining + gating are testable without
a network; the CLI wires the gateway-backed defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from chimera.eval.transfer import TransferDecision, transfer_gated_promotion
from chimera.evolution.gepa import GEPAOptimizer, GEPAResult, Scorer, TaskInstance
from chimera.evolution.learned_skill import LearnedSkill

if TYPE_CHECKING:
    from collections.abc import Sequence

    from chimera.ecosystem.trajectory import Trajectory
    from chimera.evolution.gepa import SupportsExecute, SupportsReflect


def _reference_scorer(reference: str) -> Scorer:
    """Reward reproducing the VERIFIED answer: recall of the reference's tokens in the output.

    A crude but honest "did it produce the answer that passed verification" signal — deliberately not
    the acceptance authority (the transfer/holdout gate is). Empty reference ⇒ any output scores 1.0.
    """
    ref_tokens = {t for t in reference.lower().split() if t}

    def score(output: str) -> float:
        if not ref_tokens:
            return 1.0
        out_tokens = {t for t in output.lower().split() if t}
        return len(ref_tokens & out_tokens) / len(ref_tokens)

    return score


def instances_from_trajectories(
    trajectories: Sequence[Trajectory],
    *,
    min_reward: float = 1.0,
    input_key: str = "task",
) -> list[TaskInstance]:
    """Build GEPA task instances from VERIFIED trajectories only.

    Keeps ``outcome == "success"`` trajectories that were productive (``diff_productive`` is not
    False — a hollow success never becomes training signal) and cleared ``min_reward``. Each instance
    fills the template's ``{input_key}`` with the run's prompt and scores against its verified answer.
    """
    instances: list[TaskInstance] = []
    for traj in trajectories:
        if traj.outcome != "success" or traj.reward < min_reward:
            continue
        if traj.diff_productive is False:  # a hollow success is not evidence to refine on
            continue
        instances.append(
            TaskInstance(input={input_key: traj.prompt}, scorer=_reference_scorer(traj.response))
        )
    return instances


@dataclass
class RefineOutcome:
    """The result of a transfer-gated refine: the (maybe-improved) skill + the evidence + verdict."""

    skill: LearnedSkill  # the improved skill when promoted, else the original unchanged
    result: GEPAResult
    decision: TransferDecision
    promoted: bool


def _bump(version: str) -> str:
    parts = version.split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except (ValueError, IndexError):
        return version
    return ".".join(parts)


def _with_template(skill: LearnedSkill, template: str) -> LearnedSkill:
    return LearnedSkill(
        name=skill.name,
        description=skill.description,
        prompt_template=template,
        version=_bump(skill.version),
        trigger=skill.trigger,
        do=skill.do,
        avoid=skill.avoid,
        check=skill.check,
        risk=skill.risk,
        triggers=list(skill.triggers),
        kind=skill.kind,
        status=skill.status,
        provenance=skill.provenance,
    )


def refine_skill(
    skill: LearnedSkill,
    tuned: Sequence[TaskInstance],
    *,
    executor: SupportsExecute,
    reflector: SupportsReflect,
    holdout: Sequence[TaskInstance] | None = None,
    threshold: float = 0.5,
    budget: int = 20,
    seed: int = 0,
    holdout_regression_tol: float = 0.0,
) -> RefineOutcome:
    """GEPA-refine ``skill`` on ``tuned`` and adopt the winner ONLY if it passes the transfer gate.

    Promotion requires: the refined template helps the tuned slice AND (when a ``holdout`` slice is
    given) does not regress it. Without a holdout the decision is a **dry-run** — reported, never
    persisted — because transfer was not measured. ``threshold`` binarizes GEPA's per-instance score
    into the pass/fail the paired gate consumes.
    """
    if not tuned:
        raise ValueError("refine_skill needs at least one tuned task instance")
    result = GEPAOptimizer(executor, reflector).optimize(
        skill.prompt_template, list(tuned), budget=budget, seed=seed
    )
    best_idx = max(range(len(result.candidates)), key=lambda i: result.candidates[i].mean)
    seed_scores = result.candidates[0].scores
    best_scores = result.candidates[best_idx].scores
    tuned_baseline = [s >= threshold for s in seed_scores]
    tuned_treatment = [s >= threshold for s in best_scores]

    holdout_baseline: list[bool] | None = None
    holdout_treatment: list[bool] | None = None
    if holdout:
        holdout_baseline = [_passes(executor, skill.prompt_template, inst, threshold) for inst in holdout]
        holdout_treatment = [_passes(executor, result.best_template, inst, threshold) for inst in holdout]

    decision = transfer_gated_promotion(
        tuned_baseline=tuned_baseline,
        tuned_treatment=tuned_treatment,
        holdout_baseline=holdout_baseline,
        holdout_treatment=holdout_treatment,
        holdout_regression_tol=holdout_regression_tol,
    )
    # Adopt ONLY on a measured, non-regressing generalization — a "promote" without a holdout
    # (transfer_measured=False) stays a dry-run recommendation, never persisted.
    promoted = decision.promote and decision.transfer_measured and result.improved
    skill_out = _with_template(skill, result.best_template) if promoted else skill
    return RefineOutcome(skill=skill_out, result=result, decision=decision, promoted=promoted)


def _passes(executor: SupportsExecute, template: str, instance: TaskInstance, threshold: float) -> bool:
    out = executor.run(template, instance.input)
    return instance.scorer(out) >= threshold
