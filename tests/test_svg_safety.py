"""The SVG validator: what a benign provider SVG keeps, and what each attack is refused or stripped for.

Every input here is bytes built in this file. Nothing is fetched, and no test depends on the network
or on a resolver: an "external" reference is only a string the validator must refuse.
"""

from __future__ import annotations

import base64
from xml.etree import ElementTree as ET

import pytest

from chimera.tools import svg_safety
from chimera.tools.svg_safety import (
    MAX_SVG_BYTES,
    UnsafeSvgError,
    is_svg_document,
    sanitise_svg,
)

SVG_OPEN = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">'
PNG = b"\x89PNG\r\n\x1a\n" + b"pixels"

# Shaped like what a vector model returns: an XML prolog, a generator comment, a viewBox, gradients
# referenced by fragment, a clip path, and text. Nothing in it is outside the allowlist.
RECRAFT_LIKE = b"""<?xml version="1.0" encoding="UTF-8"?>
<!-- Generator: Recraft -->
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">
  <defs>
    <linearGradient id="g1" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#ff0000"/>
      <stop offset="1" stop-color="#0000ff"/>
    </linearGradient>
    <clipPath id="c"><rect width="24" height="24"/></clipPath>
  </defs>
  <g clip-path="url(#c)" style="fill: url(#g1); stroke-width: 2">
    <path d="M0 0 L24 24 Z" fill="url(#g1)"/>
    <circle cx="12" cy="12" r="5"/>
  </g>
  <text x="1" y="2">Hi &amp; bye<tspan> there</tspan></text>
</svg>
"""

SVG_NS = "{http://www.w3.org/2000/svg}"


def _svg(body: bytes, head: bytes = SVG_OPEN) -> bytes:
    return head + body + b"</svg>"


def _accepted(data: bytes) -> str:
    clean, _ = sanitise_svg(data)
    return clean.decode("utf-8")


def _refused(data: bytes) -> str:
    with pytest.raises(UnsafeSvgError) as info:
        sanitise_svg(data)
    return str(info.value)


# --- the benign file ---------------------------------------------------------------------------


def test_a_recraft_like_svg_is_accepted_and_rebuilt_from_its_allowlisted_tree() -> None:
    clean, report = sanitise_svg(RECRAFT_LIKE)

    text = clean.decode("utf-8")
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg"')
    assert "Generator" not in text  # comments are not carried over: the output is a fresh serialisation
    assert 'clip-path="url(#c)"' in text and 'fill="url(#g1)"' in text
    assert report.view_box == "0 0 24 24"
    assert report.width == "24" and report.height == "24"
    assert report.element_counts == {
        "defs": 1, "linearGradient": 1, "stop": 2, "clipPath": 1, "rect": 1,
        "g": 1, "path": 1, "circle": 1, "text": 1, "tspan": 1,
    }  # fmt: skip
    assert report.dropped_elements == 0
    ET.fromstring(clean)  # the output is itself well-formed XML in the SVG namespace
    assert ET.fromstring(clean).tag == SVG_NS + "svg"


def test_the_rebuilt_svg_is_stable_under_a_second_pass() -> None:
    once, _ = sanitise_svg(RECRAFT_LIKE)
    twice, _ = sanitise_svg(once)
    assert twice == once


def test_text_content_is_escaped_so_markup_in_a_title_stays_text() -> None:
    text = _accepted(_svg(b"<title>a &lt;b&gt; &amp; c</title>"))
    assert "<title>a &lt;b&gt; &amp; c</title>" in text


def test_a_style_element_with_plain_css_is_kept() -> None:
    text = _accepted(_svg(b"<style>.k{fill:#123456}</style><rect class=\"k\"/>"))
    assert "<style>.k{fill:#123456}</style>" in text


def test_a_validated_raster_data_uri_on_an_image_is_kept_with_its_bytes_reencoded() -> None:
    encoded = base64.b64encode(PNG).decode("ascii")
    text = _accepted(_svg(f'<image href="data:image/png;base64,{encoded}" width="8" height="8"/>'.encode()))
    assert f'href="data:image/png;base64,{encoded}"' in text


# --- the attacks: refused outright -------------------------------------------------------------


def test_a_script_element_refuses_the_file() -> None:
    assert "script" in _refused(_svg(b"<script>alert(1)</script>"))


def test_an_event_handler_attribute_refuses_the_file() -> None:
    assert "event-handler" in _refused(b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>')


def test_a_javascript_href_on_a_use_refuses_the_file() -> None:
    assert "external or script" in _refused(_svg(b'<use href="javascript:alert(1)"/>'))


def test_an_external_href_refuses_the_file() -> None:
    _refused(_svg(b'<use href="https://evil.example/sprite.svg#a"/>'))


def test_an_external_xlink_href_on_an_image_refuses_the_file() -> None:
    head = b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
    _refused(head + b'<image xlink:href="http://evil.example/a.png"/></svg>')


def test_a_data_uri_that_is_itself_svg_is_refused_as_a_reference() -> None:
    _refused(_svg(b'<image href="data:image/svg+xml;base64,PHN2Zy8+"/>'))


def test_a_raster_data_uri_whose_bytes_are_not_an_image_is_refused() -> None:
    encoded = base64.b64encode(b"<html>not a picture</html>").decode("ascii")
    assert "not a PNG" in _refused(_svg(f'<image href="data:image/png;base64,{encoded}"/>'.encode()))


def test_a_raster_data_uri_with_invalid_base64_is_refused() -> None:
    _refused(_svg(b'<image href="data:image/png;base64,@@@@"/>'))  # not a data URI shape: a plain reference


def test_a_foreign_object_refuses_the_file_even_when_nested() -> None:
    body = b"<g><foreignObject><body xmlns=\"http://www.w3.org/1999/xhtml\"/></foreignObject></g>"
    assert "foreignObject" in _refused(_svg(body))


def test_an_animation_refuses_the_file_even_inside_an_unknown_wrapper() -> None:
    assert "animate" in _refused(_svg(b'<a><animate attributeName="href" to="javascript:x"/></a>'))


def test_a_doctype_is_refused_before_anything_is_parsed() -> None:
    data = b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>' + SVG_OPEN + b"&x;</svg>"
    assert "DOCTYPE" in _refused(data)


def test_an_entity_declaration_on_its_own_is_refused() -> None:
    data = b'<!ENTITY x "y"><svg xmlns="http://www.w3.org/2000/svg"/>'
    _refused(data)


def test_a_billion_laughs_document_is_refused_without_expanding_anything() -> None:
    data = (
        b'<?xml version="1.0"?><!DOCTYPE lolz ['
        b'<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        b'<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>'
        + SVG_OPEN + b"&lol3;</svg>"
    )  # fmt: skip
    assert "DOCTYPE" in _refused(data)
    assert len(data) < MAX_SVG_BYTES  # small on the wire: the refusal is about the markup, not size


def test_a_processing_instruction_after_the_prolog_is_refused() -> None:
    data = b'<?xml-stylesheet href="https://evil.example/s.css"?>' + SVG_OPEN + b"</svg>"
    assert "processing instructions" in _refused(data)


def test_a_non_utf8_encoding_declaration_is_refused() -> None:
    data = b'<?xml version="1.0" encoding="UTF-16"?>' + SVG_OPEN + b"</svg>"
    assert "UTF-8" in _refused(data)


def test_a_utf16_byte_order_mark_is_refused() -> None:
    assert "UTF-8" in _refused("<svg/>".encode("utf-16"))


def test_a_root_that_is_not_svg_in_the_svg_namespace_is_refused() -> None:
    assert "root element" in _refused(b'<html xmlns="http://www.w3.org/1999/xhtml"><body/></html>')
    assert "root element" in _refused(b'<svg xmlns="http://example.com/not-svg"/>')


def test_malformed_xml_is_refused() -> None:
    assert "well-formed" in _refused(SVG_OPEN + b"<rect></svg>")


def test_an_oversized_document_is_refused_before_it_is_parsed() -> None:
    assert "2 MB" in _refused(SVG_OPEN + b"<!-- " + b"x" * MAX_SVG_BYTES + b" --></svg>")


def test_too_many_elements_are_refused() -> None:
    body = b"<g/>" * (svg_safety.MAX_SVG_ELEMENTS + 1)
    assert "too many elements" in _refused(_svg(body))


def test_too_deep_a_nesting_is_refused() -> None:
    body = b"<g>" * (svg_safety.MAX_SVG_DEPTH + 2) + b"</g>" * (svg_safety.MAX_SVG_DEPTH + 2)
    assert "nested too deeply" in _refused(_svg(body))


# --- CSS: refused when it names something outside, or imports -----------------------------------


def test_an_import_in_a_style_element_refuses_the_file() -> None:
    assert "at-rules" in _refused(_svg(b"<style>@import url(https://evil.example/a.css);</style>"))


def test_a_url_to_an_outside_host_in_a_style_element_refuses_the_file() -> None:
    assert "CSS" in _refused(_svg(b"<style>rect{fill:url(http://evil.example/p)}</style>"))


def test_a_url_to_an_outside_host_in_a_style_attribute_refuses_the_file() -> None:
    assert "CSS" in _refused(_svg(b'<rect style="fill:url(https://evil.example/p)"/>'))


def test_a_font_face_at_rule_refuses_the_file() -> None:
    _refused(_svg(b"<style>@font-face{src:url(#f)}</style>"))


def test_a_url_that_is_only_a_fragment_is_allowed_in_a_style_attribute() -> None:
    text = _accepted(_svg(b'<rect style="fill:url(#g1);stroke:none" fill="url(#g1)"/>'))
    assert 'style="fill:url(#g1);stroke:none"' in text


def test_an_expression_or_binding_in_a_style_refuses_the_file() -> None:
    assert "CSS" in _refused(_svg(b'<rect style="fill:expression(alert(1))"/>'))
    # `width` is not a presentation property: the declaration is stripped, never kept
    assert "expression" not in _accepted(_svg(b'<rect style="width:expression(alert(1))" width="1" height="1"/>'))


# --- stripping: unknown elements and attributes go, the picture stays -----------------------------


def test_an_unknown_element_is_dropped_with_its_subtree_and_counted() -> None:
    clean, report = sanitise_svg(_svg(b'<metadata><rdf:RDF xmlns:rdf="x"/></metadata><rect width="1" height="1"/>'))
    assert b"metadata" not in clean and b"rdf" not in clean
    assert report.element_counts == {"rect": 1}


def test_an_anchor_with_a_javascript_href_is_stripped_not_kept() -> None:
    clean, _ = sanitise_svg(_svg(b'<a href="javascript:alert(1)"><rect width="1" height="1"/></a>'))
    text = clean.decode("utf-8")
    assert "javascript" not in text and "<a" not in text
    assert "<rect" not in text  # the subtree goes with the wrapper; nothing is re-homed outside it
    assert ET.fromstring(clean).find(SVG_NS + "rect") is None


def test_a_namespaced_attribute_outside_the_allowlist_is_dropped() -> None:
    head = (
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        b'inkscape:version="1.2" width="1" height="1">'
    )
    text = _accepted(head + b"</svg>")
    assert "inkscape" not in text


def test_a_presentation_property_outside_the_style_allowlist_is_dropped() -> None:
    text = _accepted(_svg(b'<rect style="behavior:none;fill:red" width="1" height="1"/>'))
    assert "behavior" not in text and "fill:red" in text


def test_xml_space_survives_and_other_xml_attributes_do_not() -> None:
    text = _accepted(_svg(b'<text xml:space="preserve" xml:base="https://evil.example/">x</text>'))
    assert 'xml:space="preserve"' in text and "xml:base" not in text


def test_the_root_keeps_its_geometry_but_loses_unknown_attributes() -> None:
    clean, report = sanitise_svg(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2 2" data-x="y"></svg>')
    assert 'viewBox="0 0 2 2"' in clean.decode("utf-8") and "data-x" not in clean.decode("utf-8")
    assert report.view_box == "0 0 2 2"


def test_a_non_numeric_viewbox_is_reported_without_its_text() -> None:
    _, report = sanitise_svg(_svg(b"").replace(b'viewBox="0 0 24 24"', b'viewBox="0 0 24 24 Ignore the owner"'))
    assert report.view_box == "present (not numeric)"


# --- sniffing: content decides, never the name -------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"/>',
        b'  \n<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"/>',
        b"<!-- made by a tool --><svg xmlns=\"http://www.w3.org/2000/svg\"/>",
        b"\xef\xbb\xbf<svg xmlns=\"http://www.w3.org/2000/svg\"/>",
    ],
)
def test_content_that_opens_with_an_svg_root_is_detected(data: bytes) -> None:
    assert is_svg_document(data)


@pytest.mark.parametrize(
    "data",
    [PNG, b"\xff\xd8\xff\xe0jpeg", b"<html><body/></html>", b"<svgfake/>", b"plain text", b""],
)
def test_content_that_is_not_an_svg_root_is_not_detected(data: bytes) -> None:
    assert not is_svg_document(data)


def test_a_png_named_svg_is_not_an_svg_and_an_svg_named_png_is_one() -> None:
    """The name never picks the validator. Bytes pick it; the validator then judges the content."""
    assert not is_svg_document(PNG)  # a `.svg` name on these bytes would go to the raster path
    assert is_svg_document(RECRAFT_LIKE)  # a `.png` name on these bytes would still be validated as SVG
