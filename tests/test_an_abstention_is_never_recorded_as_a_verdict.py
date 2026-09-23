"""An abstention is never recorded as a verdict — study 22, phase 0.

Three quality judges in Chimera fail OPEN by design: an outage of the judge must not block the run
(the envelope spot check, the strong verifier, the requirement checklist all say so in their own
docstrings, and that choice stands). What they must not do is write the outage down as a verdict:

* the spot check appended ``"spot"`` to ``checks_run`` — the field whose contract is "which gates
  actually EXECUTED", read by the orchestration card — BEFORE calling the model, so an outage or an
  unreadable reply reached the screen as "checked by spot check";
* the strong verifier returned a perfect ``1.0`` for a reply with no number and for a failed call;
* the checklist returned ``[]`` — "every requirement met" — when it could not grade at all.

And one number worse than any of them: the local decision backend summed all-zero shares into
``p = 0.0`` when the model put no probability mass on the labels, which the REVIEW band reads as a
confident "not dangerous". No signal is not a signal of zero.

Each test below fails on the code before this change (sabotage-checked).
"""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.core.checklist import Requirement, RequirementChecklist
from chimera.core.strong_verify import StrongVerifier
from chimera.decisions.contract import Choice
from chimera.decisions.governance import DANGER
from chimera.decisions.local import LocalLogprobBackend
from chimera.governance.band import Band, build_band
from chimera.orchestration.artifacts import ArtifactStore, build_envelope
from chimera.orchestration.envelope_verify import EnvelopeVerifier
from chimera.orchestration.spec import TaskSpec
from chimera.providers import CompletionResult

# --- the envelope spot check -----------------------------------------------------------------------


class _Replies:
    def __init__(self, reply: str | None) -> None:
        self.reply = reply

    def complete(self, messages: Any, **kwargs: Any) -> CompletionResult:
        if self.reply is None:
            raise RuntimeError("the auditor's provider is down")
        return CompletionResult(content=self.reply, model="auditor")


def _spec() -> TaskSpec:
    return TaskSpec(task_id="t", objective="Summarise the report and flag anything critical.")


def _spot(tmp_path: Path, reply: str | None, **kw: Any) -> Any:
    store = ArtifactStore(tmp_path)
    raw = "Findings:\n\n" + ("An ordinary paragraph of the report.\n\n" * 400)
    envelope = build_envelope(_spec(), raw, store)
    assert envelope.evidence_refs, "the spot check needs evidence refs to run at all"
    verifier = EnvelopeVerifier(store=store, backend=_Replies(reply), model="auditor", spot_rate=1.0, **kw)
    return verifier.verify(_spec(), envelope, force_spot=True)


def test_a_spot_check_whose_provider_is_down_is_not_listed_as_having_run(tmp_path: Path) -> None:
    outcome = _spot(tmp_path, None)
    assert outcome.passed is True  # an outage still does not block — the design stands
    assert "spot" not in outcome.checks_run
    assert outcome.spot_abstained is True


def test_an_unreadable_spot_reply_is_an_abstention_not_a_pass(tmp_path: Path) -> None:
    outcome = _spot(tmp_path, "I read both texts carefully and have some thoughts.")
    assert outcome.passed is True
    assert "spot" not in outcome.checks_run and outcome.spot_abstained is True
    assert "abstained" in outcome.detail


def test_a_readable_spot_pass_still_says_the_spot_check_ran(tmp_path: Path) -> None:
    outcome = _spot(tmp_path, "DROPPED: PASS")
    assert outcome.passed is True and "spot" in outcome.checks_run and outcome.spot_abstained is False


def test_a_readable_spot_fail_still_fails_where_the_check_rejects(tmp_path: Path) -> None:
    # The shipped default recovers what a DROPPED verdict names instead of rejecting (#433); the
    # three-criteria form rejects. Either way a readable FAIL is a verdict, and the gate ran.
    outcome = _spot(tmp_path, "INVENTED: FAIL\nDROPPED: PASS\nCONTRADICTED: PASS", recover_dropped=False)
    assert outcome.passed is False and "spot" in outcome.checks_run and outcome.spot_abstained is False


# --- the strong verifier and the checklist ---------------------------------------------------------


class _Backend:
    def __init__(self, reply: str | None) -> None:
        self.reply = reply

    def complete(self, messages: Any, **kwargs: Any) -> SimpleNamespace:
        if self.reply is None:
            raise RuntimeError("down")
        return SimpleNamespace(content=self.reply)


@pytest.mark.parametrize("reply", ["Looks solid to me.", None])
def test_a_strong_verifier_that_gave_no_grade_does_not_invent_a_ten(reply: str | None) -> None:
    passed, score = StrongVerifier(_Backend(reply)).verify("task", "answer")
    assert passed is True  # never blocks on its own outage
    assert score is None  # and never reports 1.0 for a grade it did not give


def test_a_strong_verifier_grade_is_still_read_and_still_gates() -> None:
    assert StrongVerifier(_Backend("3")).verify("task", "answer") == (False, pytest.approx(0.3))
    assert StrongVerifier(_Backend("9")).verify("task", "answer") == (True, pytest.approx(0.9))


REQS = [Requirement(text="return a + b", kind="do")]


@pytest.mark.parametrize("reply", ["not json at all", None, "[1, 2]"])
def test_a_checklist_that_could_not_grade_says_so_instead_of_all_met(reply: str | None) -> None:
    misses = RequirementChecklist(_Backend(reply)).grade("task", "answer", REQS)
    assert misses is None  # abstained — distinct from [] ("every requirement met")
    assert not misses  # and still falsy, so the solve loop does not block on it


def test_a_checklist_that_graded_still_distinguishes_met_from_missed() -> None:
    met = '{"items": [{"text": "return a + b", "met": true}]}'
    missed = '{"items": [{"text": "return a + b", "met": false}]}'
    assert RequirementChecklist(_Backend(met)).grade("task", "answer", REQS) == []
    assert RequirementChecklist(_Backend(missed)).grade("task", "answer", REQS) == ["return a + b"]


# --- the local decision backend --------------------------------------------------------------------


def _body(label: str, tops: list[tuple[str, float]], key: str = "verdict") -> dict[str, Any]:
    tokens = ["{", '"', key, '":', ' "', label, '"', "}"]
    entries: list[dict[str, Any]] = []
    for tok in tokens:
        if tok == label:
            entries.append({"token": tok, "logprob": tops[0][1], "top_logprobs": [{"token": t, "logprob": lp} for t, lp in tops]})
        else:
            entries.append({"token": tok, "logprob": -0.01, "top_logprobs": [{"token": tok, "logprob": -0.01}]})
    return {"message": {"content": f'{{"{key}": "{label}"}}'}, "logprobs": entries}


def test_zero_mass_on_the_labels_is_no_signal_not_a_confident_allow() -> None:
    backend = LocalLogprobBackend("http://127.0.0.1:11434", "qwen3:4b")
    # The label token the model wrote, with an underflowing probability, and nothing else on the
    # labels — `label_probabilities` returns all-zero shares for this.
    reading = backend.read(_body("ALLOW", [("ALLOW", -1e9)]), DANGER)
    assert reading.choice == "ALLOW"
    assert reading.p is None, "p=0.0 here would be read by the REVIEW band as certainly not dangerous"


def test_a_label_token_shared_by_two_options_gives_no_number() -> None:
    # Study 21 A4. The model writes "REVIEW" and the tokenizer splits it `RE` + `VIEW`: the label
    # token located is `RE`, which begins both REVIEW and REFUSE, so which option its probability
    # belongs to is unknowable. Before phase 0 the reading was built from the OTHER candidates'
    # residual mass — here ALLOW's, giving p = 0.0: a confident "not dangerous" read off the wrong token.
    question = Choice(key="verdict", instructions="Decide.", options=("REVIEW", "REFUSE", "ALLOW"), event=("REVIEW", "REFUSE"))
    tokens = ["{", '"', "verdict", '":', ' "', "RE", "VIEW", '"', "}"]
    entries: list[dict[str, Any]] = []
    for tok in tokens:
        if tok == "RE":
            tops = [("RE", math.log(0.7)), ("ALLOW", math.log(0.3))]
            entries.append({"token": tok, "logprob": tops[0][1], "top_logprobs": [{"token": t, "logprob": lp} for t, lp in tops]})
        else:
            entries.append({"token": tok, "logprob": -0.01, "top_logprobs": [{"token": tok, "logprob": -0.01}]})
    body = {"message": {"content": '{"verdict": "REVIEW"}'}, "logprobs": entries}
    reading = LocalLogprobBackend("http://127.0.0.1:11434", "qwen3:4b").read(body, question)
    assert reading.choice == "REVIEW"
    assert reading.p is None and reading.shares is None


def test_an_unambiguous_label_still_reads_as_before() -> None:
    backend = LocalLogprobBackend("http://127.0.0.1:11434", "qwen3:4b")
    reading = backend.read(_body("ALLOW", [("ALLOW", math.log(0.9)), ("BLOCK", math.log(0.1))]), DANGER)
    assert reading.p == pytest.approx(0.1)


# --- the band's hysteresis exit is a setting ------------------------------------------------------


def test_the_hysteresis_exit_is_read_from_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    import chimera.decisions.factory as factory

    monkeypatch.setattr(factory, "build_decider", lambda settings: object())
    settings = SimpleNamespace(
        governance_band_review_at=0.6, governance_band_allow_below=0.2, governance_band_exit_at=0.35,
    )
    assert build_band(settings).band == Band(review_at=0.6, allow_below=0.2, exit_at=0.35)
    with pytest.raises(ValueError):
        Band(review_at=0.5, allow_below=0.3, exit_at=0.6)  # the order is still enforced
