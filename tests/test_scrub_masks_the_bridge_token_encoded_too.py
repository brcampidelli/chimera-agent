"""The bridge token, encoded, is still the bridge token (study 30 review of S30-32).

`scrub` is what the desktop bridge, the MCP server and `chimera code` pass every result through
before it reaches a client. The bridge token is minted at runtime (`secrets.token_urlsafe`), so it
is not in the environment and `redact` does not know it; `scrub` masked it verbatim only, and a
base64 or hex copy of it in a tool result reached the MCP client whole. It now gets the same net as
a known secret.
"""

from __future__ import annotations

import base64
from collections.abc import Callable

import pytest

from chimera.api.bridge_routes import scrub

TOKEN = "bridge-test-token-0001-not-in-env"


@pytest.mark.parametrize(
    "encoded",
    [
        pytest.param(lambda s: base64.b64encode(s.encode()).decode(), id="base64"),
        pytest.param(lambda s: base64.b64encode(b"user:" + s.encode()).decode(), id="base64-in-basic-auth"),
        pytest.param(lambda s: s.encode().hex(" "), id="hex-spaced"),
        pytest.param(lambda s: ", ".join(str(b) for b in s.encode()), id="decimal-codes"),
        pytest.param(lambda s: s[::-1], id="reversed"),
    ],
)
def test_an_encoded_copy_of_the_bridge_token_is_masked(monkeypatch: pytest.MonkeyPatch, encoded: Callable[[str], str]) -> None:
    monkeypatch.delenv("CHIMERA_BRIDGE_TOKEN", raising=False)
    form = encoded(TOKEN)
    out = scrub({"result": f"the tool printed {form} and exited 0"}, [TOKEN])
    assert form not in out["result"]
    assert out["result"].startswith("the tool printed ")
    assert out["result"].endswith(" and exited 0")


def test_a_short_value_passed_to_scrub_is_masked_verbatim_but_not_hunted_in_dumps() -> None:
    # Below the length floor a value is masked where it appears literally, and its encodings are
    # not computed: the hex of three characters sits inside any long enough hex dump.
    short = "abc"
    dump = "00000000: 6162 6364 6566 6768 696a 6b6c 6d6e 6f70  abcdefghijklmnop"
    out = scrub({"a": "value abc here", "b": dump}, [short])
    assert out["a"] == "value [redacted] here"
    assert out["b"] == dump.replace("abc", "[redacted]")
