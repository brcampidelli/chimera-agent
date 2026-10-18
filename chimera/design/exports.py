"""One entry point for every file format a design leaves the app as (PR D7).

``export_document(doc, fmt)`` returns the bytes, the media type and the file extension; the route
(:mod:`chimera.api.design_api`) and the tool (:mod:`chimera.tools.design`) decide where they go.

Two refusals, kept apart because they ask for different things from the person:

* :class:`ExportUnavailable` — the machine lacks what the format needs (Chromium for PDF and PNG,
  the ``documents-out`` extra for PPTX). The message names what to install and what it costs, and
  nothing is downloaded behind the person's back: a 150 MB browser is not something an export
  button should fetch on its own.
* :class:`ExportFailed` — the renderer was there and the rendering did not finish (a timeout, a
  crash). The message is a sentence; the detail goes to the log.

Each exporter imports its heavy library inside the function, so importing this package costs nothing
on a machine that never exports.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from chimera.design.model import DesignDocument

#: The formats a design exports to, in the order a screen offers them.
EXPORT_FORMATS: Final = ("html", "pdf", "png", "pptx")
PPTX_MEDIA_TYPE: Final = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
#: Every media type the export route can answer with, for its OpenAPI description.
EXPORT_MEDIA_TYPES: Final = (
    "text/html",
    "application/pdf",
    "image/png",
    "application/zip",
    PPTX_MEDIA_TYPE,
)


class ExportUnavailable(Exception):
    """This machine cannot write the format; the message says what to install."""


class ExportFailed(Exception):
    """The renderer started and did not finish; the message is safe to show."""


@dataclass(frozen=True)
class Exported:
    data: bytes
    media_type: str
    extension: str


def export_document(
    doc: DesignDocument, fmt: str, *, assets: Mapping[str, bytes] | None = None
) -> Exported:
    """``doc`` as ``fmt`` (one of :data:`EXPORT_FORMATS`). Raises ``ValueError`` on any other."""
    if fmt == "html":
        from chimera.design.export_html import render_html

        return Exported(render_html(doc, assets=assets).encode("utf-8"),
                        "text/html; charset=utf-8", "html")
    if fmt == "pdf":
        from chimera.design.export_pdf import render_pdf

        return Exported(render_pdf(doc, assets=assets), "application/pdf", "pdf")
    if fmt == "png":
        from chimera.design.export_png import render_png

        data, is_zip = render_png(doc, assets=assets)
        return Exported(data, "application/zip", "zip") if is_zip else Exported(data, "image/png", "png")
    if fmt == "pptx":
        from chimera.design.export_pptx import render_pptx

        return Exported(render_pptx(doc, assets=assets), PPTX_MEDIA_TYPE, "pptx")
    raise ValueError(f"unknown export format {fmt!r}")
