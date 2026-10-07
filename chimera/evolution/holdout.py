"""Black-box holdout for auto-evolution — does a minted skill work on a task it has never seen?

Auto-evolution announces two gates before it stores a skill (:mod:`chimera.evolution.auto_evolve`).
Read against what they do, neither asks the question this module exists for:

- the **executable smoke test** runs the candidate on ``test_input`` and checks
  ``lambda out: bool(out.strip())`` — that the model said *something*. Any live model passes it.
- the **transferability gate** (:meth:`CollectiveSkillEvolver.transfer_counts`) loops over nine
  models with **the same ``test_input``** and the same non-empty check. It varies the MODEL and
  holds the TASK fixed, so ``min_transfer=0.5`` reads "at least five of nine models were reachable
  and answered". That is availability, and it is worth measuring, but it is not transfer.

And ``test_input`` is the task the skill was minted from, substituted into every placeholder. So
today a skill is kept on the strength of running once, on its own task, on models that were up.

This module adds the missing axis: score the candidate on tasks it was **not** minted from, with a
check that can actually fail, and refuse to store one that does not hold up. The cases are injected
rather than discovered, so the gate is unit-testable with fakes — the same shape
:class:`~chimera.fusion.verifier_select.VerifierSelector` uses.

Three rules are structural rather than advisory, because each one is a way this gate could look like
it worked while measuring nothing:

1. **The canary must have evidence and pass.** A missing canary refuses adoption, as does any
   failing canary case; aggregate success cannot hide a canary regression.
2. **The minting task is excluded, and the exclusion is counted.** A gate that scores a skill on its
   own task is measuring memorisation. The count is reported so "nothing was excluded" and "the
   exclusion happened" are different observations rather than the same silence.
3. **Too few remaining cases produce ``measured=False``, never a pass.** ``chimera/eval/transfer.py``
   already set this precedent for the promotion path: *"an honest 'promoted without a transfer
   check', never a silent pass"*. A gate that waves through whatever it could not measure is worse
   than no gate, because it reports a verdict.
3. **The rejection rate is part of the verdict.** A verifier that accepts everything supports no
   loop built on it — the lesson this project paid for when it measured a runtime verifier accepting
   95% of what it saw and discovered the retry loop around it almost never fired.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from chimera.telemetry import get_logger

_log = get_logger("evolution.holdout")


@dataclass(frozen=True)
class HoldoutCase:
    """One task the candidate may be scored on, plus the check that says the output was right.

    ``task_id`` is what makes the holdout black-box: it is compared against the id of the task the
    candidate was minted from, and a match is excluded. It must therefore identify the TASK and not
    the run — two runs of the same task share an id, or the exclusion does not exclude.
    """

    task_id: str
    inputs: dict[str, str]
    check: Callable[[str], bool]
    canary: bool = False
    protected_slice: bool = False


@dataclass(frozen=True)
class HoldoutVerdict:
    """What the holdout found, including whether it was in a position to find anything.

    ``measured`` False means the gate could not run — not that the skill failed and not that it
    passed. A caller that collapses those three into a boolean has re-created the silence this
    module exists to remove.
    """

    measured: bool
    passed: int = 0
    total: int = 0
    excluded: int = 0
    reason: str = ""
    errors: list[str] = field(default_factory=list)
    canary_passed: int = 0
    canary_total: int = 0
    per_task: dict[str, float] = field(default_factory=dict)
    protected_passed: int = 0
    protected_total: int = 0

    @property
    def rate(self) -> float:
        """Share of holdout cases the skill got right, or 0.0 when nothing was measured."""
        return self.passed / self.total if self.total else 0.0

    def summary(self) -> str:
        if not self.measured:
            return f"holdout not measured ({self.reason})"
        return (
            f"holdout {self.passed}/{self.total} = {self.rate:.0%} "
            f"(excluded {self.excluded} case(s) from the minting task)"
        )


class HoldoutGate:
    """Scores a candidate skill on tasks it was not minted from."""

    def __init__(
        self,
        cases: Sequence[HoldoutCase],
        *,
        min_pass: float = 0.5,
        min_cases: int = 2,
        canary_min_pass: float = 1.0,
        protected_slice_min_pass: float = 0.0,
        per_task_floor: float = 0.0,
    ) -> None:
        #: Two is the floor rather than one because a single case makes the gate a coin flip whose
        #: only outcomes are 0% and 100%, and both would clear or fail any threshold by construction.
        self.cases = list(cases)
        self.min_pass = min_pass
        self.min_cases = max(1, min_cases)
        # Missing canary evidence refuses, and every canary must pass. The parameter exists so a
        # caller can state the bar, never lower it: anything below 1.0 is raised back to 1.0, since
        # a canary allowed to fail is an aggregate case under another name.
        self.canary_min_pass = max(1.0, canary_min_pass)
        # Historical adoption records are absent, so retain both unvalidated screens OFF.
        self.protected_slice_min_pass = protected_slice_min_pass
        self.per_task_floor = per_task_floor

    def evaluate(self, skill: object, *, minted_from: str) -> HoldoutVerdict:
        """Run ``skill`` on every case that is not the minting task.

        A case whose execution raises counts as a FAILURE rather than being skipped. Skipping it
        would let an unreachable model produce a perfect score over the one case that answered —
        the same reasoning ``transfer_counts`` gives for keeping failed calls in its denominator.
        The error strings are carried on the verdict so "the skill is wrong" and "the provider was
        down" can still be told apart afterwards, which the pass count alone cannot do.
        """
        candidates = [case for case in self.cases if case.task_id != minted_from]
        excluded = len(self.cases) - len(candidates)
        if len(candidates) < self.min_cases:
            return HoldoutVerdict(
                measured=False,
                excluded=excluded,
                reason=(
                    f"{len(candidates)} case(s) left after excluding the minting task, "
                    f"below min_cases={self.min_cases}"
                ),
            )

        passed = 0
        canary_passed = 0
        canary_total = 0
        per_task_passed: dict[str, int] = {}
        per_task_total: dict[str, int] = {}
        protected_passed = 0
        protected_total = 0
        errors: list[str] = []
        for case in candidates:
            try:
                result = skill.execute(**case.inputs)  # type: ignore[attr-defined]
                case_passed = bool(
                    getattr(result, "ok", False) and case.check(getattr(result, "output", ""))
                )
                per_task_passed[case.task_id] = per_task_passed.get(case.task_id, 0) + int(case_passed)
                per_task_total[case.task_id] = per_task_total.get(case.task_id, 0) + 1
                passed += int(case_passed)
                if case.protected_slice:
                    protected_total += 1
                    protected_passed += int(case_passed)
                if case.canary:
                    canary_total += 1
                    canary_passed += int(case_passed)
            except Exception as exc:  # noqa: BLE001 — an error is a failure, never a skip
                per_task_passed[case.task_id] = per_task_passed.get(case.task_id, 0)
                per_task_total[case.task_id] = per_task_total.get(case.task_id, 0) + 1
                if case.protected_slice:
                    protected_total += 1
                if case.canary:
                    canary_total += 1
                errors.append(f"{case.task_id}: {type(exc).__name__}: {exc}")
        verdict = HoldoutVerdict(
            measured=True,
            passed=passed,
            total=len(candidates),
            excluded=excluded,
            errors=errors,
            canary_passed=canary_passed,
            canary_total=canary_total,
            per_task={
                task_id: per_task_passed.get(task_id, 0) / count
                for task_id, count in per_task_total.items()
            },
            protected_passed=protected_passed,
            protected_total=protected_total,
        )
        _log.debug("holdout for a candidate skill: %s", verdict.summary())
        return verdict

    def accepts(self, verdict: HoldoutVerdict) -> bool:
        """Whether a verdict clears the bar. An unmeasured verdict does NOT clear it.

        The caller decides what to do with an unmeasured one — store it and say so, or hold it back
        — but it may not read as a pass here, because this method is what a gate is.
        """
        if not verdict.measured or verdict.rate < self.min_pass:
            return False
        if verdict.canary_total == 0:
            return False
        if verdict.canary_passed / verdict.canary_total < self.canary_min_pass:
            return False
        # The protected-slice and task-floor settings are explicit but remain OFF (0) pending a
        # reanalysis with historical adoption records; enabled values can only add refusals.
        if self.protected_slice_min_pass > 0.0 and verdict.protected_total == 0:
            return False
        if verdict.protected_total and self.protected_slice_min_pass > 0.0 and (
            verdict.protected_passed / verdict.protected_total < self.protected_slice_min_pass
        ):
            return False
        return not (
            self.per_task_floor > 0.0
            and any(score < self.per_task_floor for score in verdict.per_task.values())
        )


def active_set_score(predictions: dict[str, dict[str, bool]]) -> float:
    """Score each card's precomputed predictions and the active set, without model calls.

    ``predictions`` maps card name to task-id outcomes. The active set passes a task when at
    least one active card passes it; empty or incomplete evidence scores conservatively as 0.
    """
    task_ids = {task_id for outcomes in predictions.values() for task_id in outcomes}
    if not task_ids or not predictions:
        return 0.0
    outcomes = list(predictions.values())
    return sum(
        any(card_outcomes.get(task_id, False) for card_outcomes in outcomes)
        for task_id in task_ids
    ) / len(task_ids)


def prediction_per_card(predictions: dict[str, dict[str, bool]]) -> dict[str, float]:
    """Return a separate, deterministic score for every card prediction."""
    return {
        name: sum(outcomes.values()) / len(outcomes) if outcomes else 0.0
        for name, outcomes in predictions.items()
    }
