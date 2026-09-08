"""The adapter returns the tool call it was given — an offline round trip through every provider route.

A tool-calling benchmark measures the serving adapter before it measures the model. Changing ONLY
the adapter moves the same model between **0.00 and 0.96** on BFCL v4, and a 2×2 over chat
template × parser puts both main effects at exactly zero (arXiv 2609.03966): the number is a
property of the pipe, not of what flows through it. `bench/tool_defer` (−26% tokens, completion
60%→50%, p=0.125) and `bench/parallel_tools` (0.0% multi-call steps on two of the eight models)
were both read as facts about models. Neither had checked that the pipe carries a tool call at
all. This module is that check — the denominator those numbers were missing.

**The inventory.** `chimera/providers/` holds ONE adapter: `LLMGateway` (`gateway.py`). Nothing
else in the package touches a tool call — `catalog`, `discovery` and `listing` are model
catalogues, `failover` is an error taxonomy, `cache`/`prompt_cache`/`thinking` transform text
around the call, `ollama.py` lists pulled tags — and a guard below pins that. The gateway hands
the OpenAI tool schema to LiteLLM verbatim and reads back LiteLLM's OpenAI-shaped response, so
the provider-specific adapter that actually runs is LiteLLM's per-prefix transformation. It is
exercised here OFFLINE by replacing the single HTTP seam LiteLLM posts through with a fake that
records the request body and answers in the provider's own wire format: the emit side is what
would go on the wire, and the parse side is LiteLLM's real parser followed by the gateway's.

**Routes, and which share code (measured on LiteLLM 1.99.0):**

- ``openrouter/``, ``deepseek/``: `OpenrouterConfig` / `DeepSeekChatConfig`, both subclasses of
  `OpenAIGPTConfig` — OpenAI wire, ``tool_calls[].function.arguments`` is a JSON *string*, ids
  travel. ``openai/`` is the same `OpenAIGPTConfig` over the OpenAI SDK's own socket; the socket
  is checked once, on the batch route, and the parser is not re-tested through it.
- ``anthropic/``: `AnthropicConfig` — content blocks, ``tool_use.input`` is an *object*; tool
  names are rewritten to ``^[a-zA-Z0-9_-]{1,128}$`` on the way out and restored on the way back.
- ``gemini/``: `GoogleAIStudioGeminiConfig` — ``functionCall`` parts, no ids on the wire (LiteLLM
  mints ``call_<hex>``), and a result is attributed by *name and order*, never by id.
- ``ollama_chat/``: `OllamaChatConfig` — ``/api/chat``, arguments are an *object*, no ids (LiteLLM
  mints a uuid4).
- ``ollama/``: `OllamaConfig` — ``/api/generate``. NOT native tool calling: the catalogue is
  pasted into the prompt as a Python ``repr`` with ``format=json`` and the answer is parsed as one
  JSON object. This is the prefix the docs recommend (``CHIMERA_DEFAULT_MODEL=ollama/llama3``),
  so its cases that cannot pass are ``xfail(strict=True)`` below rather than quietly absent.

Two gateway routes per adapter: **batch** (`complete` → `_normalize` → `_parse_tool_calls`) and
**stream** (`stream_complete` → `_delta_tool_calls` → `_finalize_stream_tool_calls`). The coding
surface streams (`code_api` declares ``stream=True``); the paired benches batch.

**What this cannot show:** anything about a model. A green preflight says the pipe is faithful,
not that the model calls tools. It also pins LiteLLM 1.99 behaviour on purpose — an upgrade that
changes an adapter is supposed to turn this red, because that is the moment every tool number in
`bench/` acquired a different denominator.

Every guard below was broken on disk and its test confirmed red; the record is in the commit
message that added this file.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
from litellm.llms.custom_httpx import http_handler

import chimera.providers
from chimera.config import get_settings
from chimera.core import Agent, AgentConfig
from chimera.core.agent import AgentResult
from chimera.providers.gateway import CompletionResult, LLMGateway, MessageLike
from chimera.tools import ToolRegistry
from chimera.tools.base import Tool
from chimera.tools.builtin import EchoTool, default_registry

Family = Literal["openai", "anthropic", "gemini", "ollama_chat", "ollama_generate"]
Route = Literal["batch", "stream"]

_KEY_VARS = (
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
)


@dataclass(frozen=True)
class _Adapter:
    """One provider prefix as the gateway routes it, and which checks apply to it."""

    name: str
    slug: str
    family: Family
    #: The wire carries no call ids, so LiteLLM mints them — and this test cannot know them ahead.
    minted_ids: bool
    #: The stream route is covered. False for ``openai/`` (a different socket, the same parser,
    #: checked on the batch route only) and for ``ollama/`` (see the generate-prefix pin below).
    streams: bool
    #: The two-turn agent round trip is run through it. False for ``openai/``: `Agent` cannot hand
    #: a mock SDK client to the gateway, and the second-turn shape is `OpenAIGPTConfig`'s, which
    #: the ``openrouter/`` and ``deepseek/`` rows check.
    agent: bool
    transport: Literal["http_handler", "sdk"] = "http_handler"


_ADAPTERS: tuple[_Adapter, ...] = (
    _Adapter("openrouter", "openrouter/openai/gpt-4o", "openai", False, True, True),
    _Adapter("deepseek", "deepseek/deepseek-chat", "openai", False, True, True),
    _Adapter("openai", "openai/gpt-4o", "openai", False, False, False, transport="sdk"),
    _Adapter("anthropic", "anthropic/claude-sonnet-4-5", "anthropic", False, True, True),
    _Adapter("gemini", "gemini/gemini-2.5-flash", "gemini", True, True, True),
    _Adapter("ollama_chat", "ollama_chat/llama3.1", "ollama_chat", True, True, True),
    _Adapter("ollama", "ollama/llama3.1", "ollama_generate", True, False, True),
)
_BY_NAME = {adapter.name: adapter for adapter in _ADAPTERS}

#: The round-trip cases an adapter is KNOWN to fail, keyed by (adapter, check[, case]). Declared in
#: one place and attached as ``xfail(strict=True)``: a fix — or an upgrade that fixes it — turns
#: the row red, which is the signal to delete it and re-read the benches that ran on that route.
_KNOWN_FAILURES: dict[tuple[str, ...], str] = {
    ("ollama", "catalogue"): (
        "the /api/generate route sends no `tools`: the catalogue is pasted into the prompt as a "
        "Python repr (see test_the_generate_prefix_never_advertises_a_tool_and_streams_the_call_as_prose)"
    ),
    ("ollama", "probe"): "the /api/generate route sends no `tools` (same as 'catalogue')",
    ("gemini", "probe"): (
        "Gemini permits `enum` on strings only and no `additionalProperties`: LiteLLM drops "
        "`limit.enum` and `options.additionalProperties` before sending, so an integer enum and a "
        "closed object are not advertised on this route"
    ),
    ("gemini", "unedited"): (
        "LiteLLM's Gemini schema builder pops `enum` (non-string) and `additionalProperties` from "
        "the caller's dict IN PLACE, and `to_openai_schema(compact=False)` hands over the tool's own "
        "`parameters` dict — one call on this route edits the catalogue for every later call"
    ),
    ("ollama", "batch", "two_parallel_in_order"): (
        "the /api/generate route parses ONE JSON object out of `response`; a second call has "
        "nowhere to go on that wire"
    ),
    ("ollama_chat", "stream", "empty_args"): (
        "LiteLLM's ollama_chat stream parser mints a call id only when the arguments are non-empty "
        "(`len(function_args) > 0`), so a streamed call with `{}` arrives with id '' and the next "
        "turn answers it under tool_call_id '' — the batch route mints one regardless"
    ),
    ("ollama", "linked"): (
        "the /api/generate route flattens the transcript into one prompt string: the observation "
        "travels as text under the tool role, with no id to attribute it by"
    ),
}


def _known(*key: str) -> tuple[pytest.MarkDecorator, ...]:
    reason = _KNOWN_FAILURES.get(key)
    return (pytest.mark.xfail(strict=True, reason=reason),) if reason else ()


def _rows(
    check: str, *, routes: bool = False, cases: tuple[str, ...] = (), agent_only: bool = False
) -> list[Any]:
    """Parametrise over adapters (× routes × cases), attaching the known failures as strict xfails."""
    rows: list[Any] = []
    for adapter in _ADAPTERS:
        if agent_only and not adapter.agent:
            continue
        route_list: tuple[Route, ...] = ("batch", "stream") if adapter.streams else ("batch",)
        for route in route_list if routes else ("batch",):
            for case in cases or ("",):
                key = (adapter.name, route if routes else check) + ((case,) if case else ())
                marks = _known(*key) or _known(adapter.name, check)
                values: tuple[Any, ...] = (adapter.name,)
                if routes:
                    values += (route,)
                if case:
                    values += (case,)
                ident = "-".join(values)
                rows.append(pytest.param(*values, id=ident, marks=marks))
    return rows


# --- the wire ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Call:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class _Request:
    url: str
    body: dict[str, Any]
    streamed: bool


@dataclass
class _Wire:
    """The provider's queued answers, and every request that was sent to fetch them."""

    answers: list[dict[str, Any] | bytes]
    requests: list[_Request] = field(default_factory=list)


def _serve(monkeypatch: pytest.MonkeyPatch, answers: list[dict[str, Any] | bytes]) -> _Wire:
    """Replace LiteLLM's one HTTP seam so every provider answers from ``answers``, offline."""
    wire = _Wire(list(answers))

    def post(
        _self: Any, url: str, data: Any = None, json: Any = None, **kwargs: Any
    ) -> httpx.Response:
        raw = json if json is not None else data
        body = _loads(raw) if isinstance(raw, str | bytes) else dict(raw or {})
        wire.requests.append(_Request(url, body, bool(kwargs.get("stream"))))
        request = httpx.Request("POST", url)
        answer = wire.answers.pop(0)
        if isinstance(answer, bytes):
            headers = {"content-type": "text/event-stream"}
            return httpx.Response(200, content=answer, headers=headers, request=request)
        return httpx.Response(200, json=answer, request=request)

    monkeypatch.setattr(http_handler.HTTPHandler, "post", post)
    return wire


def _sdk_client(wire: _Wire) -> Any:
    """An OpenAI SDK client whose socket is ``wire`` — the ``openai/`` prefix does not use LiteLLM's
    HTTP handler, it hands the request to the SDK, so its seam is the SDK's transport."""
    import openai

    def handler(request: httpx.Request) -> httpx.Response:
        wire.requests.append(_Request(str(request.url), _loads(request.content), False))
        answer = wire.answers.pop(0)
        assert isinstance(answer, dict)
        return httpx.Response(200, json=answer, request=request)

    transport = httpx.MockTransport(handler)
    return openai.OpenAI(api_key="sk-test", http_client=httpx.Client(transport=transport))


def _loads(raw: str | bytes) -> dict[str, Any]:
    loaded = json.loads(raw)
    assert isinstance(loaded, dict)
    return loaded


def _pieces(text: str, size: int = 1024) -> list[str]:
    """Split a JSON argument string the way a provider streams it: in fragments, never whole."""
    if len(text) <= 2:
        return [text[:1], text[1:]] if text else [""]
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def _usage_openai() -> dict[str, int]:
    return {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}


def _answer(
    family: Family, calls: list[_Call], text: str, *, streamed: bool
) -> dict[str, Any] | bytes:
    """One provider answer carrying ``calls`` (and ``text``), in that family's own wire format."""
    if family == "openai":
        return _openai_stream(calls, text) if streamed else _openai_batch(calls, text)
    if family == "anthropic":
        return _anthropic_stream(calls, text) if streamed else _anthropic_batch(calls, text)
    if family == "gemini":
        return _gemini_stream(calls, text) if streamed else _gemini_batch(calls, text)
    if family == "ollama_chat":
        return _ollama_chat_stream(calls, text) if streamed else _ollama_chat_batch(calls, text)
    return _ollama_generate_stream(calls, text) if streamed else _ollama_generate_batch(calls, text)


def _openai_batch(calls: list[_Call], text: str) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": _dumps(c.args)},
            }
            for c in calls
        ]
    choice = {"index": 0, "finish_reason": "tool_calls" if calls else "stop", "message": message}
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "m",
        "choices": [choice],
        "usage": _usage_openai(),
    }


def _openai_stream(calls: list[_Call], text: str) -> bytes:
    def chunk(delta: dict[str, Any], finish: str | None = None, **extra: Any) -> dict[str, Any]:
        return {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            **extra,
        }

    chunks: list[dict[str, Any]] = []
    if text:
        chunks.append(chunk({"role": "assistant", "content": text}))
    for index, c in enumerate(calls):
        head, *tail = _pieces(_dumps(c.args))
        chunks.append(
            chunk(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": index,
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": head},
                        }
                    ],
                }
            )
        )
        chunks.extend(
            chunk({"tool_calls": [{"index": index, "function": {"arguments": piece}}]})
            for piece in tail
        )
    chunks.append(chunk({}, "tool_calls" if calls else "stop", usage=_usage_openai()))
    return _sse(("data", c) for c in chunks) + b"data: [DONE]\n\n"


def _anthropic_batch(calls: list[_Call], text: str) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.args} for c in calls]
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "m",
        "stop_reason": "tool_use" if calls else "end_turn",
        "stop_sequence": None,
        "content": content,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _anthropic_stream(calls: list[_Call], text: str) -> bytes:
    events: list[tuple[str, dict[str, Any]]] = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "m",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            },
        )
    ]
    index = 0
    if text:
        events.append(
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "text", "text": ""},
                },
            )
        )
        events.append(
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "text_delta", "text": text},
                },
            )
        )
        events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))
        index += 1
    for c in calls:
        events.append(
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "tool_use", "id": c.id, "name": c.name, "input": {}},
                },
            )
        )
        events.extend(
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "input_json_delta", "partial_json": piece},
                },
            )
            for piece in _pieces(_dumps(c.args))
        )
        events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))
        index += 1
    events.append(
        (
            "message_delta",
            {
                "type": "message_delta",
                "usage": {"output_tokens": 1},
                "delta": {
                    "stop_reason": "tool_use" if calls else "end_turn",
                    "stop_sequence": None,
                },
            },
        )
    )
    events.append(("message_stop", {"type": "message_stop"}))
    return b"".join(
        f"event: {name}\n".encode() + _sse([("data", payload)]) for name, payload in events
    )


def _gemini_parts(calls: list[_Call], text: str) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = [{"text": text}] if text else []
    return parts + [{"functionCall": {"name": c.name, "args": c.args}} for c in calls]


def _gemini_batch(calls: list[_Call], text: str) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": _gemini_parts(calls, text)},
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
        "modelVersion": "m",
    }


def _gemini_stream(calls: list[_Call], text: str) -> bytes:
    # One part per chunk, the way the API streams parallel calls; the last chunk carries the stop.
    parts = _gemini_parts(calls, text)
    chunks: list[dict[str, Any]] = [
        {
            "candidates": [{"content": {"role": "model", "parts": [part]}, "index": 0}],
            "usageMetadata": {
                "promptTokenCount": 1,
                "candidatesTokenCount": 1,
                "totalTokenCount": 2,
            },
        }
        for part in parts
    ]
    chunks[-1]["candidates"][0]["finishReason"] = "STOP"
    return _sse((("data", c) for c in chunks), newline="\r\n")


def _ollama_chat_batch(calls: list[_Call], text: str) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = [{"function": {"name": c.name, "arguments": c.args}} for c in calls]
    return {
        "model": "m",
        "created_at": "2026-01-01T00:00:00Z",
        "message": message,
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 1,
        "eval_count": 1,
    }


def _ollama_chat_stream(calls: list[_Call], text: str) -> bytes:
    first = _ollama_chat_batch(calls, text)
    first["done"] = False
    last = _ollama_chat_batch([], "")
    return _ndjson([first, last])


def _ollama_generate_batch(calls: list[_Call], text: str) -> dict[str, Any]:
    # `format: json` answers: exactly one object, {"name", "arguments"}. Two calls cannot be said.
    response = _dumps({"name": calls[0].name, "arguments": calls[0].args}) if calls else text
    return {
        "model": "m",
        "created_at": "2026-01-01T00:00:00Z",
        "response": response,
        "done": True,
        "prompt_eval_count": 1,
        "eval_count": 1,
    }


def _ollama_generate_stream(calls: list[_Call], text: str) -> bytes:
    whole = _ollama_generate_batch(calls, text)["response"]
    lines = [
        {"model": "m", "created_at": "t", "response": piece, "done": False}
        for piece in _pieces(whole, 8)
    ]
    lines.append(
        {
            "model": "m",
            "created_at": "t",
            "response": "",
            "done": True,
            "prompt_eval_count": 1,
            "eval_count": 1,
        }
    )
    return _ndjson(lines)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _sse(
    items: Iterator[tuple[str, dict[str, Any]]] | list[tuple[str, dict[str, Any]]],
    *,
    newline: str = "\n",
) -> bytes:
    return "".join(f"{key}: {_dumps(payload)}{newline}{newline}" for key, payload in items).encode(
        "utf-8"
    )


def _ndjson(lines: list[dict[str, Any]]) -> bytes:
    return "".join(_dumps(line) + "\n" for line in lines).encode("utf-8")


# --- reading the request back, per family ----------------------------------------------------


def _advertised(family: Family, body: dict[str, Any]) -> list[dict[str, Any]]:
    """The function specs the model would receive, normalised to name/description/parameters."""
    tools = body.get("tools")
    if not tools:
        return []
    if family in ("openai", "ollama_chat"):
        return [dict(tool["function"]) for tool in tools]
    if family == "anthropic":
        return [
            {
                "name": t["name"],
                "description": t.get("description"),
                "parameters": t["input_schema"],
            }
            for t in tools
        ]
    if family == "gemini":
        return [
            {"name": d["name"], "description": d.get("description"), "parameters": d["parameters"]}
            for group in tools
            for d in group["function_declarations"]
        ]
    return []


def _echoed_calls(
    family: Family, body: dict[str, Any]
) -> list[tuple[str | None, str, dict[str, Any]]]:
    """The assistant's own calls as the NEXT request restates them: (id-or-None, name, args)."""
    if family in ("openai", "ollama_chat"):
        out: list[tuple[str | None, str, dict[str, Any]]] = []
        for message in body["messages"]:
            for call in message.get("tool_calls") or []:
                args = call["function"]["arguments"]
                out.append(
                    (
                        call.get("id"),
                        call["function"]["name"],
                        _loads(args) if isinstance(args, str) else dict(args),
                    )
                )
        return out
    if family == "anthropic":
        return [
            (block["id"], block["name"], dict(block["input"]))
            for message in body["messages"]
            if message["role"] == "assistant"
            for block in message["content"]
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
    if family == "gemini":
        return [
            (None, part["function_call"]["name"], dict(part["function_call"]["args"]))
            for content in body["contents"]
            if content["role"] == "model"
            for part in content["parts"]
            if "function_call" in part
        ]
    return []


def _results(family: Family, body: dict[str, Any]) -> list[tuple[str, str]]:
    """The tool results the NEXT request carries, as (link, content) — the link being what the
    provider attributes the result by: a call id, or on Gemini the function's name."""
    if family in ("openai", "ollama_chat"):
        return [
            (m["tool_call_id"], m["content"]) for m in body["messages"] if m.get("role") == "tool"
        ]
    if family == "anthropic":
        return [
            (block["tool_use_id"], block["content"])
            for message in body["messages"]
            if message["role"] == "user"
            for block in message["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
    if family == "gemini":
        return [
            (part["function_response"]["name"], part["function_response"]["response"]["content"])
            for content in body["contents"]
            if content["role"] == "user"
            for part in content["parts"]
            if "function_response" in part
        ]
    return []


# --- the catalogue -------------------------------------------------------------------------------

_PROBE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "What to look for. A second sentence compaction trims.",
        },
        "mode": {"type": "string", "enum": ["literal", "regex"]},
        "limit": {"type": "integer", "enum": [10, 20]},
        "paths": {"type": "array", "items": {"type": "string"}},
        "options": {
            "type": "object",
            "properties": {
                "case_sensitive": {"type": "boolean", "default": False},
                "depth": {"type": "integer", "minimum": 0},
            },
            "required": ["case_sensitive"],
            "additionalProperties": False,
        },
    },
    "required": ["pattern"],
}


class _ProbeTool(Tool):
    """Every schema feature the built-in catalogue does not use, so an adapter's edges show.

    `parameters` is copied per instance on purpose. The built-ins share one class-level dict, which
    is exactly the aliasing `test_emitting_the_catalogue_does_not_edit_it` is about — but a probe
    that shared its dict across tests would let one adapter's edit change what the next test
    advertises, and the suite would then pass or fail on collection order.
    """

    name = "probe"
    description = "Probe every schema feature at once. This second sentence is compaction fodder."

    def __init__(self) -> None:
        self.parameters = copy.deepcopy(_PROBE_PARAMETERS)

    def run(self, **kwargs: Any) -> str:
        return json.dumps(kwargs, sort_keys=True)


def _echo_registry(*extra: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    for tool in extra:
        registry.register(tool)
    return registry


@pytest.fixture
def armed(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every provider has a key, so the credential gate lets each route reach the (fake) wire —
    and no real socket can open, so a route this module forgot to fake fails loudly and offline
    instead of reaching a provider with `sk-test` (measured: one unfaked row did exactly that).

    Requested explicitly rather than autouse so it runs AFTER conftest's key-clearing fixture.
    """
    import litellm

    def refuse(_self: Any, request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"this test must stay offline, and it tried to reach {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    for var in _KEY_VARS:
        monkeypatch.setenv(var, "sk-test")
    monkeypatch.setattr(litellm, "suppress_debug_info", True)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _complete(
    adapter: _Adapter, wire: _Wire, route: Route, tools: list[dict[str, Any]] | None
) -> CompletionResult:
    """One gateway call through ``adapter`` on ``route``, tools attached."""
    gateway = LLMGateway()
    messages: list[MessageLike] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
    ]
    if route == "stream":
        return gateway.stream_complete(messages, model=adapter.slug, tools=tools, temperature=0)
    extra: dict[str, Any] = {"client": _sdk_client(wire)} if adapter.transport == "sdk" else {}
    return gateway.complete(messages, model=adapter.slug, tools=tools, temperature=0, **extra)


# --- the inventory ---------------------------------------------------------------------------------


def test_the_only_tool_call_parser_under_providers_is_the_gateway() -> None:
    """The docstring's inventory, pinned: a second parse site would need its own row above."""
    package = Path(chimera.providers.__file__).parent
    parsers = sorted(
        p.name for p in package.glob("*.py") if "tool_calls" in p.read_text(encoding="utf-8")
    )
    assert parsers == ["gateway.py"]


# --- emit: the catalogue reaches the wire ------------------------------------------------------


@pytest.mark.parametrize("adapter_name", _rows("catalogue"))
@pytest.mark.parametrize("compact", [False, True], ids=["verbose", "compact"])
def test_the_built_in_catalogue_reaches_the_wire_intact(
    armed: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, adapter_name: str, compact: bool
) -> None:
    """What `default_registry` advertises is what the model receives — names, descriptions, every
    parameter schema — in both compaction modes, and the registry is the same afterwards."""
    adapter = _BY_NAME[adapter_name]
    registry = default_registry(tmp_path, host_exec_confirm=None)
    schemas = registry.to_openai_schema(compact=compact)
    before = copy.deepcopy(schemas)
    wire = _serve(monkeypatch, [_answer(adapter.family, [], "done", streamed=False)])

    result = _complete(adapter, wire, "batch", schemas)

    assert result.content == "done" and result.tool_calls is None
    assert _advertised(adapter.family, wire.requests[0].body) == [s["function"] for s in before]
    assert registry.to_openai_schema(compact=compact) == before


@pytest.mark.parametrize("adapter_name", _rows("probe"))
@pytest.mark.parametrize("compact", [False, True], ids=["verbose", "compact"])
def test_every_schema_feature_reaches_the_wire(
    armed: None, monkeypatch: pytest.MonkeyPatch, adapter_name: str, compact: bool
) -> None:
    """Nested object with its own `required`, array items, string AND integer enums, a default, a
    minimum, a closed object — the probe carries what the built-ins do not, and it all arrives."""
    adapter = _BY_NAME[adapter_name]
    schemas = _echo_registry(_ProbeTool()).to_openai_schema(compact=compact)
    expected = copy.deepcopy([s["function"] for s in schemas])
    wire = _serve(monkeypatch, [_answer(adapter.family, [], "done", streamed=False)])

    _complete(adapter, wire, "batch", schemas)

    assert _advertised(adapter.family, wire.requests[0].body) == expected


def test_compaction_keeps_what_constrains_a_call(tmp_path: Path) -> None:
    """Compact mode may trim prose; it must not move a type, a `required` list or an enum."""
    registry = default_registry(tmp_path, host_exec_confirm=None)
    registry.register(_ProbeTool())
    verbose = registry.to_openai_schema(compact=False)
    compact = registry.to_openai_schema(compact=True)

    def constraints(schemas: list[dict[str, Any]]) -> set[tuple[str, str]]:
        found: set[tuple[str, str]] = set()

        def walk(node: Any, path: str) -> None:
            if isinstance(node, dict):
                for key in ("type", "required", "enum", "items"):
                    if key in node:
                        found.add((f"{path}.{key}", json.dumps(node[key], sort_keys=True)))
                for key, value in node.items():
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for i, value in enumerate(node):
                    walk(value, f"{path}[{i}]")

        for schema in schemas:
            walk(schema["function"]["parameters"], schema["function"]["name"])
        return found

    assert [s["function"]["name"] for s in compact] == [s["function"]["name"] for s in verbose]
    assert constraints(compact) == constraints(verbose)
    # And the prose was actually trimmed, so the two modes are two modes.
    probe = next(s for s in compact if s["function"]["name"] == "probe")
    assert (
        probe["function"]["parameters"]["properties"]["pattern"]["description"]
        == "What to look for."
    )


@pytest.mark.parametrize("adapter_name", _rows("unedited"))
def test_emitting_the_catalogue_does_not_edit_it(
    armed: None, monkeypatch: pytest.MonkeyPatch, adapter_name: str
) -> None:
    """`to_openai_schema(compact=False)` hands the adapter the tool's own `parameters` dict. An
    adapter that edits what it is given edits the catalogue, for every provider called after it."""
    adapter = _BY_NAME[adapter_name]
    registry = _echo_registry(_ProbeTool())
    before = copy.deepcopy(registry.to_openai_schema())
    wire = _serve(monkeypatch, [_answer(adapter.family, [], "done", streamed=False)])

    _complete(adapter, wire, "batch", registry.to_openai_schema())

    assert registry.to_openai_schema() == before


# --- parse: the call comes back as it was sent -------------------------------------------------

_CASES: dict[str, list[_Call]] = {
    "one_call": [_Call("c1", "echo", {"text": "ping"})],
    "two_parallel_in_order": [
        _Call("c1", "echo", {"text": "first"}),
        _Call("c2", "echo", {"text": "second"}),
    ],
    "nested_object": [
        _Call("c1", "probe", {"pattern": "x", "options": {"case_sensitive": True, "depth": 2}})
    ],
    "array": [_Call("c1", "probe", {"pattern": "x", "paths": ["a", "b", "c"]})],
    "unicode": [_Call("c1", "echo", {"text": "café — 中文 — 🐝"})],
    "empty_args": [_Call("c1", "echo", {})],
    "json_string_in_string": [
        _Call("c1", "echo", {"text": '{"a": [1, {"b": "}\\"{"}], "c": "x\\ny"}'})
    ],
    "ten_kb": [_Call("c1", "echo", {"text": "x" * 10_000})],
}


@pytest.mark.parametrize(
    ("adapter_name", "route", "case"), _rows("parse", routes=True, cases=tuple(_CASES))
)
def test_the_call_comes_back_as_it_was_sent(
    armed: None, monkeypatch: pytest.MonkeyPatch, adapter_name: str, route: Route, case: str
) -> None:
    """Name, arguments (exactly) and order survive the provider's wire format and both gateway
    routes. Ids survive where the wire carries them; where LiteLLM mints them they are distinct."""
    adapter = _BY_NAME[adapter_name]
    calls = _CASES[case]
    schemas = _echo_registry(_ProbeTool()).to_openai_schema()
    wire = _serve(monkeypatch, [_answer(adapter.family, calls, "", streamed=route == "stream")])

    result = _complete(adapter, wire, route, schemas)

    assert wire.requests[0].streamed is (route == "stream")
    assert result.tool_calls is not None
    assert [(c.name, c.arguments) for c in result.tool_calls] == [(c.name, c.args) for c in calls]
    ids = [c.id for c in result.tool_calls]
    if adapter.minted_ids:
        assert all(ids) and len(set(ids)) == len(ids)
    else:
        assert ids == [c.id for c in calls]
    assert result.truncated is False


# --- result: the observation goes back linked to its call ----------------------------------------


def _two_turns(
    monkeypatch: pytest.MonkeyPatch, adapter: _Adapter, *, streamed: bool, calls: list[_Call]
) -> tuple[AgentResult, _Wire]:
    """The real loop: turn one answers with ``calls``, the tools run, turn two sees the results."""
    wire = _serve(
        monkeypatch,
        [
            _answer(adapter.family, calls, "", streamed=streamed),
            _answer(adapter.family, [], "done", streamed=streamed),
        ],
    )
    agent = Agent(
        LLMGateway(),
        _echo_registry(),
        AgentConfig(model=adapter.slug, max_steps=3, inject_skill_context=False),
    )
    result = agent.run("go", on_token=(lambda _token: None) if streamed else None)
    return result, wire


def _transcript_ids(result: AgentResult) -> list[str]:
    for message in result.transcript:
        if isinstance(message, dict) and message.get("tool_calls"):
            return [str(call["id"]) for call in message["tool_calls"]]
    raise AssertionError("no assistant tool_calls message in the transcript")


@pytest.mark.parametrize(("adapter_name", "route"), _rows("linked", routes=True, agent_only=True))
def test_the_result_goes_back_linked_to_its_call(
    armed: None, monkeypatch: pytest.MonkeyPatch, adapter_name: str, route: Route
) -> None:
    """The next request restates the calls the model made and carries each observation under the
    link that provider attributes results by — the call id, or on Gemini the function name and
    its position — in the order the calls were made."""
    adapter = _BY_NAME[adapter_name]
    calls = [_Call("c1", "echo", {"text": "café 中文"}), _Call("c2", "echo", {"text": "second"})]

    result, wire = _two_turns(monkeypatch, adapter, streamed=route == "stream", calls=calls)

    assert result.answer == "done" and result.tool_calls_made == 2 and result.steps == 2
    second = wire.requests[1]
    assert second.streamed is (route == "stream")
    ids = _transcript_ids(result)
    links = ["echo", "echo"] if adapter.family == "gemini" else ids
    assert _results(adapter.family, second.body) == [(links[0], "café 中文"), (links[1], "second")]
    echoed = _echoed_calls(adapter.family, second.body)
    assert [(name, args) for _, name, args in echoed] == [(c.name, c.args) for c in calls]
    if adapter.family in ("openai", "anthropic"):
        assert [link for link, _, _ in echoed] == ids  # the restated call carries the same id


# --- what an adapter does with what it cannot carry ------------------------------------------------


@pytest.mark.parametrize("route", ["batch", "stream"])
def test_an_argument_string_that_is_not_json_drops_that_call_and_keeps_its_sibling(
    armed: None, monkeypatch: pytest.MonkeyPatch, route: Route
) -> None:
    """Pinned behaviour on the OpenAI wire (the only one where arguments travel as a string): the
    unparseable call is DROPPED at the gateway — logged, not raised, not served with `{}` — and the
    parseable sibling survives. A turn whose every call is dropped therefore reads as a text
    answer, which is the silent half of this rule and the reason it is pinned here."""
    adapter = _BY_NAME["openrouter"]
    if route == "batch":
        answer: dict[str, Any] | bytes = _openai_batch([_Call("c1", "echo", {"text": "fine"})], "")
        assert isinstance(answer, dict)
        answer["choices"][0]["message"]["tool_calls"].insert(
            0,
            {
                "id": "c0",
                "type": "function",
                "function": {"name": "echo", "arguments": '{"text": "cut'},
            },
        )
    else:
        answer = _openai_stream(
            [_Call("c0", "echo", {"text": "cut"}), _Call("c1", "echo", {"text": "fine"})], ""
        )
        answer = answer.replace(
            b'"arguments": "{\\"text\\": \\"cut\\"}"', b'"arguments": "{\\"text\\": \\"cut"'
        )
        assert b'\\"cut"' in answer  # the sabotage of the first call took
    wire = _serve(monkeypatch, [answer])

    result = _complete(adapter, wire, route, _echo_registry().to_openai_schema())

    assert result.tool_calls is not None
    assert [(c.id, c.name, c.arguments) for c in result.tool_calls] == [
        ("c1", "echo", {"text": "fine"})
    ]


def test_a_null_argument_field_is_served_as_no_arguments(
    armed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pinned, and worth knowing: `arguments: null` (or the key missing) is not malformed JSON to
    LiteLLM — it becomes `""`, which the gateway reads as `{}` — so that call RUNS with nothing,
    unlike a cut-off string, which is dropped."""
    answer = _openai_batch(
        [_Call("c1", "echo", {"text": "x"}), _Call("c2", "echo", {"text": "y"})], ""
    )
    answer["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = None
    del answer["choices"][0]["message"]["tool_calls"][1]["function"]["arguments"]
    wire = _serve(monkeypatch, [answer])

    result = _complete(_BY_NAME["openrouter"], wire, "batch", _echo_registry().to_openai_schema())

    assert result.tool_calls is not None
    assert [(c.id, c.arguments) for c in result.tool_calls] == [("c1", {}), ("c2", {})]


@pytest.mark.parametrize("adapter_name", _rows("agent", agent_only=True))
def test_a_name_outside_the_catalogue_passes_the_gateway_and_comes_back_as_an_error_observation(
    armed: None, monkeypatch: pytest.MonkeyPatch, adapter_name: str
) -> None:
    """The gateway knows no catalogue, so it passes the call through; the loop answers it with an
    `error: unknown tool` observation, linked like any other result, and the run goes on."""
    adapter = _BY_NAME[adapter_name]
    calls = [_Call("c1", "not_a_tool", {"why": "not"})]

    result, wire = _two_turns(monkeypatch, adapter, streamed=False, calls=calls)

    assert result.answer == "done" and result.tool_calls_made == 1
    observation = "error: unknown tool 'not_a_tool'"
    assert observation in json.dumps(wire.requests[1].body, ensure_ascii=False)
    if adapter.family != "ollama_generate":
        link = "not_a_tool" if adapter.family == "gemini" else _transcript_ids(result)[0]
        assert _results(adapter.family, wire.requests[1].body) == [(link, observation)]


def test_anthropic_rewrites_a_name_it_cannot_send_and_restores_it_on_the_way_back(
    armed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anthropic enforces ``^[a-zA-Z0-9_-]{1,128}$``. An imported tool named with a dot, a slash or a
    space (MCP and OpenAPI tools can be) is renamed on the wire and — pinned here because the
    dispatcher matches on the registered name — renamed back when the model calls it."""
    adapter = _BY_NAME["anthropic"]
    schemas = _echo_registry(_ProbeTool()).to_openai_schema()
    schemas[1]["function"]["name"] = "mcp.server/search files"
    answer = _anthropic_batch([_Call("c1", "mcp_server_search_files", {"pattern": "x"})], "")
    wire = _serve(monkeypatch, [answer])

    result = _complete(adapter, wire, "batch", schemas)

    assert [t["name"] for t in wire.requests[0].body["tools"]] == [
        "echo",
        "mcp_server_search_files",
    ]
    assert (
        schemas[1]["function"]["name"] == "mcp.server/search files"
    )  # the catalogue was not edited
    assert result.tool_calls is not None
    assert [(c.name, c.arguments) for c in result.tool_calls] == [
        ("mcp.server/search files", {"pattern": "x"})
    ]


def test_the_generate_prefix_never_advertises_a_tool_and_streams_the_call_as_prose(
    armed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ollama/`` — the prefix the docs recommend and the first in `_LOCAL_MODEL_PREFIXES` — is a
    prompt-template adapter, not tool calling. Pinned, in three parts: the request has no `tools`
    and carries the catalogue as a Python repr inside the prompt under `format: json`; the batch
    route parses ONE object out of the answer; and the stream route hands the same object back as
    the assistant's TEXT, so the loop sees no call at all. Every tool number measured through this
    prefix on the coding surface is a number about a model that was never offered a tool."""
    adapter = _BY_NAME["ollama"]
    schemas = _echo_registry().to_openai_schema()
    call = _Call("c1", "echo", {"text": "café"})
    wire = _serve(
        monkeypatch,
        [
            _answer("ollama_generate", [call], "", streamed=False),
            _answer("ollama_generate", [call], "", streamed=True),
        ],
    )

    batch = _complete(adapter, wire, "batch", schemas)
    stream = _complete(adapter, wire, "stream", schemas)

    request = wire.requests[0].body
    assert "tools" not in request and request["format"] == "json"
    assert repr(schemas[0]) in request["prompt"]  # a Python repr, `True`/`False` and all, not JSON
    assert batch.tool_calls is not None
    assert [(c.name, c.arguments) for c in batch.tool_calls] == [("echo", {"text": "café"})]
    assert stream.tool_calls is None
    assert json.loads(stream.content) == {"name": "echo", "arguments": {"text": "café"}}
