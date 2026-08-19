"""Every model slug this project ships as a default still exists at the provider.

A slug is a claim about somebody else's product, and it decays on their schedule rather than ours.
On 2026-08-18 an audit found **six of the fourteen** catalogue entries withdrawn — including the
``weak`` rung of every cost preset and two of the three models in the default fusion panel. So a run
that routed a role to the weak tier called a model that did not exist, and the panel whose entire
premise is several independent opinions was convening one model and two 404s.

Nothing caught it, and nothing could: the catalogue is DATA, data has no unit tests, and the failure
surfaces only as a provider error inside a user's run, attributed to whatever they were doing at the
time. This file is the check that was missing.

**Marked ``integration``, so it is deselected by default.** It asks a third party a question over the
network; running it in the ordinary gate would turn "OpenRouter is having a slow morning" into a red
build, which is how a useful test gets deleted. Run it before a release, or on a schedule:

    pytest -m integration tests/test_catalog_is_live.py

It SKIPS rather than fails when the index cannot be fetched — an unanswered question is not evidence
of a withdrawn model, and pretending otherwise is the same fabrication the listing module refuses to
make elsewhere.
"""

from __future__ import annotations

import pytest

from chimera.providers.listing import openrouter_models

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def live_slugs() -> set[str]:
    """Every model OpenRouter currently publishes, as prefixed slugs. Skips if it did not answer."""
    models, reason = openrouter_models()
    if reason:
        pytest.skip(f"OpenRouter's index was unreachable ({reason}) — no evidence either way")
    return {m.slug for m in models}


def _openrouter_only(slugs: list[str]) -> list[str]:
    """Only the OpenRouter ones can be checked against this index; the rest are other vendors'."""
    return [s for s in slugs if s.startswith("openrouter/")]


def test_every_catalogue_slug_still_exists(live_slugs: set[str]) -> None:
    from chimera.providers.catalog import CATALOG

    gone = [slug for slug in _openrouter_only([e.slug for e in CATALOG]) if slug not in live_slugs]
    assert not gone, f"withdrawn by the provider and still on offer: {gone}"


def test_every_cost_preset_rung_still_exists(live_slugs: set[str]) -> None:
    """The one that matters most: a preset rung is chosen FOR the user, not by them.

    A withdrawn slug in the catalogue is a bad suggestion. A withdrawn slug here is a run that fails
    for a reason the user cannot see, on a tier they never named.
    """
    from chimera.providers.catalog import _PRESETS

    gone = {
        f"{mode}.{tier}": slug
        for mode, ladder in _PRESETS.items()
        for tier, slug in (("weak", ladder.weak), ("mid", ladder.mid), ("top", ladder.top))
        if slug.startswith("openrouter/") and slug not in live_slugs
    }
    assert not gone, f"cost presets point at models that no longer exist: {gone}"


def test_the_default_model_still_exists(live_slugs: set[str]) -> None:
    from chimera.config import Settings
    from chimera.providers.catalog import PROVIDERS_BY_NAME

    default = Settings().default_model
    if default.startswith("openrouter/"):
        assert default in live_slugs, f"the product default is withdrawn: {default}"
    # And the wizard's suggestion, which must be the same string — a mismatch shows one slug on
    # screen and runs another, since the wizard does not write the value when it is left alone.
    assert PROVIDERS_BY_NAME["openrouter"].default_model == default


def test_the_default_fusion_roles_still_exist(live_slugs: set[str]) -> None:
    """Panel, judge and synthesiser. A fusion turn with a dead panelist is a panel of fewer opinions
    than it reports — and reporting the count is the whole point of the receipt."""
    from chimera.config import Settings

    settings = Settings()
    roles = [*settings.fusion_panel, settings.fusion_judge, settings.fusion_synthesizer]
    gone = [slug for slug in _openrouter_only(roles) if slug not in live_slugs]
    assert not gone, f"the default fusion panel names models that no longer exist: {gone}"


def test_the_transfer_panel_still_exists(live_slugs: set[str]) -> None:
    """The transfer panel decides whether a learned skill generalises. A withdrawn member does not
    return a wrong answer — it returns an error that the sample size silently absorbs."""
    from chimera.config import Settings

    gone = [slug for slug in _openrouter_only(list(Settings().transfer_panel)) if slug not in live_slugs]
    assert not gone, f"the transfer panel names models that no longer exist: {gone}"


def test_the_catalogue_prices_are_not_wildly_stale(live_slugs: set[str]) -> None:
    """Prices are documented as approximate, so this is deliberately loose: it fires on an ORDER of
    magnitude, not on a percent. The catalogue said $0.14/$0.28 for a model that had moved to
    $0.25/$0.95 — tolerable. It would also have said it after a 10x move, which is not.
    """
    from chimera.providers.catalog import CATALOG

    live = {m.slug: m for m in openrouter_models()[0]}
    wrong: list[str] = []
    for entry in CATALOG:
        current = live.get(entry.slug)
        if current is None or entry.input_per_m is None or current.input_per_m is None:
            continue
        # Zero on either side is a free tier appearing or disappearing, which is a fact worth
        # knowing and cannot be expressed as a ratio.
        if (entry.input_per_m == 0) != (current.input_per_m == 0):
            wrong.append(f"{entry.slug}: {entry.input_per_m} vs {current.input_per_m} (free tier changed)")
        elif entry.input_per_m > 0 and not (0.2 <= current.input_per_m / entry.input_per_m <= 5):
            wrong.append(f"{entry.slug}: catalogue {entry.input_per_m}, live {current.input_per_m}")
    assert not wrong, f"catalogue prices are off by an order of magnitude: {wrong}"
