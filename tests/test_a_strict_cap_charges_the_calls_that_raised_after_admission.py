"""A strict spend cap charges the calls it admitted that then raised, and counts a failed stream.

Two holes the adversarial review of the strict cap found, both of the same shape: an attempt the
provider may have billed and that the ledger never saw, so the next admission read a number below
the real spend and the run could pass a ceiling whose screen says it never does.

1. The compaction summariser and the tool router ask ``strict_refusal`` before they call, then
   swallow any failure (an optimisation may not take the run down). A timeout after generation is
   billed; they counted a fallback and charged nothing. ``SpendCappedBackend`` forfeits the
   reservation for the same error. Now both charge the admitted worst case, as an estimate, unless
   the error provably left before anything was billed.
2. A streamed step (the desktop's default) makes ONE streamed attempt on the primary and, when it
   fails before any text was shown, falls back to ``complete`` and its whole chain. Reasoning deltas
   are never shown, so a model that thought for thousands of tokens and then timed out falls back,
   and that attempt may have been billed. The strict worst case summed only ``complete``'s attempts
   and the result never named the streamed one. Now the sum counts it and the gateway names it in
   ``failed_attempts``, so ``settle_failed_attempts`` charges it.

Off, nothing changes. Free: fake backends and a replaced ``litellm.completion``, no network.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.core.agent import Agent, AgentConfig
from chimera.core.summarise import rule_summariser
from chimera.core.tool_router import ToolRouter
from chimera.fusion.receipts import ModelPrice, set_price
from chimera.orchestration.budget import BudgetExceeded, SpendBudget, worst_case_usd
from chimera.providers.gateway import CompletionResult, Message
from chimera.tools.registry import ToolRegistry

MODEL = "testvendor/raised-after-admission"
STREAMED = "openrouter/testvendor/raised-stream-primary"
#: Input free, output $1 per million: a call bounded at 300k tokens costs at most $0.30.
BOUND = 300_000
PRICE = ModelPrice(input_per_m=0.0, output_per_m=1.0)
HI = [Message(role="user", content="hi")]
OLDER = [
    {"role": "user", "content": "use the second file grep found"},
    {"role": "assistant", "content": "ok, the second one"},
]
SCHEMAS = [
    {"type": "function", "function": {"name": "read_file", "description": "read a file"}},
    {"type": "function", "function": {"name": "grep", "description": "search files"}},
]


@pytest.fixture(autouse=True)
def _pinned_prices() -> Iterator[None]:
    set_price(MODEL, PRICE)
    set_price(STREAMED, PRICE)
    yield


class _RaisesAfterAdmission:
    """Says its bound as the gateway does, then raises ``error`` from ``complete``: the shape of a
    timeout after the provider generated (and billed) the whole bound."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    def planned_call(self, model: str | None = None, max_tokens: int | None = None) -> tuple[str, int]:
        return model or MODEL, max_tokens if max_tokens is not None else BOUND

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.calls += 1
        raise self.error


def _budget(spent: float, *, strict: bool) -> SpendBudget:
    budget = SpendBudget(max_usd=1.0, strict=strict)
    budget.charge(spent)
    return budget


# --- 1. the calls beside the steps ----------------------------------------------------------------


def test_a_summariser_call_that_timed_out_after_admission_is_charged_its_worst_case() -> None:
    backend = _RaisesAfterAdmission(TimeoutError("request timed out"))
    budget = _budget(0.5, strict=True)

    summary = rule_summariser(backend, MODEL)(OLDER, spend=budget)

    assert backend.calls == 1
    assert "Standing from that span" not in summary  # the structural note, as on any failure
    assert budget.spent == pytest.approx(0.8)
    assert budget.estimated is True


def test_a_router_call_that_timed_out_after_admission_is_charged_its_worst_case() -> None:
    backend = _RaisesAfterAdmission(TimeoutError("request timed out"))
    router = ToolRouter(backend, MODEL)
    budget = _budget(0.5, strict=True)

    assert router.pick("find the bug", [], SCHEMAS, spend=budget) is None
    assert backend.calls == 1
    assert router.stats.fallbacks == 1
    assert budget.spent == pytest.approx(0.8)
    assert budget.estimated is True


def test_a_side_call_that_left_before_billing_is_not_charged() -> None:
    """The same rule as ``SpendCappedBackend``: an error that provably left first gives back."""
    for side in ("summariser", "router"):
        backend = _RaisesAfterAdmission(BudgetExceeded("a nested ceiling refused first"))
        budget = _budget(0.5, strict=True)
        if side == "summariser":
            rule_summariser(backend, MODEL)(OLDER, spend=budget)
        else:
            ToolRouter(backend, MODEL).pick("find the bug", [], SCHEMAS, spend=budget)
        assert budget.spent == pytest.approx(0.5), side
        assert budget.estimated is False, side


def test_off_a_side_call_that_raised_charges_nothing_as_before() -> None:
    for side in ("summariser", "router"):
        backend = _RaisesAfterAdmission(TimeoutError("request timed out"))
        budget = _budget(0.5, strict=False)
        if side == "summariser":
            rule_summariser(backend, MODEL)(OLDER, spend=budget)
        else:
            ToolRouter(backend, MODEL).pick("find the bug", [], SCHEMAS, spend=budget)
        assert budget.spent == pytest.approx(0.5), side
        assert budget.estimated is False, side


# --- 2. a streamed step ---------------------------------------------------------------------------


class _Billing:
    """Counts what the provider would bill: every attempt its whole bound, streamed or not."""

    def __init__(self) -> None:
        self.billed = 0.0
        self.batch_calls = 0

    def stream_once(self, **kwargs: Any) -> Iterator[Any]:
        self.billed += kwargs["max_tokens"] / 1_000_000
        thought = SimpleNamespace(content=None, tool_calls=None, reasoning_content="let me think")
        yield SimpleNamespace(choices=[SimpleNamespace(delta=thought, finish_reason=None)], usage=None)
        raise TimeoutError("request timed out")

    def completion(self, **kwargs: Any) -> Any:
        self.batch_calls += 1
        self.billed += kwargs["max_tokens"] / 1_000_000
        message = SimpleNamespace(content="ok", tool_calls=None)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=kwargs["max_tokens"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=usage
        )


def _gateway(monkeypatch: pytest.MonkeyPatch, billing: _Billing, *, strict: bool) -> Any:
    import litellm

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("CHIMERA_COMPLETION_CEILING", str(BOUND))
    monkeypatch.setenv("CHIMERA_STRICT_SPEND_CAP", "true" if strict else "false")
    get_settings.cache_clear()
    monkeypatch.setattr(litellm, "completion", billing.completion)
    from chimera.providers import LLMGateway

    gateway = LLMGateway()
    monkeypatch.setattr(gateway, "_stream_once", billing.stream_once, raising=False)
    return gateway


def test_a_failed_stream_that_fell_back_is_named_among_the_failed_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    billing = _Billing()
    gateway = _gateway(monkeypatch, billing, strict=True)

    result = gateway.stream_complete(HI, model=STREAMED, max_tokens=BOUND, on_delta=lambda _t: None)

    assert billing.batch_calls == 1  # it fell back: reasoning was never shown
    assert result.failed_attempts == [(STREAMED, BOUND)]
    assert "failed_attempts" not in result.model_dump()


def test_a_streamed_call_counts_its_stream_attempt_only_when_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _gateway(monkeypatch, _Billing(), strict=True)
    kwargs: dict[str, Any] = {"model": STREAMED, "max_tokens": BOUND}

    assert worst_case_usd(gateway, HI, kwargs, strict=True) == pytest.approx(0.3)
    assert worst_case_usd(gateway, HI, kwargs, strict=True, stream=True) == pytest.approx(0.6)
    # Off is the dearest answer, and the streamed attempt is already in it.
    assert worst_case_usd(gateway, HI, kwargs, stream=True) == pytest.approx(0.3)


def test_a_streamed_step_never_passes_a_strict_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """$0.50 strict, a step that streams: $0.30 billed by the stream that timed out after reasoning,
    $0.30 by the batch call that answered. Pricing only the batch chain admitted it at $0.30 and the
    run spent $0.60; counted, the step does not start."""
    billing = _Billing()
    gateway = _gateway(monkeypatch, billing, strict=True)

    result = Agent(gateway, ToolRegistry(), AgentConfig(model=STREAMED, max_usd=0.5)).run(
        "go", on_token=lambda _t: None
    )

    assert result.stopped_reason == "spend"
    assert billing.billed <= 0.5


def test_a_streamed_step_that_fell_back_is_charged_both_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    billing = _Billing()
    gateway = _gateway(monkeypatch, billing, strict=True)
    budget = SpendBudget(max_usd=1.0, strict=True)

    agent = Agent(gateway, ToolRegistry(), AgentConfig(model=STREAMED, max_usd=1.0))
    from chimera.core.agent import _UsageTally

    agent._step(HI, tools=None, on_token=lambda _t: None, usage=_UsageTally(), spend=budget)

    assert billing.billed == pytest.approx(0.6)
    assert budget.spent == pytest.approx(billing.billed)
    assert budget.estimated is True
