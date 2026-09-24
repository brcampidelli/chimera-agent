"""Study 24, M2: `recipient_seen` — an address must have appeared, whole, before a send uses it.

`bench/recipient_provenance`: 0/9 false flags on legitimate forms, 7/7 fabrications caught. The
cases below hold the two edges a looser matcher gets wrong (a substring, a look-alike) and the forms
a strict one must still accept.
"""

from __future__ import annotations

import pytest

from chimera.governance.recipient import addresses_in, normalise, recipient_seen


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Ana Souza <Ana.Souza@Example.com>", "ana.souza@example.com"), ("mailto:ops@acme.test", "ops@acme.test"),
     ("`ops@acme.test`.", "ops@acme.test")],
)
def test_a_recipient_is_normalised_the_same_way_on_both_sides(raw: str, expected: str) -> None:
    assert normalise(raw) == expected


def test_a_seen_address_passes_in_any_ordinary_form() -> None:
    sources = ["Please email Ana.Souza@Example.com.", "From: Carlos Lima <carlos@acme.test>"]
    assert recipient_seen("ana.souza@example.com", sources)
    assert recipient_seen("Carlos <carlos@acme.test>", sources)


@pytest.mark.parametrize(
    "fabricated",
    ["ana.beatriz@example.com", "ana.souza@gmail.com", "ana.souza@examp1e.com", "ana.souza+x@example.com", "smith@example.com"],
)
def test_an_address_close_to_a_seen_one_is_not_seen(fabricated: str) -> None:
    sources = ["contacts: Ana Souza <ana.souza@example.com>, Bob Smith <bob.smith@example.com>"]
    assert not recipient_seen(fabricated, sources)


def test_a_suffix_is_not_extracted_as_an_address_of_its_own() -> None:
    assert addresses_in("bob.smith@example.com") == {"bob.smith@example.com"}


def test_something_that_is_not_an_address_is_never_seen() -> None:
    assert not recipient_seen("Ana", ["Ana Souza <ana.souza@example.com>"])
