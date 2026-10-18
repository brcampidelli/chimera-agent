"""A design as PNG: a screenshot of each artboard of the print page, in the same locked-down Chromium
page the PDF uses (:func:`chimera.design.export_pdf.static_page` — no script, no network).

One artboard gives one ``.png``. Several give a ``.zip`` of ``NN-<artboard id>.png`` files, named
from the server's ids and the order, never from the owner's text, so an archive entry is never a
path and never echoes a title.

An artboard wider or taller than :data:`MAX_SIDE` is refused before Chromium starts: a screenshot
that size is hundreds of megabytes of pixels, and a vector PDF is the honest format for it.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Mapping
from typing import Final

from chimera.design.export_html import artboards, render_print_html
from chimera.design.export_pdf import _rendered, static_page
from chimera.design.exports import ExportFailed
from chimera.design.model import DesignDocument

#: Chromium's largest screenshot side, and a sane bound on one export's memory.
MAX_SIDE: Final = 16_384


def render_png(
    doc: DesignDocument, *, assets: Mapping[str, bytes] | None = None
) -> tuple[bytes, bool]:
    """``(bytes, is_zip)``: one PNG for a single artboard, a zip of PNGs for several."""
    boards = artboards(doc)
    if not boards:
        raise ExportFailed("this design has no artboard to draw; add one first")
    for board in boards:
        if board.width > MAX_SIDE or board.height > MAX_SIDE:
            raise ExportFailed(
                f"an artboard is larger than {MAX_SIDE} px on a side, too large for a PNG; "
                "export it as PDF instead"
            )
    html = render_print_html(doc, assets=assets)
    width = int(max(b.width for b in boards))
    height = int(max(b.height for b in boards))

    def work() -> list[bytes]:
        with static_page(html, width=width, height=height) as page:
            return [
                bytes(page.locator(f'[data-artboard="{i}"]').screenshot(type="png"))
                for i in range(len(boards))
            ]

    shots = _rendered(work, "PNG")
    if len(shots) == 1:
        return shots[0], False
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for i, (board, shot) in enumerate(zip(boards, shots, strict=True), start=1):
            archive.writestr(f"{i:02d}-{board.id}.png", shot)
    return buffer.getvalue(), True
