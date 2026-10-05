"""Under a strict spend cap with a ceiling, a fused or cascade run does not start, and says so.

``worst_case_usd`` prices a call over the models its backend names (``planned_attempts`` /
``planned_calls`` / ``planned_call``). ``RoutedBackend`` (``--fuse``, a fused desktop turn),
``CascadeBackend`` (``--cascade``) and ``FusionEngine`` name none: they pick their models, and how
many calls to make, as they go. Their worst case is unknown, so a strict ceiling refuses their first
call. That is the chosen behaviour, not an accident, and it is locked here with the REAL composite
backends, so a change to it has to be a decision: the old refusal told the person to "give the call
a bound", which nobody running a fused turn can do, and the Settings hint never said that switching
strict on stops fused and cascade runs that carry a ceiling.

Free: fake gateways, no network.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.core.agent import Agent, AgentConfig
from chimera.fusion import RoutedBackend
from chimera.fusion.receipts import ModelPrice, set_price
from chimera.orchestration.budget import SpendBudget, SpendCappedBackend, SpendExceeded
from chimera.providers.gateway import CompletionResult, Message
from chimera.tools.registry import ToolRegistry

MODEL = "testvendor/fused-run-fixed-price"


@pytest.fixture(autouse=True)
def _pinned_price() -> Iterator[None]:
    set_price(MODEL, ModelPrice(input_per_m=0.0, output_per_m=1.0))
    yield


class _Gateway:
    """Plans and answers like the gateway, on one priced model."""

    def __init__(self) -> None:
        self.calls = 0

    def planned_calls(
        self, model: str | None = None, max_tokens: int | None = None
    ) -> list[tuple[str, int | None]]:
        return [(model or MODEL, max_tokens if max_tokens is not None else 1000)]

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(content="done", model=MODEL, prompt_tokens=10, completion_tokens=10)


def _routed(gateway: _Gateway) -> RoutedBackend:
    return RoutedBackend(gateway, gateway)


def test_a_fused_agent_run_with_a_strict_ceiling_stops_before_its_first_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHIMERA_STRICT_SPEND_CAP", "true")
    get_settings.cache_clear()
    gateway = _Gateway()

    result = Agent(_routed(gateway), ToolRegistry(), AgentConfig(model=MODEL, max_usd=5.0)).run("hi")

    assert result.stopped_reason == "spend"
    assert gateway.calls == 0
    assert "fused or cascade run" in result.answer
    assert "turn strict off" in result.answer
    # The old advice named something a person running a fused turn cannot do.
    assert "give the call a bound" not in result.answer


def test_the_same_fused_run_starts_with_strict_off() -> None:
    gateway = _Gateway()

    result = Agent(_routed(gateway), ToolRegistry(), AgentConfig(model=MODEL, max_usd=5.0)).run("hi")

    assert result.stopped_reason != "spend"
    assert gateway.calls == 1


def test_the_same_fused_run_starts_with_strict_on_and_no_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Limits became warnings (2026-09-27): strict decides how hard a TYPED ceiling holds, nothing
    else."""
    monkeypatch.setenv("CHIMERA_STRICT_SPEND_CAP", "true")
    get_settings.cache_clear()
    gateway = _Gateway()

    Agent(_routed(gateway), ToolRegistry(), AgentConfig(model=MODEL)).run("hi")

    assert gateway.calls == 1


def test_a_capped_backend_around_a_fused_one_refuses_it_under_strict() -> None:
    """The autonomous runner's reviewer and any surface that wraps a composite backend in
    ``SpendCappedBackend`` get the same answer as the agent loop."""
    gateway = _Gateway()
    capped = SpendCappedBackend(_routed(gateway), SpendBudget(max_usd=5.0, strict=True))

    with pytest.raises(SpendExceeded, match="fused or cascade run"):
        capped.complete([Message(role="user", content="hi")], model=MODEL)

    assert gateway.calls == 0
