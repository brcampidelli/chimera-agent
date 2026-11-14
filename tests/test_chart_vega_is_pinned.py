"""The chart page an export writes loads Vega at an exact version, with its SRI hash.

Before this, the page named ``vega@5``, ``vega-lite@5`` and ``vega-embed@6``: a major, which moves
under an exported chart when its author publishes, and no integrity attribute, so a swapped file ran
unchecked. The hashes are not recomputed here (that would need the network in a unit test); the
test pins their shape and that the page carries them, and the desktop fixture test reads the same
page.
"""

from __future__ import annotations

import re

from chimera.tools import chart

_TAG = (
    r'<script src="https://cdn\.jsdelivr\.net/npm/{name}@(\d+\.\d+\.\d+)" '
    r'integrity="(sha384-[A-Za-z0-9+/]{{64}}={{0,2}})" crossorigin="anonymous"></script>'
)


def test_each_vega_script_names_an_exact_version_and_a_sha384_hash() -> None:
    html = chart._html({"mark": "bar"})
    for name in ("vega", "vega-lite", "vega-embed"):
        assert re.search(_TAG.format(name=re.escape(name)), html), (
            f"{name} is not pinned: {html[:300]}"
        )


def test_the_page_pins_the_same_table_the_module_declares() -> None:
    html = chart._html({"mark": "bar"})
    for name, version, sri in chart._VEGA_CDN:
        assert f'npm/{name}@{version}" integrity="{sri}"' in html
