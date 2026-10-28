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
    assert f"cut off at {propose.MAX_REPLY_TOKENS} tokens" in reask
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


def test_an_unknown_model_gets_the_full_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = ScriptedBackend(CompletionResult(content=_valid_reply(), model="fake"))
    monkeypatch.setattr(chimera.providers.catalog, "max_output_for", lambda slug: None)

    propose.propose(backend, _doc(), "x", model="openrouter/acme/model-x")

    assert backend.calls[0]["max_tokens"] == propose.MAX_REPLY_TOKENS == 16_000


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
