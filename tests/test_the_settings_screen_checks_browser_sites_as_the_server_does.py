"""The screen's site-list check and the server's `parse_sites` agree on one shared table.

The Settings screen refuses a malformed site before it sends a PATCH
(`apps/desktop/src/lib/browser-sites.ts`). Two copies of one rule drift, and the first copy of this
one already had: a regex that refused every host containing the letter "s". So both sides read the
same table, `apps/desktop/src/lib/browser-sites.cases.json` — this test against the server and
`browser-sites.test.ts` against the screen. A case added for one is checked against both.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.tools.browser_reach import parse_sites

CASES = Path(__file__).resolve().parents[1] / "apps" / "desktop" / "src" / "lib" / "browser-sites.cases.json"


def _cases() -> list[tuple[str, bool]]:
    rows = json.loads(CASES.read_text(encoding="utf-8"))
    return [(str(row["entry"]), bool(row["accepted"])) for row in rows]


def test_the_table_has_both_verdicts() -> None:
    verdicts = {accepted for _, accepted in _cases()}
    assert verdicts == {True, False}


@pytest.mark.parametrize(("entry", "accepted"), _cases())
def test_the_server_reads_each_entry_as_the_table_says(entry: str, accepted: bool) -> None:
    try:
        parse_sites(entry)
    except ValueError:
        assert not accepted, f"the server refuses {entry!r}, the table says it accepts it"
    else:
        assert accepted, f"the server accepts {entry!r}, the table says it refuses it"
