"""Tests for the fusion engine and the cost-aware router (no network)."""

from __future__ import annotations

from typing import Any

from chimera.fusion import FusionConfig, FusionEngine, RoutedBackend, RoutingPolicy
from chimera.providers import CompletionResult

CONFIG = FusionConfig(panel=["m1", "m2"], judge="judge", synthesizer="synth")


class FakeBackend:
    """Returns model-dependent content; can be told to fail for some models."""

    def __init__(self, fail_models: set[str] | None = None) -> None:
        self.fail = fail_models or set()
        self.calls: list[str | None] = []

    def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
        self.calls.append(model)
        if model in self.fail:
            raise RuntimeError(f"{model} boom")
        if model == "judge":
            return CompletionResult(content="JUDGE", model="judge")
        if model == "synth":
            return CompletionResult(content="FINAL", model="synth")
        return CompletionResult(content=f"panel:{model}", model=str(model))


def test_fusion_runs_full_pipeline() -> None:
    trace = FusionEngine(FakeBackend(), CONFIG).run([{"role": "user", "content": "hi"}])
    assert [r.content for r in trace.panel] == ["panel:m1", "panel:m2"]
    assert trace.judge_analysis == "JUDGE"
    assert trace.final == "FINAL"


def test_fusion_complete_returns_final() -> None:
    result = FusionEngine(FakeBackend(), CONFIG).complete([{"role": "user", "content": "hi"}])
    assert result.content == "FINAL"
    assert result.model == "fusion"


def test_fusion_tolerates_one_panel_failure() -> None:
    trace = FusionEngine(FakeBackend({"m2"}), CONFIG).run([{"role": "user", "content": "hi"}])
    errored = [r for r in trace.panel if r.error]
    assert len(errored) == 1 and errored[0].model == "m2"
    assert len(trace.successful_panel()) == 1
    assert trace.final == "FINAL"  # judge + synth still ran


def test_fusion_all_panel_fail() -> None:
    trace = FusionEngine(FakeBackend({"m1", "m2"}), CONFIG).run([{"role": "user", "content": "hi"}])
    assert "No panel answers" in trace.judge_analysis
    assert trace.final == "FINAL"


class StubBackend:
    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.called = False

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.called = True
        return CompletionResult(content=self.tag, model=self.tag)


def test_policy_modes() -> None:
    msg = [{"role": "user", "content": "x"}]
    assert RoutingPolicy(mode="always").should_fuse(msg) is True
    assert RoutingPolicy(mode="never").should_fuse([{"role": "user", "content": "x" * 1000}]) is False


def test_policy_auto_heuristics() -> None:
    policy = RoutingPolicy(mode="auto", min_chars=50)
    assert policy.should_fuse([{"role": "user", "content": "short and simple"}]) is False
    assert policy.should_fuse([{"role": "user", "content": "please compare X and Y"}]) is True
    assert policy.should_fuse([{"role": "user", "content": "a" * 60}]) is True


def test_policy_fuses_short_error_sensitive_tasks() -> None:
    policy = RoutingPolicy(mode="auto")  # default min_chars=280
    for prompt in (
        "The number is 68. Multiply it by 4.",  # precision verb
        "How many r's are in 'strawberry'?",  # exact count
        "Compute 7 * 11.",  # arithmetic expression
        "Reverse the digits of 58.",  # exact transformation
        "Calcule 29 vezes 11.",  # PT-BR
    ):
        assert policy.should_fuse([{"role": "user", "content": prompt}]) is True, prompt
    for casual in ("hey, how are you today?", "thanks, that's great!"):
        assert policy.should_fuse([{"role": "user", "content": casual}]) is False, casual


def test_error_sensitive_routing_can_be_disabled() -> None:
    policy = RoutingPolicy(mode="auto", fuse_error_sensitive=False)
    prompt = "The number is 68. Multiply it by 4."
    assert policy.should_fuse([{"role": "user", "content": prompt}]) is False


def test_precision_routing_is_multilingual() -> None:
    policy = RoutingPolicy(mode="auto")  # default min_chars=280
    for prompt in (
        "How many digits are in 8579?",  # en
        "Quantos dígitos tem o número 8579?",  # pt
        "¿Cuántos dígitos hay en 8579?",  # es
        "Wie viele Ziffern hat 8579?",  # de
        "Combien de chiffres dans 8579 ?",  # fr
        "计算 8579 各位数字之和。",  # zh
        "8579 の桁数はいくつですか？",  # ja
    ):
        assert policy.should_fuse([{"role": "user", "content": prompt}]) is True, prompt


def test_routed_tool_turn_goes_single() -> None:
    single, fusion = StubBackend("single"), StubBackend("fusion")
    rb = RoutedBackend(single, fusion, RoutingPolicy(mode="always"))
    result = rb.complete([{"role": "user", "content": "x"}], tools=[{"type": "function"}])
    assert result.content == "single"
    assert single.called and not fusion.called


def test_routed_reasoning_turn_fuses() -> None:
    single, fusion = StubBackend("single"), StubBackend("fusion")
    rb = RoutedBackend(single, fusion, RoutingPolicy(mode="always"))
    assert rb.complete([{"role": "user", "content": "x"}]).content == "fusion"


def test_routed_simple_turn_single() -> None:
    single, fusion = StubBackend("single"), StubBackend("fusion")
    rb = RoutedBackend(single, fusion, RoutingPolicy(mode="never"))
    assert rb.complete([{"role": "user", "content": "x"}]).content == "single"
