"""The tier ladder must be callable with the keys the user actually has.

Every cost-mode preset is a list of OpenRouter slugs. Until this check existed, a user whose only key
was Anthropic got a ladder of three models they could not call — silently, and *instead of* the model
they had configured. Picking any role profile then routed the run away from the only thing that
worked, and the error surfaced as an authentication failure from a provider they had never chosen.

That is a bad failure when the user picks the profile themselves. It is a worse one now that the
system picks for them, which is why this lands before anything auto-selects a profile.
"""

from __future__ import annotations

from typing import Any

from chimera.providers.catalog import resolve_tiers


class _S:
    """The slice of Settings the resolver duck-types, so these tests need no real config."""

    def __init__(self, providers: list[str], **kw: Any) -> None:
        self._providers = providers
        self.weak_model = kw.get("weak_model", "")
        self.mid_model = kw.get("mid_model", "")
        self.orchestrator_model = kw.get("orchestrator_model", "")
        self.cost_mode = kw.get("cost_mode", "auto")
        self.default_model = kw.get("default_model", "")

    def configured_providers(self) -> list[str]:
        return self._providers


def test_an_anthropic_only_user_gets_their_own_model_on_every_tier() -> None:
    ladder = resolve_tiers(_S(["anthropic"], default_model="anthropic/claude-opus-4-8"))

    assert ladder.ladder() == ["anthropic/claude-opus-4-8"] * 3
    assert ladder.source == "fallback_single_model"


def test_no_keys_at_all_changes_nothing() -> None:
    """A machine that is not configured yet. Nothing is reachable, so "unreachable" carries no
    information — and the presets are the documented answer until a key exists. This exception is
    also what keeps `tests/test_roles.py` (which builds a keyless Settings) passing for the right
    reason rather than by accident."""
    ladder = resolve_tiers(_S([], default_model="anthropic/claude-opus-4-8"))

    assert all(slug.startswith("openrouter/") for slug in ladder.ladder())
    assert ladder.source == "preset"


def test_an_openrouter_user_keeps_the_preset() -> None:
    ladder = resolve_tiers(_S(["openrouter"], default_model="openrouter/openai/gpt-5.5"))

    assert ladder.source == "preset"
    assert len(set(ladder.ladder())) > 1  # a real ladder, not a collapse


def test_the_provider_is_the_slug_prefix_not_the_vendor() -> None:
    """`openrouter/anthropic/claude-opus-4-8` needs an OPENROUTER key. Matching on the catalogue's
    vendor field would give the exact opposite answer for the most common case, so this pins the
    rule the gateway itself uses."""
    ladder = resolve_tiers(_S(["openrouter"], cost_mode="premium", default_model="x/y"))

    assert "openrouter/anthropic/claude-opus-4-8" in ladder.ladder()
    assert ladder.source == "preset"  # reachable through OpenRouter, so nothing was rewritten


def test_an_explicit_override_is_never_second_guessed() -> None:
    """A tier the user typed is their choice even if we think the key is missing — they may be
    running a proxy, a local gateway, or a provider we do not enumerate."""
    ladder = resolve_tiers(
        _S(["anthropic"], weak_model="mystery/model-x", default_model="anthropic/claude-opus-4-8")
    )

    assert ladder.weak == "mystery/model-x"
    assert ladder.source == "override"


def test_a_partially_reachable_ladder_is_left_alone() -> None:
    """One callable rung means the cascade still escalates through something real. Rewriting the
    reachable rungs would hide a misconfiguration the receipt is better off showing."""
    ladder = resolve_tiers(
        _S(["openrouter"], orchestrator_model="anthropic/claude-opus-4-8", default_model="openrouter/a/b")
    )

    assert ladder.top == "anthropic/claude-opus-4-8"
    assert ladder.weak.startswith("openrouter/")


def test_an_unreachable_default_is_not_swapped_in() -> None:
    """Nothing here can fix "no usable model". Inventing one would be a guess about which key to
    spend, and the honest outcome is the preset plus a failure the user can read."""
    ladder = resolve_tiers(_S(["anthropic"], default_model="openai/gpt-4"))

    assert ladder.source == "preset"


def test_no_default_model_falls_back_to_nothing_rather_than_guessing() -> None:
    ladder = resolve_tiers(_S(["anthropic"], default_model=""))

    assert ladder.source == "preset"
