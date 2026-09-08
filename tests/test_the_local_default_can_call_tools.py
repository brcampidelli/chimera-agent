"""The local default can call tools: ``ollama_chat/`` leads the list, and ``ollama/`` says so when
it is handed one.

#384 measured, offline and on every route, that LiteLLM's ``ollama/`` prefix is Ollama's
``/api/generate``: the tool catalogue is pasted into the prompt as a Python repr, the batch route
parses at most one JSON object out of the answer, and in stream the call comes back as the
assistant's TEXT — the loop sees no tool call. ``ollama_chat/`` is ``/api/chat``, native tool
calling, and round-trips. The docs recommended ``ollama/`` and `_LOCAL_MODEL_PREFIXES` listed it
first, so every tool number a user measured through the documented local default was about a
model that had never been offered a tool.

Two things are pinned. The ORDER of `_LOCAL_MODEL_PREFIXES`: the code reads it as a set
(``startswith`` takes the whole tuple), so its order is documentation — and documentation that
names the wrong prefix first is how the docs came to recommend it. And the WARNING: a request that
carries tools on an ``ollama/`` model logs once per gateway, names ``ollama_chat/``, and changes
nothing else — the model id is not rewritten and the call is not refused, because plain text
through ``ollama/`` still works and a slug the user typed is the slug that runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.providers.gateway import (
    _LOCAL_MODEL_PREFIXES,
    LLMGateway,
    Message,
    _is_local_model,
)

_LOGGER = "chimera.providers.gateway"
_TOOLS = [
    {
        "type": "function",
        "function": {"name": "echo", "parameters": {"type": "object", "properties": {}}},
    }
]
_ROUTES = ("batch", "stream", "async")


def test_the_chat_prefix_leads_the_local_list_and_the_generate_prefix_stays_on_it() -> None:
    assert _LOCAL_MODEL_PREFIXES[0] == "ollama_chat/"
    assert "ollama/" in _LOCAL_MODEL_PREFIXES  # still local and keyless; it just cannot call tools
    assert _is_local_model("ollama_chat/llama3") and _is_local_model("ollama/llama3")


@dataclass
class _Wire:
    """What reached LiteLLM, so the test can say the request was sent as given."""

    calls: list[dict[str, Any]] = field(default_factory=list)


def _batch_response(text: str) -> SimpleNamespace:
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def _stream_chunk(text: str) -> SimpleNamespace:
    delta = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> _Wire:
    import litellm

    recorded = _Wire()

    def fake(**kwargs: Any) -> Any:
        recorded.calls.append(kwargs)
        return [_stream_chunk("ok")] if kwargs.get("stream") else _batch_response("ok")

    async def afake(**kwargs: Any) -> Any:
        recorded.calls.append(kwargs)
        return _batch_response("ok")

    monkeypatch.setattr(litellm, "completion", fake)
    monkeypatch.setattr(litellm, "acompletion", afake)
    return recorded


async def _request(
    gateway: LLMGateway, route: str, model: str, tools: list[dict[str, Any]] | None
) -> None:
    messages = [Message(role="user", content="hi")]
    if route == "batch":
        gateway.complete(messages, model=model, tools=tools)
    elif route == "stream":
        gateway.stream_complete(messages, model=model, tools=tools)
    else:
        await gateway.acomplete(messages, model=model, tools=tools)


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.parametrize("route", _ROUTES)
async def test_tools_on_the_generate_prefix_warn_once_per_gateway_and_name_the_chat_prefix(
    route: str, wire: _Wire, caplog: pytest.LogCaptureFixture
) -> None:
    gateway = LLMGateway()
    # `logger=` is load-bearing: the `chimera` logger does not propagate to the root, so a plain
    # `caplog.at_level` would capture nothing and this test would fail for a reason that is not the
    # gateway's (measured before it was written).
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await _request(gateway, route, "ollama/llama3", _TOOLS)
        await _request(gateway, route, "ollama/llama3", _TOOLS)

    said = [m for m in _warnings(caplog) if "ollama_chat/" in m]
    assert len(said) == 1, _warnings(caplog)  # once, not once per step
    assert "ollama/llama3" in said[0] and "tool" in said[0]
    # Warned, and NOTHING else: the slug the user typed is the slug that ran, tools and all.
    assert [c["model"] for c in wire.calls] == ["ollama/llama3", "ollama/llama3"]
    assert all(c["tools"] is _TOOLS for c in wire.calls)


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize(
    ("model", "tools"),
    [("ollama_chat/llama3", _TOOLS), ("ollama/llama3", None)],
    ids=["chat prefix with tools", "generate prefix without tools"],
)
async def test_the_chat_prefix_and_a_toolless_request_are_not_warned_about(
    route: str,
    model: str,
    tools: list[dict[str, Any]] | None,
    wire: _Wire,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A warning that fires when nothing is wrong teaches the reader to skip it."""
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await _request(LLMGateway(), route, model, tools)

    assert _warnings(caplog) == []
    assert wire.calls[0]["model"] == model


async def test_the_once_is_per_gateway_not_per_process(
    wire: _Wire, caplog: pytest.LogCaptureFixture
) -> None:
    """A module-level flag would tell the first gateway in a process and nobody after it — and a
    bench builds one gateway per arm."""
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await _request(LLMGateway(), "batch", "ollama/llama3", _TOOLS)
        await _request(LLMGateway(), "batch", "ollama/llama3", _TOOLS)

    assert len([m for m in _warnings(caplog) if "ollama_chat/" in m]) == 2
