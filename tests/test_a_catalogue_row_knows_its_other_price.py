"""A catalogue row carries every price the live index has quoted for it, and the live check accepts
any of them — a slug served by two routes flips between two figures, and three release days went
red on the flip (#421, #443, and `glm-5.3-flash` twice on 2026-09-12). The check is for a price the
row has never seen."""

from __future__ import annotations

from chimera.providers.catalog import CATALOG, CatalogEntry, price_is_known


def _row(**kw: object) -> CatalogEntry:
    base: dict[str, object] = {"slug": "x/y", "tier": "mid", "vendor": "v", "input_per_m": 0.15,
                               "output_per_m": 0.5, "tools": True, "context_k": 100}
    base.update(kw)
    return CatalogEntry(**base)  # type: ignore[arg-type]


def test_the_catalogue_price_itself_is_known_within_the_band() -> None:
    assert price_is_known(_row(), 0.15)
    assert price_is_known(_row(), 0.11)  # 0.73x
    assert not price_is_known(_row(), 0.075)  # exactly the halving that reddened the check


def test_an_also_seen_price_is_known_and_an_unseen_one_is_not() -> None:
    row = _row(also_seen=((0.075, 0.25),))
    assert price_is_known(row, 0.075)
    assert price_is_known(row, 0.15)
    assert not price_is_known(row, 0.02)
    assert not price_is_known(row, 0.40)


def test_the_three_flipping_rows_carry_their_history() -> None:
    by = {e.slug: e for e in CATALOG}
    assert price_is_known(by["openrouter/z-ai/glm-5.3-flash"], 0.075)
    assert price_is_known(by["openrouter/deepseek/deepseek-chat-v3.1"], 0.55)
    assert price_is_known(by["openrouter/deepseek/deepseek-v4-flash-0731"], 0.065)
