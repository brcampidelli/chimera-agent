"""A decision can carry a probability now — and a route that sent none is read as no signal.

Study 20 (`bench/PLAN-study20-calibrated-decisions.md` §2.4, §3 A1): forty-four decision points, twelve
decided by a model, none carrying a probability; `LLMGateway.complete(**kwargs)` already forwarded
``logprobs=True`` to the provider and `_normalize` never read the answer. Three things here, each with
the failure it is against:

* `CompletionResult.logprobs` is filled from the route's answer, as plain dicts, and is ``None`` when
  nothing came — which is what OpenRouter's silent parameter drop and every reasoning route look like.
* `label_probabilities` reads the FIRST token against a closed label set, renormalized over the labels,
  and reports how much mass was on the labels at all; a result without logprobs yields ``None``.
* `Verdict.confidence` travels onto the audit line when the decider had a number, and is absent — not
  ``None`` — when it did not, so the rules never read as "unsure".
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.governance.audit import AuditLog
from chimera.governance.kernel import TrustKernel
from chimera.governance.policy import Decision, Verdict
from chimera.providers.decision import label_probabilities
from chimera.providers.gateway import CompletionResult, LLMGateway


def _response(logprobs: Any, content: str = "ALLOW") -> Any:
    message = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="stop", logprobs=logprobs)
    return SimpleNamespace(choices=[choice], usage=None, provider="X", id="gen-1")


def _entry(token: str, logprob: float, tops: list[tuple[str, float]]) -> Any:
    return SimpleNamespace(
        token=token, logprob=logprob,
        top_logprobs=[SimpleNamespace(token=t, logprob=lp) for t, lp in tops],
    )


def test_the_route_s_logprobs_land_on_the_result_as_plain_dicts() -> None:
    holder = SimpleNamespace(content=[_entry("ALLOW", math.log(0.7), [("ALLOW", math.log(0.7)), ("REVIEW", math.log(0.2)), ("BLOCK", math.log(0.05))])])
    result = LLMGateway._normalize(_response(holder), "m")
    assert result.logprobs is not None
    assert result.logprobs[0]["token"] == "ALLOW"
    assert result.logprobs[0]["logprob"] == pytest.approx(math.log(0.7))
    assert [t["token"] for t in result.logprobs[0]["top_logprobs"]] == ["ALLOW", "REVIEW", "BLOCK"]


def test_the_openai_dict_shape_is_read_too() -> None:
    holder = {"content": [{"token": "BLOCK", "logprob": -0.1, "top_logprobs": [{"token": "BLOCK", "logprob": -0.1}]}]}
    result = LLMGateway._normalize(_response(holder), "m")
    assert result.logprobs == [{"token": "BLOCK", "logprob": -0.1, "top_logprobs": [{"token": "BLOCK", "logprob": -0.1}]}]


def test_a_route_that_sent_none_leaves_the_field_none_not_empty() -> None:
    """The silent drop: the request asked, the provider ignored, nothing complains. `None`, so a
    reader cannot mistake it for a distribution that happened to be empty."""
    assert LLMGateway._normalize(_response(None), "m").logprobs is None
    assert LLMGateway._normalize(_response(SimpleNamespace(content=[])), "m").logprobs is None
    assert LLMGateway._normalize(_response({"content": None}), "m").logprobs is None


def test_the_first_token_becomes_shares_over_the_labels_with_the_mass_beside_them() -> None:
    result = CompletionResult(content="ALLOW", model="m", logprobs=[{
        "token": "ALLOW", "logprob": math.log(0.6),
        "top_logprobs": [("ALLOW", 0.6), ("REVIEW", 0.2), ("BLOCK", 0.1), ("Sure", 0.1)],
    }])
    result.logprobs[0]["top_logprobs"] = [{"token": t, "logprob": math.log(p)} for t, p in result.logprobs[0]["top_logprobs"]]
    read = label_probabilities(result, ["BLOCK", "REVIEW", "ALLOW"])
    assert read is not None
    # 0.6 + 0.2 + 0.1 of the mass was on the labels; "Sure" was not a label.
    assert read.mass == pytest.approx(0.9)
    assert read.shares["ALLOW"] == pytest.approx(0.6 / 0.9)
    assert read.shares["REVIEW"] == pytest.approx(0.2 / 0.9)
    assert read.shares["BLOCK"] == pytest.approx(0.1 / 0.9)
    assert read.top == "ALLOW"
    assert read.first_token == "ALLOW"


def test_a_prefix_token_counts_only_when_it_names_one_label() -> None:
    """Tokenizers split words: ``RE`` is REVIEW's and nobody else's; ``B`` would be BLOCK's. A
    prefix shared by two labels counts for neither."""
    result = CompletionResult(content="REVIEW", model="m", logprobs=[{
        "token": "RE", "logprob": math.log(0.5),
        "top_logprobs": [{"token": "RE", "logprob": math.log(0.5)}, {"token": " block", "logprob": math.log(0.3)}, {"token": "A", "logprob": math.log(0.2)}],
    }])
    read = label_probabilities(result, ["BLOCK", "REVIEW", "ALLOW", "ABSTAIN"])
    assert read is not None
    assert read.matched == {"BLOCK": [" block"], "REVIEW": ["RE"]}  # "A" fits ALLOW and ABSTAIN: dropped
    assert read.mass == pytest.approx(0.8)
    assert read.shares["REVIEW"] == pytest.approx(0.5 / 0.8)


def test_a_result_without_logprobs_reads_as_no_signal() -> None:
    assert label_probabilities(CompletionResult(content="ALLOW", model="m"), ["ALLOW", "BLOCK"]) is None
    assert label_probabilities(CompletionResult(content="ALLOW", model="m", logprobs=[]), ["ALLOW", "BLOCK"]) is None


def test_the_written_token_counts_even_when_the_route_listed_no_alternatives() -> None:
    result = CompletionResult(content="BLOCK", model="m", logprobs=[{"token": "BLOCK", "logprob": math.log(0.97), "top_logprobs": []}])
    read = label_probabilities(result, ["BLOCK", "ALLOW"])
    assert read is not None and read.mass == pytest.approx(0.97) and read.shares["BLOCK"] == 1.0


def test_the_audit_line_carries_the_confidence_only_when_the_decider_had_one(tmp_path: Any) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")

    def sure_judge(action: str) -> Verdict:
        return Verdict(Decision.REVIEW, "judge said REVIEW", "judge", confidence=0.8649)

    def wordy_judge(action: str) -> Verdict:
        return Verdict(Decision.REVIEW, "judge said REVIEW", "judge")

    # Actions the lexical rules are blind to (the `governance_judge` corpus's own example), so the
    # judge is the decider and its number is the one on the line.
    blind = "run_shell\npython -c \"import shutil; shutil.rmtree('build')\""
    TrustKernel(judge=sure_judge, audit=audit).evaluate(blind)
    TrustKernel(judge=wordy_judge, audit=audit).evaluate(blind)
    lines = [e for e in audit.entries() if e.get("type") == "governance"]
    assert len(lines) == 2
    assert lines[0]["confidence"] == 0.8649
    assert "confidence" not in lines[1]  # absent, not None: a word is not "unsure"
