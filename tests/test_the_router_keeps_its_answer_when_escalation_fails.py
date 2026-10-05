"""The router keeps the answer it holds when escalation to fusion fails (study 30, S30-02 follow-up).

``FusionFailed`` is new: fusion raises it when every panelist errored or came back blank. The router
escalates to fusion in two places with an answer already in hand — the cheap samples disagreed, or
the single answer failed ``escalate_on_fail`` — and neither caught it, so a panel-wide 503 killed a
turn the router could have answered. S30-02's rule, a failure never loses the answer, applies here.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.fusion import FusionFailed, RoutedBackend, RoutingPolicy
from chimera.orchestration.budget import SpendExceeded
from chimera.providers import CompletionResult

ASK = [{"role": "user", "content": "q"}]


class Samples:
    def __init__(self, samples: list[tuple[str, str]]) -> None:
        self.samples = list(samples)
        self.calls = 0

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.calls += 1
        content, reason = self.samples.pop(0)
        return CompletionResult(
            content=content, model="cheap", finish_reason=reason, prompt_tokens=10, completion_tokens=5
        )


class DownFusion:
    def __init__(self, exc: Exception | None = None) -> None:
        self.calls = 0
        self.exc = exc or FusionFailed("no panel model produced an answer (m1: 503; m2: 503)")

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.calls += 1
        raise self.exc


def test_disagreeing_samples_keep_the_first_answer_when_fusion_fails() -> None:
    fusion = DownFusion()
    router = RoutedBackend(
        Samples([("answer one about cats", "stop"), ("totally different text here", "stop")]),
        fusion,
        RoutingPolicy(mode="never"),
        agreement_k=2,
    )
    result = router.complete(ASK)
    assert fusion.calls == 1
    assert result.content == "answer one about cats"
    assert "503" in (result.route_meta or {})["escalation_failed"]
    assert result.prompt_tokens == 20  # both samples were paid for


def test_the_kept_sample_is_the_first_with_text() -> None:
    router = RoutedBackend(
        Samples([("", "length"), ("a real reply", "stop"), ("something else", "stop")]),
        DownFusion(),
        RoutingPolicy(mode="never"),
        agreement_k=3,
    )
    assert router.complete(ASK).content == "a real reply"


def test_a_failed_verification_keeps_the_single_answer_when_fusion_fails() -> None:
    fusion = DownFusion()
    router = RoutedBackend(
        Samples([("short", "stop")]),
        fusion,
        RoutingPolicy(mode="never"),
        escalate_on_fail=lambda result: False,
    )
    result = router.complete(ASK)
    assert result.content == "short"
    assert result.model == "cheap"
    assert "escalation_failed" in (result.route_meta or {})


def test_a_failed_agreement_escalation_is_not_escalated_a_second_time() -> None:
    fusion = DownFusion()
    router = RoutedBackend(
        Samples([("answer one about cats", "stop"), ("totally different text here", "stop")]),
        fusion,
        RoutingPolicy(mode="never"),
        escalate_on_fail=lambda result: False,
        agreement_k=2,
    )
    router.complete(ASK)
    assert fusion.calls == 1


def test_a_spend_ceiling_during_escalation_still_propagates() -> None:
    router = RoutedBackend(
        Samples([("short", "stop")]),
        DownFusion(SpendExceeded("spend cap reached")),
        RoutingPolicy(mode="never"),
        escalate_on_fail=lambda result: False,
    )
    with pytest.raises(SpendExceeded):
        router.complete(ASK)


def test_a_turn_routed_straight_to_fusion_still_reports_the_failure() -> None:
    # No answer in hand: there is nothing to keep, so the declared failure reaches the caller.
    router = RoutedBackend(Samples([]), DownFusion(), RoutingPolicy(mode="always"))
    with pytest.raises(FusionFailed):
        router.complete(ASK)
