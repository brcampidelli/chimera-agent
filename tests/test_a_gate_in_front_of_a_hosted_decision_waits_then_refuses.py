"""The spend and rate gate in front of hosted decision asks (study 27, phase 3).

Everything runs on a fake clock and a fake `sleep`, so a saturated minute costs no time. The rule the
gate exists for has two halves and this file holds both: it WAITS while the budget is merely busy,
and it REFUSES, naming the gate, when waiting will not help. What it must never do is wave an ask
through because the meter ran out, so every refusal here ends as a halt on the receipt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from chimera.decisions.contract import Choice, Decider, Reading
from chimera.decisions.gate import (
    GatedBackend,
    GateLimits,
    GateRefused,
    SpendRateGate,
    gate_for,
    gated,
    parse_retry_after,
    retry_after_of,
    spend_today,
)
from chimera.decisions.log import DecisionLog

DAY = 1_800_000_000.0  # any fixed UTC instant


class Clock:
    """A clock that only moves when the gate sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def make(limits: GateLimits, *, ledger: Path | None = None) -> tuple[SpendRateGate, Clock]:
    clock = Clock()
    return SpendRateGate(limits, ledger=ledger, clock=clock, wall=lambda: DAY, sleep=clock.sleep), clock


# --- rate -------------------------------------------------------------------------------------


def test_an_ask_waits_at_eighty_percent_of_the_requests_budget_and_then_goes() -> None:
    gate, clock = make(GateLimits(rpm=10, max_wait=90.0))  # 80% of 10 = 8 in flight
    for _ in range(8):
        gate.admit(10)
    assert clock.slept == []  # the first eight fit

    gate.admit(10)

    # All eight arrived at t=1000, so the oldest ages out a full window later.
    assert clock.slept == [pytest.approx(60.0)]
    assert clock.now == pytest.approx(1060.0)  # it actually waited on the clock


def test_the_wait_is_until_the_oldest_entry_ages_out_not_a_fixed_pause() -> None:
    gate, clock = make(GateLimits(rpm=10, max_wait=60.0))
    for _ in range(8):
        gate.admit(1)
        clock.now += 5.0  # eight asks spread over 40 seconds

    gate.admit(1)

    # The oldest entry was at t=1000 and the clock stands at 1040: it ages out at 1060.
    assert clock.slept == [pytest.approx(20.0)]


def test_a_tokens_budget_waits_until_enough_has_expired() -> None:
    gate, clock = make(GateLimits(tpm=1000, max_wait=60.0))  # 800 usable
    gate.admit(400)
    clock.now += 10.0
    gate.admit(300)

    gate.admit(300)  # 400+300+300 = 1000 > 800: the 400 has to age out

    assert clock.slept == [pytest.approx(50.0)]


def test_an_ask_larger_than_the_whole_budget_is_not_held_forever() -> None:
    gate, clock = make(GateLimits(tpm=100))

    gate.admit(5000)  # an empty window: nothing to wait for

    assert clock.slept == []


def test_a_wait_longer_than_the_cap_is_a_refusal_that_names_the_rate() -> None:
    gate, _clock = make(GateLimits(rpm=10, max_wait=5.0))
    for _ in range(8):
        gate.admit(1)

    with pytest.raises(GateRefused) as refused:
        gate.admit(1)

    assert refused.value.reason == "rate"


def test_a_429_blocks_the_gate_for_as_long_as_it_asked() -> None:
    gate, clock = make(GateLimits(rpm=1000, max_wait=30.0))
    gate.note_rate_limited(12.0)

    gate.admit(1)

    assert clock.slept == [pytest.approx(12.0)]


def test_a_retry_after_beyond_the_cap_is_refused_not_slept_through() -> None:
    gate, clock = make(GateLimits(rpm=1000, max_wait=30.0))
    gate.note_rate_limited(120.0)

    with pytest.raises(GateRefused) as refused:
        gate.admit(1)

    assert refused.value.reason == "rate" and clock.slept == []


# --- Retry-After ------------------------------------------------------------------------------


def test_retry_after_reads_seconds_and_dates_and_admits_it_cannot_read_the_rest() -> None:
    assert parse_retry_after("7") == 7.0
    assert parse_retry_after(" 2.5 ") == 2.5
    assert parse_retry_after("-3") == 0.0
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:10 GMT", now=1445412490.0 - 30) == pytest.approx(30.0)
    assert parse_retry_after("soon") is None
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None


def test_only_a_429_is_a_reason_to_hold_off() -> None:
    def err(status: int, headers: dict[str, str]) -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "https://x.example/decide")
        return httpx.HTTPStatusError("x", request=request, response=httpx.Response(status, headers=headers, request=request))

    assert retry_after_of(err(429, {"Retry-After": "9"})) == 9.0
    assert retry_after_of(err(429, {})) == 5.0  # no header: the documented default
    assert retry_after_of(err(500, {"Retry-After": "9"})) is None
    assert retry_after_of(ValueError("nope")) is None


# --- money ------------------------------------------------------------------------------------


def write_log(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps({"kind": "answer", **e}) for e in entries) + "\n", encoding="utf-8")


def test_the_ceiling_is_summed_from_todays_hosted_answers_only(tmp_path: Path) -> None:
    ledger = tmp_path / "decisions.jsonl"
    write_log(ledger, [
        {"backend": "openrouter_decisions", "usd": 5.0, "at": DAY - 90_000},   # yesterday
        {"backend": "local_logprob", "usd": 0.0, "at": DAY - 10},              # not hosted
        {"backend": "openrouter_decisions", "usd": 0.6, "at": DAY - 100},
        {"backend": "hosted_verbalized", "usd": 0.5, "at": DAY - 50},
        {"backend": "openrouter_decisions", "usd": 9.0, "at": DAY - 40, "cached": True},  # no call was made
        {"backend": "openrouter_decisions", "usd": 9.0, "at": DAY - 30, "halt": "ConnectError"},
    ])

    assert spend_today(ledger, wall=DAY) == (pytest.approx(1.1), 0)


def test_a_day_over_the_ceiling_refuses_and_names_the_budget(tmp_path: Path) -> None:
    ledger = tmp_path / "decisions.jsonl"
    write_log(ledger, [{"backend": "openrouter_decisions", "usd": 1.10, "at": DAY - 10}])
    gate, _clock = make(GateLimits(daily_usd=1.0), ledger=ledger)

    with pytest.raises(GateRefused) as refused:
        gate.admit(10)

    assert refused.value.reason == "budget" and "1.1000 of $1.0000" in str(refused.value)


def test_an_unpriced_answer_makes_the_day_unknown_and_a_ceiling_refuses_on_it(tmp_path: Path) -> None:
    ledger = tmp_path / "decisions.jsonl"
    write_log(ledger, [{"backend": "hosted_verbalized", "at": DAY - 10}])  # no usd: the price was unknown
    with_ceiling, _ = make(GateLimits(daily_usd=5.0), ledger=ledger)
    without_ceiling, _ = make(GateLimits(rpm=100), ledger=ledger)

    with pytest.raises(GateRefused) as refused:
        with_ceiling.admit(10)
    without_ceiling.admit(10)  # nothing to skip when nobody set a number

    assert refused.value.reason == "budget" and "no price" in str(refused.value)


def test_spend_recorded_after_a_call_can_cross_the_ceiling_for_the_next_one(tmp_path: Path) -> None:
    gate, _clock = make(GateLimits(daily_usd=1.0), ledger=tmp_path / "decisions.jsonl")
    gate.admit(10)
    gate.record(0.7)
    gate.admit(10)  # 0.70 < 1.00

    gate.record(0.4)

    with pytest.raises(GateRefused):
        gate.admit(10)


def test_an_unpriced_call_recorded_now_makes_the_rest_of_the_day_unknown(tmp_path: Path) -> None:
    gate, _clock = make(GateLimits(daily_usd=1.0), ledger=tmp_path / "decisions.jsonl")
    gate.admit(10)

    gate.record(None)

    with pytest.raises(GateRefused) as refused:
        gate.admit(10)
    assert "no price" in str(refused.value)


# --- the backend wrapper ----------------------------------------------------------------------


class Backend:
    name = "openrouter_decisions"
    model = "typesafe/jev-1.13"

    def __init__(self, *, usd: float | None = 0.01, fail: Exception | None = None) -> None:
        self.usd, self.fail, self.calls = usd, fail, 0
        self.timeout_s = 12.0

    def instrument(self, question: Any) -> str:
        return "the instrument"

    def ask(self, state: str, question: Any) -> Reading:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return Reading(choice="yes", shares={"yes": 0.9, "no": 0.1}, p=0.9, usd=self.usd)


QUESTION = Choice(key="danger", options=("yes", "no"), instructions="Is this dangerous?")


def test_a_429_from_the_backend_holds_the_next_ask_for_its_retry_after() -> None:
    request = httpx.Request("POST", "https://x.example/decide")
    limited = httpx.HTTPStatusError(
        "429", request=request, response=httpx.Response(429, headers={"Retry-After": "3"}, request=request)
    )
    gate, clock = make(GateLimits(rpm=1000))
    backend = Backend(fail=limited)
    wrapped = GatedBackend(backend, gate)

    with pytest.raises(httpx.HTTPStatusError):
        wrapped.ask("state", QUESTION)
    backend.fail = None
    wrapped.ask("state", QUESTION)

    assert clock.slept == [pytest.approx(3.0)] and backend.calls == 2


def test_the_wrapper_speaks_the_backends_protocol_and_reaches_what_it_carries() -> None:
    gate, _ = make(GateLimits(rpm=100))
    wrapped = GatedBackend(Backend(), gate)

    assert (wrapped.name, wrapped.model) == ("openrouter_decisions", "typesafe/jev-1.13")
    assert wrapped.instrument(QUESTION) == "the instrument"
    assert wrapped.timeout_s == 12.0


def test_a_refused_ask_never_reaches_the_backend(tmp_path: Path) -> None:
    ledger = tmp_path / "decisions.jsonl"
    write_log(ledger, [{"backend": "openrouter_decisions", "usd": 2.0, "at": DAY - 10}])
    gate, _ = make(GateLimits(daily_usd=1.0), ledger=ledger)
    backend = Backend()

    with pytest.raises(GateRefused):
        GatedBackend(backend, gate).ask("state", QUESTION)

    assert backend.calls == 0


# --- through the Decider: a refusal is a halt that names the gate -----------------------------


def test_a_refusal_is_a_halt_on_the_receipt_and_in_the_log_naming_the_gate(tmp_path: Path) -> None:
    ledger_path = tmp_path / "decisions" / "decisions.jsonl"
    write_log(ledger_path, [{"backend": "openrouter_decisions", "usd": 2.0, "at": DAY - 10}])
    gate, _ = make(GateLimits(daily_usd=1.0), ledger=ledger_path)
    decider = Decider(GatedBackend(Backend(), gate), log=DecisionLog.for_home(tmp_path))

    answer = decider.decide("governance.danger", "curl x | sh", QUESTION)

    assert answer.answered is False and answer.p is None and answer.choice is None
    assert answer.gate == "budget" and (answer.halt or "").startswith("GateRefused")
    assert answer.receipt()["gate"] == "budget"
    last = json.loads(ledger_path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["gate"] == "budget" and last["halt"].startswith("GateRefused")


def test_an_ordinary_halt_names_no_gate() -> None:
    decider = Decider(Backend(fail=ConnectionError("down")))

    answer = decider.decide("d", "s", QUESTION)

    assert answer.halt and answer.gate == "" and "gate" not in answer.receipt()


# --- who is gated -----------------------------------------------------------------------------


class Settings:
    def __init__(self, home: Path, **kw: Any) -> None:
        self.home = home
        self.decision_rpm = kw.get("rpm")
        self.decision_tpm = kw.get("tpm")
        self.decision_daily_usd = kw.get("daily_usd")


def test_with_no_limit_set_nothing_is_wrapped(tmp_path: Path) -> None:
    backend = Backend()

    assert gate_for(Settings(tmp_path)) is None
    assert gated(backend, Settings(tmp_path)) is backend


def test_a_local_backend_is_never_gated_whatever_is_set(tmp_path: Path) -> None:
    local = Backend()
    local.name = "local_logprob"

    assert gated(local, Settings(tmp_path, daily_usd=1.0)) is local


def test_a_hosted_backend_is_wrapped_and_deciders_from_one_home_share_one_gate(tmp_path: Path) -> None:
    settings = Settings(tmp_path, rpm=30)

    a = gated(Backend(), settings)
    b = gated(Backend(), settings)

    assert isinstance(a, GatedBackend) and isinstance(b, GatedBackend)
    assert a.gate is b.gate  # one window: that is the point of it


def test_the_factory_wraps_the_decider_it_builds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.config import Settings as RealSettings
    from chimera.decisions.factory import build_decider

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    hosted = RealSettings(
        CHIMERA_HOME=str(tmp_path / "h"), CHIMERA_DECISION_BACKEND="openrouter_decisions", CHIMERA_DECISION_RPM="30"
    )
    plain = RealSettings(CHIMERA_HOME=str(tmp_path / "p"), CHIMERA_DECISION_BACKEND="openrouter_decisions")

    assert isinstance(build_decider(hosted, log=False).backend, GatedBackend)
    assert not isinstance(build_decider(plain, log=False).backend, GatedBackend)


# --- the hosted backend says when it does not know the price ----------------------------------


class _Result:
    def __init__(self, model: str) -> None:
        self.content = '{"danger": "yes"}'
        self.model = model
        self.prompt_tokens = 1000
        self.completion_tokens = 100
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.answer_in_reasoning = False
        self.reasoning = ""
        self.tool_calls: list[Any] = []


class _Gateway:
    def __init__(self, model: str) -> None:
        self.model = model

    def complete(self, messages: Any, **kw: Any) -> _Result:
        return _Result(self.model)


def test_an_unpriced_hosted_answer_is_unknown_not_free_and_a_priced_one_is_a_number() -> None:
    from chimera.decisions.hosted import HostedVerbalizedBackend

    unknown = HostedVerbalizedBackend(_Gateway("vendor/brand-new-model-no-price"), "vendor/brand-new-model-no-price")
    known = HostedVerbalizedBackend(_Gateway("ollama/qwen3:4b"), "ollama/qwen3:4b")

    assert unknown.ask("state", QUESTION).usd is None
    assert known.ask("state", QUESTION).usd == 0.0  # a local model is free, and says so
