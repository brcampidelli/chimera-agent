"""The cost meter worked on the cheap models and went silent on the expensive ones.

`register_catalog_prices()` exists to feed the curated catalogue's prices into the receipt table.
It has five tests. Nothing in the package ever called it, so it had never run outside a test, and
nine of the fifteen catalogue models resolved to *price unknown*:

    HOJE                                6/15 resolve a price
    after register_catalog_prices()     15/15

The nine were `claude-opus-5` ($5/$25 per 1M), `gpt-5.5` ($5/$30), `gemini-3.1-pro`, `kimi-k2`,
`qwen3-max`, `glm-4.6`, `gpt-oss-20b`, `gpt-5.6-luna` and `gemini-2.5-flash`.

**Why nobody noticed is the interesting half.** The default model, `deepseek-chat-v3.1`, is one of
the six the hand-maintained table already covered, so the meter reads correctly for anyone who never
changes it. It goes quiet exactly when somebody switches to an expensive model — which is the moment
knowing the cost matters most. Same shape as a ruler that is accurate on a bad model and lies on a
good one.

The provider index (`known_price`) does cover some of these once the model picker has been opened
and a listing fetched. That is real and it is not enough: it needs network, a warm cache, and the
slug to be in the listing at that moment. The curated catalogue is what the app ships with, and it
should price its own models from a cold start and offline.

The registration is wired into `resolve_price` rather than into a startup hook for the reason the
listing module already writes down about itself: there must not be a second code path that has to
remember to run. A startup hook is what every new entrypoint forgets.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from chimera.fusion import receipts
from chimera.providers.catalog import CATALOG


@pytest.fixture(autouse=True)
def sem_indice(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A cold start, and a table that goes back the way it was.

    Two separate pieces of isolation, both load-bearing:

    `known_price` is stubbed out because it reads `model-prices.json` from the home directory, and a
    machine whose cache happens to be warm would make this test measure the developer's disk rather
    than the code.

    `_PRICES` is restored because `set_price` mutates a module-level list and several tests below
    prepend to it. Without this they would leak into whatever runs next in the same process — and
    the leak would be invisible when this file runs alone, which is the worst kind: green here, red
    in the full suite, in a file nobody changed.
    """
    monkeypatch.setattr("chimera.providers.listing.known_price", lambda slug: None)
    original = list(receipts._PRICES)
    registrado = receipts._catalog_registered
    yield
    receipts._PRICES[:] = original
    receipts._catalog_registered = registrado


def test_every_catalogue_model_has_a_price_from_a_cold_start() -> None:
    """The catalogue is what the app ships with. It must price its own models with no network."""
    sem_preco = [e.slug for e in CATALOG if receipts.resolve_price(e.slug) is None]

    assert sem_preco == [], (
        f"{len(sem_preco)} of {len(CATALOG)} shipped models report an unknown cost: "
        + ", ".join(sem_preco)
    )


def test_the_expensive_ones_in_particular() -> None:
    """Named, because the aggregate above would still pass if the cheap six carried it.

    These are the models where a missing price costs real money to not know about.
    """
    for slug, entrada, saida in [
        ("openrouter/anthropic/claude-opus-5", 5.0, 25.0),
        ("openrouter/openai/gpt-5.5", 5.0, 30.0),
        ("openrouter/google/gemini-3.1-pro-preview", 2.0, 12.0),
    ]:
        preco = receipts.resolve_price(slug)

        assert preco is not None, f"{slug} reports an unknown cost"
        assert (preco.input_per_m, preco.output_per_m) == (entrada, saida), slug


def test_a_price_somebody_typed_still_wins() -> None:
    """The catalogue must not overwrite an explicit `set_price`.

    Registration happens once, lazily, inside `resolve_price`; a `set_price` before the first
    lookup would be shadowed if the catalogue were prepended after it. The order in `_PRICES` is
    load-bearing and this is the case that would break silently — the number would simply be the
    catalogue's instead of the one somebody chose.
    """
    receipts.set_price("openrouter/anthropic/claude-opus-5", receipts.ModelPrice(1.0, 2.0))

    preco = receipts.resolve_price("openrouter/anthropic/claude-opus-5")

    assert preco is not None
    assert (preco.input_per_m, preco.output_per_m) == (1.0, 2.0)


def test_a_model_outside_the_catalogue_is_still_unknown() -> None:
    """Registering the catalogue must not turn "we do not know" into a guess.

    An unknown price is a real answer the receipts depend on: the panel says the cost is unknown
    rather than printing $0.00, and a spend cap that treated unknown as zero would never stop.
    """
    assert receipts.resolve_price("algum/modelo/que-nao-existe-em-lugar-nenhum") is None


def test_a_free_slug_survives_anything_prepended_in_front_of_it() -> None:
    """The property that broke while fixing the prices above, now pinned as a rule.

    "A `:free` slug prices at zero, beating its own paid family" was a POSITION: `(":free", 0)` was
    the first row of the table, and `set_price` prepends. Registering the shipped catalogue put
    `llama-3.3-70b-instruct` in front of it and the free slug started billing at the paid rate.

    The regression was caught by two existing tests, which is what a suite is for. What they could
    not say is that the rule was fragile rather than wrong — any caller doing this would break it,
    not just the catalogue. So the check below prepends a deliberately hostile pattern and requires
    the free slug to hold.
    """
    receipts.set_price("llama-3.3-70b-instruct", receipts.ModelPrice(0.71, 0.71))

    preco = receipts.resolve_price("openrouter/meta-llama/llama-3.3-70b-instruct:free")

    assert preco is not None
    assert (preco.input_per_m, preco.output_per_m) == (0.0, 0.0)


def test_but_a_price_typed_for_the_free_slug_itself_still_wins() -> None:
    """The exact-match pass is above the `:free` rule, and stays there.

    Somebody who wrote the full free slug into `set_price` named that model. A free tier that
    started charging — it happens — has to be expressible, or the honest-cost claim becomes a
    hardcoded zero nobody can correct.
    """
    slug = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
    receipts.set_price(slug, receipts.ModelPrice(0.05, 0.05))

    preco = receipts.resolve_price(slug)

    assert preco is not None
    assert (preco.input_per_m, preco.output_per_m) == (0.05, 0.05)
