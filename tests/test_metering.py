"""What the parts that are not the worker cost — and the ways that number can lie.

A receipt priced one thing: the worker's loop. The planner, the manager reviewing each attempt, the
checklist, the strong verifier — all call `backend.complete` directly and were priced nowhere. That
is a *directional* error: a model-per-role profile exists to put a stronger model on planning and
review, so the more a profile spends on what distinguishes it, the more of that spend was invisible.
The panel built to judge expensive profiles omitted their expense.

Each test below pins one way the replacement could still mislead.
"""

from __future__ import annotations

from typing import Any

from chimera.orchestration.metering import MeteredBackend, add_usd
from chimera.providers import CompletionResult


class _Backend:
    """A backend that answers with fixed usage, on a model with a KNOWN price."""

    def __init__(self, model: str = "openrouter/deepseek/deepseek-chat-v3.1") -> None:
        self.model = model
        self.calls = 0

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            content="ok", model=self.model, prompt_tokens=1000, completion_tokens=100
        )


def test_an_idle_meter_reports_zero_not_unknown() -> None:
    """"Nothing was spent" is a fact; None means "we do not know". Confusing them here would poison
    every attempt of every run that happens not to use a planner — turning a fully-known cost into
    an unknown one, which is the same dishonesty as the undercount pointing the other way."""
    meter = MeteredBackend(_Backend())
    assert meter.usd == 0.0
    assert meter.take() == (0.0, 0, 0)


def test_it_counts_tokens_and_prices_them() -> None:
    meter = MeteredBackend(_Backend())
    meter.complete([{"role": "user", "content": "x"}])
    meter.complete([{"role": "user", "content": "y"}])

    assert meter.calls == 2
    assert meter.prompt_tokens == 2000 and meter.completion_tokens == 200
    assert meter.usd is not None and meter.usd > 0


def test_one_unpriced_call_makes_the_whole_total_unknown() -> None:
    """All-or-nothing, for the same reason as the receipt: a partial sum is confidently too low, and
    too low in the direction that flatters whichever configuration used an unpriced model."""
    meter = MeteredBackend(_Backend())
    meter.complete([{"role": "user", "content": "x"}])
    meter.inner.model = "vendor/model-nobody-has-priced"
    meter.complete([{"role": "user", "content": "y"}])

    assert meter.usd is None


def test_take_resets_so_a_retry_attributes_to_the_right_attempt() -> None:
    """Without the reset, attempt 2 would carry attempt 1's review as well — and the per-attempt
    column would climb monotonically for reasons that have nothing to do with attempt 2."""
    meter = MeteredBackend(_Backend())
    meter.complete([{"role": "user", "content": "x"}])
    first, _, _ = meter.take()
    meter.complete([{"role": "user", "content": "y"}])
    second, _, _ = meter.take()

    assert first == second and first is not None and first > 0
    assert meter.take() == (0.0, 0, 0)


def test_it_prices_the_model_that_ANSWERED_not_the_one_requested() -> None:
    """A fusion panel, a cascade or a failover can answer on a different model than the caller
    named. Pricing the requested one would invent a number for a call that never happened."""
    meter = MeteredBackend(_Backend(model="openrouter/deepseek/deepseek-chat-v3.1"))
    meter.complete([{"role": "user", "content": "x"}], model="vendor/never-ran")

    assert meter.usd is not None and meter.usd > 0  # priced off the answering model


def test_the_result_object_passes_through_untouched() -> None:
    """A wrapper that normalised or re-wrapped the result would be a second place for behaviour to
    diverge — and the reason this class exists is that a second place already did."""
    backend = _Backend()
    meter = MeteredBackend(backend)
    result = meter.complete([{"role": "user", "content": "x"}])

    assert isinstance(result, CompletionResult) and result.content == "ok"
    assert backend.calls == 1


def test_it_does_not_advertise_streaming_a_backend_does_not_have() -> None:
    """`Agent._step` decides whether to stream with `hasattr(backend, "stream_complete")`. Declaring
    it as a method would answer True for every backend, so wrapping a non-streaming one would make
    it LOOK streamable and then fail at the call."""
    assert not hasattr(MeteredBackend(_Backend()), "stream_complete")


def test_it_meters_streaming_when_the_backend_does_have_it() -> None:
    class _Streaming(_Backend):
        def stream_complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
            return self.complete(messages, **kwargs)

    meter = MeteredBackend(_Streaming())
    assert hasattr(meter, "stream_complete")
    meter.stream_complete([{"role": "user", "content": "x"}])
    assert meter.prompt_tokens == 1000


def test_other_attributes_pass_through() -> None:
    """Wrapping must not silently remove capabilities callers duck-type for."""

    class _WithExtras(_Backend):
        def is_isolated(self) -> bool:
            return True

    assert MeteredBackend(_WithExtras()).is_isolated() is True


def test_add_usd_keeps_unknown_poisonous() -> None:
    """`None + 0.02` is not `0.02`. Treating it as such is how a receipt reports the cost of the
    legs it happened to price as though it were the cost of the run."""
    assert add_usd(0.01, 0.02) == 0.03
    assert add_usd(0.01, None) is None
    assert add_usd(None, None) is None
    assert add_usd() is None


def test_the_loop_adds_overhead_to_the_worker_cost(tmp_path: Any) -> None:
    """The whole point, asserted end to end: an attempt's `usd` is worker + everything else, and the
    non-worker share is reported separately because it is what a profile actually moves."""
    from chimera.core.agent import AgentResult
    from chimera.core.autonomous import AutonomousAgent, AutonomousConfig

    meter = MeteredBackend(_Backend())

    class _Worker:
        def run(self, task: str, **_: Any) -> AgentResult:
            # The planner/manager share of this attempt, simulated by driving the meter.
            meter.complete([{"role": "user", "content": "plan"}])
            return AgentResult(answer="done", steps=1, stopped_reason="final", usd=0.05)

    auto = AutonomousAgent(
        _Worker(),
        meter=meter,
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )
    result = auto.run("t")

    attempt = result.attempts[0]
    assert attempt.overhead_usd is not None and attempt.overhead_usd > 0
    assert attempt.usd == add_usd(0.05, attempt.overhead_usd)
    assert attempt.usd > 0.05  # the overhead is actually IN the total, not merely recorded


def test_without_a_meter_the_receipt_is_worker_only_as_before(tmp_path: Any) -> None:
    """Every existing caller keeps the behaviour it had — no meter, no overhead column."""
    from chimera.core.agent import AgentResult
    from chimera.core.autonomous import AutonomousAgent, AutonomousConfig

    class _Worker:
        def run(self, task: str, **_: Any) -> AgentResult:
            return AgentResult(answer="done", steps=1, stopped_reason="final", usd=0.05)

    auto = AutonomousAgent(
        _Worker(), config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False)
    )
    attempt = auto.run("t").attempts[0]

    assert attempt.usd == 0.05 and attempt.overhead_usd == 0.0
