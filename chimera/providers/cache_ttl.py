"""How long a provider keeps a prompt-cache entry after its last use.

Only numbers the provider documents belong here. A route whose lifetime is not known returns None,
and nothing downstream guesses: idle compaction does not run for it, and a miss on it is never
blamed on expiry.

Sources:

- Anthropic (``anthropic`` and ``claude`` model families, also when routed through OpenRouter): the
  ``ephemeral`` cache breakpoint that `chimera/providers/prompt_cache.py` sends has no ``ttl``, and
  Anthropic's prompt-caching documentation gives that form a 5-minute lifetime, renewed on each read
  (https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching, "Cache lifetime").
  The 1-hour form exists but is not sent here, so it is not assumed. The OpenRouter route was probed
  to read the cache on 2026-10-24 (the docstring of `prompt_cache.py`).
- Every other family: None. Their caching is automatic and their lifetimes are not written down in
  anything this project has read, so the table says so rather than inventing a number.
"""

from __future__ import annotations

from chimera.providers.prompt_cache import needs_explicit_cache_control

#: Anthropic's default ``ephemeral`` lifetime, in seconds.
ANTHROPIC_EPHEMERAL_TTL_SECONDS: float = 300.0


def explicit_cache_route(model: str) -> bool:
    """True when the route is one whose cache this project addresses with explicit breakpoints."""
    return needs_explicit_cache_control(model)


def cache_ttl_seconds(model: str) -> float | None:
    """The documented lifetime of this model's cache entries, or None when it is not known."""
    if explicit_cache_route(model):
        return ANTHROPIC_EPHEMERAL_TTL_SECONDS
    return None
