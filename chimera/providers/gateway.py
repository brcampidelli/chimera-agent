"""Provider-agnostic LLM gateway.

A thin wrapper over `LiteLLM <https://docs.litellm.ai/>`_ so the rest of Chimera
speaks to 100+ models through one interface using ``provider/model`` slugs
(e.g. ``openrouter/anthropic/claude-opus-4-8``). This is the single seam every
other subsystem — including the fusion engine — calls to reach a model.

LiteLLM is imported lazily so that importing this module (and thus the CLI) stays
fast and never fails just because a provider SDK or key is missing.
"""

from __future__ import annotations

import os
from typing import Any, Literal

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


class Message(BaseModel):
    """A single chat message."""

    role: Role
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class CompletionResult(BaseModel):
    """Normalized result of a single model call."""

    content: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    raw: dict[str, Any] | None = Field(default=None, repr=False)


MessageLike = Message | dict[str, str]


def _to_message_dicts(messages: list[MessageLike]) -> list[dict[str, str]]:
    return [m.as_dict() if isinstance(m, Message) else m for m in messages]


class MissingCredentialsError(RuntimeError):
    """Raised when a model call is attempted but no provider key is configured."""


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
        self._export_keys_to_env()

    def _export_keys_to_env(self) -> None:
        for field, env_var in _KEY_ENV_VARS.items():
            value = getattr(self.settings, field, None)
            if value and not os.environ.get(env_var):
                os.environ[env_var] = value

    def _resolve_model(self, model: str | None) -> str:
        return model or self.settings.default_model

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
        """Run a synchronous chat completion and return a normalized result."""
        import litellm  # lazy: heavy import, keep CLI startup fast

        resolved = self._resolve_model(model)
        if not self.settings.has_any_key():
            raise MissingCredentialsError(
                "No provider key configured. Set one of "
                f"{list(_KEY_ENV_VARS.values())} in your environment or .env."
            )

        _log.debug("completion model=%s msgs=%d", resolved, len(messages))
        response = litellm.completion(
            model=resolved,
            messages=_to_message_dicts(messages),
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            **kwargs,
        )
        return self._normalize(response, resolved)

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
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        try:
            content = response.choices[0].message.content or ""
        except (AttributeError, IndexError, TypeError):
            _log.warning("could not extract content from response for model=%s", model)
        usage = getattr(response, "usage", None)
        if usage is not None:
            prompt_tokens = getattr(usage, "prompt_tokens", None)
            completion_tokens = getattr(usage, "completion_tokens", None)
        return CompletionResult(
            content=content,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
