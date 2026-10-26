"""Strict SVG validation and re-serialisation for images the workspace stores or serves.

Threat model. An SVG is an XML document that a browser can execute: ``<script>``, event handlers,
``javascript:`` links, external references that leak the reader's IP or load attacker content, and
entity tricks (XXE, billion laughs) that read local files or exhaust memory while parsing. The
provider's answer is untrusted, and so is any file another process, or an agent that was talked into
it, wrote into the workspace. So the validator never decides that a file is "probably fine": it
parses the document, keeps only an allowlist of elements and attributes, and writes a fresh copy
from that tree. The bytes stored and served are the bytes this module produced, never the input.

What is refused outright (the whole file, :class:`UnsafeSvgError`):

* any ``<!`` declaration other than a comment or CDATA section (``<!DOCTYPE``, ``<!ENTITY``, …) and
  any processing instruction after the XML declaration: no DTD means no external entity and no
  entity expansion, and the expansion budget never comes into play;
* a non-UTF-8 encoding, or NUL bytes (the pre-scan works on bytes, so it must see ASCII syntax);
* a root that is not ``svg`` in the SVG namespace, a document over 2 MB, or one with too many
  elements or too much nesting;
* a dangerous element (``script``, ``foreignObject``, ``iframe``, ``object``, ``embed``, every
  animation element, ``handler``, ``listener``), an ``on*`` attribute, a ``javascript:``/``vbscript:``
  value, or any reference that is not a same-document ``#fragment`` or a base64 raster ``data:`` URI
  (whose bytes are checked as a PNG/JPEG/GIF/WebP), including ``url(...)`` in a style;
* a ``<style>`` whose CSS has an at-rule (``@import``, ``@font-face``), an escape, or a non-fragment
  ``url(``.

What is stripped silently: unknown elements (with their subtree), attributes outside the allowlist,
namespaced attributes, and style declarations whose property is not presentational. Stripping never
turns an unsafe reference into a safe one; anything that would need a reference to survive is refused.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections import Counter
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"

MAX_SVG_BYTES = 2 * 1024 * 1024
MAX_SVG_ELEMENTS = 20_000
MAX_SVG_DEPTH = 64

#: Elements whose presence makes the whole file unacceptable, not merely unlisted.
_REJECTED_ELEMENTS = frozenset(
    {
        "script", "foreignObject", "iframe", "object", "embed", "handler", "listener", "discard",
        "animate", "animateMotion", "animateTransform", "animateColor", "set",
    }
)  # fmt: skip

#: Everything else that may appear in a kept output. ``image`` and ``style`` are the two that
#: reference something or carry CSS, and each has its own check below.
_ALLOWED_ELEMENTS = frozenset(
    {
        "svg", "g", "defs", "symbol", "use", "path", "rect", "circle", "ellipse", "line", "polyline",
        "polygon", "text", "tspan", "textPath", "title", "desc", "linearGradient", "radialGradient",
        "stop", "clipPath", "mask", "pattern", "marker", "image", "style", "filter", "feBlend",
        "feColorMatrix", "feComponentTransfer", "feComposite", "feFlood", "feFuncA", "feFuncB",
        "feFuncG", "feFuncR", "feGaussianBlur", "feMerge", "feMergeNode", "feMorphology", "feOffset",
        "feDropShadow",
    }
)  # fmt: skip

#: Elements whose character data is part of the picture (text, titles) or CSS (style).
_TEXT_BEARING = frozenset({"text", "tspan", "textPath", "title", "desc", "style"})

_PRESENTATION_ATTRIBUTES = frozenset(
    {
        "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity",
        "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray",
        "stroke-dashoffset", "opacity", "color", "font-family", "font-size", "font-style",
        "font-weight", "font-variant", "letter-spacing", "word-spacing", "text-anchor",
        "text-decoration", "dominant-baseline", "alignment-baseline", "baseline-shift", "visibility",
        "display", "clip-rule", "clip-path", "mask", "filter", "stop-color", "stop-opacity",
        "flood-color", "flood-opacity", "lighting-color", "color-interpolation",
        "color-interpolation-filters", "mix-blend-mode", "isolation", "writing-mode", "direction",
        "overflow", "shape-rendering", "text-rendering", "image-rendering", "paint-order",
    }
)  # fmt: skip

_GEOMETRY_ATTRIBUTES = frozenset(
    {
        "x", "y", "width", "height", "rx", "ry", "cx", "cy", "r", "x1", "x2", "y1", "y2", "d",
        "points", "dx", "dy", "rotate", "textLength", "lengthAdjust", "fx", "fy", "fr", "pathLength",
        "transform", "viewBox", "preserveAspectRatio", "version", "offset", "gradientUnits",
        "gradientTransform", "spreadMethod", "patternUnits", "patternContentUnits",
        "patternTransform", "clipPathUnits", "maskUnits", "maskContentUnits", "markerWidth",
        "markerHeight", "markerUnits", "refX", "refY", "orient", "stdDeviation", "in", "in2",
        "result", "operator", "k1", "k2", "k3", "k4", "values", "type", "mode", "radius",
        "baseFrequency", "numOctaves", "tableValues", "slope", "intercept", "amplitude", "exponent",
        "filterUnits", "primitiveUnits", "edgeMode", "id", "class", "role", "aria-label",
        "aria-hidden", "style", "href",
    }
)  # fmt: skip

_STYLE_PROPERTIES = _PRESENTATION_ATTRIBUTES - {"transform", "clip-path", "mask", "filter"}

#: A same-document reference: ``#id`` with a plain identifier, nothing else.
_FRAGMENT = re.compile(r"#[A-Za-z0-9_][A-Za-z0-9_.:\-]*")
_DATA_RASTER = re.compile(r"data:image/(png|jpeg|gif|webp);base64,([A-Za-z0-9+/=\s]+)", re.IGNORECASE)
_URL_CALL = re.compile(r"url\s*\(([^)]*)\)", re.IGNORECASE)
_URL_OPEN = re.compile(r"url\s*\(", re.IGNORECASE)
_SCHEME_IN_VALUE = re.compile(r"javascript:|vbscript:|data:text|://", re.IGNORECASE)
_CSS_HARD = re.compile(r"@|\\|expression\s*\(|javascript|vbscript|behavior|-moz-binding|image(-set)?\s*\(|src\s*\(|element\s*\(|cross-fade", re.IGNORECASE)
_XML_DECLARATION = re.compile(rb"\A\s*<\?xml\s[^?]*\?>")
_ENCODING = re.compile(rb"""encoding\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_NON_DECLARATION_MARKUP = re.compile(rb"<!(?!--|\[CDATA\[)")
_PROCESSING_INSTRUCTION = re.compile(rb"<\?")
_SVG_SNIFF = re.compile(rb"\A(?:\xef\xbb\xbf)?\s*(?:<\?xml\s[^?]*\?>\s*|<!--.*?-->\s*)*<svg[\s>/]", re.DOTALL)  # fmt: skip
_NUMERIC_VIEWBOX = re.compile(r"[-+0-9.,\s]+(?:px|pt|em|%)?", re.IGNORECASE)


class UnsafeSvgError(ValueError):
    """The SVG is refused. The message names the rule, never the content that broke it."""


@dataclass(frozen=True)
class SvgReport:
    """What the validator kept, for a summary a model can read. Counts only, no copied content."""

    element_counts: dict[str, int] = field(default_factory=dict)
    view_box: str | None = None
    width: str | None = None
    height: str | None = None
    dropped_elements: int = 0
    dropped_attributes: int = 0


def is_svg_document(data: bytes) -> bool:
    """True when the bytes *look like* an SVG document: an ``<svg`` root after the optional prolog.

    Sniffing decides which validator a file goes to, and never whether it is safe. A file named
    ``.png`` whose bytes start with ``<svg`` is an SVG here, and a ``.svg`` holding a PNG is not.
    """
    return _SVG_SNIFF.match(data[:4096]) is not None


def sanitise_svg(data: bytes) -> tuple[bytes, SvgReport]:
    """Validate ``data`` and return ``(clean_bytes, report)``, where ``clean_bytes`` is rebuilt from
    the allowlisted tree. Raises :class:`UnsafeSvgError` for anything that cannot be made safe.
    """
    if len(data) > MAX_SVG_BYTES:
        raise UnsafeSvgError("SVG exceeds 2 MB")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in data:
        raise UnsafeSvgError("SVG must be UTF-8 without NUL bytes")
    body = _check_prolog(data)
    if _NON_DECLARATION_MARKUP.search(body):
        raise UnsafeSvgError("DOCTYPE, ENTITY and other markup declarations are refused")
    if _PROCESSING_INSTRUCTION.search(body):
        raise UnsafeSvgError("processing instructions are refused")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise UnsafeSvgError("SVG is not well-formed XML") from exc
    if _ns_and_local(root.tag) != (SVG_NS, "svg"):
        raise UnsafeSvgError("root element must be svg in the SVG namespace")
    elements = list(root.iter())
    if len(elements) > MAX_SVG_ELEMENTS:
        raise UnsafeSvgError("SVG has too many elements")
    # Checked over the whole tree, not during the walk: an unknown wrapper (say `<a>`) is dropped with
    # its subtree, and a refused element hidden inside it must still refuse the file, not vanish.
    for elem in elements:
        if _ns_and_local(elem.tag)[1] in _REJECTED_ELEMENTS:
            raise UnsafeSvgError(f"element {_ns_and_local(elem.tag)[1]!r} is refused")
    cleaner = _Cleaner()
    root_attributes = cleaner.attributes("svg", root)
    inner = cleaner.children(root, depth=1)
    open_tag = f'<svg xmlns="{SVG_NS}"{root_attributes}'
    clean = f'<?xml version="1.0" encoding="UTF-8"?>\n{open_tag}>{inner}</svg>'.encode()
    if len(clean) > MAX_SVG_BYTES:  # cannot happen (the output is a subset of the input); kept as a bound
        raise UnsafeSvgError("SVG exceeds 2 MB")
    report = SvgReport(
        element_counts=dict(cleaner.counts),
        view_box=_numeric_or_none(root.get("viewBox")),
        width=_numeric_or_none(root.get("width")),
        height=_numeric_or_none(root.get("height")),
        dropped_elements=cleaner.dropped_elements,
        dropped_attributes=cleaner.dropped_attributes,
    )
    return clean, report


def _check_prolog(data: bytes) -> bytes:
    """The bytes after the XML declaration, with the declaration's encoding checked. Byte-level so the
    markup scans below see exactly what the parser will see."""
    stripped = data[3:] if data.startswith(b"\xef\xbb\xbf") else data
    match = _XML_DECLARATION.match(stripped)
    if match is None:
        return stripped
    encoding = _ENCODING.search(match.group(0))
    if encoding and encoding.group(1).lower() not in (b"utf-8", b"us-ascii"):
        raise UnsafeSvgError("SVG must declare UTF-8 (or no encoding)")
    return stripped[match.end():]


def _numeric_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text if len(text) <= 64 and _NUMERIC_VIEWBOX.fullmatch(text) else "present (not numeric)"


def _ns_and_local(tag: object) -> tuple[str | None, str]:
    if not isinstance(tag, str):  # comments and processing instructions are not elements here
        return None, ""
    if tag.startswith("{"):
        namespace, _, local = tag[1:].partition("}")
        return namespace, local
    return None, tag


class _Cleaner:
    """Builds the output text from the parsed tree. One instance per document."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.dropped_elements = 0
        self.dropped_attributes = 0

    def children(self, elem: ET.Element, depth: int) -> str:
        """The cleaned content of ``elem``: its text (for text-bearing elements), its kept children,
        and the tails of those children (text that sits between them inside a text element)."""
        local = _ns_and_local(elem.tag)[1]
        keeps_text = local in _TEXT_BEARING
        parts: list[str] = []
        if keeps_text:
            text = elem.text or ""
            parts.append(_css(text) if local == "style" else _escape(text))
        for child in elem:
            parts.append(self.element(child, depth))
            if keeps_text and child.tail:
                parts.append(_escape(child.tail))
        return "".join(parts)

    def element(self, elem: ET.Element, depth: int) -> str:
        """The cleaned markup for one element, or ``""`` when it is dropped (its subtree with it)."""
        namespace, local = _ns_and_local(elem.tag)
        if not local:
            return ""
        if local in _REJECTED_ELEMENTS:
            raise UnsafeSvgError(f"element {local!r} is refused")
        if namespace != SVG_NS or local not in _ALLOWED_ELEMENTS:
            self.dropped_elements += 1
            return ""
        if depth >= MAX_SVG_DEPTH:
            raise UnsafeSvgError("SVG is nested too deeply")
        self.counts[local] += 1
        attributes = self.attributes(local, elem)
        inner = self.children(elem, depth + 1)
        if not inner:
            return f"<{local}{attributes}/>"
        return f"<{local}{attributes}>{inner}</{local}>"

    def attributes(self, local: str, elem: ET.Element) -> str:
        parts: list[str] = []
        seen: set[str] = set()
        for name, value in elem.attrib.items():
            namespace, attr = _ns_and_local(name)
            if attr.lower().startswith("on"):
                raise UnsafeSvgError("event-handler attributes are refused")
            if attr == "href" and namespace in (None, XLINK_NS):
                if "href" in seen:  # href and xlink:href on one element: keep the first, check both
                    self.dropped_attributes += 1
                    continue
                parts.append(f' href="{_escape_attr(_check_href(local, value))}"')
                seen.add("href")
                continue
            if namespace == XML_NS and attr == "space" and value in ("preserve", "default"):
                parts.append(f' xml:space="{value}"')
                continue
            if namespace is not None:
                self.dropped_attributes += 1
                continue
            if attr == "style":
                cleaned = _style_attribute(value)
                if cleaned:
                    parts.append(f' style="{_escape_attr(cleaned)}"')
                continue
            if attr in _GEOMETRY_ATTRIBUTES or attr in _PRESENTATION_ATTRIBUTES:
                _check_value(value)
                parts.append(f' {attr}="{_escape_attr(value)}"')
                continue
            self.dropped_attributes += 1
        return "".join(parts)


def _check_href(local: str, value: str) -> str:
    """A same-document fragment, or (on ``<image>`` only) a base64 raster data URI with its bytes
    validated. Anything else is an outside reference, and outside references are refused."""
    target = value.strip()
    if _FRAGMENT.fullmatch(target):
        return target
    if local == "image":
        match = _DATA_RASTER.fullmatch(target)
        if match is not None:
            return _validated_data_uri(match.group(1), match.group(2))
    raise UnsafeSvgError("external or script references are refused")


def _validated_data_uri(kind: str, encoded: str) -> str:
    # Local import: image_safety owns the raster magic bytes and imports this module lazily.
    from chimera.tools.image_safety import image_extension

    try:
        payload = base64.b64decode(re.sub(r"\s+", "", encoded), validate=True)
    except binascii.Error as exc:
        raise UnsafeSvgError("embedded image is not valid base64") from exc
    try:
        image_extension(payload)
    except ValueError as exc:
        raise UnsafeSvgError("embedded image is not a PNG, JPEG, GIF or WebP") from exc
    return f"data:image/{kind.lower()};base64,{base64.b64encode(payload).decode('ascii')}"


def _check_value(value: str) -> None:
    """A presentation or geometry value must not name a script, a foreign URL, or a non-fragment url()."""
    squeezed = re.sub(r"\s+", "", value)
    if _SCHEME_IN_VALUE.search(squeezed):
        raise UnsafeSvgError("script or external URLs are refused in attribute values")
    if "expression(" in squeezed.lower() or "-moz-binding" in squeezed.lower():
        raise UnsafeSvgError("script expressions are refused")
    _check_urls(value)


def _check_urls(text: str) -> None:
    """Every ``url(...)`` must be ``url(#fragment)``, and every ``url(`` must close."""
    if len(_URL_OPEN.findall(text)) != len(_URL_CALL.findall(text)):
        raise UnsafeSvgError("unterminated url() is refused")
    for match in _URL_CALL.finditer(text):
        target = match.group(1).strip().strip("'\"").strip()
        if not _FRAGMENT.fullmatch(target):
            raise UnsafeSvgError("url() may only reference the same document")


def _style_attribute(value: str) -> str:
    kept: list[str] = []
    for declaration in value.split(";"):
        prop, sep, val = declaration.partition(":")
        if not sep:
            continue
        name = prop.strip().lower()
        if name not in _STYLE_PROPERTIES:
            continue
        val = val.strip()
        _check_css(val)
        kept.append(f"{name}:{val}")
    return ";".join(kept)


def _check_css(text: str) -> None:
    if _CSS_HARD.search(text):
        raise UnsafeSvgError("CSS at-rules, escapes and script functions are refused")
    if _SCHEME_IN_VALUE.search(re.sub(r"\s+", "", text)):
        raise UnsafeSvgError("external URLs are refused in CSS")
    _check_urls(text)


def _css(text: str) -> str:
    """The text of a ``<style>`` element, checked, and escaped as character data."""
    _check_css(text)
    return _escape(text)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(text: str) -> str:
    return _escape(text).replace('"', "&quot;").replace("\n", "&#10;").replace("\r", "&#13;").replace("\t", "&#9;")
