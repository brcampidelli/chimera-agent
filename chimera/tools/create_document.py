"""create_document — write a Word, Excel, PowerPoint or PDF file from a declarative JSON spec.

Chimera could read every one of these formats (``read_document``) and write none of them: a request
for "the report as a Word file" ended in Markdown and an apology, or in the model writing python-docx
code for ``execute_code`` — which is code the model wrote, run on the owner's machine, to produce
what is only a document. This tool is the ``render_chart`` answer to the same problem: the model
writes DATA (``chimera.tools.document_spec``), and fixed renderers turn it into the file.

What it does not do, on purpose: run anything the model wrote, reach anything outside the workspace,
or write past the run's write region — the output path goes through ``resolve_for`` and
``refuse_write`` like every other writer. Spreadsheet text never becomes a formula
(``document_render._set_cell``).

**Off by default** (``CHIMERA_CREATE_DOCUMENT``), switched on from the Tools screen. The study-29
plan proposed it on, by analogy with ``todo_write``; its own critic disagreed, and the critic is
right by this project's rule: ``todo_write`` is on because it makes no quality claim and records
progress on every run, while this is a rarely-used tool whose schema would be paid on every step of
every run (the measurement behind ``defer_tools``: eighteen never-called tools were 86% of the
schema). That is ``decide``'s profile, and ``decide`` ships off. With ``CHIMERA_DEFER_TOOLS`` on it
is deferred like any other non-core tool.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from chimera.tools.base import Tool
from chimera.tools.document_render import (
    INSTALL_HINT,
    render_docx,
    render_pptx,
    render_xlsx,
    xlsx_sheets,
)
from chimera.tools.document_spec import FORMATS, DocumentSpec, SpecError, parse_spec
from chimera.tools.pdf_writer import render_pdf
from chimera.tools.workspace import resolve_for
from chimera.tools.write_region import WriteRegion, refuse_write

_RENDERERS: dict[str, Callable[[DocumentSpec], bytes]] = {
    "docx": render_docx,
    "xlsx": render_xlsx,
    "pptx": render_pptx,
}


def resolve_format(fmt: Any, path: Any) -> str:
    """The format named by ``format`` or by the path's extension; a mismatch is refused, not guessed."""
    named = str(fmt or "").lower().lstrip(".")
    suffix = Path(str(path)).suffix.lower().lstrip(".") if path else ""
    if named and named not in FORMATS:
        raise SpecError(f"unknown format {named!r} (use {', '.join(FORMATS)})")
    if named and suffix in FORMATS and suffix != named:
        raise SpecError(f"format {named!r} does not match the path's extension .{suffix}")
    if named and suffix and suffix not in FORMATS:
        # `format` used to win here, so path='README.md', format='pdf' replaced the README with PDF
        # bytes — and a binary "after" gives the ledger no diff and the screen no undo.
        raise SpecError(f"the path ends in .{suffix}, not .{named}: give a path ending in .{named}")
    chosen = named or (suffix if suffix in FORMATS else "")
    if not chosen:
        raise SpecError(f"say which format to write: 'format' ({', '.join(FORMATS)}) or a path ending in one")
    return chosen


def render(spec: DocumentSpec, fmt: str) -> tuple[bytes, str]:
    """The file's bytes and a note for the answer (empty, or what the PDF could not draw).

    Raises ``ImportError`` when the extra a format needs is absent and ``ValueError`` when the spec has
    nothing for that format (an xlsx with no table, a pptx with no slides).
    """
    if fmt == "pdf":
        data, replaced = render_pdf(spec)
        note = (
            f"; {replaced} character(s) have no glyph in the PDF's built-in font and were written as '?'"
            if replaced
            else ""
        )
        return data, note
    return _RENDERERS[fmt](spec), ""


_BLOCK_NAMES = {"bullets": "list", "pagebreak": "page break"}


def summary(spec: DocumentSpec, fmt: str) -> str:
    """What was written, for the answer: the model reads it, and so does a person scanning the log."""
    if fmt == "pptx":
        return f"{len(spec.slides)} slide(s)"
    if fmt == "xlsx":
        sheets = xlsx_sheets(spec)
        return f"{len(sheets)} sheet(s), {sum(len(s.rows) for s in sheets)} row(s)"
    parts = [f"{n} {_BLOCK_NAMES.get(kind, kind)}(s)" for kind, n in sorted(spec.counts.items())]
    return ", ".join(parts) or "no blocks"


class CreateDocumentTool(Tool):
    name = "create_document"
    description = (
        "Write a Word (.docx), Excel (.xlsx), PowerPoint (.pptx) or PDF file from a declarative JSON "
        "spec: data, not code. docx and pdf read 'blocks' (heading, paragraph, bullets, table, "
        "page_break); xlsx reads 'sheets' (or the tables among the blocks); pptx reads 'slides'. Text "
        "in a spreadsheet stays text, never a formula. docx, xlsx and pptx need the 'documents-out' "
        "extra; pdf needs none."
    )
    parameters = {
        "type": "object",
        "properties": {
            "spec": {
                "type": "object",
                "description": (
                    "{title?, blocks?: [{type: heading|paragraph|bullets|table|page_break, text?, "
                    "level? (1-3), items? (bullets), numbered?, columns?, rows? (table), name?}], "
                    "sheets?: [{name, columns, rows}], slides?: [{title, bullets?, notes?, "
                    "table?: {columns, rows}}]}. A cell is text, a number, true/false or null."
                ),
            },
            "path": {"type": "string", "description": "Output file in the workspace, e.g. report.docx."},
            "format": {"type": "string", "description": "docx, xlsx, pptx or pdf (default: the path's extension)."},
        },
        "required": ["spec", "path"],
    }

    def __init__(
        self, workspace: Path | None = None, *, write_region: WriteRegion | None = None
    ) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.write_region = write_region

    def run(self, **kwargs: Any) -> str:
        try:
            fmt = resolve_format(kwargs.get("format"), kwargs.get("path"))
            spec = parse_spec(kwargs.get("spec"))
        except SpecError as exc:
            return f"error: invalid document spec: {exc}"
        raw_path = str(kwargs.get("path") or f"document.{fmt}")
        if not Path(raw_path).suffix:
            raw_path += f".{fmt}"  # `relatorio` + format pdf: never an extension-less file like LICENSE
        out = resolve_for(self, raw_path, verb="write")
        if err := refuse_write(self.workspace, out, self.write_region):
            return err
        # Rendered whole BEFORE anything touches the disk: a failure half-way through a large table
        # must not leave a truncated report under the name the owner asked for.
        try:
            data, note = render(spec, fmt)
        except ImportError:
            return INSTALL_HINT.format(fmt=fmt)
        except ValueError as exc:
            return f"error: invalid document spec: {exc}"
        except Exception as exc:  # noqa: BLE001 — a render failure is a tool error, not a crash
            return f"error: could not render the {fmt}: {exc}"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return f"saved {fmt} document to {out} ({summary(spec, fmt)}, {len(data)} bytes){note}"
