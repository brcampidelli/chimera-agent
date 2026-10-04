"""The declarative document spec ``create_document`` renders — parsed and bounded before anything renders.

The model never writes code to build a document. It writes this: JSON naming headings, paragraphs,
lists, tables, sheets and slides, and the renderers (``document_render``, ``pdf_writer``) turn it into
bytes. That is the same choice ``render_chart`` made for charts, for the same reason — data can be
checked before it does anything, and code cannot.

Everything the renderers receive has been through :func:`parse_spec`, so they hold one invariant:
text is a ``str`` with no XML-illegal control character (python-docx raises on one, openpyxl raises
on another, and a document that fails half-way is worse than a refusal up front), every list is
bounded, and a table cell is ``str | int | float | bool | None``. Nothing else reaches them.

:func:`markdown_to_spec` is the other door in: ``chimera deliver --format docx|xlsx|pdf`` asks the
model for Markdown and converts it here, deterministically, so a binary deliverable is built by the
same renderers from the same kind of spec.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

#: The formats ``create_document`` writes. PDF needs no extra; the other three need ``documents-out``.
FORMATS = ("docx", "xlsx", "pptx", "pdf")

# Bounds. Generous for a real report, small enough that a runaway spec is a refusal and not a
# multi-hundred-megabyte file written into the owner's workspace.
MAX_BLOCKS = 2000
MAX_TEXT = 20_000
MAX_ITEMS = 200
MAX_ROWS = 5000
MAX_COLS = 50
MAX_SHEETS = 30
MAX_SLIDES = 200
#: Excel refuses a longer cell; refusing here names the cell instead of failing inside openpyxl.
MAX_CELL = 32_767

#: Characters XML 1.0 cannot carry. Tab, newline and carriage return are legal and kept.
_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")

Cell = str | int | float | bool | None


class SpecError(ValueError):
    """The spec is not a document this tool will render; the message says which part and why."""


@dataclass(frozen=True)
class Heading:
    text: str
    level: int = 1


@dataclass(frozen=True)
class Paragraph:
    text: str


@dataclass(frozen=True)
class Bullets:
    items: tuple[str, ...]
    numbered: bool = False


@dataclass(frozen=True)
class Table:
    columns: tuple[str, ...]
    rows: tuple[tuple[Cell, ...], ...]
    name: str = ""


@dataclass(frozen=True)
class PageBreak:
    pass


Block = Heading | Paragraph | Bullets | Table | PageBreak


@dataclass(frozen=True)
class Slide:
    title: str
    bullets: tuple[str, ...] = ()
    notes: str = ""
    table: Table | None = None


@dataclass(frozen=True)
class DocumentSpec:
    title: str = ""
    blocks: tuple[Block, ...] = ()
    sheets: tuple[Table, ...] = ()
    slides: tuple[Slide, ...] = ()
    counts: dict[str, int] = field(default_factory=dict)


def clean_text(value: Any, where: str, *, limit: int = MAX_TEXT) -> str:
    """A string with the XML-illegal characters removed, or a :class:`SpecError` naming ``where``."""
    if value is None:
        return ""
    if not isinstance(value, str | int | float) or isinstance(value, bool):
        raise SpecError(f"{where} must be text")
    text = _XML_ILLEGAL.sub("", str(value))
    if len(text) > limit:
        raise SpecError(f"{where} is {len(text)} characters (the limit is {limit})")
    return text


def _cell(value: Any, where: str) -> Cell:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        # NaN and infinity have no cell to go in; written as text they at least say what they were.
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        return clean_text(value, where, limit=MAX_CELL)
    raise SpecError(f"{where} must be text, a number, true/false or null")


def _list(value: Any, where: str, limit: int) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SpecError(f"{where} must be a list")
    if len(value) > limit:
        raise SpecError(f"{where} has {len(value)} entries (the limit is {limit})")
    return value


def _table(raw: Any, where: str) -> Table:
    if not isinstance(raw, dict):
        raise SpecError(f"{where} must be an object with 'columns' and 'rows'")
    columns = tuple(
        clean_text(c, f"{where}.columns[{i}]", limit=MAX_CELL)
        for i, c in enumerate(_list(raw.get("columns"), f"{where}.columns", MAX_COLS))
    )
    rows: list[tuple[Cell, ...]] = []
    for r, row in enumerate(_list(raw.get("rows"), f"{where}.rows", MAX_ROWS)):
        cells = _list(row, f"{where}.rows[{r}]", MAX_COLS)
        rows.append(tuple(_cell(v, f"{where}.rows[{r}][{c}]") for c, v in enumerate(cells)))
    if not columns and not rows:
        raise SpecError(f"{where} has neither columns nor rows")
    name = clean_text(raw.get("name") or raw.get("title"), f"{where}.name", limit=200)
    return Table(columns=columns, rows=tuple(rows), name=name)


def _block(raw: Any, where: str) -> Block:
    if not isinstance(raw, dict):
        raise SpecError(f"{where} must be an object with a 'type'")
    kind = str(raw.get("type") or "").lower()
    if kind == "heading":
        level = raw.get("level", 1)
        if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 3:
            raise SpecError(f"{where}.level must be 1, 2 or 3")
        return Heading(clean_text(raw.get("text"), f"{where}.text"), level)
    if kind == "paragraph":
        return Paragraph(clean_text(raw.get("text"), f"{where}.text"))
    if kind in ("bullets", "list"):
        items = _list(raw.get("items"), f"{where}.items", MAX_ITEMS)
        return Bullets(
            tuple(clean_text(t, f"{where}.items[{i}]") for i, t in enumerate(items)),
            numbered=bool(raw.get("numbered", False)),
        )
    if kind == "table":
        return _table(raw, where)
    if kind == "page_break":
        return PageBreak()
    raise SpecError(
        f"{where}.type {kind!r} is not one of heading, paragraph, bullets, table, page_break"
    )


def _slide(raw: Any, where: str) -> Slide:
    if not isinstance(raw, dict):
        raise SpecError(f"{where} must be an object with a 'title'")
    bullets = _list(raw.get("bullets"), f"{where}.bullets", MAX_ITEMS)
    return Slide(
        title=clean_text(raw.get("title"), f"{where}.title", limit=500),
        bullets=tuple(clean_text(b, f"{where}.bullets[{i}]") for i, b in enumerate(bullets)),
        notes=clean_text(raw.get("notes"), f"{where}.notes"),
        table=_table(raw["table"], f"{where}.table") if raw.get("table") else None,
    )


def parse_spec(raw: Any) -> DocumentSpec:
    """The spec as the renderers will see it, or a :class:`SpecError` saying what is wrong."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SpecError(f"the spec is not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict):
        raise SpecError("the spec must be a JSON object")
    blocks = tuple(
        _block(b, f"blocks[{i}]") for i, b in enumerate(_list(raw.get("blocks"), "blocks", MAX_BLOCKS))
    )
    sheets = tuple(
        _table(s, f"sheets[{i}]") for i, s in enumerate(_list(raw.get("sheets"), "sheets", MAX_SHEETS))
    )
    slides = tuple(
        _slide(s, f"slides[{i}]") for i, s in enumerate(_list(raw.get("slides"), "slides", MAX_SLIDES))
    )
    if not (blocks or sheets or slides):
        raise SpecError("the spec has no 'blocks', 'sheets' or 'slides' — nothing to write")
    counts: dict[str, int] = {}
    for block in blocks:
        key = type(block).__name__.lower()
        counts[key] = counts.get(key, 0) + 1
    return DocumentSpec(
        title=clean_text(raw.get("title"), "title", limit=500),
        blocks=blocks, sheets=sheets, slides=slides, counts=counts,
    )


# --- Markdown -> spec (the `chimera deliver` path) -------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_EMPHASIS = re.compile(r"(\*\*|__|`)")
#: A plain decimal number and nothing else: an optional minus, no leading zero (an id like 007 or a
#: CEP keeps its zeros as text), at most 15 digits (beyond that a float loses digits — a card or
#: account number stays text), and no thousands separator, which is ambiguous between 1,200.50
#: and 1.200,50. `=`, `+` and `@` never match, so nothing that could lead a formula becomes a number.
_PLAIN_NUMBER = re.compile(r"^-?(0|[1-9]\d{0,14})(\.\d{1,15})?$")


def _inline(text: str) -> str:
    """Markdown emphasis markers dropped: the renderers write plain text, and `**` in a Word file is noise."""
    return _EMPHASIS.sub("", text).strip()


def _row(line: str) -> list[str]:
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") else body
    return [_inline(c) for c in body.split("|")]


def _number_or_text(cell: str) -> str | int | float:
    """``1200.50`` as a number, so a spreadsheet can sum it; anything else stays the text it was."""
    if not _PLAIN_NUMBER.match(cell):
        return cell
    return float(cell) if "." in cell else int(cell)


def markdown_to_spec(markdown: str, *, title: str = "", numbers: bool = False) -> dict[str, Any]:
    """A spec dict (the shape :func:`parse_spec` reads) from Markdown — headings, lists, pipe tables.

    Deterministic and lossy on purpose: links, images and nested lists become their text, because the
    renderers write text. A table takes the nearest heading above it as its name, which is what lets
    the xlsx path name a sheet after the section that introduced it.

    ``numbers`` turns a body cell that is a plain decimal number into one (see :data:`_PLAIN_NUMBER`).
    It is for a spreadsheet, where `| jan | 1200.50 |` must sum; a Word or PDF table prints what the
    model wrote, and `1200.50` as a float would print `1200.5`.
    """
    blocks: list[dict[str, Any]] = []
    para: list[str] = []
    last_heading = ""
    lines = markdown.replace("\r\n", "\n").split("\n")

    def flush() -> None:
        if para:
            blocks.append({"type": "paragraph", "text": " ".join(para)})
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            flush()
            i += 1
            code: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            blocks.append({"type": "paragraph", "text": "\n".join(code)})
        elif m := _HEADING.match(line):
            flush()
            last_heading = _inline(m.group(2))
            blocks.append({"type": "heading", "text": last_heading, "level": min(len(m.group(1)), 3)})
        elif "|" in line and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            flush()
            columns = _row(line)
            i += 2
            rows: list[list[str | int | float]] = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                cells = _row(lines[i])
                rows.append([_number_or_text(c) for c in cells] if numbers else list(cells))
                i += 1
            blocks.append({"type": "table", "columns": columns, "rows": rows, "name": last_heading})
            continue
        elif _BULLET.match(line) or _NUMBERED.match(line):
            flush()
            # One list per run of the same marker: a numbered list right after a bulleted one is a
            # second list, as it is when Markdown renders it.
            numbered = _BULLET.match(line) is None
            pattern = _NUMBERED if numbered else _BULLET
            items: list[str] = []
            while i < len(lines) and (hit := pattern.match(lines[i])):
                items.append(_inline(hit.group(1)))
                i += 1
            blocks.append({"type": "bullets", "items": items, "numbered": numbered})
            continue
        elif not line.strip():
            flush()
        else:
            para.append(_inline(line))
        i += 1
    flush()
    return {"title": title, "blocks": blocks}
