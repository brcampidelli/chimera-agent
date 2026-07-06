"""Tests for agreement-based escalation in the router (M14 B2)."""

from __future__ import annotations

from chimera.fusion.router import RoutedBackend, RoutingPolicy
from chimera.providers.gateway import CompletionResult, Message


class _ScriptedSingle:
    """Single model: returns queued contents in order; records call count."""

    def __init__(self, contents: list[str]) -> None:
        self.contents = list(contents)
        self.calls = 0

    def complete(self, messages: object, **kwargs: object) -> CompletionResult:
        self.calls += 1
        content = self.contents.pop(0) if self.contents else "(none)"
        return CompletionResult(content=content, model="single", prompt_tokens=2, completion_tokens=3)


class _Fusion:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: object, **kwargs: object) -> CompletionResult:
        self.calls += 1
        return CompletionResult(content="FUSED", model="fusion")


def _msgs() -> list[object]:
    return [Message(role="user", content="short cheap task")]


def test_agreement_takes_consensus_without_fusion() -> None:
    single = _ScriptedSingle(["42", "42", "42"])
    fusion = _Fusion()
    backend = RoutedBackend(single, fusion, agreement_k=3)
    result = backend.complete(_msgs())
    assert result.content == "42" and fusion.calls == 0  # they agreed → cheap path
    assert single.calls == 3  # sampled K
    assert result.prompt_tokens == 6  # tokens summed across the K samples


def test_disagreement_escalates_to_fusion() -> None:
    single = _ScriptedSingle(["alpha", "beta", "gamma"])
    fusion = _Fusion()
    backend = RoutedBackend(single, fusion, agreement_k=3)
    result = backend.complete(_msgs())
    assert result.content == "FUSED" and fusion.calls == 1  # disagreement → fusion
    assert single.calls == 3


def test_k1_keeps_original_single_path() -> None:
    single = _ScriptedSingle(["once"])
    fusion = _Fusion()
    result = RoutedBackend(single, fusion, agreement_k=1).complete(_msgs())
    assert result.content == "once" and single.calls == 1 and fusion.calls == 0


def test_tool_turn_never_samples() -> None:
    single = _ScriptedSingle(["tool-answer"])
    fusion = _Fusion()
    backend = RoutedBackend(single, fusion, agreement_k=3)
    backend.complete(_msgs(), tools=[{"type": "function"}])
    assert single.calls == 1 and fusion.calls == 0  # tool turns bypass agreement + fusion


def test_apriori_fuse_skips_agreement() -> None:
    # A keyword-routed turn goes straight to fusion — no cheap sampling first.
    single = _ScriptedSingle(["x", "y", "z"])
    fusion = _Fusion()
    backend = RoutedBackend(single, fusion, RoutingPolicy(mode="always"), agreement_k=3)
    result = backend.complete(_msgs())
    assert result.content == "FUSED" and fusion.calls == 1 and single.calls == 0
