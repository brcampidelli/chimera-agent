"""Deliverable Mode — produce a polished, self-contained artifact, not a chat reply.

Where ``run``/``chat`` answer conversationally, ``deliver`` asks the model for a
complete, well-structured document (a report, plan, spec, README, ...) and the CLI
writes it to a file. Optionally routed through fusion for higher quality.

The binary formats (docx, xlsx, pdf) never ask the model for the file itself. The model writes
Markdown, :func:`chimera.tools.document_spec.markdown_to_spec` turns it into the same declarative spec
the ``create_document`` tool reads, and the same renderers write the bytes — so no model output is
ever executed to produce a document, and a spreadsheet cell from the model is text, never a formula —
except a cell that is a plain decimal number, which becomes a number so the sheet can sum it.
"""

from __future__ import annotations

from chimera.providers.gateway import Message, MessageLike, SupportsComplete

_FORMATS = {
    "md": "Markdown",
    "txt": "plain text",
    "html": "a complete, standalone HTML document",
    # The three below are rendered from Markdown; the label is what the MODEL is asked to write.
    "docx": "Markdown (it will be converted to a Word document: use #/##/### headings, paragraphs, "
    "- lists and pipe tables; no images or raw HTML)",
    "pdf": "Markdown (it will be converted to a PDF: use #/##/### headings, paragraphs, - lists and "
    "pipe tables; no images or raw HTML)",
    "xlsx": "Markdown pipe tables (they will be converted to an Excel workbook: put a ### heading "
    "naming each sheet directly above its table, a header row first, one value per cell)",
}

#: Formats written as bytes by the document renderers rather than as the model's text.
BINARY_FORMATS = frozenset({"docx", "xlsx", "pdf"})

#: Every format `chimera deliver --format` accepts.
FORMATS = tuple(_FORMATS)


def deliverable_system_prompt(fmt: str) -> str:
    label = _FORMATS.get(fmt, "Markdown")
    return (
        f"You are producing a polished, self-contained deliverable in {label}. "
        "Give it a clear title and well-structured sections, complete and ready to "
        "hand off as-is. Output ONLY the document itself — no preamble, no closing "
        "commentary, and do not wrap the whole thing in a code fence."
    )


def produce_deliverable(
    backend: SupportsComplete,
    request: str,
    *,
    fmt: str = "md",
    model: str | None = None,
) -> str:
    """Generate a complete deliverable document for ``request`` in ``fmt``."""
    messages: list[MessageLike] = [
        Message(role="system", content=deliverable_system_prompt(fmt)),
        Message(role="user", content=request),
    ]
    return backend.complete(messages, model=model, temperature=0.4).content


def render_deliverable(markdown: str, fmt: str) -> tuple[bytes, str]:
    """The bytes of a binary deliverable built from the model's Markdown, and a note for the owner.

    Raises :class:`chimera.tools.document_spec.SpecError` (a ``ValueError``) when the Markdown holds
    nothing the format can carry — an xlsx asked for and no table written — and ``ImportError`` when
    the ``documents-out`` extra a format needs is absent.
    """
    from chimera.tools.create_document import render
    from chimera.tools.document_spec import markdown_to_spec, parse_spec

    spec_dict = markdown_to_spec(markdown, numbers=fmt == "xlsx")  # a number in a sheet must sum
    blocks = spec_dict["blocks"]
    # The first top heading is the document's title, not its first section — written once, not twice.
    if blocks and blocks[0]["type"] == "heading" and blocks[0]["level"] == 1:
        spec_dict["title"] = blocks.pop(0)["text"]
    return render(parse_spec(spec_dict), fmt)
