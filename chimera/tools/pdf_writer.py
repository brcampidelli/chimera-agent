"""A small PDF writer for ``create_document`` — standard library only, text and tables.

Why not a library: every PDF generator worth bundling is either large (ReportLab) or copyleft
(fpdf2 is LGPL), and what a report needs from a PDF is narrow — headings, wrapped paragraphs, lists,
a ruled table, pages. Those fit in one file with no dependency, so the PDF format works on every
install, including the ones that never added the ``documents-out`` extra.

The honest limit, reported rather than hidden: the text is set in the PDF's 14 standard fonts
(Helvetica), which carry the Windows-1252 repertoire — Portuguese, Spanish, French, German and the
rest of Western Europe — and nothing else. A character outside it (Chinese, Cyrillic, most emoji) is
written as ``?`` and :func:`render_pdf` returns how many were, so the tool can say so in its answer.
Embedding a TrueType font would fix that and is the next step if anyone needs it.

Content streams are left uncompressed: a few kilobytes more, and a file whose text can be checked
without a PDF library — which is exactly how the tests read it back.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from chimera.tools.document_spec import Bullets, DocumentSpec, Heading, PageBreak, Paragraph, Table

PAGE_W, PAGE_H = 595.0, 842.0  # A4 in points
MARGIN = 56.0
_BODY = 10.5
_SIZES = {0: 20.0, 1: 16.0, 2: 13.0, 3: 11.5}  # 0 = the document title
_LEADING = 1.35
_CELL_PAD = 4.0

# Helvetica advance widths (1/1000 em) for the printable ASCII range, from the AFM Adobe publishes.
# Characters beyond it are measured by their base letter (`é` as `e`) or as 556, an average glyph —
# wrapping only needs to be conservative, not exact.
_ASCII_WIDTHS = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
)
_BOLD_FACTOR = 1.1  # Helvetica-Bold runs wider; overestimating keeps a bold line inside the margin


def _char_width(ch: str) -> int:
    code = ord(ch)
    if 32 <= code <= 126:
        return _ASCII_WIDTHS[code - 32]
    base = unicodedata.normalize("NFD", ch)[:1]
    if base and 32 <= ord(base) <= 126:
        return _ASCII_WIDTHS[ord(base) - 32]
    return 556


def text_width(text: str, size: float, *, bold: bool = False) -> float:
    width = sum(_char_width(ch) for ch in text) * size / 1000.0
    return width * _BOLD_FACTOR if bold else width


def wrap(text: str, size: float, max_width: float, *, bold: bool = False) -> list[str]:
    """Lines no wider than ``max_width``; a word longer than a line is cut, never lost."""
    lines: list[str] = []
    for raw in text.split("\n") or [""]:
        line = ""
        for word in raw.split(" "):
            candidate = f"{line} {word}" if line else word
            if text_width(candidate, size, bold=bold) <= max_width:
                line = candidate
                continue
            if line:
                lines.append(line)
            while text_width(word, size, bold=bold) > max_width and len(word) > 1:
                cut = len(word)
                while cut > 1 and text_width(word[:cut], size, bold=bold) > max_width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)
    return lines


def _pdf_string(text: str, counter: list[int]) -> str:
    """A PDF literal string in WinAnsi, escaped; characters it cannot carry become ``?`` and are counted."""
    out: list[str] = []
    for ch in text:
        try:
            byte = ch.encode("cp1252")
        except UnicodeEncodeError:
            counter[0] += 1
            byte = b"?"
        for b in byte:
            if b in (0x28, 0x29, 0x5C):  # ( ) \
                out.append("\\" + chr(b))
            elif 32 <= b < 127:
                out.append(chr(b))
            else:
                out.append(f"\\{b:03o}")
    return "(" + "".join(out) + ")"


@dataclass
class _Layout:
    pages: list[list[str]] = field(default_factory=lambda: [[]])
    y: float = PAGE_H - MARGIN
    replaced: list[int] = field(default_factory=lambda: [0])

    @property
    def ops(self) -> list[str]:
        return self.pages[-1]

    def new_page(self) -> None:
        self.pages.append([])
        self.y = PAGE_H - MARGIN

    def ensure(self, height: float) -> None:
        if self.y - height < MARGIN and self.ops:
            self.new_page()

    def text(self, x: float, line: str, size: float, *, bold: bool = False) -> None:
        font = "F2" if bold else "F1"
        self.ops.append(
            f"BT /{font} {size:.1f} Tf {x:.2f} {self.y - size:.2f} Td "
            f"{_pdf_string(line, self.replaced)} Tj ET"
        )

    def lines(self, lines: list[str], size: float, *, x: float = MARGIN, bold: bool = False) -> None:
        for line in lines:
            self.ensure(size * _LEADING)
            self.text(x, line, size, bold=bold)
            self.y -= size * _LEADING


_CONTENT_W = PAGE_W - 2 * MARGIN


def _cell_text(value: object) -> str:
    return "" if value is None else str(value)


def _table(layout: _Layout, table: Table) -> None:
    ncols = max([len(table.columns)] + [len(r) for r in table.rows]) or 1
    col_w = _CONTENT_W / ncols
    inner = max(col_w - 2 * _CELL_PAD, 10.0)
    line_h = _BODY * _LEADING
    page_room = PAGE_H - 2 * MARGIN

    def row_lines(cells: tuple[object, ...], bold: bool) -> list[list[str]]:
        return [
            wrap(_cell_text(cells[c]) if c < len(cells) else "", _BODY, inner, bold=bold)
            for c in range(ncols)
        ]

    header = row_lines(table.columns, True) if table.columns else []
    header_h = max((len(w) for w in header), default=0) * line_h + 2 * _CELL_PAD
    # The header is repeated on a continuation page only while it leaves room for the rows: a header
    # taller than half a page, repeated, would crowd every row onto yet another page.
    repeat_header = bool(header) and header_h <= page_room / 2

    def lines_that_fit() -> int:
        return int((layout.y - MARGIN - 2 * _CELL_PAD) / line_h)

    def continue_on_new_page(bold: bool) -> None:
        layout.new_page()
        if repeat_header and not bold:
            draw(header, True)  # the header again, so a continued table still reads

    def draw(wrapped: list[list[str]], bold: bool) -> None:
        # A row taller than what is left of the page continues on the next one, header repeated,
        # until every line is drawn. The earlier version cut a tall cell to one page (422 of 1500
        # words reached the PDF while the tool answered "saved") and could start a row below the
        # repeated header with nothing left to stand on, drawing it at a negative y, off the page.
        total = max(len(w) for w in wrapped)
        height_whole = total * line_h + 2 * _CELL_PAD
        room_on_fresh_page = page_room - (header_h if repeat_header and not bold else 0.0)
        if layout.y - height_whole < MARGIN and height_whole <= room_on_fresh_page and layout.ops:
            continue_on_new_page(bold)  # a row that fits a page is never split
        start = 0
        while start < total:
            if lines_that_fit() < 1:
                continue_on_new_page(bold)
            take = min(lines_that_fit(), total - start)
            height = take * line_h + 2 * _CELL_PAD
            top = layout.y
            for c, cell_lines in enumerate(wrapped):
                layout.y = top - _CELL_PAD
                for line in cell_lines[start : start + take]:
                    layout.text(MARGIN + c * col_w + _CELL_PAD, line, _BODY, bold=bold)
                    layout.y -= line_h
                layout.ops.append(
                    f"{MARGIN + c * col_w:.2f} {top - height:.2f} {col_w:.2f} {height:.2f} re S"
                )
            layout.y = top - height
            start += take

    if table.name:
        layout.lines(wrap(table.name, _BODY, _CONTENT_W, bold=True), _BODY, bold=True)
    if header:
        draw(header, True)
    for row in table.rows:
        draw(row_lines(row, False), False)
    layout.y -= _BODY * 0.6


def _layout(spec: DocumentSpec) -> _Layout:
    layout = _Layout()
    if spec.title:
        layout.lines(wrap(spec.title, _SIZES[0], _CONTENT_W, bold=True), _SIZES[0], bold=True)
        layout.y -= _SIZES[0] * 0.5
    for block in spec.blocks:
        if isinstance(block, Heading):
            size = _SIZES[block.level]
            layout.y -= size * 0.4
            layout.ensure(size * _LEADING * 2)  # never a heading alone at the foot of a page
            layout.lines(wrap(block.text, size, _CONTENT_W, bold=True), size, bold=True)
        elif isinstance(block, Paragraph):
            layout.lines(wrap(block.text, _BODY, _CONTENT_W), _BODY)
            layout.y -= _BODY * 0.5
        elif isinstance(block, Bullets):
            for n, item in enumerate(block.items, start=1):
                marker = f"{n}." if block.numbered else "-"
                lines = wrap(item, _BODY, _CONTENT_W - 18)
                layout.ensure(_BODY * _LEADING)
                layout.text(MARGIN + 4, marker, _BODY)
                layout.lines(lines, _BODY, x=MARGIN + 18)
            layout.y -= _BODY * 0.5
        elif isinstance(block, Table):
            _table(layout, block)
        elif isinstance(block, PageBreak):
            layout.new_page()
    return layout


def render_pdf(spec: DocumentSpec) -> tuple[bytes, int]:
    """The PDF's bytes, and how many characters were written as ``?`` for want of a glyph."""
    layout = _layout(spec)
    objects: list[bytes] = []
    n_pages = len(layout.pages)
    page_ids = [5 + 2 * i for i in range(n_pages)]
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode("ascii"))
    for base in ("Helvetica", "Helvetica-Bold"):
        objects.append(
            f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} /Encoding /WinAnsiEncoding >>".encode("ascii")
        )
    for i, ops in enumerate(layout.pages):
        stream = ("\n".join(["0.5 w"] + ops) + "\n").encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W:.0f} {PAGE_H:.0f}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {page_ids[i] + 1} 0 R >>".encode("ascii")
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream")
    info_id = len(objects) + 1
    title = _pdf_string(spec.title, layout.replaced) if spec.title else "()"
    objects.append(f"<< /Title {title} /Producer (Chimera create_document) >>".encode("latin-1"))

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, info_id, xref,
    )
    return bytes(out), layout.replaced[0]
