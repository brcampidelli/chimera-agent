"""Provider-agnostic LLM gateway.

A thin wrapper over `LiteLLM <https://docs.litellm.ai/>`_ so the rest of Chimera
speaks to 100+ models through one interface using ``provider/model`` slugs
(e.g. ``openrouter/anthropic/claude-opus-4-8``). This is the single seam every
other subsystem — including the fusion engine — calls to reach a model.

LiteLLM is imported lazily so that importing this module (and thus the CLI) stays
fast and never fails just because a provider SDK or key is missing.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from chimera.config import Settings, get_settings
from chimera.providers.cache import CompletionCache
from chimera.providers.failover import (
    CredentialPool,
    FailoverReason,
    RecoveryAction,
    action_for,
    classify,
)
from chimera.providers.prompt_cache import apply_cache_control
from chimera.telemetry import get_logger

_log = get_logger("providers.gateway")

Role = Literal["system", "user", "assistant", "tool"]

# Maps our settings field -> the env var LiteLLM expects.
_KEY_ENV_VARS = {
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
}


def _image_to_url(ref: str) -> str:
    """A URL/data-URI passes through; a local path is read and base64 data-encoded."""
    if ref.startswith(("http://", "https://", "data:")):
        return ref
    path = Path(ref)
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


class Message(BaseModel):
    """A single chat message, optionally carrying images (for vision models)."""

    role: Role
    content: str
    images: list[str] = Field(default_factory=list)  # local paths or URLs

    def as_dict(self) -> dict[str, Any]:
        if not self.images:
            return {"role": self.role, "content": self.content}
        # OpenAI/LiteLLM multimodal format: content becomes a list of typed parts.
        parts: list[dict[str, Any]] = []
        if self.content:
            parts.append({"type": "text", "text": self.content})
        for ref in self.images:
            parts.append({"type": "image_url", "image_url": {"url": _image_to_url(ref)}})
        return {"role": self.role, "content": parts}


class ToolCall(BaseModel):
    """A single tool/function call requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class CompletionResult(BaseModel):
    """Normalized result of a single model call."""

    content: str
    model: str
    tool_calls: list[ToolCall] | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cache_read_tokens: int | None = None
    """Prompt-cache HITS the provider reported (billed at the read rate). None = unknown."""
    cache_write_tokens: int | None = None
    """Prompt-cache WRITES the provider reported (billed at the write rate). None = unknown."""
    raw: dict[str, Any] | None = Field(default=None, repr=False)


MessageLike = Message | dict[str, Any]


def _to_message_dicts(messages: list[MessageLike]) -> list[dict[str, Any]]:
    return [m.as_dict() if isinstance(m, Message) else m for m in messages]


class SupportsComplete(Protocol):
    """Structural type for anything that answers a chat completion.

    The single-model :class:`LLMGateway` satisfies it today; the LLM-Fusion engine
    (M2) will satisfy it too, so agents and skills can depend on this interface
    rather than a concrete backend.
    """

    def complete(
        self,
        messages: list[MessageLike],
        *,
        model: str | None = ...,
        temperature: float = ...,
        max_tokens: int | None = ...,
        tools: list[dict[str, Any]] | None = ...,
        **kwargs: Any,
    ) -> CompletionResult: ...


class SupportsStream(Protocol):
    """A backend that can stream a completion, pushing text deltas to ``on_delta``.

    Only the single-model :class:`LLMGateway` implements this; the composite backends (fusion /
    cascade / budgeted) expose ``complete`` only. Consumers feature-detect with ``hasattr(backend,
    "stream_complete")`` and fall back to blocking :meth:`complete` when it is absent — so streaming
    is a progressive enhancement, never a hard requirement.
    """

    def stream_complete(
        self,
        messages: list[MessageLike],
        *,
        model: str | None = ...,
        temperature: float = ...,
        max_tokens: int | None = ...,
        tools: list[dict[str, Any]] | None = ...,
        on_delta: Callable[[str], None] | None = ...,
        **kwargs: Any,
    ) -> CompletionResult: ...


class MissingCredentialsError(RuntimeError):
    """Raised when a model call is attempted but no provider key is configured."""


class _KeyRotator:
    """Round-robin over a provider's credential pool, thread-safe.

    The fusion panel calls the gateway from multiple threads at once, so the
    rotating index is guarded by a lock. ``order()`` returns every key (so a
    single call can fail over across keys) starting at the rotating offset, and
    advances the offset by one so successive calls spread load round-robin.
    """

    def __init__(self, keys: list[str]) -> None:
        self._keys = keys
        self._index = 0
        self._lock = threading.Lock()

    def order(self) -> list[str]:
        if not self._keys:
            return []
        n = len(self._keys)
        with self._lock:
            start = self._index
            self._index = (self._index + 1) % n
        return [self._keys[(start + offset) % n] for offset in range(n)]


class LLMGateway:
    """Calls LLMs through LiteLLM with Chimera's defaults.

    Parameters
    ----------
    settings:
        Optional settings override; defaults to the process-wide settings. On
        construction, any configured provider keys are exported to ``os.environ``
        so LiteLLM (which reads from the environment) can see keys that were only
        provided via ``.env``.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._rotators: dict[str, _KeyRotator] = {}
        # M15-C2: per-credential cooldown pool — a rate-limited/revoked key is rested, not hammered.
        self._cred_pool = CredentialPool()
        self.cache: CompletionCache | None = (
            CompletionCache(self.settings.home / "cache" / "completions.json")
            if self.settings.cache
            else None
        )
        self._export_keys_to_env()

    def _export_keys_to_env(self) -> None:
        for field, env_var in _KEY_ENV_VARS.items():
            value = getattr(self.settings, field, None)
            if value and not os.environ.get(env_var):
                os.environ[env_var] = value

    def _resolve_model(self, model: str | None) -> str:
        return model or self.settings.default_model

    def _provider_kwargs(self) -> dict[str, Any]:
        """Extra litellm kwargs — a custom endpoint for self-hosted/compatible servers."""
        return {"api_base": self.settings.api_base} if self.settings.api_base else {}

    def _model_candidates(self, resolved: str) -> list[str]:
        """The primary model followed by any configured fallbacks, in order, deduped."""
        candidates = [resolved]
        for fallback in self.settings.fallback_models:
            if fallback and fallback not in candidates:
                candidates.append(fallback)
        return candidates

    def _key_order(self, provider: str) -> list[str]:
        """Keys to try for a provider this call ([] when there is no pool/key).

        Empty means "let LiteLLM read the key from the environment" (today's
        behaviour); a non-empty pool is rotated round-robin across calls.
        """
        rotator = self._rotators.get(provider)
        if rotator is None:
            rotator = _KeyRotator(self.settings.credential_pool(provider))
            self._rotators[provider] = rotator
        order = rotator.order()
        # Skip keys still cooling down from a recent failure; if ALL are cooling, try them anyway
        # (a stale cooldown must never leave the gateway with zero keys and a hard failure).
        return self._cred_pool.available(order) or order

    def complete(
        self,
        messages: list[MessageLike],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        """Run a synchronous chat completion (with the fallback chain) and normalize it."""
        import litellm  # lazy: heavy import, keep CLI startup fast

        resolved = self._resolve_model(model)
        if not self.settings.has_any_key():
            raise MissingCredentialsError(
                "No provider key configured. Set one of "
                f"{list(_KEY_ENV_VARS.values())} in your environment or .env."
            )

        extra = self._provider_kwargs()
        message_dicts = _to_message_dicts(messages)

        # HORIZON-style prompt caching: serve an identical tool-free request from cache.
        cache_key: str | None = None
        if self.cache is not None and tools is None:
            # Fold the other response-affecting request fields into the key so e.g. two calls that
            # differ only in top_p / seed / stop / response_format / api_base don't collide.
            key_params = {k: v for k, v in {**extra, **kwargs}.items() if k != "api_key"}
            cache_key = CompletionCache.key(
                model=resolved,
                messages=message_dicts,
                temperature=temperature,
                max_tokens=max_tokens,
                params=key_params,
            )
            hit = self.cache.get(cache_key)
            if hit is not None:
                _log.debug("cache hit model=%s", resolved)
                # A cache hit made NO API call and billed nothing: report 0 fresh tokens and surface
                # the original count under cache_read_tokens, so a cost tally doesn't double-count a $0
                # served-from-cache turn as if the tokens were spent again.
                cached_total = (hit.get("prompt_tokens") or 0) + (hit.get("completion_tokens") or 0)
                return CompletionResult(
                    content=hit.get("content", ""),
                    model=hit.get("model", resolved),
                    prompt_tokens=0,
                    completion_tokens=0,
                    cache_read_tokens=cached_total or None,
                )

        last_exc: Exception | None = None
        for candidate in self._model_candidates(resolved):
            provider = candidate.split("/", 1)[0]
            api_keys: tuple[str | None, ...] = tuple(self._key_order(provider)) or (None,)
            next_model = False
            for api_key in api_keys:
                call_kwargs: dict[str, Any] = dict(extra, **kwargs)
                if api_key:
                    call_kwargs["api_key"] = api_key
                call_messages = message_dicts
                if self.settings.prompt_cache:
                    call_messages = apply_cache_control(message_dicts, candidate)
                try:
                    _log.debug("completion model=%s msgs=%d", candidate, len(messages))
                    response = litellm.completion(
                        model=candidate,
                        messages=call_messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        tools=tools,
                        **call_kwargs,
                    )
                    if api_key:
                        self._cred_pool.reset(api_key)  # a working key clears its cooldown
                    result = self._normalize(response, candidate)
                    # Only cache when the PRIMARY model answered: the key is derived from `resolved`,
                    # so storing a fallback's answer under it would later serve the weaker fallback for
                    # a primary request even after the primary recovers.
                    if (
                        cache_key is not None
                        and self.cache is not None
                        and result.tool_calls is None
                        and candidate == resolved
                    ):
                        self.cache.put(
                            cache_key,
                            {
                                "content": result.content,
                                "model": result.model,
                                "prompt_tokens": result.prompt_tokens,
                                "completion_tokens": result.completion_tokens,
                            },
                        )
                    return result
                except Exception as exc:  # noqa: BLE001 — classify, then recover per the taxonomy
                    last_exc = exc
                    reason = classify(exc)
                    action = action_for(reason)
                    if api_key and reason in (FailoverReason.AUTH, FailoverReason.RATE_LIMIT,
                                              FailoverReason.OVERLOADED, FailoverReason.TIMEOUT,
                                              FailoverReason.UNKNOWN):
                        self._cred_pool.penalize(api_key, reason)  # rest this credential
                    _log.warning("model %s failed (%s -> %s): %s", candidate, reason.value, action.value, exc)
                    if action is RecoveryAction.ABORT:
                        raise  # context-overflow / content-policy: another key/model won't help
                    if action is RecoveryAction.FALLBACK_MODEL:
                        next_model = True
                        break  # skip the remaining keys, go straight to the next model
                    # ROTATE_KEY: fall through to the next credential
            if next_model:
                continue
        assert last_exc is not None  # there is always at least one (candidate, key) attempt
        raise last_exc

    async def acomplete(
        self,
        messages: list[MessageLike],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        """Async variant of :meth:`complete` (used by the parallel fusion panel)."""
        import litellm

        resolved = self._resolve_model(model)
        if not self.settings.has_any_key():
            raise MissingCredentialsError(
                "No provider key configured. Set one of "
                f"{list(_KEY_ENV_VARS.values())} in your environment or .env."
            )

        response = await litellm.acompletion(
            model=resolved,
            messages=_to_message_dicts(messages),
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            **self._provider_kwargs(),
            **kwargs,
        )
        return self._normalize(response, resolved)

    def quick(self, prompt: str, *, model: str | None = None, system: str | None = None) -> str:
        """Convenience single-turn helper returning just the text."""
        messages: list[MessageLike] = []
        if system:
            messages.append(Message(role="system", content=system))
        messages.append(Message(role="user", content=prompt))
        return self.complete(messages, model=model).content

    def stream(
        self,
        messages: list[MessageLike],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        """Yield model output token-by-token (the raw streaming primitive).

        The low-level seam the live terminal, messaging gateway and A2A ``message/stream``
        endpoint build on. No fallback chain or cache — streaming is a single, direct call.
        """
        import litellm  # lazy: heavy import, keep CLI startup fast

        resolved = self._resolve_model(model)
        if not self.settings.has_any_key():
            raise MissingCredentialsError(
                "No provider key configured. Set one of "
                f"{list(_KEY_ENV_VARS.values())} in your environment or .env."
            )
        call_kwargs = dict(self._provider_kwargs(), **kwargs)
        keys = self._key_order(resolved.split("/", 1)[0])
        if keys:
            call_kwargs["api_key"] = keys[0]
        response = litellm.completion(
            model=resolved,
            messages=_to_message_dicts(messages),
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            **call_kwargs,
        )
        for chunk in response:
            text = _delta_text(chunk)
            if text:
                yield text

    def stream_complete(
        self,
        messages: list[MessageLike],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        on_delta: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        """Streaming completion that still returns a normalized :class:`CompletionResult`.

        A drop-in for :meth:`complete` for the interactive/single-model path: it streams the model's
        text (each delta pushed to ``on_delta``) while reassembling the full content, any tool-call
        deltas (by index) and the final usage — so the agent loop keeps its structure and still sees
        ``tool_calls``. Like :meth:`stream`, this is one direct call: NO fallback chain and NO cache
        (both meaningless for a live stream). Callers that need those keep using :meth:`complete`.
        """
        import litellm  # lazy: heavy import, keep CLI startup fast

        resolved = self._resolve_model(model)
        if not self.settings.has_any_key():
            raise MissingCredentialsError(
                "No provider key configured. Set one of "
                f"{list(_KEY_ENV_VARS.values())} in your environment or .env."
            )
        call_kwargs = dict(self._provider_kwargs(), **kwargs)
        keys = self._key_order(resolved.split("/", 1)[0])
        if keys:
            call_kwargs["api_key"] = keys[0]
        response = litellm.completion(
            model=resolved,
            messages=_to_message_dicts(messages),
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            stream=True,
            stream_options={"include_usage": True},  # ask the provider for a final usage chunk
            **call_kwargs,
        )
        content: list[str] = []
        tool_acc: dict[int, dict[str, Any]] = {}
        usage: dict[str, int | None] = {}
        for chunk in response:
            text = _delta_text(chunk)
            if text:
                content.append(text)
                if on_delta is not None:
                    on_delta(text)
            _delta_tool_calls(chunk, tool_acc)
            _accumulate_stream_usage(chunk, usage)
        return CompletionResult(
            content="".join(content),
            model=resolved,
            tool_calls=_finalize_stream_tool_calls(tool_acc),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            cache_read_tokens=usage.get("cache_read_tokens"),
            cache_write_tokens=usage.get("cache_write_tokens"),
        )

    def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        """Embed a batch of texts into vectors (one call), for semantic memory recall.

        Uses ``settings.embed_model`` unless overridden. Returns vectors in input order.
        Raises :class:`MissingCredentialsError` if no key is configured — callers that want
        graceful degradation (e.g. ``MemoryManager.search``) catch and fall back to keyword.
        """
        import litellm  # lazy: heavy import, keep CLI startup fast

        if not texts:
            return []
        resolved = model or self.settings.embed_model
        if not self.settings.has_any_key():
            raise MissingCredentialsError(
                "No provider key configured. Set one of "
                f"{list(_KEY_ENV_VARS.values())} in your environment or .env."
            )
        call_kwargs = self._provider_kwargs()
        provider = resolved.split("/", 1)[0]
        keys = self._key_order(provider)
        if keys:
            call_kwargs["api_key"] = keys[0]
        response = litellm.embedding(model=resolved, input=texts, **call_kwargs)
        return [list(item["embedding"]) for item in response["data"]]

    @staticmethod
    def _normalize(response: Any, model: str) -> CompletionResult:
        content = ""
        tool_calls: list[ToolCall] | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        try:
            message = response.choices[0].message
            content = message.content or ""
            tool_calls = LLMGateway._parse_tool_calls(message)
        except (AttributeError, IndexError, TypeError):
            _log.warning("could not extract content from response for model=%s", model)
        cache_read_tokens: int | None = None
        cache_write_tokens: int | None = None
        usage = getattr(response, "usage", None)
        if usage is not None:
            prompt_tokens = getattr(usage, "prompt_tokens", None)
            completion_tokens = getattr(usage, "completion_tokens", None)
            cache_read_tokens, cache_write_tokens = LLMGateway._extract_cache_tokens(usage)
        return CompletionResult(
            content=content,
            model=model,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        )

    @staticmethod
    def _extract_cache_tokens(usage: Any) -> tuple[int | None, int | None]:
        """Read prompt-cache accounting across provider shapes (Anthropic + OpenAI-style)."""
        # Anthropic (via litellm): cache_read_input_tokens / cache_creation_input_tokens.
        read = getattr(usage, "cache_read_input_tokens", None)
        write = getattr(usage, "cache_creation_input_tokens", None)
        # OpenAI-style: prompt_tokens_details.cached_tokens (read only, no write line).
        if read is None:
            details = getattr(usage, "prompt_tokens_details", None)
            if details is not None:
                read = getattr(details, "cached_tokens", None)
                if read is None and isinstance(details, dict):
                    read = details.get("cached_tokens")
        return read, write

    @staticmethod
    def _parse_tool_calls(message: Any) -> list[ToolCall] | None:
        raw_calls = getattr(message, "tool_calls", None)
        if not raw_calls:
            return None
        parsed: list[ToolCall] = []
        for call in raw_calls:
            fn = getattr(call, "function", None)
            if fn is None:
                continue
            raw_args = getattr(fn, "arguments", None)
            arguments: dict[str, Any] = {}
            if isinstance(raw_args, str):
                try:
                    loaded = json.loads(raw_args or "{}")
                    if isinstance(loaded, dict):
                        arguments = loaded
                except json.JSONDecodeError:
                    _log.warning("could not parse tool arguments: %r", raw_args)
            elif isinstance(raw_args, dict):
                arguments = raw_args
            parsed.append(
                ToolCall(id=getattr(call, "id", "") or "", name=fn.name, arguments=arguments)
            )
        return parsed or None


def _delta_text(chunk: Any) -> str:
    """Extract the incremental text from a streaming chunk, defensively (empty on any shape)."""
    try:
        delta = chunk.choices[0].delta
        return str(delta.content or "")
    except (AttributeError, IndexError, TypeError):
        return ""


def _delta_tool_calls(chunk: Any, acc: dict[int, dict[str, Any]]) -> None:
    """Merge a chunk's streamed tool-call deltas into ``acc`` (keyed by call index).

    Providers stream a tool call in fragments: the name/id arrive once, the JSON arguments arrive as
    a run of string pieces. We accumulate name/id (first non-empty wins) and concatenate arguments;
    :func:`_finalize_stream_tool_calls` JSON-parses them at the end. Defensive: any odd shape is a
    no-op, never a raise.
    """
    try:
        deltas = chunk.choices[0].delta.tool_calls
    except (AttributeError, IndexError, TypeError):
        return
    if not deltas:
        return
    for delta in deltas:
        index = getattr(delta, "index", None)
        if index is None:
            index = len(acc)
        slot = acc.setdefault(int(index), {"id": "", "name": "", "arguments": ""})
        call_id = getattr(delta, "id", None)
        if call_id and not slot["id"]:
            slot["id"] = str(call_id)
        fn = getattr(delta, "function", None)
        if fn is not None:
            name = getattr(fn, "name", None)
            if name and not slot["name"]:
                slot["name"] = str(name)
            args = getattr(fn, "arguments", None)
            if args:
                slot["arguments"] += str(args)


def _finalize_stream_tool_calls(acc: dict[int, dict[str, Any]]) -> list[ToolCall] | None:
    """Turn accumulated tool-call fragments into ``ToolCall``s (JSON-parsing the arguments)."""
    if not acc:
        return None
    calls: list[ToolCall] = []
    for index in sorted(acc):
        slot = acc[index]
        if not slot.get("name"):
            continue  # a fragment with no name is not a usable call
        arguments: dict[str, Any] = {}
        raw = slot.get("arguments") or ""
        if raw:
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    arguments = loaded
            except json.JSONDecodeError:
                _log.warning("could not parse streamed tool arguments: %r", raw)
        calls.append(ToolCall(id=slot.get("id") or "", name=slot["name"], arguments=arguments))
    return calls or None


def _accumulate_stream_usage(chunk: Any, state: dict[str, int | None]) -> None:
    """Capture token usage from the trailing ``include_usage`` chunk (defensive, last-wins)."""
    usage = getattr(chunk, "usage", None)
    if usage is None:
        return
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    if prompt is not None:
        state["prompt_tokens"] = prompt
    if completion is not None:
        state["completion_tokens"] = completion
    read, write = LLMGateway._extract_cache_tokens(usage)
    if read is not None:
        state["cache_read_tokens"] = read
    if write is not None:
        state["cache_write_tokens"] = write
