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


# The mutation gate (study 30, S30-37) found the edges of `normalise` that no case above reached.


def test_an_upper_case_mailto_is_dropped_too() -> None:
    # A mail client that writes `MAILTO:` must not leave the scheme glued to the address, or a seen
    # address reads as unseen and every send to it asks for nothing.
    assert normalise("MAILTO:Ops@Acme.test") == "ops@acme.test"
    assert recipient_seen("MAILTO:ops@acme.test", ["write to ops@acme.test"])


def test_only_quotes_are_trimmed_never_the_letters_of_the_address() -> None:
    # The trim sets are quote and punctuation characters. A set that grew a letter would eat the
    # start or end of an address that happens to begin or end with it — and turn a seen address into
    # an unseen one (or worse, two different addresses into one).
    assert normalise("Xavier@Acme.test") == "xavier@acme.test"
    assert normalise("ops@acme.BOX") == "ops@acme.box"
    assert normalise('"ops@acme.test"') == "ops@acme.test"
