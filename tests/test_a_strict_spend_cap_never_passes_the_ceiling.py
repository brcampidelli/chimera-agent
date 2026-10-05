"""A STRICT spend cap admits a call only when its worst case still fits; the run never passes it.

The reservation cap (study 30, phase 0) admits a call while ``spent + reserved < max_usd``, so the
last call admitted can carry the run past the ceiling by its own worst case: a $1 cap with $0.30
calls ends at $1.20. That is the shipped behaviour and it stays the default. The owner decided on
2026-10-05 that a STRICT mode is his to switch on (``CHIMERA_STRICT_SPEND_CAP``, off by default):
with it on, a call is dispatched only when ``spent + reserved + its worst case <= max_usd``, the
worst case priced over the whole fallback chain the gateway may answer on, and a call whose worst
case cannot be priced is refused instead of being queued, because "never passes the cap" cannot be
promised for a call of unknown cost.

Respecting "limits became warnings" (2026-09-27): strict mode changes nothing for a run without a
ceiling the person typed, and a refusal is the same ``SpendExceeded`` the cap already raises.

Everything here is free: fake backends, no network.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from chimera.core.agent import Agent, AgentConfig
from chimera.fusion.receipts import ModelPrice, set_price
from chimera.orchestration.budget import (
    SpendBudget,
    SpendCappedBackend,
    SpendExceeded,
    worst_case_usd,
)
from chimera.providers.gateway import CompletionResult, Message, ToolCall
from chimera.tools.registry import Tool, ToolRegistry

#: Input free, output $1 per million: a call's worst case is its ``max_tokens`` in micro-dollars,
#: whatever the prompt estimate says, so the admission arithmetic below is exact.
MODEL = "testvendor/strict-fixed-price"
PRICE = ModelPrice(input_per_m=0.0, output_per_m=1.0)
FREE = "testvendor/strict-cheap:free"
#: Input-priced only, for the one test about the prompt side of the worst case.
PROMPT_PRICED = "testvendor/strict-prompt-priced"

DELAY = 0.4


@pytest.fixture(autouse=True)
def _pinned_price() -> Iterator[None]:
    set_price(MODEL, PRICE)
    set_price(PROMPT_PRICED, ModelPrice(input_per_m=1.0, output_per_m=0.0))
    yield


class _SlowBackend:
    """Takes ``DELAY`` per call, answers with exactly ``max_tokens`` completion tokens on ``MODEL``,
    and counts how many calls were inside it at once."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0
        self.calls = 0
        self._lock = threading.Lock()

    def planned_call(self, model: str | None = None, max_tokens: int | None = None) -> tuple[str, int | None]:
        return model or MODEL, max_tokens

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        with self._lock:
            self.calls += 1
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            time.sleep(DELAY)
            return CompletionResult(
                content="ok", model=MODEL, prompt_tokens=10, completion_tokens=kwargs.get("max_tokens") or 0
            )
        finally:
            with self._lock:
                self.in_flight -= 1


class _FallsBackToPaid(_SlowBackend):
    """Asks a ``:free`` model first and answers on the paid fallback, listing its whole chain the
    way ``LLMGateway.planned_calls`` does."""

    def planned_calls(
        self, model: str | None = None, max_tokens: int | None = None
    ) -> list[tuple[str, int | None]]:
        return [(FREE, max_tokens), (MODEL, max_tokens)]


class _CannotPlan(_SlowBackend):
    """A backend that cannot say how much a call may write."""

    def __getattribute__(self, name: str) -> Any:
        if name == "planned_call":
            raise AttributeError(name)
        return super().__getattribute__(name)


def _fan_out(capped: SpendCappedBackend, n: int, **kwargs: Any) -> tuple[float, list[BaseException]]:
    errors: list[BaseException] = []

    def one() -> None:
        try:
            capped.complete([Message(role="user", content="x")], model=MODEL, **kwargs)
        except BaseException as exc:  # collected, so the assertion can say which
            errors.append(exc)

    threads = [threading.Thread(target=one) for _ in range(n)]
    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return time.perf_counter() - start, errors


# --- the setting ---------------------------------------------------------------------------------


def test_strict_mode_is_off_unless_the_owner_turns_it_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.config import get_settings

    assert SpendBudget(max_usd=1.0).strict is False

    monkeypatch.setenv("CHIMERA_STRICT_SPEND_CAP", "true")
    get_settings.cache_clear()
    assert SpendBudget(max_usd=1.0).strict is True
    # Said in so many words, not left to an argument: a caller that passes it wins.
    assert SpendBudget(max_usd=1.0, strict=False).strict is False


# --- the fan-out ---------------------------------------------------------------------------------


def test_strict_never_lets_n_threads_pass_the_ceiling() -> None:
    """The same ten $0.30 calls against $1 that the reservation cap ends at $1.20 with: strict admits
    three, because a fourth would have committed $1.20 before it started."""
    budget = SpendBudget(max_usd=1.0, strict=True)
    capped = SpendCappedBackend(_SlowBackend(), budget)

    _elapsed, errors = _fan_out(capped, 10, max_tokens=300_000)

    refused = [e for e in errors if isinstance(e, SpendExceeded)]
    assert len(refused) == 7, errors
    assert budget.spent == pytest.approx(0.9)
    assert budget.spent <= budget.max_usd
    assert all("strict" in str(e) for e in refused), refused[0]


def test_strict_prices_the_whole_fallback_chain() -> None:
    """A ``:free`` primary prices at $0; the call may answer on the paid fallback, so strict admits
    by the fallback's worst case. Priced on the primary alone, all five would have started."""
    budget = SpendBudget(max_usd=2.5, strict=True)
    capped = SpendCappedBackend(_FallsBackToPaid(), budget)

    _elapsed, errors = _fan_out(capped, 5, max_tokens=1_000_000)

    assert len([e for e in errors if isinstance(e, SpendExceeded)]) == 3, errors
    assert budget.spent == pytest.approx(2.0)
    assert budget.spent <= budget.max_usd


def test_strict_keeps_the_calls_that_fit_running_together() -> None:
    inner = _SlowBackend()
    capped = SpendCappedBackend(inner, SpendBudget(max_usd=10.0, strict=True))

    elapsed, errors = _fan_out(capped, 4, max_tokens=1000)

    assert errors == []
    assert inner.peak == 4, f"only {inner.peak} call(s) were in flight at once"
    assert elapsed < 2 * DELAY, f"4 strict calls took {elapsed:.2f}s; one takes {DELAY}s"


def test_strict_refuses_a_call_whose_worst_case_cannot_be_priced() -> None:
    """Off, such a call is queued and runs; strict cannot promise the ceiling for a call of unknown
    cost, so it refuses before anything is dispatched, and says why."""
    inner = _CannotPlan()
    capped = SpendCappedBackend(inner, SpendBudget(max_usd=10.0, strict=True))

    with pytest.raises(SpendExceeded, match="cannot be priced"):
        capped.complete([Message(role="user", content="x")], model=MODEL)

    assert inner.calls == 0


def test_strict_without_a_ceiling_changes_nothing() -> None:
    """Limits became warnings (2026-09-27): no typed ceiling, nothing to refuse for money."""
    inner = _CannotPlan()
    capped = SpendCappedBackend(inner, SpendBudget(strict=True))

    _elapsed, errors = _fan_out(capped, 3)

    assert errors == []
    assert inner.calls == 3


def test_off_keeps_the_reservation_behaviour() -> None:
    """The shipped default, unchanged: the same ten calls end one call past the ceiling."""
    budget = SpendBudget(max_usd=1.0, strict=False)
    capped = SpendCappedBackend(_SlowBackend(), budget)

    _elapsed, errors = _fan_out(capped, 10, max_tokens=300_000)

    assert len([e for e in errors if isinstance(e, SpendExceeded)]) == 6, errors
    assert budget.spent == pytest.approx(1.2)


def test_strict_bounds_the_prompt_by_its_bytes_not_by_chars_over_four() -> None:
    """chars/4 is an average, and a prompt that tokenizes worse than average is billed past it. The
    strict worst case counts every UTF-8 byte as a token, which no byte-level tokenizer exceeds."""
    text = "ação " * 1000  # 5000 characters, 7000 bytes
    messages = [Message(role="user", content=text)]
    kwargs: dict[str, Any] = {"model": PROMPT_PRICED, "max_tokens": 10}
    backend = _SlowBackend()

    loose = worst_case_usd(backend, messages, kwargs)
    strict = worst_case_usd(backend, messages, kwargs, strict=True)

    assert loose is not None and strict is not None
    assert strict >= len(text.encode("utf-8")) / 1_000_000
    assert strict > loose


# --- the agent loop -------------------------------------------------------------------------------


class _Ping(Tool):
    name = "ping"
    description = "answers with what it was given"
    parameters: dict[str, Any] = {"type": "object", "properties": {"n": {"type": "integer"}}}

    def run(self, **kwargs: Any) -> str:
        return f"pong {kwargs.get('n')}"


class _LoopBackend:
    """Calls ``ping`` forever, each answer billed at its whole ``max_tokens`` bound.

    Each call with a new argument and a new result, so the tool-loop breaker never trips and only
    the ceiling can end the run."""

    def __init__(self, bound: int) -> None:
        self.bound = bound
        self.calls = 0

    def planned_calls(
        self, model: str | None = None, max_tokens: int | None = None
    ) -> list[tuple[str, int | None]]:
        return [(MODEL, max_tokens if max_tokens is not None else self.bound)]

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            content=f"step {self.calls}",
            model=MODEL,
            prompt_tokens=10,
            completion_tokens=self.bound,
            tool_calls=[ToolCall(id=f"c{self.calls}", name="ping", arguments={"n": self.calls})],
        )


def _loop_agent(backend: _LoopBackend, max_usd: float) -> Agent:
    registry = ToolRegistry()
    registry.register(_Ping())
    return Agent(backend, registry, AgentConfig(model=MODEL, max_steps=20, max_usd=max_usd))


def test_the_agent_loop_under_strict_stops_before_the_call_that_would_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each step may cost $0.30; a $1 ceiling admits three steps strict, four without it."""
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_STRICT_SPEND_CAP", "true")
    get_settings.cache_clear()
    backend = _LoopBackend(bound=300_000)

    result = _loop_agent(backend, 1.0).run("do something long")

    assert result.stopped_reason == "spend"
    assert "strict" in result.answer
    assert backend.calls == 3


def test_the_agent_loop_without_strict_is_unchanged() -> None:
    backend = _LoopBackend(bound=300_000)

    result = _loop_agent(backend, 1.0).run("do something long")

    assert result.stopped_reason == "spend"
    assert backend.calls == 4
