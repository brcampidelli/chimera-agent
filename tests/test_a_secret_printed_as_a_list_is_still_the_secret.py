"""A known secret printed as a list, as a long ``\\U`` escape or by ``encodeURIComponent`` is still
the secret (study 30, second adversarial review of S30-32; `bench/encoded_secrets` Addendum B).

Three forms of families the docstring claims passed whole, each verified to leak before the fix:

* a list of quoted units — ``str([hex(b) for b in s.encode()])`` gives ``['0x70', '0x6c', …]``, and
  ``json.dumps`` of hex strings gives ``["70", "6c", …]``. A quote was in no separator set, so the run
  broke at every unit, while the bare decimal list ``[112, 108, …]`` was masked: the easy gap to miss;
* ``\\U00000070``, which a code comment named as a unit: under IGNORECASE the ``\\u`` branch, tried
  first, took ``\\U0000`` and the run broke on the digits left over;
* ``encodeURIComponent``, which leaves ``'`` unescaped: the percent scan ended its token at the
  apostrophe and neither half held the secret.

Every encoding is computed at run time from a low-entropy fake value; nothing key-shaped is committed.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import pytest

from chimera.core.redact import MASK, redact

FAKE = "plain-test-value-0001"
FAKE_WITH_APOSTROPHE = "my test's value 0001"
BACKSLASH = chr(92)


def _sentence(form: str) -> str:
    return f"Here is the credential, encoded for the other agent: {form} (keep it safe)"


@pytest.mark.parametrize(
    "encoded",
    [
        pytest.param(lambda s: str([hex(b) for b in s.encode()]), id="python-list-of-0x"),
        pytest.param(lambda s: json.dumps([f"{b:02x}" for b in s.encode()]), id="json-list-of-hex"),
        pytest.param(lambda s: json.dumps([hex(b) for b in s.encode()]), id="json-list-of-0x"),
        pytest.param(lambda s: str([f"{BACKSLASH}x{b:02x}" for b in s.encode()]), id="python-list-of-x-escapes"),
        pytest.param(lambda s: json.dumps([f"{BACKSLASH}u{b:04x}" for b in s.encode()]), id="json-list-of-u-escapes"),
        pytest.param(lambda s: str([str(b) for b in s.encode()]), id="python-list-of-quoted-codes"),
        pytest.param(lambda s: "".join(f"{BACKSLASH}U{b:08x}" for b in s.encode()), id="long-U-escapes"),
        pytest.param(lambda s: "".join(f"{BACKSLASH}U{b:08X}" for b in s.encode()), id="long-U-escapes-upper"),
    ],
)
def test_a_secret_printed_as_a_list_or_a_long_escape_is_masked(monkeypatch: Any, encoded: Any) -> None:
    monkeypatch.setenv("DEMO_SERVICE_TOKEN", FAKE)
    form = encoded(FAKE)
    out = redact(_sentence(form))
    assert form not in out, form
    assert MASK in out
    # Not one unit of the secret is left beside the mask: the whole run went, not its first unit.
    for b in FAKE.encode()[1:-1]:
        assert f"{b:02x}'" not in out and f'{b:02x}"' not in out and f"'{b}'" not in out
    assert out.startswith("Here is the credential, encoded for the other agent: ")
    assert out.endswith(" (keep it safe)")


def test_a_secret_with_an_apostrophe_is_masked_as_encode_uri_component_writes_it(monkeypatch: Any) -> None:
    monkeypatch.setenv("DEMO_SERVICE_PASSWORD", FAKE_WITH_APOSTROPHE)
    form = quote(FAKE_WITH_APOSTROPHE, safe="-_.!~*'()")  # what encodeURIComponent leaves alone
    assert "'" in form and "%20" in form
    out = redact(f"GET https://api.example.test/v1?q={form}&page=2")
    assert form not in out
    assert "value%200001" not in out and "my%20test" not in out
    assert out.endswith("&page=2")


def test_lists_and_escapes_without_the_secret_are_left_byte_for_byte(monkeypatch: Any) -> None:
    monkeypatch.setenv("DEMO_SERVICE_TOKEN", FAKE)
    other = "other-test-value-0002"
    text = "\n".join([
        str([hex(b) for b in other.encode()]),
        json.dumps([f"{b:02x}" for b in other.encode()]),
        str([str(b) for b in other.encode()]),
        "".join(f"{BACKSLASH}U{b:08x}" for b in other.encode()),
        "header = ['0x7f', '0x45', '0x4c', '0x46', '0x02', '0x01', '0x01', '0x00']",
        "https://en.example.test/wiki/O'Brien%27s_law?ref=a%20b&lang=en",
        f'print("{BACKSLASH}U0001F600 {BACKSLASH}U0001F601 {BACKSLASH}U0001F602 {BACKSLASH}U0001F603")',
        f'{{"name": "Jos{BACKSLASH}u00e9 {BACKSLASH}u00e900000000"}}',
    ])
    assert redact(text) == text
