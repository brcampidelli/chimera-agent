"""A reasoning model with no `max_tokens` can spend the provider's ceiling thinking and return
nothing at 200 OK — measured three times on 2026-09-11/12 (the spec-test generator at 131,072 tokens,
the AIME writers, the fusion judge for 52 minutes). Two bounds now: the gateway applies
`Settings.completion_ceiling` to every call whose caller set no budget, and the fusion judge and
synthesiser ask under their own explicit budgets, ask once more on an empty reply, and put the
provider's `finish_reason` on the trace. Fakes only — no network."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from chimera.config import Settings, get_settings
from chimera.fusion import FusionConfig, FusionEngine
from chimera.providers import CompletionResult
from chimera.providers.gateway import Message

PANEL = ["openrouter/vendor-a/big", "openrouter/vendor-b/mid", "openrouter/vendor-c/small"]


# --------------------------------------------------------------------------- the gateway


def _resp(text: str) -> SimpleNamespace:
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)


def _gateway(monkeypatch: pytest.MonkeyPatch, seen: list[dict[str, Any]], **env: str) -> Any:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    import litellm

    def fake(**kwargs: Any) -> SimpleNamespace:
        seen.append(kwargs)
        return _resp("ok")

    monkeypatch.setattr(litellm, "completion", fake)
    from chimera.providers import LLMGateway

    return LLMGateway()


def test_the_ceiling_is_thirty_two_thousand_by_default() -> None:
    assert Settings().completion_ceiling == 32_000


def test_a_call_with_no_budget_gets_the_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    _gateway(monkeypatch, seen).complete([Message(role="user", content="hi")], model="prov/m")
    assert seen[-1]["max_tokens"] == 32_000


def test_a_caller_that_chose_a_budget_keeps_it(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    _gateway(monkeypatch, seen).complete([Message(role="user", content="hi")], model="prov/m", max_tokens=500)
    assert seen[-1]["max_tokens"] == 500


def test_zero_restores_the_providers_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    _gateway(monkeypatch, seen, CHIMERA_COMPLETION_CEILING="0").complete(
        [Message(role="user", content="hi")], model="prov/m"
    )
    assert seen[-1]["max_tokens"] is None


def test_the_ceiling_reaches_the_streaming_path_too(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    gateway = _gateway(monkeypatch, seen, CHIMERA_COMPLETION_CEILING="4000")
    import litellm

    def fake_stream(**kwargs: Any) -> list[SimpleNamespace]:
        seen.append(kwargs)
        delta = SimpleNamespace(content="hi", tool_calls=None)
        return [SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason="stop")], usage=None)]

    monkeypatch.setattr(litellm, "completion", fake_stream)
    gateway.stream_complete([Message(role="user", content="hi")], model="prov/m")
    assert seen[-1]["max_tokens"] == 4000 and seen[-1].get("stream") is True


# --------------------------------------------------------------------------- the fusion stages


class Recording:
    """Answers per model, records every call's kwargs, and can script the judge's first reply."""

    def __init__(self, judge_replies: list[tuple[str, str]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.judge_replies = list(judge_replies or [("JUDGE", "stop")])

    def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
        self.calls.append({"model": model, **kwargs})
        if model == "judge":
            content, finish = self.judge_replies.pop(0) if self.judge_replies else ("JUDGE", "stop")
            return CompletionResult(content=content, model="judge", finish_reason=finish)
        if model == "synth":
            return CompletionResult(content="FINAL", model="synth", finish_reason="stop")
        return CompletionResult(content=f"text by {model}", model=str(model), finish_reason="stop")


def _config(**kw: Any) -> FusionConfig:
    return FusionConfig(panel=list(PANEL), judge="judge", synthesizer="synth", **kw)


def test_the_judge_and_the_synthesiser_ask_under_explicit_budgets() -> None:
    backend = Recording()
    FusionEngine(backend, _config()).run([{"role": "user", "content": "q"}])
    judge = [c for c in backend.calls if c["model"] == "judge"]
    synth = [c for c in backend.calls if c["model"] == "synth"]
    assert judge and all(c["max_tokens"] == 16_000 for c in judge)
    assert synth and all(c["max_tokens"] == 16_000 for c in synth)


def test_the_budgets_are_the_configs() -> None:
    backend = Recording()
    FusionEngine(backend, _config(judge_max_tokens=2_000, synth_max_tokens=3_000)).run(
        [{"role": "user", "content": "q"}]
    )
    assert [c["max_tokens"] for c in backend.calls if c["model"] == "judge"] == [2_000]
    assert [c["max_tokens"] for c in backend.calls if c["model"] == "synth"] == [3_000]


def test_the_trace_and_the_route_meta_carry_the_finish_reasons() -> None:
    backend = Recording()
    engine = FusionEngine(backend, _config())
    trace = engine.run([{"role": "user", "content": "q"}])
    assert trace.finish_reasons == {"judge": "stop", "synth": "stop"}
    result = engine.complete([{"role": "user", "content": "q"}])
    assert result.route_meta is not None and result.route_meta["finish_reasons"] == {"judge": "stop", "synth": "stop"}


def test_an_empty_judge_reply_is_asked_once_more_with_twice_the_budget_after_length() -> None:
    backend = Recording(judge_replies=[("", "length"), ("JUDGE", "stop")])
    trace = FusionEngine(backend, _config()).run([{"role": "user", "content": "q"}])
    judge = [c for c in backend.calls if c["model"] == "judge"]
    assert [c["max_tokens"] for c in judge] == [16_000, 32_000]
    assert trace.judge_analysis == "JUDGE"
    assert trace.finish_reasons == {"judge": "stop", "judge_first": "length", "synth": "stop"}


def test_an_empty_reply_that_simply_stopped_is_asked_again_at_the_same_budget() -> None:
    backend = Recording(judge_replies=[("   ", "stop"), ("JUDGE", "stop")])
    FusionEngine(backend, _config()).run([{"role": "user", "content": "q"}])
    assert [c["max_tokens"] for c in backend.calls if c["model"] == "judge"] == [16_000, 16_000]


def test_two_empty_replies_leave_the_second_and_no_third_call() -> None:
    backend = Recording(judge_replies=[("", "length"), ("", "length"), ("JUDGE", "stop")])
    trace = FusionEngine(backend, _config()).run([{"role": "user", "content": "q"}])
    assert len([c for c in backend.calls if c["model"] == "judge"]) == 2
    assert trace.judge_analysis == "" and trace.finish_reasons["judge"] == "length"


def test_a_good_first_reply_is_never_asked_twice() -> None:
    backend = Recording(judge_replies=[("JUDGE", "stop"), ("SECOND", "stop")])
    FusionEngine(backend, _config()).run([{"role": "user", "content": "q"}])
    assert len([c for c in backend.calls if c["model"] == "judge"]) == 1
