"""A price one test pins does not reach the next test.

Found on 2026-09-30 by bisecting the full suite. `set_price` inserts into the process-wide price
table and nothing undid it: `test_a_long_task_keeps_going_until_it_is_done_or_stopped.py` pins
deepseek-chat at US$ 100 per million tokens for its own arithmetic, and every later test that priced
a deepseek-chat model got that price. A receipt test then billed 12k tokens at US$ 1.20.

It hid for months because the tests shared one `.chimera` folder, whose price cache, written by
other tests, took precedence over the table. Once each test got its own folder (R12), the leaked
price was the only one left.

`tests/conftest.py` now puts the table back after every test. These two run in order: the first
pins a price, the second must not see it.
"""

from __future__ import annotations

from chimera.fusion.receipts import ModelPrice, resolve_price, set_price

MODEL = "openrouter/leak-probe/model-zz"


def test_1_a_test_pins_a_price() -> None:
    set_price(MODEL, ModelPrice(input_per_m=999.0, output_per_m=999.0))

    assert resolve_price(MODEL) is not None


def test_2_the_next_test_does_not_see_it() -> None:
    assert resolve_price(MODEL) is None, "a price pinned by an earlier test leaked into this one"


CATALOGUED = "openrouter/anthropic/claude-opus-5"


def test_3_a_test_that_first_folds_in_the_catalogue() -> None:
    # The first lookup in a process folds the shipped catalogue into the table, once.
    assert resolve_price(CATALOGUED) is not None


def test_4_the_next_test_still_has_the_catalogue() -> None:
    """Putting the table back must not lose the catalogue: restoring the list without the flag that
    says it was folded in left every later test pricing it as unknown."""
    assert resolve_price(CATALOGUED) is not None, "the catalogue's prices were lost between tests"
