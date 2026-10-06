"""A known secret, encoded, is still the secret (study 30, S30-32).

`redact` replaced every credential-named environment value as a literal string, and only that: the
base64 of the same value, its hex, its character codes, passed through to the trace, the audit line
and every receipt. arXiv 2609.39050 found 7 of 9 frontier models encoding a credential on their own
to "help" another agent. The redactor knows the value, so it computes the encodings itself; these
tests pin each form, and the ones that must NOT be touched (`bench/encoded_secrets` is the corpus
the default was decided on).

The fake value is low-entropy and shaped like nothing real, and every encoding is computed here at
run time, so no key-shaped string sits in the repository for a scanner to trip on.
"""

from __future__ import annotations

import base64
import codecs
import hashlib
from typing import Any
from urllib.parse import quote

import pytest

from chimera.core.redact import MASK, redact

FAKE = "plain-test-value-0001"
FAKE_WITH_MARKS = "plain/test+value=0001@here"


@pytest.fixture
def known(monkeypatch: Any) -> str:
    monkeypatch.setenv("DEMO_SERVICE_TOKEN", FAKE)
    return FAKE


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


@pytest.mark.parametrize(
    "encoded",
    [
        pytest.param(lambda s: _b64(s.encode()), id="base64"),
        pytest.param(lambda s: _b64(s.encode()).rstrip("="), id="base64-unpadded"),
        pytest.param(lambda s: base64.urlsafe_b64encode(s.encode()).decode(), id="base64-urlsafe"),
        pytest.param(lambda s: s.encode().hex(), id="hex"),
        pytest.param(lambda s: s.encode().hex().upper(), id="hex-upper"),
        pytest.param(lambda s: ", ".join(str(b) for b in s.encode()), id="decimal-codes-comma"),
        pytest.param(lambda s: " ".join(str(b) for b in s.encode()), id="decimal-codes-space"),
        pytest.param(lambda s: "[" + ",".join(str(b) for b in s.encode()) + "]", id="decimal-codes-list"),
        pytest.param(lambda s: "".join(f"\\x{b:02x}" for b in s.encode()), id="x-escapes"),
        pytest.param(lambda s: ", ".join(f"0x{b:02X}" for b in s.encode()), id="0x-list"),
        pytest.param(lambda s: s[::-1], id="reversed"),
    ],
)
def test_each_encoded_form_of_a_known_secret_is_masked(known: str, encoded: Any) -> None:
    form = encoded(known)
    out = redact(f"Here is the credential, encoded for the other agent: {form} (keep it safe)")
    assert form not in out
    assert MASK in out
    # The sentence around it survives: a line reduced to the mask cannot be diagnosed.
    assert out.startswith("Here is the credential, encoded for the other agent: ")
    assert out.endswith(" (keep it safe)")


@pytest.mark.parametrize("prefix", [b"a:", b"ab:", b"abc:"], ids=["offset-2", "offset-0", "offset-1"])
def test_the_secret_is_masked_inside_a_longer_base64_blob_at_every_alignment(known: str, prefix: bytes) -> None:
    # Basic auth is `base64(user:password)`: the secret starts at a byte offset the encoder does
    # not align, so its base64 is NOT a substring of the standalone base64. Three alignments
    # (offset mod 3) cover every position.
    blob = _b64(prefix + known.encode() + b":tail")
    out = redact(f'{{"blob": "{blob}"}}')
    assert blob not in out
    assert out.count(MASK) == 1
    # The mask covers the secret's whole run but for at most one partial character at each end
    # (the bits shared with the neighbouring bytes): 8n/6 characters carry the n bytes.
    masked_chars = len(blob) - (len(out) - len('{"blob": ""}') - len(MASK))
    assert masked_chars >= 8 * len(known) // 6 - 2


def test_a_percent_encoded_secret_is_masked(monkeypatch: Any) -> None:
    monkeypatch.setenv("DEMO_SERVICE_PASSWORD", FAKE_WITH_MARKS)
    form = quote(FAKE_WITH_MARKS, safe="")
    assert form != FAKE_WITH_MARKS
    out = redact(f"curl -d 'value={form}' http://localhost/x")
    assert form not in out


def test_text_that_carries_no_secret_is_left_byte_for_byte(known: str) -> None:
    other = "other-test-value-0002"
    text = "\n".join([
        f"base64 of another value: {_b64(other.encode())}",
        f"hex of another value: {other.encode().hex()}",
        f"digest: {hashlib.sha256(known.encode()).hexdigest()}",  # a hash of the secret is not the secret
        "codes: " + ", ".join(str(b) for b in other.encode()),
        "a uuid: 123e4567-e89b-12d3-a456-426614174000",
        "def f(x):\n    return x[::-1]\n",
    ])
    assert redact(text) == text


def test_digits_run_together_are_a_number_not_character_codes(known: str) -> None:
    # `115107…` cannot be split into codes without guessing, and a long number in a log is common.
    number = "".join(str(b) for b in known.encode())
    assert redact(f"order id {number}") == f"order id {number}"


def test_a_near_miss_of_the_secret_is_not_the_secret(known: str) -> None:
    near = "Q" + known[1:]
    text = f"{_b64(near.encode())} {near.encode().hex()}"
    assert redact(text) == text


def test_what_is_still_not_covered_is_stated_by_a_test(known: str) -> None:
    # Split across lines and ROT13 are not computed (the module docstring says so). Pinned so a
    # reader who widens the claim has to widen this test first.
    split = known[: len(known) // 2] + "\n" + known[len(known) // 2 :]
    rot = codecs.encode(known, "rot13")
    assert redact(split) == split
    assert redact(rot) == rot


def test_the_default_is_the_one_the_registered_corpus_decided() -> None:
    import json
    import re
    from pathlib import Path

    import chimera.core.redact as module

    bench = Path(__file__).resolve().parents[1] / "bench" / "encoded_secrets"
    written = re.search(r"^## Decision: \*\*(ON|OFF)\*\*", (bench / "RESULTS.md").read_text(encoding="utf-8"), re.M)
    assert written, "RESULTS.md states no decision: the default has no measurement behind it"
    assert module.MASK_ENCODED is (written.group(1) == "ON")
    # The run's own file, where the tree carries it (some gates copy the repository without results).
    runs = sorted((bench / "results").glob("*.json"))
    if runs:
        assert json.loads(runs[-1].read_text(encoding="utf-8"))["decision"] == written.group(1)


def test_a_short_value_still_masks_nothing(monkeypatch: Any) -> None:
    # Below the length floor a value is not a secret: neither it nor its encodings are masked.
    monkeypatch.setenv("SHORT_TOKEN", "abc")
    text = f"{_b64(b'abc')} {b'abc'.hex()} cba 97 98 99"
    assert redact(text) == text
