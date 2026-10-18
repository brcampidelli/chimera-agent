"""A static HTML rendering of a design document, and the checks of a document against its tokens.

The page is inert by construction, not by filtering:

- every string from the document goes through :func:`html.escape` (titles, names, text, alt);
- every other value written into markup is a number, a colour that matched ``#RRGGBB[AA]``, a font
  family that matched a pattern without ``;{}<>"\\``, or a literal from this file;
- there is no ``<script>``, no event attribute, and no URL except ``data:`` images built here from
  bytes the caller supplies and :func:`chimera.design.assets.validate_asset` re-identifies — an asset
  that fails is drawn as a labelled placeholder box;
- a Content-Security-Policy meta forbids scripts, external fetches, forms and base changes, so a bug
  in the first three points still has no network and no script to reach.

Frames are artboard coordinates, so every element is absolutely positioned inside its artboard and a
group draws nothing of its own (its children carry their own frames).
"""

from __future__ import annotations

import base64
import html
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from chimera.design.assets import AssetRejected, validate_asset
from chimera.design.model import (
    Artboard,
    DesignDocument,
    DesignNode,
    GroupNode,
    ImageNode,
    ShapeNode,
    TextNode,
)
from chimera.design.tokens import AA_LARGE, AA_NORMAL, contrast_ratio

CSP = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_FALLBACK_FONT = "system-ui, sans-serif"
_FALLBACK_TEXT = "#000000"

_STYLE = (
    "body{margin:0;padding:24px;background:#E5E7EB;font-family:system-ui,sans-serif;color:#111827}"
    "h1{font-size:20px;margin:0 0 16px}h2{font-size:16px;margin:24px 0 8px}"
    ".artboard{position:relative;overflow:hidden;margin:0 0 24px;box-shadow:0 1px 3px #0000004D}"
    ".el{position:absolute;box-sizing:border-box;margin:0}"
    ".text{white-space:pre-wrap;overflow-wrap:anywhere}"
    ".placeholder{display:flex;align-items:center;justify-content:center;text-align:center;"
    "background:#F3F4F6;border:1px dashed #9CA3AF;color:#4B5563;font-size:12px}"
)
#: The print page: the artboards and nothing else. ``print-color-adjust`` keeps backgrounds in the
#: PDF, which Chromium otherwise drops when printing.
_PRINT_STYLE = (
    "html,body{margin:0;padding:0;background:#FFFFFF;font-family:system-ui,sans-serif}"
    ".artboard{position:relative;overflow:hidden;margin:0;break-after:page;"
    "print-color-adjust:exact;-webkit-print-color-adjust:exact}"
    ".artboard:last-child{break-after:auto}"
    ".el{position:absolute;box-sizing:border-box;margin:0}"
    ".text{white-space:pre-wrap;overflow-wrap:anywhere}"
    ".placeholder{display:flex;align-items:center;justify-content:center;text-align:center;"
    "background:#F3F4F6;border:1px dashed #9CA3AF;color:#4B5563;font-size:12px}"
)


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _num(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".") or "0"


def render_html(doc: DesignDocument, *, assets: Mapping[str, bytes] | None = None) -> str:
    """The whole document as one self-contained HTML page."""
    assets = assets or {}
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_esc(doc.title)}</title><style>{_STYLE}</style></head><body>",
        f"<h1>{_esc(doc.title)}</h1>",
    ]
    for page in doc.pages:
        parts.append(f'<section aria-label="{_esc(page.name or page.id)}">')
        parts.append(f"<h2>{_esc(page.name or page.id)}</h2>")
        for board in page.artboards:
            _render_board(doc, board, assets, parts)
        parts.append("</section>")
    parts.append("</body></html>")
    return "\n".join(parts)


def _render_board(
    doc: DesignDocument,
    board: Artboard,
    assets: Mapping[str, bytes],
    out: list[str],
    *,
    css_class: str = "artboard",
    index: int | None = None,
) -> None:
    marker = f' data-artboard="{index}"' if index is not None else ""
    out.append(
        f'<div class="{css_class}"{marker} aria-label="{_esc(board.name or board.id)}" style="'
        f"width:{_num(board.width)}px;height:{_num(board.height)}px;"
        f'background:{board.background}">'
    )
    for node_id in board.node_ids:
        _render_node(doc, doc.nodes[node_id], assets, out)
    out.append("</div>")


def artboards(doc: DesignDocument) -> list[Artboard]:
    """Every artboard of ``doc``, page by page, in the order a reader meets them."""
    return [board for page in doc.pages for board in page.artboards]


def render_print_html(doc: DesignDocument, *, assets: Mapping[str, bytes] | None = None) -> str:
    """The artboards alone, edge to edge, one printed page each at the artboard's own size.

    What the PDF and PNG exporters load into Chromium: no headings, no padding, no shadow, so a
    page of the PDF and a screenshot of an artboard are exactly the artboard. Each artboard is a CSS
    named page (``@page bN {size: W H}``), which is how one PDF holds pages of different sizes, and
    carries ``data-artboard="N"`` so a screenshot can find it. Same guarantees as
    :func:`render_html`: every value written is escaped, a number, a validated colour or a literal
    from this file, and the same CSP rides along.
    """
    assets = assets or {}
    boards = artboards(doc)
    pages_css = "".join(
        f"@page b{i}{{size:{_num(b.width)}px {_num(b.height)}px;margin:0}}"
        f'.print[data-artboard="{i}"]{{page:b{i}}}'
        for i, b in enumerate(boards)
    )
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">',
        f"<title>{_esc(doc.title)}</title><style>{_PRINT_STYLE}{pages_css}</style></head><body>",
    ]
    for i, board in enumerate(boards):
        _render_board(doc, board, assets, parts, css_class="artboard print", index=i)
    parts.append("</body></html>")
    return "\n".join(parts)


def _box(node: DesignNode) -> str:
    f = node.frame
    css = [
        f"left:{_num(f.x)}px", f"top:{_num(f.y)}px",
        f"width:{_num(f.width)}px", f"height:{_num(f.height)}px",
    ]
    if f.rotation:
        css.append(f"transform:rotate({_num(f.rotation)}deg)")
    if node.style.opacity is not None:
        css.append(f"opacity:{_num(node.style.opacity)}")
    if node.style.radius is not None:
        css.append(f"border-radius:{_num(node.style.radius)}px")
    return ";".join(css)


def _render_node(
    doc: DesignDocument, node: DesignNode, assets: Mapping[str, bytes], out: list[str]
) -> None:
    if not node.visible:
        return
    if isinstance(node, GroupNode):
        for child in node.child_ids:
            _render_node(doc, doc.nodes[child], assets, out)
        return
    box = _box(node)
    if isinstance(node, TextNode):
        out.append(f'<p class="el text" style="{box};{_esc(_text_css(doc, node))}">'
                   f"{_esc(node.text)}</p>")
    elif isinstance(node, ShapeNode) and node.shape in ("line", "path"):
        out.append(_stroke_svg(node, box))
    elif isinstance(node, ShapeNode):
        out.append(f'<div class="el" role="presentation" style="{box};{_shape_css(node)}"></div>')
    elif isinstance(node, ImageNode):
        out.append(_image(node, box, assets.get(node.asset_id)))


def _text_css(doc: DesignDocument, node: TextNode) -> str:
    css = [f"color:{text_color(doc, node)}", f"text-align:{node.align or 'left'}"]
    font = doc.tokens.typography.get(node.font_token or "")
    if font is not None:
        css += [f"font-family:{font.family}", f"font-size:{_num(font.size)}px",
                f"font-weight:{font.weight}"]
        if font.line_height is not None:
            css.append(f"line-height:{_num(font.line_height)}")
    else:
        css.append(f"font-family:{_FALLBACK_FONT}")
    return ";".join(css)


def _stroke_svg(node: ShapeNode, box: str) -> str:
    """A line or path as inline SVG in a unit viewBox: its points are already 0..1 of the frame, so
    the drawing scales with the frame, and the stroke keeps its pixel width. Only numbers and
    validated colours are written; there is no ``href`` and no script."""
    style = node.style
    points = node.points or [0.0, 0.0, 1.0, 1.0]
    coords = " ".join(f"{_num(points[i])},{_num(points[i + 1])}" for i in range(0, len(points), 2))
    width = style.stroke_width if style.stroke_width is not None else 1.0
    return (
        f'<svg class="el" role="presentation" viewBox="0 0 1 1" preserveAspectRatio="none" '
        f'style="{box};overflow:visible"><polyline points="{coords}" fill="none" '
        f'stroke="{style.stroke or "#000000"}" stroke-width="{_num(width)}" '
        f'vector-effect="non-scaling-stroke" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )


def _shape_css(node: ShapeNode) -> str:
    style = node.style
    width = style.stroke_width if style.stroke_width is not None else 1.0
    css = [f"background:{style.fill or 'transparent'}"]
    if style.stroke is not None:
        css.append(f"border:{_num(width)}px solid {style.stroke}")
    if node.shape == "ellipse":
        css.append("border-radius:50%")
    return ";".join(css)


def _image(node: ImageNode, box: str, data: bytes | None) -> str:
    label = _esc(node.alt or node.name or "image")
    if data is not None:
        try:
            info = validate_asset(data)
        except AssetRejected:
            info = None
        if info is not None:
            encoded = base64.b64encode(data).decode("ascii")
            return (f'<img class="el" alt="{label}" src="data:{info.mime};base64,{encoded}" '
                    f'style="{box};object-fit:{node.fit}">')
    return f'<div class="el placeholder" role="img" aria-label="{label}" style="{box}">{label}</div>'


def text_color(doc: DesignDocument, node: TextNode) -> str:
    """The colour a text element is drawn in: its colour token, else its fill, else black."""
    token = doc.tokens.colors.get(node.color_token or "")
    if token is not None:
        return token
    return node.style.fill or _FALLBACK_TEXT


# ---- checks -------------------------------------------------------------------------------------

IssueKind = Literal[
    "color_not_in_tokens", "font_not_from_tokens", "color_token_missing", "low_contrast",
    "outside_artboard",
]


@dataclass(frozen=True)
class TokenIssue:
    kind: IssueKind
    node_id: str | None
    message: str
    artboard_id: str | None = None


def check_against_tokens(doc: DesignDocument) -> list[TokenIssue]:
    """What in ``doc`` departs from its own tokens, or from legibility, as observed facts.

    Limits, stated: contrast is measured against the artboard background only, not against a shape
    the text may sit on; "outside" means any part of the frame leaves the artboard, ignoring rotation.
    """
    issues: list[TokenIssue] = []
    palette = {value.upper() for value in doc.tokens.colors.values()}
    for page in doc.pages:
        for board in page.artboards:
            if palette and board.background.upper() not in palette:
                issues.append(TokenIssue("color_not_in_tokens", None,
                                         f"background {board.background} is not a token colour",
                                         board.id))
            stack = list(board.node_ids)
            while stack:
                node = doc.nodes[stack.pop()]
                if isinstance(node, GroupNode):
                    stack.extend(node.child_ids)
                issues += _check_node(doc, node, board.id, board.width, board.height,
                                      board.background, palette)
    return issues


def _check_node(
    doc: DesignDocument, node: DesignNode, board_id: str, width: float, height: float,
    background: str, palette: set[str],
) -> list[TokenIssue]:
    out: list[TokenIssue] = []
    f = node.frame
    if f.x < 0 or f.y < 0 or f.x + f.width > width or f.y + f.height > height:
        out.append(TokenIssue("outside_artboard", node.id, "extends past the artboard edge", board_id))
    for label, value in (("fill", node.style.fill), ("stroke", node.style.stroke)):
        if value is not None and palette and value.upper() not in palette:
            out.append(TokenIssue("color_not_in_tokens", node.id,
                                  f"{label} {value} is not a token colour", board_id))
    if not isinstance(node, TextNode):
        return out
    font = doc.tokens.typography.get(node.font_token or "")
    if font is None:
        what = f"font token {node.font_token!r} is not defined" if node.font_token else "no font token"
        out.append(TokenIssue("font_not_from_tokens", node.id, what, board_id))
    if node.color_token is not None and node.color_token not in doc.tokens.colors:
        out.append(TokenIssue("color_token_missing", node.id,
                              f"colour token {node.color_token!r} is not defined", board_id))
    if node.text.strip() and node.visible:
        ratio = contrast_ratio(text_color(doc, node), background)
        large = font is not None and (font.size >= 24 or (font.size >= 18.66 and font.weight >= 700))
        needed = AA_LARGE if large else AA_NORMAL
        if ratio < needed:
            out.append(TokenIssue("low_contrast", node.id,
                                  f"contrast {ratio:.2f}:1 against the artboard, needs {needed}:1",
                                  board_id))
    return out
