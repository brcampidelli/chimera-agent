"""A dollar ceiling on a fan-out reserves each call's worst case; it does not queue the calls.

``SpendCappedBackend`` held its lock across the whole model call. Its docstring defended that as
free — "releasing it between the check and the record lets N threads all read 'under budget' and
then all spend" — and the defence was right about the race and wrong about the price: every crew,
hierarchy and fused panel with a typed ``max_usd`` ran its "parallel" members one at a time. Study
30 measured 4 workers x 1.0 s at 4.01 s capped against 1.00 s uncapped. Nothing failed; a capped
run was simply N times slower than the same run without a ceiling, which reads as "the model is
slow", not as a defect.

The second defect hid in the same ``with`` block: a call that RAISED skipped ``record_result``.
A provider that bills and then times out, or a stream cut after the tokens were generated, cost
money the ceiling never saw.

The fix reserves ``prompt estimate + max_tokens`` at the model's rate under the lock, releases the
lock for the call, and settles on the reported usage. A call that raises keeps its reservation and
marks the run's spend ``estimated``. A call whose worst case cannot be priced still runs alone, as
before — reserving zero for it would reopen exactly the race the old lock closed.

Everything here is free: fake backends, no network.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from chimera.fusion.engine import FusionConfig, FusionEngine
from chimera.fusion.receipts import ModelPrice, set_price
from chimera.orchestration.budget import SpendBudget, SpendCappedBackend, SpendExceeded
from chimera.providers.gateway import CompletionResult, Message

#: Input is free and output costs $1 per million, so a call's reservation is exactly its
#: ``max_tokens`` in micro-dollars and the arithmetic below needs no prompt estimate.
MODEL = "testvendor/fixed-price-model"
PRICE = ModelPrice(input_per_m=0.0, output_per_m=1.0)
PANEL = [f"testvendor/fixed-price-panel-{i}" for i in range(4)]

#: How long each fake call takes. Long enough that four serial calls cannot hide under scheduling
#: noise, short enough that the file stays quick.
DELAY = 0.4


@pytest.fixture(autouse=True)
def _pinned_price() -> Iterator[None]:
    set_price(MODEL, PRICE)
    for member in PANEL:
        set_price(member, PRICE)
    set_price("testvendor/fixed-price-judge", PRICE)
    yield


class _SlowBackend:
    """Takes ``DELAY`` per call, answers with exactly ``max_tokens`` completion tokens, and counts
    how many calls were inside it at once."""

    def __init__(self, *, fail: bool = False, bound: int | None = 1000) -> None:
        self.fail = fail
        self.bound = bound
        self.in_flight = 0
        self.peak = 0
        self.calls = 0
        self._lock = threading.Lock()

    def planned_call(self, model: str | None = None, max_tokens: int | None = None) -> tuple[str, int | None]:
        """What `LLMGateway` answers: the model that will be asked and the most it may write."""
        return model or MODEL, max_tokens if max_tokens is not None else self.bound

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        with self._lock:
            self.calls += 1
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            time.sleep(DELAY)
            if self.fail:
                raise TimeoutError("the provider billed the tokens and then the socket closed")
            budget = kwargs.get("max_tokens") or self.bound or 0
            return CompletionResult(
                content="ok",
                model=kwargs.get("model") or MODEL,
                prompt_tokens=10,
                completion_tokens=budget,
            )
        finally:
            with self._lock:
                self.in_flight -= 1


class _UnboundedBackend(_SlowBackend):
    """A backend that cannot say how much a call may write — a composite, or a fake predating the
    method. Its worst case is unknown, so the ceiling cannot reserve for it."""

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


def test_capped_calls_in_parallel_take_about_one_call_not_n() -> None:
    inner = _SlowBackend()
    capped = SpendCappedBackend(inner, SpendBudget(max_usd=10.0))

    elapsed, errors = _fan_out(capped, 4, max_tokens=1000)

    assert errors == []
    assert inner.peak == 4, f"only {inner.peak} call(s) were in flight at once"
    # Serial would be 4 x DELAY = 1.6 s. Twice one call leaves room for a slow CI box.
    assert elapsed < 2 * DELAY, f"4 capped calls took {elapsed:.2f}s; one takes {DELAY}s"


def test_a_fused_panel_under_a_cap_runs_its_members_together() -> None:
    """The hierarchy route builds ``FusionEngine(capped)`` (orchestration_api), so every panel member
    reaches the model through the same ceiling. Serialized, a four-member panel took four calls'
    time before the judge even started."""
    inner = _SlowBackend()
    capped = SpendCappedBackend(inner, SpendBudget(max_usd=10.0))
    engine = FusionEngine(
        capped,
        FusionConfig(
            panel=PANEL,
            judge="testvendor/fixed-price-judge",
            synthesizer="testvendor/fixed-price-judge",
            max_workers=4,
            judge_max_tokens=1000,
            synth_max_tokens=1000,
        ),
    )

    start = time.perf_counter()
    engine.complete([Message(role="user", content="qual é a capital do Peru?")])
    elapsed = time.perf_counter() - start

    assert inner.peak == 4, f"the panel ran {inner.peak} member(s) at a time"
    # Panel (1 x DELAY together) + judge + synthesis = 3 x DELAY; serial would be 6 x DELAY.
    assert elapsed < 4.5 * DELAY, f"the fused call took {elapsed:.2f}s"


def test_a_call_that_raises_is_still_charged_and_the_total_says_estimated() -> None:
    budget = SpendBudget(max_usd=10.0)
    capped = SpendCappedBackend(_SlowBackend(fail=True), budget)

    with pytest.raises(TimeoutError):
        capped.complete([Message(role="user", content="x")], model=MODEL, max_tokens=500_000)

    # The reservation was 500k output tokens at $1/M. It stays charged: the provider may have
    # billed every one of them, and a ceiling that assumes otherwise is guessing in its own favour.
    assert budget.spent == pytest.approx(0.5)
    assert budget.estimated is True


def test_a_successful_call_is_settled_on_what_it_really_used() -> None:
    budget = SpendBudget(max_usd=10.0)
    inner = _SlowBackend()
    capped = SpendCappedBackend(inner, budget)

    capped.complete([Message(role="user", content="x")], model=MODEL, max_tokens=300_000)

    assert budget.spent == pytest.approx(0.3)
    assert budget.reserved == 0.0
    assert budget.estimated is False


def test_reservations_admit_no_more_calls_than_the_serial_ceiling_did() -> None:
    """The race the old lock closed stays closed. A $1 ceiling with $0.30 calls admits four in
    sequence (0, 0.3, 0.6 and 0.9 are all under $1); ten threads at once must admit the same four,
    not ten that all read "under budget" before any of them spent.

    This is NOT "spend never exceeds the cap", which study 30 listed as the measure: the run ends
    at $1.20 against $1.00, exactly as the serial version did. Admission checks what is committed,
    not what the next call may add, so one admitted call can carry the run past the ceiling by its
    own worst case. Closing that changes which calls may start and is the owner's decision."""
    budget = SpendBudget(max_usd=1.0)
    capped = SpendCappedBackend(_SlowBackend(), budget)

    _elapsed, errors = _fan_out(capped, 10, max_tokens=300_000)

    refused = [e for e in errors if isinstance(e, SpendExceeded)]
    assert len(refused) == 6, errors
    assert budget.spent == pytest.approx(1.2)
    # Never more than one call past the ceiling — the serial cap's own bound, since it checks
    # before a call and cannot know what that call will cost.
    assert budget.spent <= budget.max_usd + 0.3 + 1e-9


def test_a_call_whose_worst_case_cannot_be_priced_still_runs_alone() -> None:
    inner = _UnboundedBackend()
    capped = SpendCappedBackend(inner, SpendBudget(max_usd=10.0))

    _elapsed, errors = _fan_out(capped, 3)  # no max_tokens, and the backend cannot say

    assert errors == []
    assert inner.peak == 1


def test_an_uncapped_budget_does_not_serialize_unpriceable_calls() -> None:
    """Without a ceiling there is nothing to protect by queueing: an unknown worst case cannot push
    the run past a number nobody set."""
    inner = _UnboundedBackend()
    capped = SpendCappedBackend(inner, SpendBudget())

    _elapsed, errors = _fan_out(capped, 3)

    assert errors == []
    assert inner.peak == 3


def test_the_gateway_plans_the_call_it_will_actually_make(tmp_path: Any) -> None:
    """The ceiling reserves from `planned_call`, so it must say what `complete` will send: the
    default model when none was asked for, and the completion ceiling when no bound was given."""
    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway

    settings = Settings(
        CHIMERA_HOME=str(tmp_path),
        CHIMERA_DEFAULT_MODEL="testvendor/fixed-price-model",
        CHIMERA_COMPLETION_CEILING=1234,
    )
    gateway = LLMGateway(settings=settings)

    assert gateway.planned_call() == ("testvendor/fixed-price-model", 1234)
    assert gateway.planned_call("testvendor/other", 50) == ("testvendor/other", 50)
    assert gateway.planned_call(None, None)[1] == gateway._bounded(None, MODEL)


#: A free primary and a paid fallback. Each answer costs exactly $1 (1M output tokens at $1/M).
FREE = "testvendor/cheap:free"
PAID = "testvendor/fixed-price-model"


class _FallsBackToPaid(_SlowBackend):
    """Asks a ``:free`` model first and answers on a $1 fallback, as ``LLMGateway.complete`` does
    when the primary is rate-limited. Lists its whole chain, the way the gateway now does."""

    def planned_calls(
        self, model: str | None = None, max_tokens: int | None = None
    ) -> list[tuple[str, int | None]]:
        return [(FREE, max_tokens), (PAID, max_tokens)]

    def planned_call(self, model: str | None = None, max_tokens: int | None = None) -> tuple[str, int | None]:
        return FREE, max_tokens

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        result = super().complete(messages, **kwargs)
        result.model = PAID
        return result


class _NamesOnlyAFreePrimary(_FallsBackToPaid):
    """The same backend without ``planned_calls``: it can name the model it asks first, not the one
    that will answer."""

    def __getattribute__(self, name: str) -> Any:
        if name == "planned_calls":
            raise AttributeError(name)
        return super().__getattribute__(name)


def _fan_out_free(capped: SpendCappedBackend, n: int) -> list[BaseException]:
    errors: list[BaseException] = []

    def one() -> None:
        try:
            capped.complete([Message(role="user", content="x")], model=FREE, max_tokens=1_000_000)
        except BaseException as exc:  # collected, so the assertion can say which
            errors.append(exc)

    threads = [threading.Thread(target=one) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def test_a_free_primary_with_a_paid_fallback_reserves_the_fallbacks_price() -> None:
    """Reserving for the primary alone priced a ``:free`` slug at $0, reserved nothing, and let all
    ten threads in: $10 spent against a $1 ceiling. The serial lock admitted one."""
    budget = SpendBudget(max_usd=1.0)
    capped = SpendCappedBackend(_FallsBackToPaid(), budget)

    errors = _fan_out_free(capped, 10)

    refused = [e for e in errors if isinstance(e, SpendExceeded)]
    assert len(refused) == 9, errors
    assert budget.spent == pytest.approx(1.0)


def test_a_backend_that_names_only_a_free_primary_is_queued_not_trusted() -> None:
    """A $0 primary from a backend that cannot list its fallbacks says nothing about what will
    answer, so the call is treated as unpriceable and runs alone, as before the reservation."""
    inner = _NamesOnlyAFreePrimary()
    budget = SpendBudget(max_usd=1.0)
    capped = SpendCappedBackend(inner, budget)

    errors = _fan_out_free(capped, 10)

    assert inner.peak == 1
    assert len([e for e in errors if isinstance(e, SpendExceeded)]) == 9, errors
    assert budget.spent == pytest.approx(1.0)


def test_the_gateway_plans_every_model_in_its_fallback_chain(tmp_path: Any) -> None:
    """``planned_calls`` must walk what ``complete`` walks, each leg with its own bound."""
    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway

    settings = Settings(
        CHIMERA_HOME=str(tmp_path),
        CHIMERA_DEFAULT_MODEL=FREE,
        CHIMERA_FALLBACK_MODELS=f"{PAID},{FREE}",
        CHIMERA_COMPLETION_CEILING=1234,
    )
    gateway = LLMGateway(settings=settings)

    legs = gateway.planned_calls()
    assert [m for m, _ in legs] == gateway._model_candidates(FREE) == [FREE, PAID]
    assert legs == [(m, gateway._bounded(None, m)) for m in (FREE, PAID)]
    assert gateway.planned_calls(None, 50) == [(FREE, 50), (PAID, 50)]


class _Refuses(_SlowBackend):
    def __init__(self, exc: BaseException) -> None:
        super().__init__()
        self.exc = exc

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        raise self.exc


class ContextWindowExceededError(Exception):
    """Named like LiteLLM's class, which is what ``failover.classify`` reads."""


def _never_left(kind: str) -> BaseException:
    from chimera.providers.gateway import MissingCredentialsError

    if kind == "missing-credentials":
        return MissingCredentialsError("No provider key configured")
    if kind == "context-overflow":
        return ContextWindowExceededError("maximum context length is 8192 tokens")
    return SpendExceeded("a nested ceiling refused first")


@pytest.mark.parametrize("kind", ["missing-credentials", "context-overflow", "nested-ceiling"])
def test_an_error_raised_before_the_request_left_is_charged_nothing(kind: str) -> None:
    """Forfeiting every exception made an agent that retried after a context overflow reach its
    typed ceiling having spent $0: each refusal charged the whole completion bound."""
    error = _never_left(kind)
    budget = SpendBudget(max_usd=10.0)
    capped = SpendCappedBackend(_Refuses(error), budget)

    with pytest.raises(type(error)):
        capped.complete([Message(role="user", content="x")], model=MODEL, max_tokens=500_000)

    assert budget.spent == 0.0
    assert budget.reserved == 0.0
    assert budget.estimated is False


def test_the_refusal_says_when_the_total_includes_failed_calls() -> None:
    """``estimated`` had no reader: a run stopped mostly by forfeited reservations reported a spend
    that looked measured. The refusal sentence is where a capped run shows its total."""
    budget = SpendBudget(max_usd=0.4)
    capped = SpendCappedBackend(_SlowBackend(fail=True), budget)

    with pytest.raises(TimeoutError):
        capped.complete([Message(role="user", content="x")], model=MODEL, max_tokens=500_000)
    with pytest.raises(SpendExceeded) as refused:
        capped.complete([Message(role="user", content="x")], model=MODEL, max_tokens=10)

    assert "estimated" in str(refused.value)
    assert "failed" in str(refused.value)


def test_a_measured_total_does_not_say_estimated() -> None:
    budget = SpendBudget(max_usd=0.2)
    capped = SpendCappedBackend(_SlowBackend(), budget)

    capped.complete([Message(role="user", content="x")], model=MODEL, max_tokens=300_000)
    with pytest.raises(SpendExceeded) as refused:
        capped.complete([Message(role="user", content="x")], model=MODEL, max_tokens=10)

    assert "estimated" not in str(refused.value)
