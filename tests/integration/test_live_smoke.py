"""Live integration smoke test — opt-in, hits a real provider.

Skipped unless ``OPENROUTER_API_KEY`` is in the environment (CI injects it from a
repository secret). Kept to a single cheap call so the cost per CI run is
negligible. Run locally with::

    OPENROUTER_API_KEY=sk-or-... uv run pytest -m integration
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

_HAS_KEY = bool(os.environ.get("OPENROUTER_API_KEY"))
_CHEAP_MODEL = "openrouter/deepseek/deepseek-chat-v3.1"


@pytest.mark.skipif(not _HAS_KEY, reason="OPENROUTER_API_KEY not set")
def test_live_completion_smoke() -> None:
    """The gateway -> LiteLLM -> OpenRouter path returns a real completion."""
    from chimera.providers import LLMGateway
    from chimera.providers.gateway import Message

    gateway = LLMGateway()
    result = gateway.complete(
        [Message(role="user", content="Reply with exactly: OK")],
        model=_CHEAP_MODEL,
        temperature=0.0,
    )
    assert result.content.strip(), "expected a non-empty completion from the provider"
