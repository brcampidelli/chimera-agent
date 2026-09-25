"""The compaction trigger is set by what the model was measured to read well, not by the listing.

Compaction had never fired: 0 of 137 traced runs. The Code screen spends 0.6 of the model's
*advertised* window and compacts at 0.8 of that, which for the default model (1,048k) meant
503,040 tokens, against a largest-ever-observed prompt of 64,067. `bench/useful_context` measured
the default model on 90 paired agent transcripts: 90/90 at 4k, 87/90 at 128k, every rung within
−10 pp. The catalogue row now carries that floor as `useful_k`, and the budget spends at most it.
"""

from __future__ import annotations

import re
from pathlib import Path

from chimera.core.context_budget import ContextBudget, useful_tokens, window_tokens
from chimera.providers.catalog import CATALOG

DEFAULT = "openrouter/deepseek/deepseek-v4-flash-0731"
REPO = Path(__file__).resolve().parents[1]


def test_the_default_model_compacts_before_the_end_of_what_was_measured() -> None:
    budget = ContextBudget.for_model(DEFAULT, fraction=0.6)
    assert budget.window == 1_048_000
    assert budget.budget == 128_000
    assert budget.threshold == 102_400
    assert budget.threshold < 128_000  # compaction lands before the untested territory, not at it


def test_a_model_nobody_measured_keeps_the_window_fraction() -> None:
    measured = {entry.slug for entry in CATALOG if entry.useful_k}
    other = next(entry for entry in CATALOG if entry.slug not in measured)
    budget = ContextBudget.for_model(other.slug, fraction=0.6)
    assert useful_tokens(other.slug) is None
    assert budget.budget == int(window_tokens(other.slug) * 0.6)


def test_a_fraction_smaller_than_the_measurement_still_wins() -> None:
    # The cap only lowers a budget; a caller that asked for less keeps less.
    budget = ContextBudget.for_model(DEFAULT, fraction=0.05)
    assert budget.budget == int(1_048_000 * 0.05) < 128_000


def test_an_explicit_useful_overrides_the_catalogue() -> None:
    assert ContextBudget.for_model(DEFAULT, fraction=0.6, useful=None).budget == int(1_048_000 * 0.6)
    assert ContextBudget(window=1_000_000, fraction=0.6, useful=50_000).budget == 50_000


def test_every_measured_row_is_below_its_window_and_names_a_bench_that_exists() -> None:
    source = (REPO / "chimera" / "providers" / "catalog.py").read_text(encoding="utf-8")
    for entry in CATALOG:
        if not entry.useful_k:
            continue
        assert 0 < entry.useful_k <= entry.context_k, entry.slug
        # The measurement is cited next to the row, and the cited bench is in the repo.
        start = source.index(f'"{entry.slug}"')
        row = source[start : source.index("CatalogEntry(", start) if "CatalogEntry(" in source[start:] else None]
        cited = re.findall(r"bench/([a-z_]+)", row)
        assert cited, f"{entry.slug}: useful_k with no bench named beside it"
        assert all((REPO / "bench" / name).is_dir() for name in cited), cited
