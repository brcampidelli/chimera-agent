"""A model outside the nineteen got a flat 128,000, and for 31 of 431 that is too generous.

`FALLBACK_CONTEXT_TOKENS` says what it costs in its own comment — *"over-estimating costs a dead
run"* — and `failover.py` maps `CONTEXT_OVERFLOW` to `ABORT`, so there is no recovery behind it.
Measured against OpenRouter's live index on 2026-09-05: **31 of 431 models publish a window at or
below 64,000**, and 128,000 with the default fraction puts the compaction trigger at 61,440. For
those, the budget would never fire before the wall it exists to avoid.

The number was never unknown. `available_models` downloads it for all 431 and `remember_models`
wrote down price, vision and tools while dropping the window — so the fallback was reached for
models the app had already been told about.

This also holds the ORDER, which is not arbitrary: the catalogue is checked by hand against what a
provider actually *serves*, and the index publishes what it *advertises*. Those disagree for 39 of
the 431, by up to 20%, and the hand-checked figure is the safer one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.core.context_budget import FALLBACK_CONTEXT_TOKENS, ContextBudget, window_tokens
from chimera.providers.listing import ModelOption, known_window, remember_models


def _cache(tmp_path: Path, monkeypatch: Any, models: list[ModelOption]) -> None:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    import chimera.providers.listing as listing

    monkeypatch.setattr(listing, "_index_cache", None, raising=False)
    remember_models(models)


def _option(slug: str, context_k: int | None) -> ModelOption:
    return ModelOption(
        slug=slug, label=slug, vendor="x", source="live",
        context_k=context_k, input_per_m=0.1, output_per_m=0.2,
    )


# --- the window travels now ---------------------------------------------------------------------


def test_a_window_the_index_published_is_written_down(tmp_path: Path, monkeypatch: Any) -> None:
    _cache(tmp_path, monkeypatch, [_option("openrouter/x/small", 32)])

    assert known_window("openrouter/x/small") == 32_000


def test_a_model_outside_the_catalogue_gets_its_real_window(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The whole point: 32k was reported as 128k, and the trigger then sat past the wall."""
    _cache(tmp_path, monkeypatch, [_option("openrouter/x/small", 32)])

    assert window_tokens("openrouter/x/small") == 32_000


def test_the_trigger_now_lands_inside_the_window(tmp_path: Path, monkeypatch: Any) -> None:
    """Before this, a 32k model compacted at 61,440 — which is to say, never."""
    _cache(tmp_path, monkeypatch, [_option("openrouter/x/small", 32)])

    budget = ContextBudget.for_model("openrouter/x/small", fraction=0.6)

    assert budget.window == 32_000
    assert budget.threshold < 32_000


# --- the order, and it is not arbitrary ------------------------------------------------------------


def test_the_catalogue_wins_over_the_index(tmp_path: Path, monkeypatch: Any) -> None:
    """The catalogue records what a provider SERVES; the index records what it ADVERTISES.

    Those disagree for 39 of the 431, by up to 20%, and the hand-checked figure is the safer one.
    """
    from chimera.providers.catalog import CATALOG

    entry = CATALOG[0]
    _cache(tmp_path, monkeypatch, [_option(entry.slug, 9_999)])

    assert window_tokens(entry.slug) == entry.context_k * 1000


def test_with_neither_the_constant_still_answers(tmp_path: Path, monkeypatch: Any) -> None:
    """A slug never fetched, on an install whose cache was never warmed. Unchanged behaviour."""
    _cache(tmp_path, monkeypatch, [_option("openrouter/x/known", 32)])

    assert window_tokens("openrouter/x/never-seen") == FALLBACK_CONTEXT_TOKENS


def test_an_unreadable_cache_is_a_missing_answer_not_an_error(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A truncated file must not take down a run that only wanted to size its context."""
    _cache(tmp_path, monkeypatch, [_option("openrouter/x/small", 32)])
    import chimera.providers.listing as listing

    listing._price_cache_path().write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(listing, "_index_cache", None, raising=False)

    assert window_tokens("openrouter/x/small") == FALLBACK_CONTEXT_TOKENS


def test_a_cache_entry_with_no_window_falls_through(tmp_path: Path, monkeypatch: Any) -> None:
    """None means the provider did not say, and a guess is worse than the documented fallback."""
    _cache(tmp_path, monkeypatch, [_option("openrouter/x/quiet", None)])

    assert known_window("openrouter/x/quiet") is None
    assert window_tokens("openrouter/x/quiet") == FALLBACK_CONTEXT_TOKENS


def test_the_older_cache_shape_still_reads(tmp_path: Path, monkeypatch: Any) -> None:
    """An install that upgraded from 0.48.0rc2 has prices on disk and no windows; it must not break."""
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    import chimera.providers.listing as listing

    path = listing._price_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"prices": {"openrouter/x/old": [0.1, 0.2]}}), encoding="utf-8")
    monkeypatch.setattr(listing, "_index_cache", None, raising=False)

    assert known_window("openrouter/x/old") is None
    assert window_tokens("openrouter/x/old") == FALLBACK_CONTEXT_TOKENS


def test_a_lookup_that_cannot_run_is_a_missing_answer(tmp_path: Path, monkeypatch: Any) -> None:
    """Not the corrupt-file case — `listing` already handles that, and a sabotage proved it.

    This is the look-up being unavailable: an import that fails on a partial install, or settings
    that raise on a malformed config. Sizing a context must not be what takes a run down.
    """
    import chimera.providers.listing as listing

    def explode(_slug: str) -> int | None:
        raise RuntimeError("this install has no listing")

    monkeypatch.setattr(listing, "known_window", explode, raising=True)

    assert window_tokens("openrouter/x/never-catalogued") == FALLBACK_CONTEXT_TOKENS
