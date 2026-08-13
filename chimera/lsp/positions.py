"""Where a character is, according to three different counting systems.

The Language Server Protocol counts columns in **UTF-16 code units**. Python counts them in
code points. A text editor counts them in whatever its buffer uses. For the ASCII that most code
is made of, all three agree — which is exactly why this is dangerous: every test written against
English source passes, and the first file with an emoji in a comment or a CJK string literal puts
every diagnostic on the wrong column, in a way nobody can reproduce from a bug report that says
"the squiggle is in the wrong place".

The rule, stated once:

* a character below U+10000 is **one** UTF-16 code unit and one Python character;
* a character at or above U+10000 (emoji, rarer CJK, mathematical alphanumerics) is **two** UTF-16
  code units and still one Python character.

So the conversion is a count of astral characters, and the only correct implementation is one that
walks the line. Multiplying by a factor, or assuming len() is the answer, is right for ASCII and
silently wrong for the case the user is looking at when they report the bug.

These functions are total: an out-of-range column clamps to the end of its line rather than raising,
because a language server that is one edit ahead of us will send positions for text we have not
applied yet, and a crash there loses every diagnostic in the file instead of one.
"""

from __future__ import annotations

#: The first code point that needs a surrogate pair in UTF-16.
ASTRAL = 0x10000


def utf16_length(text: str) -> int:
    """How many UTF-16 code units ``text`` occupies.

    Not ``len(text)``: an emoji is one Python character and two UTF-16 units. Not
    ``len(text.encode("utf-16-le")) // 2`` either — that allocates a whole encoded copy of every
    line on every conversion, and this runs per diagnostic per keystroke.
    """
    return len(text) + sum(1 for ch in text if ord(ch) >= ASTRAL)


def to_utf16_column(line: str, column: int) -> int:
    """A Python character index within ``line`` → the LSP column for the same place."""
    if column <= 0:
        return 0
    clamped = min(column, len(line))
    return utf16_length(line[:clamped])


def from_utf16_column(line: str, utf16_column: int) -> int:
    """An LSP column → the Python character index for the same place.

    A column that lands in the MIDDLE of a surrogate pair — which a buggy server can send, and
    which no valid position ever is — resolves to the start of that character rather than raising.
    Splitting an emoji in half produces a lone surrogate, and a lone surrogate is a string Python
    can hold and most things downstream cannot encode.
    """
    if utf16_column <= 0:
        return 0
    seen = 0
    for index, char in enumerate(line):
        width = 2 if ord(char) >= ASTRAL else 1
        if seen + width > utf16_column:
            return index  # the column fell inside this character
        seen += width
        if seen == utf16_column:
            return index + 1
    return len(line)


def offset_of(text: str, line: int, utf16_column: int) -> int:
    """An LSP position → an absolute index into ``text``.

    Line endings are counted as the document actually stores them, so a CRLF file does not drift by
    one character per line — which presents as diagnostics that are correct at the top of the file
    and increasingly wrong further down, the single most confusing shape this bug takes.
    """
    if line < 0:
        return 0
    offset = 0
    current = 0
    remaining = text
    while current < line:
        newline = remaining.find("\n")
        if newline < 0:
            return len(text)  # past the end: clamp rather than raise
        offset += newline + 1
        remaining = remaining[newline + 1 :]
        current += 1
    end = remaining.find("\n")
    line_text = remaining if end < 0 else remaining[:end]
    # `\r` belongs to the line terminator, not to the line, so a CRLF document's columns match what
    # every editor shows.
    line_text = line_text.removesuffix("\r")
    return offset + from_utf16_column(line_text, utf16_column)


def position_of(text: str, offset: int) -> tuple[int, int]:
    """The inverse: an absolute index into ``text`` → an LSP ``(line, utf16_column)``."""
    if offset <= 0:
        return 0, 0
    clamped = min(offset, len(text))
    before = text[:clamped]
    line = before.count("\n")
    start = before.rfind("\n") + 1
    return line, utf16_length(before[start:].removesuffix("\r"))
