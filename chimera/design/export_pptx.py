"""A design as PowerPoint: one slide per artboard, every element an absolutely positioned shape.

python-pptx is the ``documents-out`` extra (``pip install 'chimera-agent[documents-out]'``); without
it this raises :class:`~chimera.design.exports.ExportUnavailable` naming that command. Nothing here
runs a browser, so a PPTX is the export that works on a machine without Chromium.

**Geometry.** A presentation has ONE slide size, and artboards may differ. The slide takes the first
artboard's size at 96 px per inch (9525 EMU per px, PowerPoint's own conversion), scaled down — or
up — uniformly when that would leave PowerPoint's limits of 1 to 56 inches a side. Every artboard is
then scaled uniformly to fit that slide and centred, so an artboard with another aspect is
letterboxed, never stretched. Each element's frame is multiplied by the same scale and offset; the
tests check the EMU against the frame.

**What maps to what.**

=================  ==============================================================================
rectangle          an autoshape rectangle (rounded when the element has a radius)
ellipse            an oval
line               a straight connector between the two points, in the frame
path               a freeform polyline through its points
text               a text box, no insets, wrapping; size, weight and colour from the font and colour
                   tokens, as the HTML export draws them; one paragraph per line
image              the picture when the caller passes its bytes and they are PNG or JPEG; otherwise a
                   dashed placeholder box labelled with the alt text
group              nothing of its own: its children carry their own frames
=================  ==============================================================================

Rotation carries over (both are degrees clockwise), except on a line, whose two ends are already
placed. Not carried: opacity and the alpha of an
``#RRGGBBAA`` colour (the colour is used opaque), an image's ``fit`` (a picture fills its frame),
and fonts the machine opening the file does not have (PowerPoint substitutes).
"""

from __future__ import annotations

import io
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from chimera.design.assets import AssetRejected, validate_asset
from chimera.design.export_html import artboards, render_html_artboard, text_color
from chimera.design.export_pdf import MAX_FILL_HEIGHT, _rendered, static_page
from chimera.design.exports import ExportFailed, ExportUnavailable
from chimera.design.model import (
    Artboard,
    DesignDocument,
    DesignNode,
    GroupNode,
    ImageNode,
    ShapeNode,
    TextNode,
)
from chimera.telemetry import get_logger

#: PowerPoint's conversion: 914400 EMU an inch, 96 CSS px an inch.
EMU_PER_PX: Final = 9525
#: PowerPoint refuses a slide side outside 1 to 56 inches.
MIN_SLIDE_EMU: Final = 914_400
MAX_SLIDE_EMU: Final = 51_206_400
EMU_PER_PT: Final = 12_700
_BLANK_LAYOUT = 6
_PLACEHOLDER_FILL = "F3F4F6"
_PLACEHOLDER_LINE = "9CA3AF"
_PLACEHOLDER_TEXT = "4B5563"
#: Picture types python-pptx can embed. WebP is a valid asset here and becomes a placeholder there.
_EMBEDDABLE = frozenset({"image/png", "image/jpeg"})

MISSING: Final = (
    "PPTX export needs python-pptx, the documents-out extra: "
    "pip install 'chimera-agent[documents-out]' (or: uv sync --extra documents-out)"
)


@dataclass(frozen=True)
class Placement:
    """How one artboard lands on the slide: EMU per px, and the EMU offset of its top-left corner."""

    scale: float
    left: float
    top: float

    def x(self, px: float) -> int:
        return round(self.left + px * self.scale)

    def y(self, px: float) -> int:
        return round(self.top + px * self.scale)

    def size(self, px: float) -> int:
        return max(1, round(px * self.scale))


def slide_size(first: Artboard) -> tuple[int, int]:
    """The slide in EMU: the first artboard at 96 dpi, scaled uniformly into PowerPoint's limits."""
    width = first.width * EMU_PER_PX
    height = first.height * EMU_PER_PX
    factor = 1.0
    if max(width, height) > MAX_SLIDE_EMU:
        factor = MAX_SLIDE_EMU / max(width, height)
    elif min(width, height) < MIN_SLIDE_EMU:
        factor = min(MIN_SLIDE_EMU / min(width, height), MAX_SLIDE_EMU / max(width, height))
    return (
        min(MAX_SLIDE_EMU, max(MIN_SLIDE_EMU, round(width * factor))),
        min(MAX_SLIDE_EMU, max(MIN_SLIDE_EMU, round(height * factor))),
    )


def placement(board: Artboard, slide_width: int, slide_height: int) -> Placement:
    """``board`` scaled uniformly to fit the slide, centred."""
    scale = min(slide_width / board.width, slide_height / board.height)
    return Placement(
        scale,
        (slide_width - board.width * scale) / 2,
        (slide_height - board.height * scale) / 2,
    )


def render_pptx(doc: DesignDocument, *, assets: Mapping[str, bytes] | None = None) -> bytes:
    """``doc`` as ``.pptx`` bytes, one slide per artboard in reading order."""
    try:
        from pptx import Presentation
    except ImportError:
        raise ExportUnavailable(MISSING) from None
    boards = artboards(doc)
    if not boards:
        raise ExportFailed("this design has no artboard to put on a slide; add one first")
    prs: Any = Presentation()
    width, height = slide_size(boards[0])
    prs.slide_width = width
    prs.slide_height = height
    for board in boards:
        slide = prs.slides.add_slide(prs.slide_layouts[_BLANK_LAYOUT])
        place = placement(board, width, height)
        if board.html is not None:
            _add_html_artboard(slide, doc, board, place, assets or {})
        else:
            _background(slide, board, place)
            for node_id in board.node_ids:
                _draw(slide, doc, doc.nodes[node_id], place, assets or {})
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


#: Our artboard is the body's direct child; the author's content cannot be the match.
_HTML_ARTBOARD = "body > main[data-chimera-artboard]"
#: Text leaves get a marker through the automation protocol, not page script. SVG text is skipped:
#: it has no ``inner_text``, and it stays in the picture, which draws it faithfully.
_MARK_TEXT_JS = (
    "root => { for (const node of root.querySelectorAll('*')) { "
    "if (!(node instanceof SVGElement) && node.children.length === 0 && node.textContent.trim()) "
    "node.setAttribute('data-export-text', '1'); } }"
)
_TEXT_STYLE_JS = (
    "element => { const s = getComputedStyle(element); return `font-size:${s.fontSize};"
    "font-weight:${s.fontWeight};color:${s.color};text-align:${s.textAlign};"
    "font-family:${s.fontFamily}`; }"
)
_HIDE_TEXT_CSS = "[data-export-text]{color:transparent!important;-webkit-text-fill-color:transparent!important}"
#: Chromium reports a computed colour as rgb()/rgba(); #RRGGBB is accepted too.
_COLOUR_RE = re.compile(
    r"(?:^|;)\s*color:\s*(?:(#[0-9A-Fa-f]{6})|rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+)\s*)?\))"
)
_log = get_logger("design.export_pptx")


def _add_html_artboard(slide: Any, doc: DesignDocument, board: Artboard, place: Placement,
                       assets: Mapping[str, bytes]) -> None:
    html_source = render_html_artboard(board, doc.tokens, assets=assets)

    def work() -> tuple[bytes, list[tuple[dict[str, str], dict[str, float]]]]:
        with static_page(html_source, width=round(board.width), height=round(board.height)) as page:
            _fit_html_fill(page, board)
            artboard = page.locator(_HTML_ARTBOARD)
            artboard.evaluate(_MARK_TEXT_JS)
            measured = [m for el in artboard.locator("[data-export-text]").all()
                        if (m := _measure_text(el)) is not None]
            page.add_style_tag(content=_HIDE_TEXT_CSS)
            return bytes(artboard.screenshot(type="png")), measured

    background, measured = _rendered(work, "PPTX")
    slide.shapes.add_picture(io.BytesIO(background), place.x(0), place.y(0),
                             width=place.size(board.width), height=place.size(board.height))
    for data, box in measured:
        _add_text_box(slide, place, data, box)


def _measure_text(element: Any) -> tuple[dict[str, str], dict[str, float]] | None:
    """One text element's text, computed style and box, or None when there is nothing to draw or
    Chromium cannot measure it. An element that fails stays in the picture; the export goes on."""
    try:
        text = element.inner_text().strip()
        box = element.bounding_box() if text else None
        if not text or box is None:
            return None
        return {"text": text, "style": element.evaluate(_TEXT_STYLE_JS)}, box
    except Exception as exc:  # noqa: BLE001 - one unmeasurable element must not stop the export
        _log.warning("design PPTX export: a text element was left in the picture: %s", exc)
        return None


def _add_text_box(slide: Any, place: Placement, data: dict[str, str], box: dict[str, float]) -> None:
    from pptx.enum.text import MSO_ANCHOR
    from pptx.util import Emu, Pt

    style = data["style"]
    shape = slide.shapes.add_textbox(
        place.x(box["x"]), place.y(box["y"]), place.size(max(1, box["width"])),
        place.size(max(1, box["height"])),
    )
    frame = shape.text_frame
    frame.clear()
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    frame.vertical_anchor = MSO_ANCHOR.TOP
    paragraph = frame.paragraphs[0]
    paragraph.text = data["text"]
    paragraph.alignment = _html_alignment(style)
    size_match = re.search(r"font-size:\s*([0-9.]+)px", style)
    # Artboard pixels times the slide's EMU per pixel: the same conversion the v1 text uses.
    size = (Emu(max(EMU_PER_PT, round(float(size_match.group(1)) * place.scale)))
            if size_match else Pt(12))
    colour = _style_colour(style)
    family = _style_family(style)
    bold = bool(re.search(r"font-weight:\s*(?:bold|[6-9]00)", style))
    for run in paragraph.runs:
        run.font.size = size
        run.font.bold = bold
        if colour is not None:
            run.font.color.rgb = _rgb(colour)
        if family is not None:
            run.font.name = family
    shape.name = "editable html text"


def _style_colour(style: str) -> str | None:
    """#RRGGBB from a computed style, or None when the colour is fully transparent (nothing to draw,
    so PowerPoint's default colour stays)."""
    match = _COLOUR_RE.search(style)
    if match is None:
        return None
    if match.group(1):
        return match.group(1)
    alpha = match.group(5)
    if alpha is not None and float(alpha) == 0:
        return None
    return "#" + "".join(f"{min(255, int(match.group(i))):02X}" for i in (2, 3, 4))


def _style_family(style: str) -> str | None:
    """The first font family of a computed style, without its quotes."""
    match = re.search(r"(?:^|;)\s*font-family:\s*([^;]+)", style)
    if match is None:
        return None
    first = match.group(1).split(",")[0].strip().strip("'\"").strip()
    return first or None


def _html_alignment(style: str) -> Any:
    from pptx.enum.text import PP_ALIGN

    match = re.search(r"(?:^|;)\s*text-align:\s*([a-z-]+)", style)
    value = match.group(1) if match else "left"
    return {
        "center": PP_ALIGN.CENTER,
        "right": PP_ALIGN.RIGHT,
        "end": PP_ALIGN.RIGHT,
        "justify": PP_ALIGN.JUSTIFY,
    }.get(value, PP_ALIGN.LEFT)


def _fit_html_fill(page: Any, board: Artboard) -> None:
    if board.mode != "fill":
        return
    box = page.locator(_HTML_ARTBOARD).bounding_box()
    height = min(MAX_FILL_HEIGHT, max(1, round(box["height"] if box else board.height)))
    page.add_style_tag(content=f'body > main[data-chimera-artboard]{{height:{height}px;min-height:{height}px}}')


# ---- drawing ------------------------------------------------------------------------------------


def _rgb(color: str) -> Any:
    from pptx.dml.color import RGBColor

    rgb: Any = RGBColor  # python-pptx ships no type hints for this constructor
    return rgb.from_string(color.lstrip("#")[:6].upper())


def _frame(node: DesignNode, place: Placement) -> tuple[int, int, int, int]:
    f = node.frame
    return place.x(f.x), place.y(f.y), place.size(f.width), place.size(f.height)


def _background(slide: Any, board: Artboard, place: Placement) -> None:
    from pptx.enum.shapes import MSO_SHAPE

    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, place.x(0), place.y(0), place.size(board.width), place.size(board.height)
    )
    shape.name = f"artboard {board.id}"
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(board.background)
    shape.line.fill.background()


def _draw(
    slide: Any, doc: DesignDocument, node: DesignNode, place: Placement, assets: Mapping[str, bytes]
) -> None:
    if not node.visible:
        return
    if isinstance(node, GroupNode):
        for child in node.child_ids:
            _draw(slide, doc, doc.nodes[child], place, assets)
        return
    if isinstance(node, TextNode):
        shape = _text(slide, doc, node, place)
    elif isinstance(node, ShapeNode) and node.shape == "line":
        shape = _line(slide, node, place)
    elif isinstance(node, ShapeNode) and node.shape == "path":
        shape = _path(slide, node, place)
    elif isinstance(node, ShapeNode):
        shape = _box(slide, node, place)
    else:
        assert isinstance(node, ImageNode)
        shape = _image(slide, node, place, assets.get(node.asset_id))
    if shape is None:
        return
    shape.name = node.id
    if node.frame.rotation and not (isinstance(node, ShapeNode) and node.shape == "line"):
        shape.rotation = float(node.frame.rotation)


def _stroke(shape: Any, node: DesignNode, place: Placement, *, default: str | None) -> None:
    color = node.style.stroke or default
    if color is None:
        shape.line.fill.background()
        return
    width = node.style.stroke_width if node.style.stroke_width is not None else 1.0
    shape.line.color.rgb = _rgb(color)
    shape.line.width = max(1, round(width * place.scale))


def _box(slide: Any, node: ShapeNode, place: Placement) -> Any:
    from pptx.enum.shapes import MSO_SHAPE

    if node.shape == "ellipse":
        kind = MSO_SHAPE.OVAL
    elif node.style.radius:
        kind = MSO_SHAPE.ROUNDED_RECTANGLE
    else:
        kind = MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(kind, *_frame(node, place))
    if kind == MSO_SHAPE.ROUNDED_RECTANGLE and node.style.radius:
        shortest = min(node.frame.width, node.frame.height)
        shape.adjustments[0] = min(0.5, node.style.radius / shortest)
    if node.style.fill is not None:
        shape.fill.solid()
        shape.fill.fore_color.rgb = _rgb(node.style.fill)
    else:
        shape.fill.background()
    _stroke(shape, node, place, default=None)
    return shape


def _points(node: ShapeNode, place: Placement) -> list[tuple[int, int]]:
    """The element's 0..1 points, in slide EMU."""
    f = node.frame
    raw = node.points or [0.0, 0.0, 1.0, 1.0]
    return [
        (place.x(f.x + raw[i] * f.width), place.y(f.y + raw[i + 1] * f.height))
        for i in range(0, len(raw) - 1, 2)
    ]


def _line(slide: Any, node: ShapeNode, place: Placement) -> Any:
    from pptx.enum.shapes import MSO_CONNECTOR

    (x0, y0), (x1, y1) = _points(node, place)[:2]
    shape = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, x0, y0, x1, y1)
    _stroke(shape, node, place, default="#000000")
    return shape


def _path(slide: Any, node: ShapeNode, place: Placement) -> Any:
    points = _points(node, place)
    if len(points) < 2:
        return None
    builder = slide.shapes.build_freeform(points[0][0], points[0][1], scale=1.0)
    builder.add_line_segments(points[1:], close=False)
    shape = builder.convert_to_shape()
    if node.style.fill is not None:
        shape.fill.solid()
        shape.fill.fore_color.rgb = _rgb(node.style.fill)
    else:
        shape.fill.background()
    _stroke(shape, node, place, default="#000000")
    return shape


def _text(slide: Any, doc: DesignDocument, node: TextNode, place: Placement) -> Any:
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Emu

    box = slide.shapes.add_textbox(*_frame(node, place))
    frame = box.text_frame
    frame.word_wrap = True
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    frame.vertical_anchor = MSO_ANCHOR.TOP
    font = doc.tokens.typography.get(node.font_token or "")
    size_px = font.size if font is not None else 16.0
    family = font.family.split(",")[0].strip().strip("'") if font is not None else None
    bold = font is not None and font.weight >= 600
    color = _rgb(text_color(doc, node))
    align = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}[
        node.align or "left"
    ]
    for index, line in enumerate(node.text.split("\n")):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = align
        run = paragraph.add_run()
        run.text = line
        run.font.size = Emu(max(EMU_PER_PT, round(size_px * place.scale)))
        run.font.bold = bold
        run.font.color.rgb = color
        if family:
            run.font.name = family
    return box


def _image(slide: Any, node: ImageNode, place: Placement, data: bytes | None) -> Any:
    if data is not None:
        try:
            info = validate_asset(data)
        except AssetRejected:
            info = None
        if info is not None and info.mime in _EMBEDDABLE:
            return slide.shapes.add_picture(io.BytesIO(data), *_frame(node, place))
    from pptx.enum.dml import MSO_LINE_DASH_STYLE
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Pt

    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, *_frame(node, place))
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(_PLACEHOLDER_FILL)
    shape.line.color.rgb = _rgb(_PLACEHOLDER_LINE)
    shape.line.dash_style = MSO_LINE_DASH_STYLE.DASH
    frame = shape.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    paragraph = frame.paragraphs[0]
    paragraph.alignment = PP_ALIGN.CENTER
    run = paragraph.add_run()
    run.text = node.alt or node.name or "image"
    run.font.size = Pt(9)
    run.font.color.rgb = _rgb(_PLACEHOLDER_TEXT)
    return shape
