"""The model catalogue is data, and these are the things that must stay true of it.

`chimera/providers/catalog.py` says of itself: *DATA ONLY — extend/correct freely*. That makes it one
of the few places in the tree where a newcomer can send a genuinely useful two-line pull request:
model slugs get renamed, prices get cut, context windows grow, and a stale catalogue quietly routes
work to a model that no longer exists at a price that is no longer real.

"Freely" only works if the obvious mistakes are caught by something other than a maintainer reading
a table of numbers. That is all this file is — the mechanical half, so review can be about whether
the price is *right* rather than whether it is *negative*.

Deliberately absent: any assertion that a slug resolves at a provider. That would make the suite
depend on the network and on someone's API key, and would fail for reasons having nothing to do with
the commit under test. Whether a slug is real stays a human check.
"""

from __future__ import annotations

import pytest

from chimera.providers.catalog import CATALOG, CatalogEntry


def test_the_catalogue_is_not_empty() -> None:
    assert CATALOG, "an empty catalogue would make every parametrized test below vacuous"


def test_slugs_are_unique() -> None:
    """A duplicate slug is a silent conflict: whichever entry loses is invisible to every reader.

    Reported with the duplicates named, because finding them by eye in a table of dozens is exactly
    the tedium this test exists to remove.
    """
    slugs = [e.slug for e in CATALOG]
    dupes = sorted({s for s in slugs if slugs.count(s) > 1})
    assert not dupes, f"duplicate slugs in CATALOG: {dupes}"


def test_every_tier_has_at_least_one_model() -> None:
    """`resolve_tiers` picks defaults per tier. An empty tier is not a thin catalogue — it is a
    crash waiting for the first person whose cost mode reaches for it."""
    for tier in ("weak", "mid", "top"):
        assert any(e.tier == tier for e in CATALOG), f"no model in the '{tier}' tier"


@pytest.mark.parametrize("entry", CATALOG, ids=lambda e: e.slug)
def test_entry_is_well_formed(entry: CatalogEntry) -> None:
    assert entry.slug.strip(), "a blank slug routes nowhere"
    assert "/" in entry.slug, f"{entry.slug}: expected a provider-qualified slug (provider/model)"
    assert entry.vendor.strip(), f"{entry.slug}: vendor is shown by `chimera models`"
    assert entry.tier in ("weak", "mid", "top"), f"{entry.slug}: unknown tier {entry.tier!r}"
    assert entry.context_k > 0, f"{entry.slug}: a zero context window would route nothing"


@pytest.mark.parametrize("entry", CATALOG, ids=lambda e: e.slug)
def test_prices_are_absent_or_sane(entry: CatalogEntry) -> None:
    """`None` means unknown and is always allowed — the field's own docstring says a price is never
    guessed. What is not allowed is a *negative* price, or output cheaper than input, which is not
    how any provider on this list charges and is the shape a transposed pair of columns makes.
    """
    for name, price in (("input", entry.input_per_m), ("output", entry.output_per_m)):
        assert price is None or price >= 0, f"{entry.slug}: negative {name} price {price}"
    if entry.input_per_m is not None and entry.output_per_m is not None:
        assert entry.output_per_m >= entry.input_per_m, (
            f"{entry.slug}: output ({entry.output_per_m}) is cheaper than input "
            f"({entry.input_per_m}) — check the columns are not swapped"
        )


@pytest.mark.parametrize("entry", CATALOG, ids=lambda e: e.slug)
def test_a_free_model_is_free_on_both_sides(entry: CatalogEntry) -> None:
    """Half-free is a typo, not a pricing model."""
    if entry.input_per_m == 0.0 or entry.output_per_m == 0.0:
        assert entry.input_per_m == 0.0 and entry.output_per_m == 0.0, (
            f"{entry.slug}: one side is 0.0 and the other is not"
        )
