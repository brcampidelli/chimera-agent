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
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from chimera.config import Settings, get_settings
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
        return rotator.order()

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
        last_exc: Exception | None = None
        for candidate in self._model_candidates(resolved):
            provider = candidate.split("/", 1)[0]
            api_keys: tuple[str | None, ...] = tuple(self._key_order(provider)) or (None,)
            for api_key in api_keys:
                call_kwargs: dict[str, Any] = dict(extra, **kwargs)
                if api_key:
                    call_kwargs["api_key"] = api_key
                try:
                    _log.debug("completion model=%s msgs=%d", candidate, len(messages))
                    response = litellm.completion(
                        model=candidate,
                        messages=_to_message_dicts(messages),
                        temperature=temperature,
                        max_tokens=max_tokens,
                        tools=tools,
                        **call_kwargs,
                    )
                    return self._normalize(response, candidate)
                except Exception as exc:  # noqa: BLE001 — try next key, then next model
                    last_exc = exc
                    _log.warning("model %s failed: %s", candidate, exc)
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
        usage = getattr(response, "usage", None)
        if usage is not None:
            prompt_tokens = getattr(usage, "prompt_tokens", None)
            completion_tokens = getattr(usage, "completion_tokens", None)
        return CompletionResult(
            content=content,
            model=model,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

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
