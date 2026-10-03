"""A word in an error message is not a content-policy verdict; the status and the class are.

Study 29 P0.5. `classify` read any message containing ``flagged`` or ``safety`` as CONTENT_POLICY,
and CONTENT_POLICY maps to ABORT: the turn stops and ``CHIMERA_FALLBACK_MODELS`` is never tried.
The critic's objection — "the status is read first" — holds only for the seven statuses in
``_BY_STATUS``. Measured against the real LiteLLM classes, everything else reaches the prose:
``InternalServerError`` and ``APIConnectionError`` (500), ``Timeout`` (408), ``NotFoundError``
(404), and any ``APIError`` carrying a status LiteLLM has no class for — which is how LiteLLM
re-raises an OpenRouter 403 or 500. The prose rule also ran BEFORE the not-found and timeout rules,
so a 404 for a model whose slug says "safety" aborted instead of changing model.

The words are not rare in a coding agent's traffic. A provider error body quotes the prompt back
(measured, see test_what_a_failed_call_leaves_behind.py), and "thread-safety" or "the flagged rows"
are ordinary things to ask about. Guard-model slugs carry the word in their name.

The messages below are shaped on what each provider and LiteLLM write (LiteLLM's own mapping table
names the OpenAI, Azure and Anthropic phrases), built locally — no call was made to draw them.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import litellm
import pytest

from chimera.config import get_settings
from chimera.providers.failover import FailoverReason, RecoveryAction, action_for, classify

# --- a genuine content-policy refusal still stops the turn --------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        litellm.ContentPolicyViolationError(
            message=(
                "ContentPolicyViolationError: OpenAIException - Your request was rejected as a"
                " result of our safety system. Your prompt may contain text that is not allowed by"
                " our safety system."
            ),
            model="gpt-4o",
            llm_provider="openai",
        ),
        litellm.ContentPolicyViolationError(
            message=(
                "AzureException - The response was filtered due to the prompt triggering Azure"
                " OpenAI's content management policy."
            ),
            model="azure/gpt-4o",
            llm_provider="azure",
        ),
        # Anthropic's wording is "content filtering policy", which none of the substrings match:
        # the class name is what carries it, and it must keep carrying it at any status.
        litellm.ContentPolicyViolationError(
            message="AnthropicError - Output blocked by content filtering policy",
            model="vertex_ai/claude-sonnet-4",
            llm_provider="vertex_ai",
        ),
        # OpenRouter passes an upstream refusal on as a 400 with the provider's text inside the
        # body; LiteLLM re-raises it as a plain BadRequestError, so the prose is all there is.
        litellm.BadRequestError(
            message=(
                'OpenrouterException - {"error":{"message":"Provider returned error","code":400,'
                '"metadata":{"raw":"Invalid prompt: your prompt was flagged as potentially'
                ' violating our usage policy.","provider_name":"OpenAI"}}}'
            ),
            model="openrouter/openai/gpt-4o",
            llm_provider="openrouter",
        ),
        litellm.BadRequestError(
            message="GeminiException - The request was blocked by the safety filters.",
            model="gemini/gemini-2.5-flash",
            llm_provider="gemini",
        ),
    ],
    ids=["openai-cpv", "azure-cpv", "anthropic-cpv-class-only", "openrouter-400", "gemini-400"],
)
def test_a_genuine_content_policy_refusal_still_aborts(exc: BaseException) -> None:
    assert classify(exc) is FailoverReason.CONTENT_POLICY
    assert action_for(classify(exc)) is RecoveryAction.ABORT


def test_a_statusless_error_that_names_the_content_policy_still_aborts() -> None:
    # No LiteLLM class arrives without a status (APIConnectionError carries 500, Timeout 408), so a
    # status-less error came from somewhere else and only its words are left. The full phrase names
    # the thing; the bare words do not, which is the next test.
    assert classify(RuntimeError("request blocked by the content policy")) is (
        FailoverReason.CONTENT_POLICY
    )


def test_a_statusless_error_with_only_a_bare_word_is_not_read_as_policy() -> None:
    reason = classify(RuntimeError("worker crashed while checking thread-safety of the patch"))
    assert reason is not FailoverReason.CONTENT_POLICY
    assert action_for(reason) is not RecoveryAction.ABORT


# --- a server error that happens to say the word falls back instead -----------------------------


@pytest.mark.parametrize(
    "exc,expected",
    [
        # A 500 whose body echoes the prompt, and the prompt was about thread-safety.
        (
            litellm.InternalServerError(
                message=(
                    'OpenrouterException - {"error":{"message":"Internal Server Error","code":500,'
                    '"metadata":{"raw":"upstream failed on input: review the thread-safety of'
                    ' the cache"}}}'
                ),
                model="openrouter/deepseek/deepseek-chat",
                llm_provider="openrouter",
            ),
            FailoverReason.UNKNOWN,
        ),
        # Anthropic's overload, which LiteLLM re-raises as InternalServerError (500, not 529).
        (
            litellm.InternalServerError(
                message=(
                    'AnthropicError - {"type":"error","error":{"type":"overloaded_error",'
                    '"message":"Overloaded"}} while answering: list the flagged transactions'
                ),
                model="anthropic/claude-sonnet-4",
                llm_provider="anthropic",
            ),
            FailoverReason.OVERLOADED,
        ),
        # A guard model whose slug carries the word, gone from the router: a 404 must change model.
        (
            litellm.NotFoundError(
                message=(
                    'NotFoundError: OpenrouterException - {"error":{"message":"No endpoints found'
                    ' for nvidia/llama-3.1-nemoguard-8b-content-safety.","code":404}}'
                ),
                model="openrouter/nvidia/llama-3.1-nemoguard-8b-content-safety",
                llm_provider="openrouter",
            ),
            FailoverReason.MODEL_NOT_FOUND,
        ),
        (
            litellm.Timeout(
                message=(
                    "Timeout Error: OpenrouterException - Request timed out for model"
                    " llama-3.1-nemoguard-8b-content-safety"
                ),
                model="openrouter/nvidia/llama-3.1-nemoguard-8b-content-safety",
                llm_provider="openrouter",
            ),
            FailoverReason.TIMEOUT,
        ),
        (
            litellm.APIConnectionError(
                message="APIConnectionError: OpenrouterException - connection reset; safety net retry exhausted",
                model="openrouter/qwen/qwen3-coder",
                llm_provider="openrouter",
            ),
            FailoverReason.UNKNOWN,
        ),
        # How LiteLLM re-raises an OpenRouter status it has no class for.
        (
            litellm.APIError(
                status_code=500,
                message="APIError: OpenrouterException - upstream safety classifier unavailable",
                llm_provider="openrouter",
                model="openrouter/meta-llama/llama-4-maverick",
            ),
            FailoverReason.UNKNOWN,
        ),
    ],
    ids=["or-500-echo", "anthropic-overload", "or-404-guard-slug", "timeout", "conn", "apierror-500"],
)
def test_a_server_error_mentioning_safety_or_flagged_does_not_abort(
    exc: BaseException, expected: FailoverReason
) -> None:
    assert classify(exc) is expected
    assert action_for(classify(exc)) is not RecoveryAction.ABORT


# --- through the gateway: the fallback model is reached, or not, accordingly --------------------


def _resp(content: str) -> SimpleNamespace:
    msg = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None, model="m")


def _run(monkeypatch: pytest.MonkeyPatch, primary_error: BaseException) -> tuple[Any, list[str]]:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("CHIMERA_FALLBACK_MODELS", "prov/backup")
    get_settings.cache_clear()
    seen: list[str] = []

    def fake(*, model: str, **_: Any) -> SimpleNamespace:
        seen.append(model)
        if model == "prov/primary":
            raise primary_error
        return _resp("ok-from-backup")

    monkeypatch.setattr(litellm, "completion", fake)
    from chimera.providers import LLMGateway
    from chimera.providers.gateway import Message

    try:
        result: Any = LLMGateway().complete([Message(role="user", content="hi")], model="prov/primary")
    except Exception as exc:  # noqa: BLE001 — the test reads what came out either way
        result = exc
    return result, seen


def test_a_500_that_echoes_thread_safety_reaches_the_fallback_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = litellm.InternalServerError(
        message="OpenrouterException - upstream failed on input: review the thread-safety of the cache",
        model="openrouter/prov/primary",
        llm_provider="openrouter",
    )
    result, seen = _run(monkeypatch, error)
    assert seen == ["prov/primary", "prov/backup"]
    assert result.content == "ok-from-backup"


def test_a_content_policy_violation_does_not_reach_the_fallback_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = litellm.ContentPolicyViolationError(
        message="OpenAIException - Your request was rejected as a result of our safety system.",
        model="prov/primary",
        llm_provider="openai",
    )
    result, seen = _run(monkeypatch, error)
    assert seen == ["prov/primary"]
    assert isinstance(result, litellm.ContentPolicyViolationError)
