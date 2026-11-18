"""A proposal reply cut off at the output budget says so, and asks for less (`chimera.design.propose`).

The live failure of 0.79.0 was a reply that ran out of tokens mid-object and surfaced as "the reply
did not contain a JSON object" after two minutes. These tests pin the three things that changed:
a cut-off reply is named as cut off (by ``finish_reason`` or by an object that never closes), the
re-ask asks for at most ``MAX_RETRY_OPERATIONS``, and the budget and JSON mode are chosen from the
model that will actually be called.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

import chimera.config
import chimera.providers.catalog
from chimera.design import propose
from chimera.design.model import DesignDocument
from chimera.providers.gateway import CompletionResult


class ScriptedBackend:
    """Replies in order, recording every call's messages and keyword arguments."""

    def __init__(self, *replies: CompletionResult) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.calls.append({"messages": [dict(m) for m in messages], **kwargs})
        return self.replies.pop(0)


def _doc() -> DesignDocument:
    return DesignDocument.model_validate({"id": "d1", "title": "Fornalha"})


def _valid_reply() -> str:
    return json.dumps({"summary": "ok", "operations": [
        {"type": "page.add", "page": {"id": "home", "name": "Home", "artboards": []}},
    ]})


def test_a_reply_cut_off_at_the_budget_is_named_and_re_asked_for_fewer_operations() -> None:
    cut = CompletionResult(content='{"summary": "ok", "operations": [{"type": "page.add"',
                           model="fake", finish_reason="length")
    backend = ScriptedBackend(cut, CompletionResult(content=_valid_reply(), model="fake"))

    outcome = propose.propose(backend, _doc(), "uma landing page", model="openrouter/acme/model-x")

    assert outcome.ok, outcome.error
    assert outcome.attempts == 2
    reask = backend.calls[1]["messages"][-1]["content"]
    # An unconfirmed model gets the default, and the message names the budget it was actually given.
    assert f"cut off at {propose.DEFAULT_REPLY_TOKENS} tokens" in reask
    assert f"send at most {propose.MAX_RETRY_OPERATIONS} operations" in reask


def test_an_unclosed_object_is_a_cut_off_even_when_the_provider_reports_no_reason() -> None:
    cut = CompletionResult(content='{"summary": "a", "operations": [', model="fake", finish_reason="")
    backend = ScriptedBackend(cut, cut)

    outcome = propose.propose(backend, _doc(), "uma landing page", model="openrouter/acme/model-x")

    assert not outcome.ok
    assert outcome.error is not None
    assert "was cut off" in outcome.error
    assert "did not contain a JSON object" not in outcome.error


def test_a_reply_with_no_object_and_no_cut_off_keeps_the_plain_message() -> None:
    prose = CompletionResult(content="Here is my plan, no JSON at all.", model="fake", finish_reason="stop")
    backend = ScriptedBackend(prose, prose)

    outcome = propose.propose(backend, _doc(), "uma landing page", model="openrouter/acme/model-x")

    assert outcome.error == "the reply did not contain a JSON object"


def test_the_budget_is_sent_and_lowered_to_what_the_catalogue_says_the_model_serves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = ScriptedBackend(CompletionResult(content=_valid_reply(), model="fake"))
    monkeypatch.setattr(chimera.providers.catalog, "max_output_for", lambda slug: 4_000)

    propose.propose(backend, _doc(), "x", model="openrouter/acme/model-x")

    assert backend.calls[0]["max_tokens"] == 4_000


def test_an_unknown_model_gets_the_default_budget_not_the_larger_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This test used to assert the larger budget for an unknown model, on the premise that a bigger
    # ceiling was harmless. It is not: OpenRouter reads max_tokens as a route filter (incident #639),
    # so the larger number excludes routes. Only a catalogue-confirmed model may take it.
    backend = ScriptedBackend(CompletionResult(content=_valid_reply(), model="fake"))
    monkeypatch.setattr(chimera.providers.catalog, "max_output_for", lambda slug: None)

    propose.propose(backend, _doc(), "x", model="openrouter/acme/model-x")

    assert backend.calls[0]["max_tokens"] == propose.DEFAULT_REPLY_TOKENS == 8_000


def test_a_model_the_catalogue_confirms_serves_the_larger_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = ScriptedBackend(CompletionResult(content=_valid_reply(), model="fake"))
    monkeypatch.setattr(chimera.providers.catalog, "max_output_for", lambda slug: 32_000)

    propose.propose(backend, _doc(), "x", model="openrouter/acme/model-x")

    assert backend.calls[0]["max_tokens"] == propose.MAX_REPLY_TOKENS == 16_000


def test_a_model_between_the_two_budgets_stays_at_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chimera.providers.catalog, "max_output_for", lambda slug: 12_000)

    assert propose._reply_budget("openrouter/acme/model-x") == propose.DEFAULT_REPLY_TOKENS


def test_the_default_model_is_resolved_when_none_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    # The live turn passes model=None; the budget and JSON mode must be decided for the default.
    monkeypatch.setattr(chimera.config, "get_settings",
                        lambda: SimpleNamespace(default_model="openrouter/acme/model-x#high"))

    assert propose._effective_model(None) == "openrouter/acme/model-x"
    assert propose._effective_model("openrouter/other/m") == "openrouter/other/m"


def test_json_object_mode_is_requested_only_where_the_provider_lists_it() -> None:
    assert propose._json_object_mode("openai/gpt-4o-mini") == {"response_format": {"type": "json_object"}}
    assert propose._json_object_mode(None) == {}


def test_json_object_mode_is_sent_on_the_call_when_the_model_lists_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = ScriptedBackend(CompletionResult(content=_valid_reply(), model="fake"))
    monkeypatch.setattr(propose, "_json_object_mode", lambda model: {"response_format": {"type": "json_object"}})

    propose.propose(backend, _doc(), "x", model="openai/gpt-4o-mini")

    assert backend.calls[0]["response_format"] == {"type": "json_object"}


def test_json_object_mode_is_withheld_from_a_model_whose_provider_does_not_list_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The blocking gateway call sends every keyword on and does not set drop_params, so asking a
    # provider that does not list the parameter would fail the whole proposal. Withheld, the call
    # still goes out and only the reply's format is left to the prompt.
    import litellm

    monkeypatch.setattr(litellm, "get_supported_openai_params",
                        lambda **_kwargs: ["temperature", "max_tokens"])

    assert propose._json_object_mode("openrouter/acme/model-x") == {}


@pytest.mark.parametrize(
    ("path", "effort"),
    [("batch", None), ("stream", None), ("stream-to-batch", None), ("batch", "high"), ("stream", "low")],
)
def test_deepseek_generation_reserves_the_reply_for_validated_design(
    monkeypatch: pytest.MonkeyPatch, path: str, effort: str | None,
) -> None:
    import litellm

    from chimera.orchestration.metering import MeteredBackend
    from chimera.providers.gateway import LLMGateway

    slug = "openrouter/deepseek/deepseek-v4-flash-0731"
    requested = f"{slug}#{effort}" if effort else slug
    reasoning = {"effort": effort} if effort else {"enabled": False}
    monkeypatch.setenv("CHIMERA_DEFAULT_MODEL", requested)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-" + "test")
    monkeypatch.setattr(litellm, "get_supported_openai_params", lambda **_: ["response_format"])
    chimera.config.get_settings.cache_clear()

    sent: list[dict[str, Any]] = []

    def provider(**kwargs: Any) -> Any:
        sent.append(kwargs)
        streamed = kwargs.get("stream", False)
        if streamed and path == "stream-to-batch":
            raise RuntimeError("503 provider overloaded")
        fits = (
            "response_format" not in kwargs
            and kwargs.get("extra_body", {}).get("reasoning") == reasoning
        )
        content = _valid_reply() if fits else None
        finish = "stop" if fits else "length"
        usage = SimpleNamespace(prompt_tokens=400, completion_tokens=2169 if fits else 8000)
        message = SimpleNamespace(content=content, tool_calls=None, reasoning_content="private draft")
        if streamed:
            return [SimpleNamespace(choices=[SimpleNamespace(delta=message, finish_reason=finish)], usage=usage)]
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)], usage=usage)

    monkeypatch.setattr(litellm, "completion", provider)
    meter = MeteredBackend(LLMGateway(), label="design")
    doc = _doc()
    shown: list[str] = []
    outcome = propose.propose(meter, doc, "make an artboard", on_delta=None if path == "batch" else shown.append)

    assert outcome.ok, outcome.error
    assert outcome.attempts == meter.calls == 1
    assert outcome.preview is not None and outcome.preview.pages[0].name == "Home"
    assert not doc.pages
    assert meter.last_model == requested
    assert (meter.prompt_tokens, meter.completion_tokens) == (400, 2169)
    assert [call.get("stream", False) for call in sent] == {
        "batch": [False], "stream": [True], "stream-to-batch": [True, False],
    }[path]
    for call in sent:
        assert call["model"] == slug
        assert "response_format" not in call
        assert call["max_tokens"] == 8_000
        assert call["extra_body"]["reasoning"] == reasoning
    assert "".join(shown) == (_valid_reply() if path == "stream" else "")


def test_a_truncated_deepseek_fallback_names_its_budget_and_reasks_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import litellm

    from chimera.orchestration.metering import MeteredBackend
    from chimera.providers.gateway import LLMGateway

    primary = "openrouter/mistralai/mistral-small-3.2-24b-instruct"
    fallback = "openrouter/deepseek/deepseek-v4-flash-0731"
    monkeypatch.setenv("CHIMERA_FALLBACK_MODELS", fallback)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-" + "test")
    chimera.config.get_settings.cache_clear()
    seen: list[tuple[str, int]] = []

    def provider(**kwargs: Any) -> SimpleNamespace:
        seen.append((kwargs["model"], kwargs["max_tokens"]))
        if kwargs["model"] == primary:
            raise RuntimeError("primary unavailable")
        message = SimpleNamespace(content=None, reasoning_content="private draft", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="length")], usage=None)

    monkeypatch.setattr(litellm, "completion", provider)
    meter = MeteredBackend(LLMGateway(), label="design")

    outcome = propose.propose(meter, _doc(), "x", model=primary)

    assert not outcome.ok
    assert outcome.attempts == meter.calls == 2
    assert outcome.error is not None and "cut off at 8000 tokens" in outcome.error
    assert seen == [(primary, 16_000), (fallback, 8_000)] * 2


def test_deepseek_refinement_and_audit_keep_the_existing_request_format(monkeypatch: pytest.MonkeyPatch) -> None:
    import litellm

    from chimera.providers.gateway import LLMGateway

    slug = "openrouter/deepseek/deepseek-v4-flash-0731"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-" + "test")
    monkeypatch.setattr(litellm, "get_supported_openai_params", lambda **_: ["response_format"])
    sent: list[dict[str, Any]] = []

    def provider(**kwargs: Any) -> Any:
        sent.append(kwargs)
        content = _valid_reply() if len(sent) == 1 else '{"issues":[]}'
        message = SimpleNamespace(content=content, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)

    monkeypatch.setattr(litellm, "completion", provider)
    gateway = LLMGateway()
    assert propose.refine(gateway, _doc(), "change it", model=slug).ok
    assert propose.audit(gateway, _doc(), "review it", model=slug).audit is not None
    assert len(sent) == 2
    for call in sent:
        assert call["response_format"] == {"type": "json_object"}
        assert "reasoning" not in call.get("extra_body", {})
