"""The HTML export carries no script and no remote URL; the token checks and asset checks say why."""

from __future__ import annotations

import re
import struct
import zlib

import pytest

from chimera.design.assets import AssetRejected, validate_asset
from chimera.design.export_html import check_against_tokens, render_html
from chimera.design.model import DesignDocument
from chimera.design.ops import apply_operations
from chimera.design.tokens import AA_NORMAL, contrast_ratio, default_tokens
from tests.design_fixtures import frame, sample_dict, sample_doc, text


def _png(width: int = 2, height: int = 3, *, pixels: bool = True) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    # Header-only when asked: the validator reads IHDR, and an 8000x8000 body is 192 MB.
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height)) if pixels else b""
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def _jpeg(width: int = 640, height: int = 480) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def _webp(width: int = 300, height: int = 200) -> bytes:
    body = b"\x2f" + ((width - 1) | ((height - 1) << 14)).to_bytes(4, "little") + b"\x00" * 8
    chunk = b"VP8L" + struct.pack("<I", len(body)) + body
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


_HOSTILE = '</p><script>alert("x")</script><img src=x onerror=alert(1)> javascript:alert(1)'


def _hostile_doc() -> DesignDocument:
    raw = sample_dict()
    raw["title"] = "<script>document.cookie</script>"
    raw["nodes"]["t1"]["text"] = _HOSTILE
    raw["nodes"]["t1"]["name"] = '"><script>'
    raw["nodes"]["im"]["alt"] = '" onload="alert(1)'
    raw["pages"][0]["name"] = "<b>page</b>"
    return DesignDocument.model_validate(raw)


def test_the_export_escapes_every_string_and_has_no_script_or_remote_url() -> None:
    page = render_html(_hostile_doc())
    assert "<script" not in page.lower()
    assert "javascript:" not in page.replace("javascript:alert(1)</p>", "")
    assert not re.search(r"https?://", page)
    assert "<img" not in page and 'onload="' not in page  # the injected tag is text, not a tag
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in page
    assert "Content-Security-Policy" in page and "default-src 'none'" in page
    assert page.count("<p ") == 3  # t1, t2 and t3, nothing injected


def test_the_only_text_mention_of_javascript_is_escaped_text_inside_a_paragraph() -> None:
    page = render_html(_hostile_doc())
    for match in re.finditer("javascript:", page):
        before = page[: match.start()]
        assert before.rfind("<p ") > before.rfind("</p>")  # inside a text element's content


def test_images_are_data_uris_from_validated_bytes_or_a_placeholder() -> None:
    doc = sample_doc()
    with_image = render_html(doc, assets={"asset_1": _png()})
    assert 'src="data:image/png;base64,' in with_image
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    for bytes_ in (None, svg, b"not an image"):
        page = render_html(doc, assets={} if bytes_ is None else {"asset_1": bytes_})
        assert "<img" not in page and 'class="el placeholder"' in page
        assert "<script" not in page and "xmlns" not in page  # the SVG bytes were not embedded


def test_paths_and_lines_are_inline_svg_of_numbers_only() -> None:
    doc, _ = apply_operations(sample_doc(), [
        {"type": "node.add", "pageId": "p1", "artboardId": "a1", "node": {
            "id": "ln", "type": "shape", "frame": frame(0, 0, 50, 50), "shape": "line",
            "points": [1, 0, 0, 1], "style": {"stroke": "#B91C1C", "strokeWidth": 3}}},
    ])
    page = render_html(doc)
    assert '<polyline points="0,0 0.5,1 1,0.25"' in page  # the path, normalised to the frame
    assert '<polyline points="1,0 0,1" fill="none" stroke="#B91C1C" stroke-width="3"' in page
    assert "href" not in page and "xmlns" not in page


def test_hidden_elements_and_their_groups_are_not_drawn() -> None:
    doc, _ = apply_operations(sample_doc(), [
        {"type": "node.update", "nodeId": "g1", "patch": {"visible": False}},
    ])
    page = render_html(doc)
    assert "Inside" not in page and "Headline" in page


def test_token_checks_report_colour_font_contrast_and_overflow() -> None:
    doc, _ = apply_operations(sample_doc(), [
        {"type": "node.add", "pageId": "p1", "artboardId": "a1",
         "node": text("pale", colorToken=None, style={"fill": "#EEEEEE"})},
        {"type": "node.add", "pageId": "p1", "artboardId": "a1", "node": text("nofont", fontToken="ghost")},
        {"type": "node.add", "pageId": "p1", "artboardId": "a1",
         "node": text("edge", frame=frame(750, 10, 100, 40))},
        {"type": "artboard.update", "pageId": "p2", "artboardId": "a2", "patch": {"background": "#123456"}},
    ])
    found = {(i.kind, i.node_id) for i in check_against_tokens(doc)}
    assert ("color_not_in_tokens", "pale") in found
    assert ("low_contrast", "pale") in found
    assert ("font_not_from_tokens", "nofont") in found
    assert ("outside_artboard", "edge") in found
    assert ("color_not_in_tokens", None) in found  # the artboard background
    assert ("low_contrast", "t3") in found  # dark text on the new dark background
    assert not {k for k, n in found if n == "t1"}


def test_the_default_tokens_are_accessible() -> None:
    tokens = default_tokens()
    for name, roles in tokens.components.items():
        if "text" in roles and "fill" in roles:
            ratio = contrast_ratio(tokens.colors[roles["text"]], tokens.colors[roles["fill"]])
            assert ratio >= AA_NORMAL, name
    for color in ("text", "text-muted", "primary", "accent", "danger", "success"):
        assert contrast_ratio(tokens.colors[color], tokens.colors["background"]) >= AA_NORMAL
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)


@pytest.mark.parametrize(("data", "mime", "size"), [
    (_png(2, 3), "image/png", (2, 3)),
    (_jpeg(640, 480), "image/jpeg", (640, 480)),
    (_webp(300, 200), "image/webp", (300, 200)),
], ids=["png", "jpeg", "webp"])
def test_raster_images_are_identified_by_their_bytes(
    data: bytes, mime: str, size: tuple[int, int]
) -> None:
    info = validate_asset(data)
    assert (info.mime, (info.width, info.height)) == (mime, size)


@pytest.mark.parametrize(("data", "declared", "reason"), [
    (b'<?xml version="1.0"?><svg><script/></svg>', None, "SVG"),
    (b"  <svg onload='x'/>", "image/png", "SVG"),
    (_png(), "image/svg+xml", "SVG"),
    (b"<html><script>x</script></html>", "image/png", "not a PNG"),
    (b"", None, "empty"),
    (_png(), "image/jpeg", "declared"),
    (_png(9000, 10, pixels=False), None, "outside"),
    (_png(8000, 8000, pixels=False), None, "pixels"),
    (b"\xff\xd8\xff\xe0\x00\x10JFIF", None, "frame header"),
], ids=["xml-svg", "bare-svg", "declared-svg", "html", "empty", "mime-mismatch", "too-wide",
        "too-many-pixels", "truncated-jpeg"])
def test_other_files_are_refused_with_a_reason(
    data: bytes, declared: str | None, reason: str
) -> None:
    with pytest.raises(AssetRejected, match=reason):
        validate_asset(data, declared_mime=declared)


def test_the_size_cap_is_checked_before_parsing() -> None:
    with pytest.raises(AssetRejected, match="limit"):
        validate_asset(_png(), max_bytes=10)
