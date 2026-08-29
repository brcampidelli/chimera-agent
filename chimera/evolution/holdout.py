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

1. **The minting task is excluded, and the exclusion is counted.** A gate that scores a skill on its
   own task is measuring memorisation. The count is reported so "nothing was excluded" and "the
   exclusion happened" are different observations rather than the same silence.
2. **Too few remaining cases produce ``measured=False``, never a pass.** ``chimera/eval/transfer.py``
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
    ) -> None:
        #: Two is the floor rather than one because a single case makes the gate a coin flip whose
        #: only outcomes are 0% and 100%, and both would clear or fail any threshold by construction.
        self.cases = list(cases)
        self.min_pass = min_pass
        self.min_cases = max(1, min_cases)

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
        errors: list[str] = []
        for case in candidates:
            try:
                result = skill.execute(**case.inputs)  # type: ignore[attr-defined]
                if getattr(result, "ok", False) and case.check(getattr(result, "output", "")):
                    passed += 1
            except Exception as exc:  # noqa: BLE001 — an error is a failure, never a skip
                errors.append(f"{case.task_id}: {type(exc).__name__}: {exc}")
        verdict = HoldoutVerdict(
            measured=True,
            passed=passed,
            total=len(candidates),
            excluded=excluded,
            errors=errors,
        )
        _log.debug("holdout for a candidate skill: %s", verdict.summary())
        return verdict

    def accepts(self, verdict: HoldoutVerdict) -> bool:
        """Whether a verdict clears the bar. An unmeasured verdict does NOT clear it.

        The caller decides what to do with an unmeasured one — store it and say so, or hold it back
        — but it may not read as a pass here, because this method is what a gate is.
        """
        return verdict.measured and verdict.rate >= self.min_pass
