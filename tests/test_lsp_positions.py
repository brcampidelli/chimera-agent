"""UTF-16 positions (:mod:`chimera.lsp.positions`).

Property tests, not example tests, and the reason is the shape of the bug: for ASCII, Python's
counting and the protocol's counting agree exactly, so every example written against English source
passes whether or not the code is correct. The failure appears only in a file with an emoji in a
comment or a CJK string literal — and it appears as "the squiggle is in the wrong place", which is
not a reproducible bug report.

The property is round-tripping: converting a position out and back must land where it started, for
every index in every line, over an alphabet that deliberately mixes one-unit and two-unit characters.
Hypothesis is not a dependency here, so the generation is a deterministic sweep — exhaustive over
small inputs, which for this function is stronger than sampling.
"""

from __future__ import annotations

from chimera.lsp.positions import (
    from_utf16_column,
    offset_of,
    position_of,
    to_utf16_column,
    utf16_length,
)

#: One-unit and two-unit characters side by side. Ĥ and 漢 are one UTF-16 unit each despite being
#: multi-BYTE in UTF-8 — the distinction this module exists for is units, not bytes, and an
#: alphabet without both would let a byte-based implementation pass.
ALPHABET = ["a", " ", "Ĥ", "漢", "🙂", "𝔘", "\t"]


def _lines() -> list[str]:
    """Every string of length 0-3 over the alphabet. 400 lines, exhaustively."""
    out = [""]
    current = [""]
    for _ in range(3):
        current = [prefix + ch for prefix in current for ch in ALPHABET]
        out.extend(current)
    return out


LINES = _lines()


def test_ascii_is_where_the_bug_hides() -> None:
    """The premise, asserted so nobody removes the astral characters from the alphabet above.

    For ASCII the two counting systems agree, which is why an example-based test on English source
    cannot fail.
    """
    assert utf16_length("def main():") == len("def main():")
    assert utf16_length("🙂") == 2 != len("🙂")


def test_a_column_round_trips_through_utf16_everywhere() -> None:
    """The property. Out and back, for every index of every line in the alphabet."""
    for line in LINES:
        for column in range(len(line) + 1):
            back = from_utf16_column(line, to_utf16_column(line, column))
            assert back == column, f"{line!r} column {column} came back as {back}"


def test_the_utf16_length_matches_the_encoder() -> None:
    """Cross-checked against Python's own UTF-16 encoder — a second implementation of the same
    question, which is the only way to know the fast path is not consistently wrong."""
    for line in LINES:
        assert utf16_length(line) == len(line.encode("utf-16-le")) // 2


def test_a_column_inside_a_surrogate_pair_resolves_to_its_start() -> None:
    """A buggy server can send one, and no valid position ever is.

    Splitting an emoji in half produces a lone surrogate — a string Python can hold and most things
    downstream cannot encode, so the failure would surface far from here.
    """
    line = "a🙂b"
    assert from_utf16_column(line, 1) == 1  # just after "a"
    assert from_utf16_column(line, 2) == 1  # INSIDE the emoji → its start
    assert from_utf16_column(line, 3) == 2  # just after the emoji


def test_columns_past_the_end_clamp_rather_than_raise() -> None:
    """A language server one edit ahead of us sends positions for text we have not applied yet.
    Raising there loses every diagnostic in the file instead of one."""
    assert from_utf16_column("ab", 99) == 2
    assert to_utf16_column("ab", 99) == 2
    assert to_utf16_column("ab", -5) == 0
    assert from_utf16_column("ab", -5) == 0


# --- whole-document offsets --------------------------------------------------------------------


def test_an_offset_round_trips_through_a_position() -> None:
    text = "def f():\n    return '🙂漢'\n\nx = 1\n"
    for offset in range(len(text) + 1):
        line, column = position_of(text, offset)
        # Offsets that land inside a line-ending pair are the one place this is not injective, so
        # the property is "the position maps back to a character boundary at or before the offset".
        assert offset_of(text, line, column) <= offset


def test_a_position_round_trips_through_an_offset() -> None:
    """The direction that matters: the server sends positions and we resolve them into the buffer.

    Over the columns that are POSITIONS — character boundaries. A first version swept every UTF-16
    unit and failed on column 2 of "a🙂b", which is inside the surrogate pair: not a position any
    conforming server can send, and documented to resolve backwards to the character's start. The
    test was asserting something stronger than the contract, and the contract is the right one.
    """
    text = "a🙂b\n漢字 = 1\n𝔘 = 2\n"
    for line_number, line in enumerate(text.split("\n")):
        boundaries = [to_utf16_column(line, index) for index in range(len(line) + 1)]
        for column in boundaries:
            offset = offset_of(text, line_number, column)
            assert position_of(text, offset) == (line_number, column)


def test_a_position_inside_a_surrogate_pair_is_not_one() -> None:
    # The exception the sweep above excludes, pinned so the clamping stays deliberate.
    text = "a🙂b\n"
    assert offset_of(text, 0, 2) == offset_of(text, 0, 1)  # both land at the emoji's start


def test_a_crlf_document_does_not_drift(  ) -> None:
    """`\\r` belongs to the line terminator, not the line.

    Counting it as content makes diagnostics correct at the top of a file and increasingly wrong
    further down — the most confusing shape this bug takes, because the first thing anyone checks
    is the first error.
    """
    crlf = "import os\r\nx = 1\r\ny = 2\r\n"
    lf = crlf.replace("\r\n", "\n")

    for line in range(3):
        column = 3
        assert crlf[offset_of(crlf, line, column)] == lf[offset_of(lf, line, column)]


def test_the_last_column_of_a_crlf_line_is_the_end_of_its_text() -> None:
    crlf = "abc\r\ndef\r\n"
    # Column 3 on line 0 is just past "abc" — before the \r, not after it.
    assert crlf[offset_of(crlf, 0, 3) :].startswith("\r\n")


def test_a_line_past_the_end_clamps_to_the_document() -> None:
    text = "one\ntwo\n"
    assert offset_of(text, 99, 0) == len(text)
    assert offset_of(text, -1, 0) == 0


def test_an_empty_document_has_one_position() -> None:
    assert offset_of("", 0, 0) == 0
    assert position_of("", 0) == (0, 0)
