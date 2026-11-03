"""An unmeasured model gets half its window, at most 128k, on a surface where a person is waiting.

Owner's rule, 2026-11-03: a model with no measured `useful_k` gets `min(window * 0.5, 128_000)`
instead of the fixed 64k it had. A fixed number was wrong at both ends: a 1M-window model that was
never measured got 64k (too little to be useful for a long task), and a 100k-window model got 64k
(64% of its window, past where it is safe to assume it reads well). A measured `useful_k` always wins.
"""

from __future__ import annotations

import pytest

from chimera.core import context_budget as cb
from chimera.core.context_budget import (
    UNMEASURED_FALLBACK_TOKENS,
    ContextBudget,
    unmeasured_useful_tokens,
)

UNMEASURED_SLUG = "some/model-nobody-measured"
MEASURED_SLUG = "openrouter/z-ai/glm-5.3-flash"  # useful_k = 512k in the catalogue


def _window(monkeypatch: pytest.MonkeyPatch, tokens: int | None) -> None:
    monkeypatch.setattr(cb, "known_window_tokens", lambda model: tokens)


def test_a_million_token_window_gets_the_128k_ceiling() -> None:
    assert unmeasured_useful_tokens(1_000_000) == 128_000


def test_a_100k_window_gets_half_of_it() -> None:
    assert unmeasured_useful_tokens(100_000) == 50_000


def test_the_ceiling_starts_where_half_the_window_reaches_it() -> None:
    assert unmeasured_useful_tokens(256_000) == 128_000
    assert unmeasured_useful_tokens(255_998) == 127_999


def test_an_unknown_window_gets_the_named_64k_fallback() -> None:
    assert UNMEASURED_FALLBACK_TOKENS == 64_000
    assert unmeasured_useful_tokens(None) == 64_000
    assert unmeasured_useful_tokens(0) == 64_000


def test_the_budget_for_an_unmeasured_model_uses_its_real_window(monkeypatch: pytest.MonkeyPatch) -> None:
    _window(monkeypatch, 1_000_000)
    budget = ContextBudget.for_model(UNMEASURED_SLUG, fraction=0.6, cap_unmeasured=True)

    assert budget.useful == 128_000
    assert budget.budget == 128_000  # min(600k, 128k)


def test_the_budget_for_a_100k_window_is_capped_at_50k(monkeypatch: pytest.MonkeyPatch) -> None:
    _window(monkeypatch, 100_000)
    budget = ContextBudget.for_model(UNMEASURED_SLUG, fraction=0.6, cap_unmeasured=True)

    assert budget.budget == 50_000  # min(60k, 50k)


def test_an_unknown_window_falls_back_to_64k_for_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    _window(monkeypatch, None)
    budget = ContextBudget.for_model(UNMEASURED_SLUG, fraction=0.6, cap_unmeasured=True)

    assert budget.useful == 64_000
    assert budget.budget == 64_000  # min(0.6 * 128k fallback window, 64k)


def test_without_the_opt_in_an_unmeasured_model_keeps_its_window_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _window(monkeypatch, 1_000_000)
    budget = ContextBudget.for_model(UNMEASURED_SLUG, fraction=0.6)

    assert budget.useful is None
    assert budget.budget == int(cb.window_tokens(UNMEASURED_SLUG) * 0.6)


def test_a_measured_useful_k_wins_over_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _window(monkeypatch, 1_000_000)
    budget = ContextBudget.for_model(MEASURED_SLUG, fraction=0.6, cap_unmeasured=True)

    assert budget.useful == 512_000  # the measurement, not 128k
    assert budget.budget == 512_000


def test_an_explicit_useful_wins_over_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _window(monkeypatch, 1_000_000)
    budget = ContextBudget.for_model(UNMEASURED_SLUG, fraction=0.6, useful=20_000, cap_unmeasured=True)

    assert budget.budget == 20_000
