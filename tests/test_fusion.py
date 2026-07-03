"""Tests for the fusion engine and the cost-aware router (no network)."""

from __future__ import annotations

from typing import Any

from chimera.fusion import FusionConfig, FusionEngine, PanelResponse, RoutedBackend, RoutingPolicy
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


class TokenBackend:
    """Backend that reports token usage, to exercise per-stage telemetry (#4)."""

    def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
        if model == "judge":
            return CompletionResult(content="JUDGE", model="judge", prompt_tokens=10, completion_tokens=5)
        if model == "synth":
            return CompletionResult(content="FINAL", model="synth", prompt_tokens=8, completion_tokens=4)
        return CompletionResult(
            content=f"panel:{model}", model=str(model), prompt_tokens=6, completion_tokens=3
        )


def test_fusion_captures_stage_tokens() -> None:
    trace = FusionEngine(TokenBackend(), CONFIG).run([{"role": "user", "content": "hi"}])
    assert len(trace.usage) == 4  # 2 panel + judge + synth
    assert trace.prompt_tokens() == 6 + 6 + 10 + 8
    assert trace.completion_tokens() == 3 + 3 + 5 + 4
    assert trace.total_tokens() == (6 + 6 + 10 + 8) + (3 + 3 + 5 + 4)
    assert trace.by_stage()["panel"] == (12, 6)


def test_fusion_complete_propagates_tokens() -> None:
    result = FusionEngine(TokenBackend(), CONFIG).complete([{"role": "user", "content": "hi"}])
    assert result.content == "FINAL" and result.model == "fusion"
    assert result.prompt_tokens == 6 + 6 + 10 + 8
    assert result.completion_tokens == 3 + 3 + 5 + 4


def test_fusion_usage_none_safe() -> None:
    # A backend that reports no usage (the default fake) must not fabricate zeros.
    trace = FusionEngine(FakeBackend(), CONFIG).run([{"role": "user", "content": "hi"}])
    assert trace.total_tokens() is None
    result = FusionEngine(FakeBackend(), CONFIG).complete([{"role": "user", "content": "hi"}])
    assert result.prompt_tokens is None and result.completion_tokens is None


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


SELECTIVE = FusionConfig(
    panel=["m1", "m2", "m3"], judge="judge", synthesizer="synth", mode="selective", probe_k=2
)


class ScriptedBackend:
    """Returns a fixed answer per model, and records which models were called."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.calls: list[str | None] = []

    def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
        self.calls.append(model)
        if model == "synth":
            return CompletionResult(content="FINAL", model="synth")
        if model == "judge":
            return CompletionResult(content="JUDGE", model="judge")
        return CompletionResult(content=self.answers.get(str(model), f"ans:{model}"), model=str(model))


def test_selective_early_stops_on_agreement() -> None:
    # m1 and m2 give the same answer -> probe agrees -> skip m3 and the judge.
    backend = ScriptedBackend({"m1": "the answer is 42", "m2": "the answer is 42"})
    trace = FusionEngine(backend, SELECTIVE).run([{"role": "user", "content": "q"}])
    assert trace.early_stopped is True
    assert "m3" not in backend.calls  # remaining panel skipped
    assert "judge" not in backend.calls  # judge skipped
    assert trace.final == "FINAL"
    assert len(trace.successful_panel()) == 2


def test_selective_escalates_on_disagreement() -> None:
    # m1 and m2 disagree -> escalate: run m3, the judge, and synth (== full pipeline).
    backend = ScriptedBackend(
        {"m1": "the answer is 42", "m2": "completely different unrelated text here"}
    )
    trace = FusionEngine(backend, SELECTIVE).run([{"role": "user", "content": "q"}])
    assert trace.early_stopped is False
    assert "m3" in backend.calls and "judge" in backend.calls
    assert trace.judge_analysis == "JUDGE"
    assert trace.final == "FINAL"
    assert len(trace.panel) == 3


def test_selective_agreement_signal() -> None:
    engine = FusionEngine(ScriptedBackend({}), SELECTIVE)
    same = [PanelResponse(model="a", content="hello world"), PanelResponse(model="b", content="Hello   world")]
    diff = [PanelResponse(model="a", content="hello world"), PanelResponse(model="b", content="xyz abc")]
    assert engine._agree(same) is True
    assert engine._agree(diff) is False


def test_full_mode_is_unchanged_by_selective_code() -> None:
    # A full-mode run must call every panel model, the judge, and synth.
    backend = ScriptedBackend({"m1": "a", "m2": "a", "m3": "a"})
    trace = FusionEngine(
        backend, FusionConfig(panel=["m1", "m2", "m3"], judge="judge", synthesizer="synth")
    ).run([{"role": "user", "content": "q"}])
    assert trace.early_stopped is False
    assert {"m1", "m2", "m3", "judge", "synth"} <= set(backend.calls)


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
        "What is 15% of 80?",  # percentage — an exact numeric answer
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


def test_fuse_reason_attributes_the_trigger() -> None:
    policy = RoutingPolicy(mode="auto")

    def reason(text: str) -> str:
        return policy.fuse_reason([{"role": "user", "content": text}])

    assert reason("a" * 300) == "length"
    assert reason("please analyze the trade-off") == "keyword"
    assert reason("How many digits are in 8579?") == "precision"
    assert reason("What is 7 * 11?") == "arithmetic"
    assert reason("hi there, thanks a lot!") == "none"
    assert RoutingPolicy(mode="always").fuse_reason([{"role": "user", "content": "x"}]) == "mode"


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
