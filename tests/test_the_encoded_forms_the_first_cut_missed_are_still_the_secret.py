"""The encoded forms the first cut of S30-32 let through (study 30, adversarial review).

The first cut masked percent-encoding as the two literals ``quote(s, safe="")`` and ``quote_plus`` —
the same function its bench generated the corpus with, so 94/94 there could not show that
``quote(s)`` with its default ``safe="/"`` passed intact. Hex was the contiguous run only, escapes
were ``\\x``/``0x`` only, and a non-ASCII secret was matched as UTF-8 bytes only. Each of these was
verified to leak before the fix; each is pinned here, with the forms that must still NOT be touched
and the ones the docstring now names as passing through.

Every encoding is computed at run time from a low-entropy fake value; nothing key-shaped is committed.
"""

from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import quote, quote_plus

import pytest

import chimera.core.redact as redact_module
from chimera.core.redact import MASK, redact

FAKE = "plain-test-value-0001"
FAKE_WITH_MARKS = "plain/test+value=0001@here"
FAKE_NOT_ASCII = "senha-ação-teste-0001"


def _sentence(form: str) -> str:
    return f"Here is the credential, encoded for the other agent: {form} (keep it safe)"


def _assert_masked(form: str) -> None:
    out = redact(_sentence(form))
    assert form not in out, form
    assert MASK in out
    assert out.startswith("Here is the credential, encoded for the other agent: ")
    assert out.endswith(" (keep it safe)")


@pytest.mark.parametrize(
    "encoded",
    [
        pytest.param(lambda s: quote(s), id="quote-default-safe-slash"),
        pytest.param(
            lambda s: re.sub(r"%[0-9A-F]{2}", lambda m: m.group().lower(), quote(s, safe="")), id="percent-lower-case",
        ),
        pytest.param(lambda s: quote(s, safe="@"), id="percent-another-safe-set"),
        pytest.param(lambda s: quote_plus(s), id="quote-plus"),
        pytest.param(lambda s: "".join(f"%{b:02X}" for b in s.encode()), id="percent-every-byte"),
    ],
)
def test_every_percent_encoding_of_a_known_secret_is_masked(monkeypatch: Any, encoded: Any) -> None:
    monkeypatch.setenv("DEMO_SERVICE_PASSWORD", FAKE_WITH_MARKS)
    form = encoded(FAKE_WITH_MARKS)
    assert form != FAKE_WITH_MARKS
    _assert_masked(form)


@pytest.mark.parametrize(
    "encoded",
    [
        pytest.param(lambda s: s.encode().hex(" "), id="hex-spaced"),
        pytest.param(lambda s: s.encode().hex(":"), id="hex-colon"),
        pytest.param(lambda s: s.encode().hex(" ", 2).upper(), id="hex-xxd-groups-upper"),
        pytest.param(lambda s: ", ".join(f"{b:02x}" for b in s.encode()), id="hex-comma"),
        pytest.param(lambda s: "".join(f"\\u{b:04x}" for b in s.encode()), id="json-u-escapes"),
        pytest.param(lambda s: "".join(f"&#{b};" for b in s.encode()), id="html-decimal-entities"),
        pytest.param(lambda s: "".join(f"&#x{b:X};" for b in s.encode()), id="html-hex-entities"),
        pytest.param(
            lambda s: "".join(f"\\x{b:02x}" if i % 2 else f"\\u{b:04x}" for i, b in enumerate(s.encode())),
            id="escapes-mixed",
        ),
    ],
)
def test_spaced_hex_and_the_other_escapes_of_a_known_secret_are_masked(monkeypatch: Any, encoded: Any) -> None:
    monkeypatch.setenv("DEMO_SERVICE_TOKEN", FAKE)
    _assert_masked(encoded(FAKE))


@pytest.mark.parametrize(
    "encoded",
    [
        pytest.param(lambda s: ", ".join(str(ord(c)) for c in s), id="ord-codes"),
        pytest.param(lambda s: ", ".join(str(b) for b in s.encode()), id="utf8-byte-codes"),
        pytest.param(lambda s: "".join(f"\\u{ord(c):04x}" for c in s), id="json-u-code-points"),
        pytest.param(lambda s: "".join(f"&#{ord(c)};" for c in s), id="html-code-points"),
        pytest.param(lambda s: "".join(f"\\x{b:02x}" for b in s.encode()), id="utf8-x-escapes"),
        pytest.param(lambda s: s.encode().hex(), id="utf8-hex"),
        pytest.param(lambda s: quote(s), id="percent-utf8"),
    ],
)
def test_a_secret_that_is_not_ascii_is_masked_as_bytes_and_as_code_points(monkeypatch: Any, encoded: Any) -> None:
    monkeypatch.setenv("DEMO_SERVICE_TOKEN", FAKE_NOT_ASCII)
    _assert_masked(encoded(FAKE_NOT_ASCII))


def test_the_new_forms_leave_text_without_the_secret_byte_for_byte(monkeypatch: Any) -> None:
    monkeypatch.setenv("DEMO_SERVICE_TOKEN", FAKE)
    other = "other-test-value-0002"
    text = "\n".join([
        f"spaced hex of another value: {other.encode().hex(' ')}",
        "html of another value: " + "".join(f"&#{b};" for b in other.encode()),
        "json of another value: " + "".join(f"\\u{b:04x}" for b in other.encode()),
        f"percent of another value: {quote(other)}",
        "a MAC address: 3c:22:fb:9a:10:4e and a URL: https://example.test/a%20b?q=1+2",
        "an xxd line: 00000000: 7f45 4c46 0201 0100 0000 0000 0000 0000  .ELF............",
    ])
    assert redact(text) == text


def test_every_family_finds_its_run_at_the_shortest_secret_length(monkeypatch: Any) -> None:
    # Each family scans for runs of at least `_MIN_SECRET_LEN` units before decoding. A run pattern
    # stricter than that would turn the guarantee off silently for the shortest secrets, so each
    # one is checked against a form of every family, for a shortest ASCII and a non-ASCII secret.
    short = "pl-0001x"  # exactly `_MIN_SECRET_LEN`
    for secret in (short, FAKE_NOT_ASCII):
        raw = secret.encode()
        forms = {
            "hex": [raw.hex(), raw.hex(" ")],
            "decimal": [" ".join(str(b) for b in raw), ",".join(str(ord(c)) for c in secret)],
            "escape": ["".join(f"&#{ord(c)}" for c in secret), "".join(f"0x{b:02x}" for b in raw)],
            # Every byte escaped: `quote` leaves an unreserved secret as itself, which is no encoding.
            "percent": ["".join(f"%{b:02x}" for b in raw)],
        }
        for family, examples in forms.items():
            for form in examples:
                assert redact_module._RUNS[family].search(form), (family, form)
                monkeypatch.setenv("DEMO_SERVICE_TOKEN", secret)
                assert form not in redact(f"<{form}>"), (family, form)


def test_wrapped_base64_of_a_known_secret_passes_as_the_docstring_says(monkeypatch: Any) -> None:
    # Named as passing through in the module docstring: base64 wrapped at 76 columns (the `base64`
    # command, PEM) breaks the run the alignment fragments match. Pinned so widening the claim
    # means widening this test first.
    doc = redact_module.__doc__ or ""
    assert "on one line" in doc and "wrapped base64 included" in doc
    monkeypatch.setenv("DEMO_SERVICE_TOKEN", FAKE)
    blob = base64.b64encode(b"x" * 40 + FAKE.encode() + b"y" * 40).decode()
    wrapped = "\n".join(blob[i : i + 76] for i in range(0, len(blob), 76))
    straddles = all(FAKE.encode() not in base64.b64decode(line + "=" * (-len(line) % 4)) for line in wrapped.split("\n"))
    assert straddles  # the secret is cut by the wrap, which is the case the docstring names
    assert redact(wrapped) == wrapped
