"""Every call to an Ollama model asks for its context window, and a cut prompt is never silent.

Found on 2026-10-08 in the S30-51 bench (`bench/report_defect`): the agent loop sent no ``num_ctx``
to ``ollama_chat/``, so Ollama served the machine default (4,096 tokens, `/api/ps`) and cut the
prompt to about half of it without an error. The first request of a `chimera solve` was ~20,500
characters — system prompt, 26 tool schemas, the task — and came back read as 2,050 tokens; the
model never saw the task and wrote "Hello, world!" to example.txt. The bench worked around it by
patching ``litellm.completion``; every local-model user ran into it with nothing on screen, because
the trace records what Chimera sent, not what the model read.

Two halves, tested apart:

* **the cause** — ``num_ctx`` (`Settings.ollama_num_ctx`) reaches the wire, inside ``options``, on
  every gateway path that can reach Ollama (batch, stream, async, raw stream, a fallback), and on
  no path to another provider;
* **the silence** — when Ollama's own prompt count shows it read less than it was sent, the gateway
  warns, on the half-window rule `chimera.decisions.local` already raises on.

The wire tests replace LiteLLM's single HTTP seam with a fake, so the request body asserted on is
the one LiteLLM's real Ollama adapter built. Nothing here opens a socket or calls a model.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from litellm.llms.custom_httpx import http_handler

from chimera.config import Settings, get_settings
from chimera.core.context_budget import FALLBACK_CONTEXT_TOKENS, ContextBudget, window_tokens
from chimera.decisions.local import NUM_CTX as DECISION_NUM_CTX
from chimera.decisions.local import PROMPT_BUDGET
from chimera.providers import gateway as gateway_module
from chimera.providers.gateway import LLMGateway, MessageLike
from chimera.providers.ollama import is_ollama_route, prompt_budget, truncation_suspected

DEFAULT_NUM_CTX = Settings.model_fields["ollama_num_ctx"].default

#: The size of the request that was cut in the bench: ~20,500 characters of system prompt + task.
#: Built as plain text so the count is exact: the tool schemas are left off.
BIG_PROMPT = "word " * 4_100  # 20,500 characters

#: What the bench measured Ollama reporting for it under the 4,096 default.
CUT_COUNT = 2_050


# --- the fake wire ---------------------------------------------------------------------------------


@dataclass
class _Wire:
    answers: list[dict[str, Any] | bytes]
    bodies: list[dict[str, Any]] = field(default_factory=list)


def _serve(monkeypatch: pytest.MonkeyPatch, answers: list[dict[str, Any] | bytes]) -> _Wire:
    """LiteLLM's one HTTP seam, answering from ``answers`` and keeping every request body."""
    wire = _Wire(list(answers))

    def post(_self: Any, url: str, data: Any = None, json: Any = None, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", url)
        # LiteLLM's own model lookup at the end of an ollama_chat stream (see the adapter test):
        # answered as Ollama answers a model it does not have, never queued nor recorded.
        if httpx.URL(url).path.endswith("/api/show"):
            return httpx.Response(404, json={"error": "model 'm' not found"}, request=request)
        raw = json if json is not None else data
        wire.bodies.append(_loads(raw) if isinstance(raw, str | bytes) else dict(raw or {}))
        answer = wire.answers.pop(0)
        if isinstance(answer, bytes):
            return httpx.Response(200, content=answer, request=request)
        return httpx.Response(200, json=answer, request=request)

    monkeypatch.setattr(http_handler.HTTPHandler, "post", post)
    return wire


def _loads(raw: str | bytes) -> dict[str, Any]:
    loaded = json.loads(raw)
    assert isinstance(loaded, dict)
    return loaded


def _chat(text: str = "done", prompt_eval_count: int = 12) -> dict[str, Any]:
    return {
        "model": "m", "created_at": "2026-10-08T00:00:00Z",
        "message": {"role": "assistant", "content": text},
        "done": True, "done_reason": "stop", "prompt_eval_count": prompt_eval_count, "eval_count": 1,
    }


def _chat_stream(text: str = "done", prompt_eval_count: int = 12) -> bytes:
    first = {"model": "m", "created_at": "t", "message": {"role": "assistant", "content": text}, "done": False}
    last = _chat("", prompt_eval_count)
    return b"".join(json.dumps(line).encode() + b"\n" for line in (first, last))


def _generate(text: str = "done", prompt_eval_count: int = 12) -> dict[str, Any]:
    return {
        "model": "m", "created_at": "t", "response": text, "done": True,
        "prompt_eval_count": prompt_eval_count, "eval_count": 1,
    }


def _openai(text: str = "done") -> dict[str, Any]:
    return {
        "id": "gen-1", "object": "chat.completion", "created": 0, "model": "openai/gpt-4o",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    }


@pytest.fixture
def armed(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A hosted key for the non-Ollama rows, no real socket, and the settings re-read."""
    import litellm

    def refuse(_self: Any, request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"this test must stay offline, and it tried to reach {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.delenv("CHIMERA_OLLAMA_NUM_CTX", raising=False)
    monkeypatch.setattr(litellm, "suppress_debug_info", True)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _messages(text: str = "go") -> list[MessageLike]:
    return [{"role": "system", "content": "sys"}, {"role": "user", "content": text}]


# --- the cause: num_ctx reaches the wire -----------------------------------------------------------


def test_the_default_window_is_the_one_the_agent_prompt_was_measured_to_need() -> None:
    """32,768: 16,384 was too small for the solve prompt in the bench, 4,096 is the cut that was found."""
    assert DEFAULT_NUM_CTX == 32_768
    assert Settings.model_fields["ollama_num_ctx"].validation_alias == "CHIMERA_OLLAMA_NUM_CTX"


@pytest.mark.parametrize("route", ["batch", "stream"])
def test_an_ollama_chat_call_carries_num_ctx_in_its_options(
    armed: None, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    """The agent loop's two paths: `complete` (batch) and `stream_complete` (the coding turn)."""
    wire = _serve(monkeypatch, [_chat_stream() if route == "stream" else _chat()])
    gateway = LLMGateway()
    if route == "stream":
        result = gateway.stream_complete(_messages(), model="ollama_chat/qwen3:4b", temperature=0)
    else:
        result = gateway.complete(_messages(), model="ollama_chat/qwen3:4b", temperature=0)

    assert result.content == "done"
    assert wire.bodies[0]["options"]["num_ctx"] == DEFAULT_NUM_CTX


def test_an_ollama_generate_call_carries_num_ctx_too(armed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ollama/` is /api/generate: no tools, but the same server and the same silent cut."""
    wire = _serve(monkeypatch, [_generate()])

    LLMGateway().complete(_messages(), model="ollama/qwen3:4b", temperature=0)

    assert wire.bodies[0]["options"]["num_ctx"] == DEFAULT_NUM_CTX


def test_a_hosted_call_is_never_sent_num_ctx(armed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Behaviour for every other provider is unchanged: the option is Ollama's and nobody else's."""
    wire = _serve(monkeypatch, [_openai()])

    LLMGateway().complete(_messages(), model="openrouter/openai/gpt-4o", temperature=0)

    assert "num_ctx" not in json.dumps(wire.bodies[0])


def test_the_configured_window_is_the_one_sent(armed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_OLLAMA_NUM_CTX", "8192")
    get_settings.cache_clear()
    wire = _serve(monkeypatch, [_chat()])

    LLMGateway().complete(_messages(), model="ollama_chat/qwen3:4b", temperature=0)

    assert wire.bodies[0]["options"]["num_ctx"] == 8192


def test_zero_leaves_the_server_default(armed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The way out for a server whose own default (OLLAMA_CONTEXT_LENGTH) is already right."""
    monkeypatch.setenv("CHIMERA_OLLAMA_NUM_CTX", "0")
    get_settings.cache_clear()
    wire = _serve(monkeypatch, [_chat()])

    LLMGateway().complete(_messages(), model="ollama_chat/qwen3:4b", temperature=0)

    assert "num_ctx" not in wire.bodies[0].get("options", {})


# --- the other gateway paths, through a recording litellm ------------------------------------------


class _Recorder:
    """Stands in for `litellm.completion`/`acompletion`/`embedding`, keeping every call's kwargs."""

    def __init__(self, fail_models: tuple[str, ...] = ()) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_models = fail_models
        self.prompt_tokens = 12

    def _response(self, kwargs: dict[str, Any]) -> Any:
        self.calls.append(kwargs)
        if kwargs["model"] in self.fail_models:
            raise RuntimeError("503 service unavailable")
        message = SimpleNamespace(content="done", tool_calls=None, reasoning_content=None)
        choice = SimpleNamespace(message=message, finish_reason="stop", logprobs=None)
        usage = SimpleNamespace(prompt_tokens=self.prompt_tokens, completion_tokens=1, prompt_tokens_details=None)
        return SimpleNamespace(choices=[choice], usage=usage, model=kwargs["model"], id="g", provider="")

    def completion(self, **kwargs: Any) -> Any:
        if kwargs.get("stream"):
            self.calls.append(kwargs)
            delta = SimpleNamespace(content="done", tool_calls=None, reasoning_content=None)
            chunk = SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None)
            usage = SimpleNamespace(prompt_tokens=self.prompt_tokens, completion_tokens=1, prompt_tokens_details=None)
            last = SimpleNamespace(choices=[], usage=usage)
            return iter([chunk, last])
        return self._response(kwargs)

    async def acompletion(self, **kwargs: Any) -> Any:
        return self._response(kwargs)

    def embedding(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return {"data": [{"embedding": [0.0]} for _ in kwargs["input"]]}


@pytest.fixture
def recorder(armed: None, monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    import litellm

    rec = _Recorder()
    monkeypatch.setattr(litellm, "completion", rec.completion)
    monkeypatch.setattr(litellm, "acompletion", rec.acompletion)
    monkeypatch.setattr(litellm, "embedding", rec.embedding)
    return rec


def test_the_async_path_sends_it(recorder: _Recorder) -> None:
    import asyncio

    asyncio.run(LLMGateway().acomplete(_messages(), model="ollama_chat/qwen3:4b"))

    assert recorder.calls[0]["num_ctx"] == DEFAULT_NUM_CTX


def test_the_raw_stream_sends_it(recorder: _Recorder) -> None:
    """`stream`: the live terminal, the messaging gateway and A2A build on it."""
    assert "".join(LLMGateway().stream(_messages(), model="ollama_chat/qwen3:4b")) == "done"

    assert recorder.calls[0]["num_ctx"] == DEFAULT_NUM_CTX


def test_a_fallback_gets_the_window_of_its_own_route(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    """Decided per candidate: a hosted primary that fails over to Ollama sends it on the Ollama
    attempt only, and an Ollama primary that fails over to a hosted model does not carry it along."""
    recorder.fail_models = ("openrouter/openai/gpt-4o",)
    monkeypatch.setenv("CHIMERA_FALLBACK_MODELS", "ollama_chat/qwen3:4b")
    get_settings.cache_clear()
    LLMGateway().complete(_messages(), model="openrouter/openai/gpt-4o")
    hosted, local = recorder.calls
    assert "num_ctx" not in hosted and local["num_ctx"] == DEFAULT_NUM_CTX

    recorder.calls.clear()
    recorder.fail_models = ("ollama_chat/qwen3:4b",)
    monkeypatch.setenv("CHIMERA_FALLBACK_MODELS", "openrouter/openai/gpt-4o")
    get_settings.cache_clear()
    LLMGateway().complete(_messages(), model="ollama_chat/qwen3:4b")
    local, hosted = recorder.calls
    assert local["num_ctx"] == DEFAULT_NUM_CTX and "num_ctx" not in hosted


def test_a_callers_own_num_ctx_wins(recorder: _Recorder) -> None:
    """A bench that pins its own window (as `bench/report_defect` did) keeps it."""
    LLMGateway().complete(_messages(), model="ollama_chat/qwen3:4b", num_ctx=4096)

    assert recorder.calls[0]["num_ctx"] == 4096


def test_an_embedder_is_not_asked_for_the_chat_window(recorder: _Recorder) -> None:
    """An embedding model has its own, smaller window; the chat one would only reload it bigger."""
    LLMGateway().embed(["a"], model="ollama/nomic-embed-text")

    assert "num_ctx" not in recorder.calls[0]


# --- the compaction budget is the served window ----------------------------------------------------


def test_an_ollama_model_is_budgeted_against_the_window_it_is_served(armed: None) -> None:
    """Budgeted against the 128,000 fallback, a run grew past Ollama's window long before it would
    compact, and every later step was read cut. The budget now fires inside the served window."""
    assert window_tokens("ollama_chat/qwen3:4b") == DEFAULT_NUM_CTX
    assert ContextBudget.for_model("ollama_chat/qwen3:4b").threshold < DEFAULT_NUM_CTX
    assert window_tokens("openrouter/some/unknown-model-xyz") == FALLBACK_CONTEXT_TOKENS


def test_with_no_window_sent_the_budget_is_what_it_was(armed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_OLLAMA_NUM_CTX", "0")
    get_settings.cache_clear()

    assert window_tokens("ollama_chat/some-local-tag-xyz") == FALLBACK_CONTEXT_TOKENS


# --- the silence: a cut prompt is said out loud ----------------------------------------------------


def test_the_rule_reads_the_measured_cuts_and_not_a_whole_prompt() -> None:
    # The bench: ~20,500 characters read as 2,050 tokens, under the 4,096 default (num_ctx not sent).
    assert truncation_suspected(CUT_COUNT, 20_500, 0)
    # The same cut while asking for 4,096 explicitly: the half-window band.
    assert truncation_suspected(CUT_COUNT, 20_500, 4_096)
    # decisions/local: a 30,314-token prompt read as 8,194 under 16,384 (~5.5 characters a token).
    assert truncation_suspected(8_194, int(30_314 * 5.5), 16_384)
    # A code-heavy prompt just past the window (34,000 tokens at ~3.5 characters a token, read as
    # half of 32,768): only ~7.3 characters a token, under the loose rule, caught by the band.
    assert truncation_suspected(16_386, 34_000 * 7 // 2, DEFAULT_NUM_CTX)
    # A whole prompt, read whole: code/JSON ~3.5 characters a token, prose ~5.5.
    assert not truncation_suspected(5_900, 20_500, DEFAULT_NUM_CTX)
    assert not truncation_suspected(20_000, 20_000 * 5, DEFAULT_NUM_CTX)
    # A prompt in the half-window band that FITS (it is between half and the whole window).
    assert not truncation_suspected(20_000, int(20_000 * 3.5), DEFAULT_NUM_CTX)
    # No count is no evidence (a cache hit, an older server), and a short request is not judged.
    assert not truncation_suspected(None, 20_500, DEFAULT_NUM_CTX)
    assert not truncation_suspected(0, 20_500, DEFAULT_NUM_CTX)
    assert not truncation_suspected(10, 1_000, DEFAULT_NUM_CTX)


def test_the_decision_backend_and_the_gateway_share_one_rule() -> None:
    """`chimera.decisions.local` raises on the same half-window budget; its number did not move."""
    assert PROMPT_BUDGET == prompt_budget(DECISION_NUM_CTX) == 8_192


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if "PROMPT TRUNCATED" in r.getMessage()]


@pytest.mark.parametrize("route", ["batch", "stream"])
def test_a_cut_ollama_prompt_is_warned_about(
    armed: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, route: str
) -> None:
    """The bench's request and the count Ollama gave back for it, through LiteLLM's real parser."""
    answer = _chat_stream(prompt_eval_count=CUT_COUNT) if route == "stream" else _chat(prompt_eval_count=CUT_COUNT)
    _serve(monkeypatch, [answer])
    gateway = LLMGateway()
    with caplog.at_level(logging.WARNING, logger="chimera.providers.gateway"):
        if route == "stream":
            result = gateway.stream_complete(_messages(BIG_PROMPT), model="ollama_chat/qwen3:4b", temperature=0)
        else:
            result = gateway.complete(_messages(BIG_PROMPT), model="ollama_chat/qwen3:4b", temperature=0)

    assert result.content == "done"  # warned, not refused: the caller still gets its answer
    (line,) = _warnings(caplog)
    assert "ollama_chat/qwen3:4b" in line and str(CUT_COUNT) in line and "CHIMERA_OLLAMA_NUM_CTX" in line


@pytest.mark.parametrize("route", ["batch", "stream"])
def test_a_whole_ollama_prompt_is_not_warned_about(
    armed: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, route: str
) -> None:
    """The same request read whole: ~4 characters a token is an ordinary count."""
    whole = len(BIG_PROMPT) // 4
    answer = _chat_stream(prompt_eval_count=whole) if route == "stream" else _chat(prompt_eval_count=whole)
    _serve(monkeypatch, [answer])
    gateway = LLMGateway()
    with caplog.at_level(logging.WARNING, logger="chimera.providers.gateway"):
        if route == "stream":
            gateway.stream_complete(_messages(BIG_PROMPT), model="ollama_chat/qwen3:4b", temperature=0)
        else:
            gateway.complete(_messages(BIG_PROMPT), model="ollama_chat/qwen3:4b", temperature=0)

    assert _warnings(caplog) == []


def test_a_hosted_model_is_never_judged(
    armed: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A hosted provider refuses an overflow with an error; only Ollama cuts in silence."""
    _serve(monkeypatch, [_openai()])  # 3 prompt tokens for 20,500 characters
    with caplog.at_level(logging.WARNING, logger="chimera.providers.gateway"):
        LLMGateway().complete(_messages(BIG_PROMPT), model="openrouter/openai/gpt-4o", temperature=0)

    assert _warnings(caplog) == []


def test_an_image_is_not_counted_as_text() -> None:
    """A base64 picture is hundreds of thousands of characters and a few hundred tokens."""
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 500_000}}
    sent = [{"role": "user", "content": [{"type": "text", "text": "what is this?"}, image]}]

    assert gateway_module._request_chars(sent, None) == len("what is this?")


def test_the_routes_are_ollamas() -> None:
    assert is_ollama_route("ollama_chat/qwen3:4b") and is_ollama_route("ollama/llama3")
    assert not is_ollama_route("openrouter/ollama/x") and not is_ollama_route("lm_studio/m")


@pytest.mark.parametrize("path", ["async", "raw_stream"])
@pytest.mark.parametrize("count", [CUT_COUNT, len(BIG_PROMPT) // 4], ids=["cut", "whole"])
def test_the_other_paths_warn_on_a_cut_and_only_on_a_cut(
    recorder: _Recorder, caplog: pytest.LogCaptureFixture, path: str, count: int
) -> None:
    import asyncio

    recorder.prompt_tokens = count
    gateway = LLMGateway()
    with caplog.at_level(logging.WARNING, logger="chimera.providers.gateway"):
        if path == "async":
            asyncio.run(gateway.acomplete(_messages(BIG_PROMPT), model="ollama_chat/qwen3:4b"))
        else:
            assert "".join(gateway.stream(_messages(BIG_PROMPT), model="ollama_chat/qwen3:4b")) == "done"

    assert len(_warnings(caplog)) == (1 if count == CUT_COUNT else 0)
