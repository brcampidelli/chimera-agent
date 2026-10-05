"""The OpenRouter route reports its cache writes.

The gateway read prompt-cache writes only from Anthropic's key (``cache_creation_input_tokens``). On
the OpenAI-shaped route — OpenRouter, the app's default — the count arrives as
``prompt_tokens_details.cache_write_tokens``, LiteLLM keeps it there on its own usage type, and the
gateway never looked. Every write on that route was recorded as ``None``: the receipt field existed,
was always empty, and nothing could price what it never saw.
"""

from __future__ import annotations

from chimera.providers.gateway import LLMGateway


def test_the_openrouter_shape_reports_its_cache_writes() -> None:
    """OpenRouter sends ``prompt_tokens_details: {cached_tokens, cache_write_tokens}``; LiteLLM keeps
    both on its own usage type. Only the read was taken, so on the default route a write was never
    recorded, and no pricer could ever have charged it."""
    from litellm.types.utils import Usage

    usage = Usage(
        prompt_tokens=1_000,
        completion_tokens=10,
        prompt_tokens_details={"cached_tokens": 800, "cache_write_tokens": 100},
    )

    assert LLMGateway._extract_cache_tokens(usage) == (800, 100)


def test_the_anthropic_shape_still_reports_both() -> None:
    from litellm.types.utils import Usage

    usage = Usage(
        prompt_tokens=1_900, completion_tokens=10,
        cache_read_input_tokens=800, cache_creation_input_tokens=100,
    )

    assert LLMGateway._extract_cache_tokens(usage) == (800, 100)
