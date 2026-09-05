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
    """The catalogue price is not decoration: `register_catalog_prices` seeds the receipt table
    with it, so an error here is an error in what a user is told they spent.

    This used to allow a factor of five either way, on the reasoning that prices are documented as
    approximate. Measured against that: on 2026-09-04 the catalogue carried 0.25/0.95 for
    `deepseek-chat-v3.1` while the live index said 0.55/1.65 — **2.2x low, and this test passed**,
    because 2.2 is inside 5. Every fusion receipt for that model under-reported by the same factor
    for as long as it stood, and the entry's own note asserted the wrong figure had been verified.

    Tightened to 50%. A price that has genuinely moved by half is worth a line in the catalogue
    anyway, and this file is `-m integration`, so a real market move reddens a run somebody chose
    rather than the build.
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
        elif entry.input_per_m > 0 and not (0.67 <= current.input_per_m / entry.input_per_m <= 1.5):
            wrong.append(f"{entry.slug}: catalogue {entry.input_per_m}, live {current.input_per_m}")
    assert not wrong, f"catalogue prices are off by more than half: {wrong}"


def _served_windows() -> dict[str, int]:
    """`top_provider.context_length` per slug, fetched raw.

    `openrouter_models()` keeps only the ADVERTISED `context_length`, so asking it this question
    would return None for every entry and the test would pass by having nothing to check — a guard
    that cannot fire. The raw index is read here instead rather than widening the production
    listing for one integration test.
    """
    import json
    import urllib.request

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/models", headers={"User-Agent": "chimera-tests"}
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read())["data"]
    served: dict[str, int] = {}
    for entry in data:
        window = (entry.get("top_provider") or {}).get("context_length")
        if isinstance(window, int) and window > 0:
            served[f"openrouter/{entry['id']}"] = window
    return served


def test_no_catalogue_window_promises_more_than_the_provider_serves(live_slugs: set[str]) -> None:
    """`context_length` and `top_provider.context_length` are different numbers, and the second wins.

    Measured on 2026-09-04: 39 of the 431 models in the live index advertise a window their provider
    does not serve, and the gap reaches **20%** on exactly the three slugs this project uses as its
    mid default, its fusion judge and the top rung of two presets — 1,310,720 advertised against
    1,048,576 served.

    It did not bite, and only by accident: the compaction trigger is `0.6 x 0.8 = 0.48` of the
    window, so 628,800 sat well inside what was actually served. Raise that fraction and the margin
    is gone. `context_k` now carries the served figure for those three, and this holds the rule: a
    catalogue window may be smaller than the provider's, never larger.
    """
    from chimera.providers.catalog import CATALOG

    served = _served_windows()
    if not served:
        pytest.skip("the index did not report a served window for anything")
    over = [
        f"{entry.slug}: catalogue {entry.context_k}k, served {served[entry.slug]}"
        for entry in CATALOG
        if entry.slug in served and entry.context_k * 1000 > served[entry.slug]
    ]
    assert not over, f"a catalogue window promises more than the provider serves: {over}"
