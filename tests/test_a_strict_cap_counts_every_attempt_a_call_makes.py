"""A strict spend cap counts every attempt one call may make, not only the one that answers.

``LLMGateway.complete`` walks a chain: on a timeout or an unknown error it rotates to the next key
or falls back to the next model, inside the same call. A provider can bill an attempt and then fail
it (a timeout after generation, a cut stream). The first strict cap reserved the DEAREST leg of the
chain and recorded only the attempt that answered, so with a $1 ceiling, $0.30 per attempt and a
primary that billed then timed out, every call really cost $0.60 while the ledger saw $0.30: three
calls admitted, $1.80 spent, under a setting whose screen says the spend never passes the ceiling.

Strict now reserves the SUM over every attempt (each model once per key it may be tried with), and
the attempts that raised before one answered are charged at their worst case afterwards, from
``CompletionResult.failed_attempts``. Off, nothing changes: the dearest leg, and only the answer
charged.

Everything here is free: ``litellm.completion`` is replaced, no network.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.core.agent import Agent, AgentConfig
from chimera.fusion.receipts import ModelPrice, set_price
from chimera.orchestration.budget import (
    SpendBudget,
    SpendCappedBackend,
    SpendExceeded,
    settle_failed_attempts,
    worst_case_usd,
)
from chimera.providers.gateway import CompletionResult, Message, ToolCall
from chimera.tools.registry import Tool, ToolRegistry

PRIMARY = "openrouter/testvendor/attempts-primary"
FALLBACK = "openrouter/testvendor/attempts-fallback"
#: Input free, output $1 per million: an attempt bounded at 300k tokens costs exactly $0.30.
PRICE = ModelPrice(input_per_m=0.0, output_per_m=1.0)
BOUND = 300_000
HI = [Message(role="user", content="hi")]


@pytest.fixture(autouse=True)
def _pinned_prices() -> Iterator[None]:
    set_price(PRIMARY, PRICE)
    set_price(FALLBACK, PRICE)
    yield


class _Provider:
    """Stands in for ``litellm.completion``: every attempt is BILLED its whole bound, and the
    attempts listed in ``fail`` then raise, the way a timeout after generation does."""

    def __init__(self, fail: Any) -> None:
        self.fail = fail
        self.billed = 0.0
        self.attempts: list[tuple[str, str | None]] = []

    def __call__(self, **kwargs: Any) -> Any:
        model = kwargs["model"]
        self.attempts.append((model, kwargs.get("api_key")))
        self.billed += kwargs["max_tokens"] / 1_000_000
        error = self.fail(model, kwargs.get("api_key"))
        if error is not None:
            raise error
        message = SimpleNamespace(content="ok", tool_calls=None)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=kwargs["max_tokens"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=usage
        )


def _gateway(monkeypatch: pytest.MonkeyPatch, provider: _Provider, **env: str) -> Any:
    import litellm

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    monkeypatch.setattr(litellm, "completion", provider)
    from chimera.providers import LLMGateway

    return LLMGateway()


def _primary_times_out(model: str, _key: str | None) -> Exception | None:
    return TimeoutError("request timed out") if model == PRIMARY else None


def _until_refused(capped: SpendCappedBackend, limit: int = 10) -> int:
    done = 0
    for _ in range(limit):
        try:
            capped.complete(HI, model=PRIMARY, max_tokens=BOUND)
        except SpendExceeded:
            break
        done += 1
    return done


def test_a_billed_timeout_then_a_fallback_never_passes_a_strict_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _Provider(_primary_times_out)
    gateway = _gateway(monkeypatch, provider, CHIMERA_FALLBACK_MODELS=FALLBACK)
    budget = SpendBudget(max_usd=1.0, strict=True)

    calls = _until_refused(SpendCappedBackend(gateway, budget))

    # One call is $0.60 in the worst case (two attempts at $0.30); a second would commit $1.20.
    assert calls == 1
    assert provider.billed == pytest.approx(0.6)
    assert provider.billed <= budget.max_usd
    # The ledger saw the timed-out attempt too, as an estimate.
    assert budget.spent == pytest.approx(provider.billed)
    assert budget.estimated is True


def test_the_gateway_names_the_attempts_that_failed_before_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _gateway(monkeypatch, _Provider(_primary_times_out), CHIMERA_FALLBACK_MODELS=FALLBACK)

    result = gateway.complete(HI, model=PRIMARY, max_tokens=BOUND)

    assert result.model == FALLBACK
    assert result.failed_attempts == [(PRIMARY, BOUND)]
    # Out of every serialised form: no receipt or response body changes shape.
    assert "failed_attempts" not in result.model_dump()


def test_a_rotated_key_is_an_attempt_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown error rotates to the next key of the pool, on the same model: two keys, two
    attempts, each possibly billed, so the strict worst case is twice the answer's."""

    def first_key_fails(_model: str, key: str | None) -> Exception | None:
        return RuntimeError("upstream exploded") if key == "k1" else None

    provider = _Provider(first_key_fails)
    gateway = _gateway(monkeypatch, provider, CHIMERA_OPENROUTER_KEYS="k1,k2")
    kwargs: dict[str, Any] = {"model": PRIMARY, "max_tokens": BOUND}

    assert worst_case_usd(gateway, HI, kwargs, strict=True) == pytest.approx(0.6)
    budget = SpendBudget(max_usd=1.0, strict=True)
    SpendCappedBackend(gateway, budget).complete(HI, **kwargs)

    assert [key for _m, key in provider.attempts] == ["k1", "k2"]
    assert budget.spent == pytest.approx(provider.billed)


def test_off_the_worst_case_is_still_the_dearest_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipped default is not this change's to tighten: off prices the dearest leg and charges
    only the answer, exactly as before."""
    provider = _Provider(_primary_times_out)
    gateway = _gateway(monkeypatch, provider, CHIMERA_FALLBACK_MODELS=FALLBACK)
    kwargs: dict[str, Any] = {"model": PRIMARY, "max_tokens": BOUND}

    assert worst_case_usd(gateway, HI, kwargs) == pytest.approx(0.3)
    budget = SpendBudget(max_usd=1.0, strict=False)
    SpendCappedBackend(gateway, budget).complete(HI, **kwargs)

    assert budget.spent == pytest.approx(0.3)
    assert budget.estimated is False


def test_settling_without_a_strict_ceiling_charges_nothing() -> None:
    result = CompletionResult(content="ok", model=FALLBACK, failed_attempts=[(PRIMARY, BOUND)])
    for budget in (SpendBudget(max_usd=1.0, strict=False), SpendBudget(strict=True)):
        settle_failed_attempts(budget, result, HI, {"model": PRIMARY, "max_tokens": BOUND})
        assert budget.spent == 0.0


# --- the agent loop -------------------------------------------------------------------------------


class _Ping(Tool):
    name = "ping"
    description = "answers with what it was given"
    parameters: dict[str, Any] = {"type": "object", "properties": {"n": {"type": "integer"}}}

    def run(self, **kwargs: Any) -> str:
        return f"pong {kwargs.get('n')}"


class _FallsBackEveryStep:
    """Each step's primary attempt is billed and fails; the fallback answers with a tool call, so
    only the ceiling ends the run. Lists its chain the way the gateway does."""

    def __init__(self) -> None:
        self.calls = 0

    def planned_calls(
        self, model: str | None = None, max_tokens: int | None = None
    ) -> list[tuple[str, int | None]]:
        return [(PRIMARY, BOUND), (FALLBACK, BOUND)]

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            content=f"step {self.calls}",
            model=FALLBACK,
            prompt_tokens=10,
            completion_tokens=BOUND,
            tool_calls=[ToolCall(id=f"c{self.calls}", name="ping", arguments={"n": self.calls})],
            failed_attempts=[(PRIMARY, BOUND)],
        )


def test_the_agent_loop_charges_the_attempts_a_step_gave_up_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """$0.60 per step really spent ($0.30 billed and failed, $0.30 answered) against $1 strict: one
    step. Charging only the answer read $0.30 and admitted a second, $1.20 in all."""
    monkeypatch.setenv("CHIMERA_STRICT_SPEND_CAP", "true")
    get_settings.cache_clear()
    registry = ToolRegistry()
    registry.register(_Ping())
    backend = _FallsBackEveryStep()

    result = Agent(backend, registry, AgentConfig(model=PRIMARY, max_steps=10, max_usd=1.0)).run("go")

    assert result.stopped_reason == "spend"
    assert backend.calls == 1
