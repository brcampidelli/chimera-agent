"""The QR code `chimera pair` and the Access card draw (`chimera/core/qr.py`) is a standard one.

Written here rather than taken as a dependency, so it has to be checked against something that is
not itself. Two things are: the standard's own tables (format bits for every mask, the version-7
block, the data capacity per version at level M), and an independent encoder — node-qrcode 1.5.4,
run once to write ``fixtures/qr_reference_node_qrcode.json`` with the mask forced — whose symbols
these must reproduce module for module, across versions 4, 6, 7 (the first with version bits) and 10.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.core import qr

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "qr_reference_node_qrcode.json"


def test_the_standards_tables() -> None:
    # ISO/IEC 18004, table C.1: the 15-bit format string of level M for masks 0..7.
    assert [format(qr.format_bits(m), "015b") for m in range(8)] == [
        "101010000010010", "101000100100101", "101111001111100", "101101101001011",
        "100010111111001", "100000011001110", "100111110010111", "100101010100000",
    ]
    assert qr.version_bits(7) == 0x07C94  # table D.1
    # Data codewords at level M, versions 1..10 (table 9).
    assert [qr._data_codewords(v) for v in range(1, 11)] == [16, 28, 44, 64, 86, 108, 124, 154, 182, 216]
    assert qr._alignment_positions(7) == [6, 22, 38]


def test_it_reproduces_an_independent_encoder_module_for_module() -> None:
    reference = json.loads(FIXTURE.read_text(encoding="utf-8"))
    symbols = reference["symbols"]
    assert {s["version"] for s in symbols} >= {4, 7, 10}
    for symbol in symbols:
        mine = qr.to_rows(qr.encode(symbol["text"], mask=symbol["mask"]))
        assert len(mine) == 4 * symbol["version"] + 17
        assert mine == symbol["rows"], f"version {symbol['version']}, mask {symbol['mask']} differs"


def test_a_pairing_url_fits_and_too_much_text_is_refused() -> None:
    url = "http://192.168.100.200:54321/pair?code=ABCD-EFGH"
    matrix = qr.encode(url)
    assert len(matrix) == 33  # version 4
    text = qr.to_terminal(matrix)
    assert len(text.splitlines()) == (33 + 4 + 1) // 2
    with pytest.raises(qr.TooLong):
        qr.encode("x" * 214)
