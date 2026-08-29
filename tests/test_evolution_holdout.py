"""The black-box holdout: does a minted skill work on a task it has never seen?

Auto-evolution had two gates before this and neither asked that question. The **smoke test** runs the
candidate on ``test_input`` — which is the minting task substituted into every placeholder — and
checks ``bool(out.strip())``, so any live model passes it. The **transferability gate** loops over
nine models with that same input and that same check, varying the MODEL while holding the TASK
fixed; ``min_transfer=0.5`` therefore reads "at least five of nine models were reachable".

Everything here is deterministic and free: the skill is a fake with a scripted answer table, so what
is under test is the gate's arithmetic and its refusals, never a model.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.evolution.auto_evolve import AutoSkillEvolver, _task_id
from chimera.evolution.holdout import HoldoutCase, HoldoutGate


class _Result:
    def __init__(self, ok: bool, output: str) -> None:
        self.ok, self.output = ok, output


class _Skill:
    """A skill whose answer depends only on the input, so a case can be made to pass or fail."""

    name = "fake_skill"

    def __init__(self, answers: dict[str, str], *, raises: set[str] | None = None) -> None:
        self.answers = answers
        self.raises = raises or set()
        self.seen: list[str] = []

    def execute(self, **inputs: str) -> _Result:
        key = inputs.get("q", "")
        self.seen.append(key)
        if key in self.raises:
            raise RuntimeError("provider is down")
        return _Result(True, self.answers.get(key, ""))


def _case(task_id: str, question: str, expected: str) -> HoldoutCase:
    return HoldoutCase(task_id=task_id, inputs={"q": question}, check=lambda out: out == expected)


# --------------------------------------------------------------------------- the exclusion


def test_the_minting_task_is_never_scored() -> None:
    """A gate that scores a skill on its own task is measuring memorisation and calling it transfer.

    The count of exclusions is on the verdict for a reason: "the minting task was not in the set"
    and "the minting task was excluded" would otherwise be the same silence.
    """
    skill = _Skill({"own": "yes", "a": "A", "b": "B"})
    gate = HoldoutGate([_case("T1", "own", "yes"), _case("T2", "a", "A"), _case("T3", "b", "B")])

    verdict = gate.evaluate(skill, minted_from="T1")

    assert "own" not in skill.seen, "the skill was scored on the task it was minted from"
    assert verdict.excluded == 1
    assert verdict.total == 2 and verdict.passed == 2


def test_too_few_cases_left_is_unmeasured_and_not_a_pass() -> None:
    """`chimera/eval/transfer.py` set this rule for the promotion path: an honest "promoted without
    a transfer check", never a silent pass. A gate that waves through what it could not measure is
    worse than no gate, because it reports a verdict."""
    gate = HoldoutGate([_case("T1", "own", "yes"), _case("T2", "a", "A")], min_cases=2)

    verdict = gate.evaluate(_Skill({"a": "A"}), minted_from="T1")

    assert verdict.measured is False
    assert gate.accepts(verdict) is False, "an unmeasured verdict cleared the gate"
    assert "below min_cases" in verdict.reason


def test_a_single_case_is_not_enough_by_default() -> None:
    """One case makes the gate a coin flip whose only outcomes are 0% and 100%, and both would clear
    or fail any threshold by construction."""
    gate = HoldoutGate([_case("T2", "a", "A")])

    assert gate.evaluate(_Skill({"a": "A"}), minted_from="T1").measured is False


# --------------------------------------------------------------------------- the scoring


def test_a_wrong_answer_fails_where_the_shipped_smoke_test_would_pass() -> None:
    """The gap this module exists for. The existing gate checks `bool(out.strip())`, so a confident
    wrong answer clears it; here the case's own check decides."""
    skill = _Skill({"a": "WRONG BUT NON-EMPTY", "b": "ALSO WRONG"})
    gate = HoldoutGate([_case("T2", "a", "A"), _case("T3", "b", "B")])

    verdict = gate.evaluate(skill, minted_from="T1")

    assert verdict.passed == 0 and verdict.total == 2
    assert gate.accepts(verdict) is False
    assert all(answer.strip() for answer in skill.answers.values()), (
        "the fixture must produce NON-EMPTY wrong answers, or it is not testing the gap"
    )


def test_an_execution_error_counts_as_a_failure_and_is_not_skipped() -> None:
    """Skipping it would let one reachable model produce a perfect score over the single case that
    answered — the reasoning `transfer_counts` already gives for keeping failed calls in its
    denominator. The error strings are kept so "wrong" and "the provider was down" stay separable."""
    skill = _Skill({"a": "A", "b": "B"}, raises={"b"})
    gate = HoldoutGate([_case("T2", "a", "A"), _case("T3", "b", "B")])

    verdict = gate.evaluate(skill, minted_from="T1")

    assert verdict.total == 2, "the failed case left the denominator"
    assert verdict.passed == 1
    assert len(verdict.errors) == 1 and "provider is down" in verdict.errors[0]


def test_the_threshold_is_the_rate_over_the_cases_that_ran() -> None:
    gate = HoldoutGate(
        [_case("T2", "a", "A"), _case("T3", "b", "B"), _case("T4", "c", "C")], min_pass=0.6
    )

    verdict = gate.evaluate(_Skill({"a": "A", "b": "B", "c": "nope"}), minted_from="T1")

    assert verdict.rate == pytest.approx(2 / 3)
    assert gate.accepts(verdict) is True


# --------------------------------------------------------------------------- the wiring


class _Store(dict):
    def add(self, skill: Any) -> None:
        self[skill.name] = skill


class _Evolver:
    """Proposes one fixed candidate and passes every smoke test — like the shipped one does."""

    def __init__(self, candidate: Any) -> None:
        self.candidate = candidate

    def propose(self, task: str, solution: str) -> Any:
        return self.candidate

    def test_skill(self, skill: Any, test_input: dict, check: Any) -> bool:
        return True


class _Candidate(_Skill):
    def __init__(self, answers: dict[str, str]) -> None:
        super().__init__(answers)
        self.prompt_template = "{q}"
        self.provenance = "clean"
        self.status = "active"

    def to_dict(self) -> dict:
        return {"name": self.name}


def _auto(candidate: Any, holdout: HoldoutGate | None, store: _Store) -> AutoSkillEvolver:
    return AutoSkillEvolver(_Evolver(candidate), store, min_recurrences=1, holdout=holdout)


def test_without_a_gate_nothing_changes() -> None:
    """Opt-in means opt-in. A behaviour change that arrives without being asked for is the thing
    this project refuses on principle, and it would silently alter what a running agent learns."""
    store = _Store()

    _auto(_Candidate({"a": "WRONG"}), None, store).maybe_evolve("t", "s", prior_successes=2)

    assert "fake_skill" in store


def test_a_candidate_that_fails_the_holdout_is_not_stored() -> None:
    store = _Store()
    gate = HoldoutGate([_case("T2", "a", "A"), _case("T3", "b", "B")])

    kept = _auto(_Candidate({"a": "WRONG", "b": "ALSO WRONG"}), gate, store).maybe_evolve(
        "t", "s", prior_successes=2
    )

    assert kept is None and "fake_skill" not in store


def test_a_candidate_that_clears_the_holdout_is_stored() -> None:
    store = _Store()
    gate = HoldoutGate([_case("T2", "a", "A"), _case("T3", "b", "B")])

    kept = _auto(_Candidate({"a": "A", "b": "B"}), gate, store).maybe_evolve(
        "t", "s", prior_successes=2
    )

    assert kept is not None and "fake_skill" in store


def test_an_unmeasured_holdout_stores_the_skill_rather_than_blocking_it() -> None:
    """Unmeasured is not a failure. It must not reject — but the audit row below is what keeps
    "checked and cleared" and "never checked" from becoming the same fact."""
    store = _Store()
    gate = HoldoutGate([_case("T2", "a", "A")], min_cases=2)

    kept = _auto(_Candidate({"a": "A"}), gate, store).maybe_evolve("t", "s", prior_successes=2)

    assert kept is not None and "fake_skill" in store


def test_the_rejected_ones_reach_the_audit_log() -> None:
    """The rejected half is the half worth keeping. A gate whose rejection rate is zero supports
    nothing built on top of it, and the surviving skills cannot show that."""

    class _Audit:
        def __init__(self) -> None:
            self.rows: list[tuple[str, dict]] = []

        def record(self, kind: str, payload: dict) -> None:
            self.rows.append((kind, payload))

    audit = _Audit()
    gate = HoldoutGate([_case("T2", "a", "A"), _case("T3", "b", "B")])
    evolver = AutoSkillEvolver(
        _Evolver(_Candidate({"a": "WRONG", "b": "WRONG"})),
        _Store(),
        min_recurrences=1,
        holdout=gate,
        audit=audit,  # type: ignore[arg-type]
    )

    evolver.maybe_evolve("t", "s", prior_successes=2)

    rows = [payload for kind, payload in audit.rows if kind == "skill_holdout"]
    assert rows and rows[0]["kept"] is False
    assert rows[0]["passed"] == 0 and rows[0]["total"] == 2


def test_the_task_identity_is_hashed_and_not_a_prefix_slug() -> None:
    """A slug of the opening words collides across tasks that begin the same way, and a collision
    here does not fail loudly — it silently drops a case that should have been scored, shrinking the
    holdout toward "unmeasured" with nothing saying so."""
    a = "fix the failing test in the parser module"
    b = "fix the failing test in the renderer module"

    assert _task_id(a) != _task_id(b)
    assert _task_id(a) == _task_id("fix   the failing\ttest in the parser module"), (
        "whitespace should not change a task's identity"
    )
