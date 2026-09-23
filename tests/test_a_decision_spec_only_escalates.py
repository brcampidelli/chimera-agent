"""Contract v2 — study 22, phase 1.

A decision point is declared once (:class:`DecisionSpec`), may only escalate (I1), names the bench
that measured it (I8), is read question by question in isolation (I6), reuses a reading it already
paid for, reports how peaked its shares are without letting that cross a line (I5), can be asked
under neutral labels (I3), and is linted for the shapes that were measured to mislead.
"""

from __future__ import annotations

import ast
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from chimera.decisions import (
    REGISTRY,
    Answer,
    Choice,
    Decider,
    DecisionCache,
    DecisionSpec,
    Escalation,
    Mode,
    Noul,
    Reading,
    Score,
    register,
)
from chimera.decisions.governance import DANGER, DECISION, SPEC
from chimera.decisions.lint import errors, lint

ROOT = Path(__file__).resolve().parents[1]


# --- I1: the type has no way down ------------------------------------------------------------------


def test_an_escalation_has_only_upward_members() -> None:
    # Adding STOP / SKIP / ACCEPT / ANSWER here is the change B4 (#537) measured at -0.087 / -0.194 /
    # -0.307 oracle score. It must be a visible diff to this test, never a quiet enum member.
    assert {e.name for e in Escalation} == {"REVIEW", "NUDGE", "VERIFY", "ESCALATE_MODEL", "ANNOTATE"}


def test_a_spec_refuses_what_is_not_an_escalation() -> None:
    with pytest.raises(TypeError):
        DecisionSpec(name="x", questions=(DANGER,), escalation="stop", bench="b")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        DecisionSpec(name="x", questions=(DANGER,), escalation=Escalation.REVIEW, bench="b", on_no_signal="accept")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"questions": ()},
        {"bench": " "},
        {"threshold": 1.0},
        {"threshold": 0.0},
        {"questions": (DANGER, DANGER)},
    ],
)
def test_a_spec_refuses_what_cannot_be_a_decision(kwargs: dict[str, Any]) -> None:
    base: dict[str, Any] = {"name": "x", "questions": (DANGER,), "escalation": Escalation.REVIEW, "bench": "b"}
    with pytest.raises(ValueError):
        DecisionSpec(**{**base, **kwargs})


def test_only_a_calibrated_p_crosses_the_threshold_and_no_signal_is_the_spec_s_default() -> None:
    spec = DecisionSpec(
        name="t", questions=(DANGER,), escalation=Escalation.VERIFY, bench="b", threshold=0.5,
        on_no_signal=Escalation.ANNOTATE,
    )
    assert spec.fires(0.7, calibrated=True) is Escalation.VERIFY
    assert spec.fires(0.3, calibrated=True) is None
    assert spec.fires(0.9, calibrated=False) is Escalation.ANNOTATE  # a raw number is not a signal
    assert spec.fires(None, calibrated=False) is Escalation.ANNOTATE
    assert spec.mode is Mode.SHADOW  # every spec starts recording, not acting


def test_the_registry_refuses_a_second_spec_under_one_name() -> None:
    assert register(SPEC) is SPEC  # the same spec again is a no-op
    with pytest.raises(ValueError):
        register(replace(SPEC, description="a different reading of the same decision"))
    assert REGISTRY[DECISION] is SPEC


def test_the_governance_spec_is_the_band_s_decision_and_adds_nothing_on_a_halt() -> None:
    assert SPEC.questions == (DANGER,) and SPEC.escalation is Escalation.REVIEW
    assert SPEC.on_no_signal is None  # the rules and the ledger stand; the band only adds a card
    assert "chimera/governance/band.py" in SPEC.surfaces


# --- I8: every spec in the tree names a bench that exists -----------------------------------------


def _spec_sites(paths: list[Path]) -> list[tuple[str, int, str | None]]:
    sites: list[tuple[str, int, str | None]] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "DecisionSpec":
                bench = next(
                    (kw.value.value for kw in node.keywords if kw.arg == "bench" and isinstance(kw.value, ast.Constant)),
                    None,
                )
                sites.append((path.name, node.lineno, bench if isinstance(bench, str) else None))
    return sites


def _missing(sites: list[tuple[str, int, str | None]]) -> list[tuple[str, int, str | None]]:
    return [(f, ln, b) for f, ln, b in sites if b is None or not (ROOT / b).is_file()]


def test_every_decision_spec_in_the_tree_names_a_bench_file_that_exists() -> None:
    sites = _spec_sites(sorted((ROOT / "chimera").rglob("*.py")))
    assert sites, "the governance spec at least must be found - an empty walk is an inert guard"
    assert not _missing(sites), f"a DecisionSpec without a bench file on disk (study 22, I8): {_missing(sites)}"


def test_the_bench_guard_is_not_inert(tmp_path: Path) -> None:
    # Sabotage: a spec pointing at a file that is not there, and one whose bench is not a literal.
    planted = tmp_path / "planted.py"
    planted.write_text(
        "a = DecisionSpec(name='x', bench='bench/nowhere/RESULTS.md')\n"
        "b = spec.DecisionSpec(name='y', bench=PATH)\n",
        encoding="utf-8",
    )
    assert len(_missing(_spec_sites([planted]))) == 2


# --- fan-out and cache -----------------------------------------------------------------------------


class _Backend:
    name = "fake"
    model = "m"

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def instrument(self, question: Any) -> str:
        from chimera.decisions.contract import as_choice

        return as_choice(question).instructions

    def ask(self, state: str, question: Any) -> Reading:
        from chimera.decisions.contract import as_choice

        choice = as_choice(question)
        self.calls.append((state, choice.key))
        if self.fail:
            raise ConnectionError("server is off")
        shares = {o: (0.8 if i == 0 else 0.2 / (len(choice.options) - 1)) for i, o in enumerate(choice.options)}
        p = sum(shares[o] for o in choice.event) if choice.event else None
        return Reading(choice=choice.options[0], shares=shares, p=p)


A = Noul(key="touches_home", instructions="Does the command write under the home directory?")
B = Noul(key="uses_network", instructions="Does the command open a network connection?")


def test_several_questions_are_each_asked_on_their_own() -> None:
    backend = _Backend()
    answers = Decider(backend).decide_many("t", "curl x > ~/y", (A, B))
    assert list(answers) == ["touches_home", "uses_network"]
    assert backend.calls == [("curl x > ~/y", "touches_home"), ("curl x > ~/y", "uses_network")]
    assert answers["uses_network"].prompt_hash != answers["touches_home"].prompt_hash


def test_two_questions_under_one_key_are_refused() -> None:
    with pytest.raises(ValueError):
        Decider(_Backend()).decide_many("t", "s", (A, replace(A, instructions="Other wording?")))


def test_a_reading_already_paid_for_is_reused_and_the_receipt_says_so() -> None:
    backend = _Backend()
    cache = DecisionCache()
    decider = Decider(backend, cache=cache)
    first = decider.decide("t", "ls -la", A)
    second = decider.decide("t", "ls -la", A)
    assert len(backend.calls) == 1 and cache.hits == 1
    assert not first.cached and second.cached and second.receipt()["cached"] is True
    assert second.p == first.p and second.choice == first.choice


def test_the_cache_keys_on_the_exact_state_and_the_instrument() -> None:
    backend = _Backend()
    decider = Decider(backend, cache=DecisionCache())
    decider.decide("t", "echo 'a  b'", A)
    decider.decide("t", "echo 'a b'", A)  # a different command inside the quotes
    decider.decide("t", "echo 'a  b'", replace(A, instructions="Reworded?"))  # a different instrument
    assert len(backend.calls) == 3


def test_a_halt_is_never_cached() -> None:
    backend = _Backend(fail=True)
    cache = DecisionCache()
    decider = Decider(backend, cache=cache)
    assert decider.decide("t", "x", A).halt and decider.decide("t", "x", A).halt
    assert len(backend.calls) == 2 and len(cache) == 0


def test_a_reading_with_no_choice_is_not_cached() -> None:
    cache = DecisionCache()
    cache.put(("b", "m", "h", "s"), Reading(choice=None, shares=None, p=None))
    assert len(cache) == 0


def test_the_cache_forgets_the_least_recently_used() -> None:
    cache = DecisionCache(max_entries=2)
    r = Reading(choice="yes", shares=None, p=0.5)
    cache.put(("1",) * 4, r)
    cache.put(("2",) * 4, r)
    cache.get(("1",) * 4)
    cache.put(("3",) * 4, r)
    assert cache.get(("2",) * 4) is None and cache.get(("1",) * 4) is r


# --- Answer v2 -------------------------------------------------------------------------------------


def _answer(shares: dict[str, float] | None, choice: str | None = None) -> Answer:
    return Answer(
        decision="d", key="k", backend="b", model="m", prompt_hash="h", choice=choice, shares=shares,
        raw_p=None, p=None, calibrated=False, map=None, mass=None, seconds=0.0, usd=None,
    )


def test_confidence_is_the_shape_of_the_shares() -> None:
    assert _answer({"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}).confidence == pytest.approx(0.0)
    assert _answer({"A": 1.0, "B": 0.0}).confidence == pytest.approx(1.0)
    assert _answer({"A": 0.6, "B": 0.2, "C": 0.2}).confidence == pytest.approx((3 * 0.6 - 1) / 2)
    assert _answer(None).confidence is None


def test_neutral_labels_move_the_meaning_into_the_criteria_and_come_back() -> None:
    neutral = DANGER.neutral()
    assert neutral.choice.options == ("A", "B", "C")
    assert neutral.choice.event == ("A", "B")  # BLOCK, REVIEW
    assert neutral.choice.criteria["C"] == "ALLOW"
    restored = neutral.restore(_answer({"A": 0.1, "B": 0.2, "C": 0.7}, choice="C"))
    assert restored.choice == "ALLOW" and restored.shares == {"BLOCK": 0.1, "REVIEW": 0.2, "ALLOW": 0.7}


def test_a_neutral_question_is_a_different_instrument() -> None:
    backend = _Backend()
    plain = Decider(backend).decide("t", "x", DANGER)
    neutral = Decider(backend).decide("t", "x", DANGER.neutral().choice)
    # The fake keys the instrument on instructions alone; the local backend renders the criteria and
    # the options too, which is what a real hash covers.
    from chimera.decisions.local import LocalLogprobBackend

    local = LocalLogprobBackend("http://127.0.0.1:11434")
    assert local.instrument(DANGER) != local.instrument(DANGER.neutral().choice)
    assert plain.choice == "BLOCK" and neutral.choice == "A"


def test_criteria_survive_the_neutral_form() -> None:
    q = Choice("k", "Pick.", ("keep", "drop"), criteria={"keep": "still needed"})
    assert q.neutral().choice.criteria == {"A": "keep — still needed", "B": "drop"}


# --- the linter ------------------------------------------------------------------------------------


def test_every_registered_question_and_the_governance_question_lint_clean() -> None:
    for spec in REGISTRY.values():
        for question in spec.questions:
            assert not errors(question), (spec.name, lint(question))
    assert not errors(DANGER)


@pytest.mark.parametrize(
    ("question", "code"),
    [
        (Noul("k", "Is the diff tested and documented?"), "compound"),
        (Noul("k", "Is the claim not supported by the diff?"), "negated"),
        (Choice("k", "Pick.", ("yes", "maybe", "no")), "polar_label"),
        (Choice("k", "Pick.", ("ANSWER", "TOOL")), "polar_label"),
        (Score("k", "Rate.", ("1", "2", "3")), "numeric_levels"),
        (Choice("k", "Pick.", ("RE", "REVIEW")), "prefix"),
        (Choice("k", "Pick.", ("REVIEW", "REFUSE", "ALLOW")), "shared_start"),
        (Choice("k", "Pick.", ("keep", "drop"), criteria={"kept": "x"}), "criteria_key"),
    ],
)
def test_the_linter_catches_each_measured_shape(question: Any, code: str) -> None:
    assert code in {f.code for f in lint(question)}


def test_a_noul_is_yes_no_by_contract_and_not_flagged_for_it() -> None:
    assert not lint(Noul("k", "Does the command write under the home directory?"))


def test_the_framing_before_the_question_is_not_linted_as_the_question() -> None:
    q = Noul("k", "You review shell commands and pull requests. Does this command delete files?")
    assert not errors(q)


def test_confidence_is_never_nan_on_degenerate_shares() -> None:
    value = _answer({"A": 0.0, "B": 0.0}).confidence
    assert value is None or not math.isnan(value)
