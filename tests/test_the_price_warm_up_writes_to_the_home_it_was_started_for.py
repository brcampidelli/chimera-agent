"""The price warm-up writes the cache into the home it was started for, not whichever is current.

Found on 2026-09-30, when tests began to get a data folder each (R12). `chimera app` starts
`warm_price_cache(settings)` on a daemon thread. The thread was handed the app's settings, but wrote
through `remember_models`, which resolves the folder with `get_settings()` at the moment it writes.
A test that ran `chimera app` with a fake price list left that thread running, and it finished
inside a LATER test's folder. A receipt test then priced 12k tokens of deepseek-chat at US$ 1.20.

In the app the folder does not move under a running process, so users never saw it. The fix is
still in the product rather than the tests: a writer handed a folder writes there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.config import Settings, get_settings
from chimera.providers import listing
from chimera.providers.listing import PRICE_CACHE_NAME, ModelOption


def _model() -> ModelOption:
    return ModelOption(
        slug="openrouter/acme/model-x", label="Model X", vendor="acme", source="openrouter",
        input_per_m=1.0, output_per_m=2.0,
        vision=False, tools=True, context_k=128,
    )


def test_the_warm_up_writes_into_the_home_it_was_given(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started_for, current = tmp_path / "started-for", tmp_path / "current"
    started_for.mkdir()
    current.mkdir()
    settings = Settings(CHIMERA_HOME=str(started_for), OPENROUTER_API_KEY="sk-or-test")
    monkeypatch.setattr(listing, "openrouter_models", lambda *a, **k: ([_model()], ""))
    # By the time the thread writes, the process's settings point somewhere else.
    monkeypatch.setenv("CHIMERA_HOME", str(current))
    get_settings.cache_clear()

    listing.warm_price_cache(settings)

    # Written somewhere at all: `warm_price_cache` swallows every error, so a test whose fake model
    # failed to build would pass the "not in the other folder" half by writing nothing.
    assert (started_for / PRICE_CACHE_NAME).is_file() or (current / PRICE_CACHE_NAME).is_file(), "nothing written"
    assert (started_for / PRICE_CACHE_NAME).is_file(), "the warm-up did not write where it was started"
    assert not (current / PRICE_CACHE_NAME).exists(), "the warm-up wrote into another folder"
