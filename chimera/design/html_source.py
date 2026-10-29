"""Allowlisted, inert HTML/CSS source for design artboards.

Every rule here refuses by default: a construct is accepted only when a rule names it. The parser
runs with ``convert_charrefs=False`` so text reaches the handlers raw, a stray ``<`` is visible, and
the attribute values it hands over are already decoded once, which is what a browser sees too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from html import unescape
from html.parser import HTMLParser
from urllib.parse import unquote

from chimera.design.model import DesignError, DesignTokens

MAX_ARTBOARD_HTML = 512 * 1024
_ECHO_MAX = 40
_ID = r"[A-Za-z][A-Za-z0-9_-]{0,63}"


def _css_string(value: str) -> str:
    """A CSS string body: backslash and double quote escaped by a backslash, line breaks as hex escapes.

    Same rules as the desktop's ``cssString`` (the H3 canon), so both sides write the same bytes.
    """
    out: list[str] = []
    for char in value:
        if char in ("\\", '"'):
            out.append("\\" + char)
        elif char == "\n":
            out.append("\\a ")
        elif char == "\r":
            out.append("\\d ")
        elif char == "\f":
            out.append("\\c ")
        else:
            out.append(char)
    return "".join(out)


def _js_number(value: float) -> str:
    """A number the way JavaScript's ``String(n)`` writes it: ``16`` not ``16.0``, ``0.00001`` not ``1e-05``."""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    text = repr(number)
    if "e" in text:
        return format(Decimal(text), "f")
    return text


def tokens_css(tokens: DesignTokens) -> str:
    """Render the design tokens as deterministic CSS custom properties, in the desktop's exact bytes."""
    declarations: list[str] = [
        f"--ds-color-{name}: {colour}" for name, colour in sorted(tokens.colors.items())
    ]
    for name, token in sorted(tokens.typography.items()):
        declarations.append(f'--ds-font-{name}-family: "{_css_string(token.family)}"')
        declarations.append(f"--ds-font-{name}-size: {_js_number(token.size)}px")
        declarations.append(f"--ds-font-{name}-weight: {_js_number(token.weight)}")
        if token.line_height is not None:
            declarations.append(f"--ds-font-{name}-line-height: {_js_number(token.line_height)}")
    for name, space in sorted(tokens.spacing.items()):
        declarations.append(f"--ds-space-{name}: {_js_number(space)}px")
    for name, radius in sorted(tokens.radii.items()):
        declarations.append(f"--ds-radius-{name}: {_js_number(radius)}px")
    return ":root{" + ";".join(declarations) + "}"


class DesignHtmlError(DesignError):
    """A source location and concise reason for refusing HTML/CSS.

    ``echo`` is the offending tag, attribute or value as the author wrote it. It is cut to
    ``_ECHO_MAX`` characters here, the one place a message is composed, so a 3000-character
    name cannot make the refusal longer than the rest of the message.
    """

    def __init__(self, line: int, reason: str, echo: str = "") -> None:
        self.line = line
        self.reason = reason
        shown = echo if len(echo) <= _ECHO_MAX else echo[:_ECHO_MAX] + "..."
        super().__init__(f"line {line}: {shown + ' ' if echo else ''}{reason}")


_HTML_TAGS = frozenset(["html", "head", "body", "title", "meta", "style", "link", "div", "span", "section", "header", "footer", "main", "nav", "article", "aside", "h1", "h2", "h3", "h4", "h5", "h6", "p", "a", "img", "button", "input", "label", "textarea", "select", "option", "form", "fieldset", "legend", "ul", "ol", "li", "dl", "dt", "dd", "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "figure", "figcaption", "strong", "em", "b", "i", "u", "s", "small", "sup", "sub", "mark", "br", "hr", "blockquote", "code", "pre", "time", "abbr", "picture", "svg"])
# Lowercase: the parser lowercases every tag name, so a camel-case entry could never match.
_SVG_TAGS = frozenset(["svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan", "defs", "lineargradient", "radialgradient", "stop", "clippath", "desc"])
# SVG-only tags are refused outside <svg>; inside it, `style` and `title` are refused (not listed).
_SVG_ONLY_TAGS = _SVG_TAGS - {"svg"}
_ALLOWED_TAGS = _HTML_TAGS | _SVG_TAGS
_FORBIDDEN_TAGS = frozenset(["iframe", "object", "embed", "base", "frame", "frameset", "applet", "foreignobject", "use", "image", "animate", "animatemotion", "animatetransform", "set", "template", "slot", "portal", "noscript"])
_FORBIDDEN_ATTRS = frozenset(["srcdoc", "formaction", "action", "xlink:href", "ping", "http-equiv", "srcset", "poster", "background"])
_PAINT_ATTRS = frozenset(["fill", "stroke", "filter", "clip-path", "mask", "marker-start", "marker-mid", "marker-end"])
_SAFE_ID = re.compile(_ID)
_ASSET_REF = re.compile(rf"asset:{_ID}")
_FRAGMENT_REF = re.compile(rf"#{_ID}")
_URL_CALL = re.compile(r"url\s*\(", re.I)
_FRAGMENT_CALL = re.compile(rf"url\s*\(\s*#{_ID}\s*\)", re.I)
_FONT_FACE = re.compile(r"@\s*font-face\s*\{([^}]*)\}", re.I)
_FONT_SRC = re.compile(
    rf"""\s*url\(\s*(?:"asset:{_ID}"|'asset:{_ID}'|asset:{_ID})\s*\)\s*"""
    r"""(?:format\(\s*(?:"[^"]*"|'[^']*'|[A-Za-z0-9-]+)\s*\)\s*)?""", re.I)
_SRC_DECL = re.compile(r"(?<![\w-])src\s*:([^;]*)", re.I)
_SCHEME = re.compile(r"(?:javascript|data|vbscript):", re.I)
_CONTROL = re.compile(r"[\x00-\x20\x7f]+")
_LEADING_JUNK = re.compile(r"^[\x00-\x20]+")
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_CSS_AT_RULES = frozenset(["media", "keyframes", "supports", "font-face", "layer"])
_CSS_FUNCTIONS = frozenset(["rgb", "rgba", "hsl", "hsla", "hwb", "lab", "lch", "oklab", "oklch", "color", "color-mix", "var", "calc", "min", "max", "clamp", "repeat", "minmax", "fit-content", "linear-gradient", "radial-gradient", "conic-gradient", "repeating-linear-gradient", "repeating-radial-gradient", "repeating-conic-gradient", "translate", "translatex", "translatey", "translate3d", "rotate", "rotatex", "rotatey", "rotatez", "scale", "scalex", "scaley", "skew", "skewx", "skewy", "matrix", "perspective", "cubic-bezier", "steps", "blur", "brightness", "contrast", "drop-shadow", "grayscale", "hue-rotate", "invert", "opacity", "saturate", "sepia", "env", "format", "url"])
_CSS_BANNED_PROPERTIES = frozenset(["behavior", "-moz-binding"])
_GLOBAL_ATTRS = frozenset(["id", "class", "style", "title", "lang", "dir", "role", "tabindex", "hidden"])
_TAG_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset(["href", "rel", "download"]),
    "img": frozenset(["src", "alt", "width", "height", "loading"]),
    "meta": frozenset({"charset"}),
    "link": frozenset({"rel", "href"}),
    "input": frozenset(["type", "name", "value", "placeholder", "checked", "disabled", "required", "min", "max", "step"]),
    "button": frozenset(["type", "name", "value", "disabled"]),
    "label": frozenset({"for"}),
    "textarea": frozenset(["name", "placeholder", "rows", "cols", "disabled", "required"]),
    "select": frozenset(["name", "multiple", "disabled", "required"]),
    "option": frozenset(["value", "selected", "disabled"]),
    "form": frozenset(["method", "enctype", "autocomplete"]),
    "th": frozenset(["scope", "colspan", "rowspan"]),
    "td": frozenset(["colspan", "rowspan"]),
    "time": frozenset({"datetime"}),
    "abbr": frozenset({"title"}),
    "svg": frozenset(["viewbox", "xmlns", "width", "height", "fill", "stroke", "role"]),
    "defs": frozenset(),
    "g": frozenset(["fill", "stroke", "transform"]),
    "path": frozenset(["d", "fill", "stroke", "stroke-width", "transform"]),
    "rect": frozenset(["x", "y", "width", "height", "rx", "ry", "fill", "stroke", "stroke-width"]),
    "circle": frozenset(["cx", "cy", "r", "fill", "stroke", "stroke-width"]),
    "ellipse": frozenset(["cx", "cy", "rx", "ry", "fill", "stroke", "stroke-width"]),
    "line": frozenset(["x1", "y1", "x2", "y2", "stroke", "stroke-width"]),
    "polyline": frozenset(["points", "fill", "stroke", "stroke-width"]),
    "polygon": frozenset(["points", "fill", "stroke", "stroke-width"]),
    "text": frozenset(["x", "y", "fill", "font-size", "text-anchor"]),
    "tspan": frozenset(["x", "y", "dx", "dy"]),
    "lineargradient": frozenset(["id", "x1", "y1", "x2", "y2", "gradientunits"]),
    "radialgradient": frozenset(["id", "cx", "cy", "r", "gradientunits"]),
    "stop": frozenset(["offset", "stop-color", "stop-opacity"]),
    "clippath": frozenset(["id", "clippathunits"]),
}


def _mask_css_strings(source: str, line: int) -> str:
    """Return ``source`` with string bodies blanked (same length), so structure checks skip them."""
    masked = list(source)
    i = 0
    while i < len(source):
        if source[i] in "\"'":
            end = i + 1
            while end < len(source) and source[end] not in (source[i], "\n"):
                end += 1
            if end >= len(source) or source[end] != source[i]:
                raise DesignHtmlError(line, "unterminated CSS string")
            for j in range(i + 1, end):
                masked[j] = "x"
            i = end + 1
        else:
            i += 1
    return "".join(masked)


def _css_url_ok(argument: str) -> bool:
    inner = argument.strip()
    if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in "\"'":
        inner = inner[1:-1].strip()
    return _ASSET_REF.fullmatch(inner) is not None or _FRAGMENT_REF.fullmatch(inner) is not None


def _css(source: str, line: int) -> None:
    """Refuse any CSS that is not a plain declaration list or allowed at-rule; see module docs."""
    # A backslash can spell a forbidden name (`u\72l(`); no legitimate artboard needs one.
    if "\\" in source:
        raise DesignHtmlError(line, "CSS escapes (backslash) are not allowed")
    # Refused outright: a comment-blanking pass would let `url(a/*)` hide the fetch that follows.
    if "/*" in source:
        raise DesignHtmlError(line, "CSS comments are not allowed")
    masked = _mask_css_strings(source, line)
    depth = 0
    for char in masked:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth < 0:
            break
    if depth != 0:
        raise DesignHtmlError(line, "unbalanced parentheses in CSS")
    for at in re.finditer(r"@\s*([A-Za-z_-][\w-]*)", masked):
        if at.group(1).lower() not in _CSS_AT_RULES:
            raise DesignHtmlError(line, "CSS at-rule is not allowed", echo="@" + at.group(1))
    for call in re.finditer(r"([A-Za-z_-][\w-]*)?\(", masked):
        name = (call.group(1) or "").lower()
        # `@media(` names an at-rule, which the at-rule check above has already judged.
        is_at_rule = bool(name) and masked[call.start(1) - 1:call.start(1)] == "@"
        if name and not is_at_rule and name not in _CSS_FUNCTIONS:
            raise DesignHtmlError(line, "CSS function is not allowed", echo=name + "()")
    for url in _URL_CALL.finditer(masked):
        close = masked.find(")", url.end())
        if close < 0 or not _css_url_ok(source[url.end():close]):
            raise DesignHtmlError(line, "CSS URLs must use asset:<id> or #<id>")
    for prop in re.finditer(r"([A-Za-z_-][\w-]*)\s*:", masked):
        if prop.group(1).lower() in _CSS_BANNED_PROPERTIES:
            raise DesignHtmlError(line, "CSS property is not allowed", echo=prop.group(1))
    if re.search(r"javascript\s*:", masked, re.I):
        raise DesignHtmlError(line, "unsafe CSS construct")
    faces = list(_FONT_FACE.finditer(masked))
    if len(re.findall(r"@\s*font-face\b", masked, re.I)) != len(faces):
        raise DesignHtmlError(line, "@font-face requires src url(asset:<id>)")
    for face in faces:
        body_masked = masked[face.start(1):face.end(1)]
        body = source[face.start(1):face.end(1)]
        sources = list(_SRC_DECL.finditer(body_masked))
        if not sources or any(not _FONT_SRC.fullmatch(body[s.start(1):s.end(1)]) for s in sources):
            raise DesignHtmlError(line, "@font-face requires src url(asset:<id>)")


def _path_form(value: str) -> str | None:
    """The path a browser would request: decoded to a fixed point, backslashes as slashes.

    Returns None when it still decodes after three rounds, since a browser decodes once and a
    value that needs more is hiding something.
    """
    path = value
    for _ in range(3):
        decoded = unquote(path)
        if decoded == path:
            return path.replace("\\", "/")
        path = decoded
    return None


def _anchor_ok(value: str) -> bool:
    # A browser skips leading controls and spaces before it reads the scheme, so this check does.
    value = _LEADING_JUNK.sub("", value)
    # `//host/x` is protocol-relative: it leaves the document's own origin.
    if value.startswith("//"):
        return False
    if value.startswith(("https://", "http://", "mailto:", "#")):
        return True
    path = _path_form(value)
    if path is None:
        return False
    return (path.endswith(".html") and ":" not in path and not path.startswith("/")
            and ".." not in path.split("/"))


def _paint_url_ok(value: str) -> bool:
    """SVG paint and filter references must point inside the document: ``url(#<id>)`` only."""
    return len(_URL_CALL.findall(value)) == len(_FRAGMENT_CALL.findall(value))


# Element ordinals: an element is addressed by the ordinal of its opening tag among every start
# tag the parser reports, in document order. Comments, declarations and text never count, and
# neither does the content of a raw-text element, which the browser also reads as text.
_VOID_ELEMENTS = frozenset(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"])
_RAW_TEXT_ELEMENTS = frozenset(["script", "style", "textarea", "title"])
# The attribute pattern of html.parser (its ``attrfind_tolerant``), copied: typeshed does not export it.
_ATTRIBUTE = re.compile(
    r"((?<=['\"\t\n\r\f /])[^\t\n\r\f />][^\t\n\r\f /=>]*)"
    r"([\t\n\r\f ]*=[\t\n\r\f ]*"
    r"('[^']*'|\"[^\"]*\"|(?!['\"])[^>\t\n\r\f ]*))?"
    r"(?:[\t\n\r\f ]|/(?!>))*"
)
_TAG_HEAD = re.compile(r"<([a-zA-Z][^\t\n\r\f />\x00]*)(?:\s|/(?!>))*")


@dataclass(frozen=True)
class HtmlElementOffsets:
    """One start tag, located by its ordinal. Offsets index the source string exactly."""

    index: int
    tag: str
    tag_start: int
    tag_end: int
    content_end: int
    text_only: bool
    self_closing: bool
    closed: bool

    @property
    def content_start(self) -> int:
        return self.tag_end


@dataclass
class _StartTag:
    tag: str
    start: int
    end: int
    self_closing: bool
    closed: bool = False
    content_end: int = 0
    has_children: bool = False


class _StartTagScanner(HTMLParser):
    """Records every start tag in order. Line and column come from ``getpos``, which counts ``\n`` only."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self._line_starts = [0] + [match.end() for match in re.finditer("\n", source)]
        self.starts: list[_StartTag] = []
        self._open: list[_StartTag] = []
        self.feed(source)
        self.close()

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_starts[line - 1] + column

    def _record(self, element: _StartTag) -> None:
        if self._open:
            self._open[-1].has_children = True
        self.starts.append(element)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        start = self._offset()
        element = _StartTag(lowered, start, start + len(self.get_starttag_text() or ""),
                            self_closing=lowered in _VOID_ELEMENTS)
        self._record(element)
        if not element.self_closing:
            self._open.append(element)
        if lowered in _RAW_TEXT_ELEMENTS:
            # The parser switches to raw text only for script and style; the browser does so for these too.
            self.set_cdata_mode(lowered)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        start = self._offset()
        element = _StartTag(lowered, start, start + len(self.get_starttag_text() or ""), self_closing=True)
        element.content_end = element.end
        self._record(element)
        if lowered in _RAW_TEXT_ELEMENTS:
            self.set_cdata_mode(lowered)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        for position in range(len(self._open) - 1, -1, -1):
            element = self._open[position]
            if element.tag == lowered:
                element.closed = True
                element.content_end = self._offset()
                del self._open[position:]
                return


def list_start_tags(source: str) -> list[tuple[str, int]]:
    """The ``(tag, offset)`` of every start tag, in the order their ordinals are assigned."""
    return [(element.tag, element.start) for element in _StartTagScanner(source).starts]


def locate_html_element(source: str, index: int) -> HtmlElementOffsets:
    """Locate the element whose opening tag is the ``index``-th start tag of ``source`` (see above)."""
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise DesignHtmlError(1, "invalid element index")
    starts = _StartTagScanner(source).starts
    if index >= len(starts):
        raise DesignHtmlError(1, "element index does not exist")
    element = starts[index]
    return HtmlElementOffsets(
        index=index,
        tag=element.tag,
        tag_start=element.start,
        tag_end=element.end,
        content_end=element.content_end if element.closed else element.end,
        text_only=not element.has_children,
        self_closing=element.self_closing,
        closed=element.closed,
    )


def tag_style_attribute(tag_text: str) -> tuple[int, int, str] | None:
    """The ``style`` attribute of one opening tag: its span in ``tag_text`` and its decoded value.

    Walks the attributes with the regex ``html.parser`` itself uses, so the span is the one the
    parser reads. The value is decoded the way the parser decodes it.
    """
    head = _TAG_HEAD.match(tag_text)
    if head is None:
        return None
    position = head.end()
    while position < len(tag_text):
        attribute = _ATTRIBUTE.match(tag_text, position)
        if attribute is None:
            break
        if attribute.group(1).lower() == "style":
            raw = attribute.group(3) or ""
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
                raw = raw[1:-1]
            end = attribute.end(3) if attribute.group(3) is not None else attribute.end(1)
            return attribute.start(1), end, unescape(raw)
        position = attribute.end()
    return None


def tag_name_end(tag_text: str) -> int:
    """Offset just past the tag name of an opening tag, where a new attribute is inserted."""
    head = _TAG_HEAD.match(tag_text)
    if head is None:
        raise DesignHtmlError(1, "not an opening tag")
    return head.end(1)



# Format lint is a belt, not the lock: the model's code runs only in an opaque-origin sandbox, and
# the frame's policy (apps/desktop/index.html, HtmlArtboardFrame.tsx) is what stops a navigation.
# Case-sensitive on purpose: JavaScript identifiers are, so `function (x)` and `'important'` are
# not the Function constructor and the `import` keyword, and `el.style.top` is not the window's top.
_SCRIPT_BLOCK_RE = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.I | re.S)
_SCRIPT_BANNED: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.M), reason) for pattern, reason in [
        (r"\b(?:innerHTML|outerHTML|insertAdjacentHTML)\b", "HTML injection APIs are not allowed"),
        (r"\bdocument\s*\.\s*write\b", "document.write is not allowed"),
        (r"\beval\s*\(", "eval is not allowed"),
        (r"\bFunction\s*\(|\bnew\s+Function\b", "Function constructor is not allowed"),
        (r"\bset(?:Timeout|Interval)\s*\(\s*['\"`]", "string timers are not allowed"),
        (r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|Worker|importScripts)\b", "network/workers are not allowed"),
        (r"\bnavigator\s*\.\s*sendBeacon\b", "sendBeacon is not allowed"),
        # A statement that starts with `import`, or the dynamic `import(`. Not `'important'`, not a
        # comment, not an identifier that merely begins with the word.
        (r"\bimport\s*\(|(?:^|[;{}])[ \t]*import\b", "imports are not allowed"),
        # Frame reach: `location` anywhere, and `top`/`parent`/`opener` only where they name the
        # window (a bare word or `window.top`, `self.top`, `globalThis.top`, or `top[...]`), never a
        # property such as `el.style.top`.
        (r"\blocation\b", "frame navigation/access is not allowed"),
        (r"\b(?:window|self|globalThis|frames)\s*\.\s*(?:top|parent|opener)\b", "frame navigation/access is not allowed"),
        (r"(?<![\w$.])(?:top|parent|opener)\s*(?:\.|\[)", "frame navigation/access is not allowed"),
        (r"\b(?:window|self|globalThis)\s*\[\s*['\"`](?:top|parent|opener|location)['\"`]\s*\]", "frame navigation/access is not allowed"),
        (r"\bwindow\s*\.\s*open\b", "window.open is not allowed"),
        (r"\bdocument\s*\.\s*cookie\b", "document.cookie is not allowed"),
        (r"\b(?:localStorage|sessionStorage|indexedDB)\b", "storage is not allowed"),
        (r"\b(?:webkit)?RTCPeerConnection\b", "WebRTC is not allowed"),
        (r"\bpostMessage\b", "postMessage is not allowed"),
        (r"(?:window|document)\s*\.\s*addEventListener\s*\(\s*['\"`]key", "global keyboard listeners are not allowed"),
    ]
)


def _validate_model_script(source: str, line: int) -> None:
    matches = list(_SCRIPT_BLOCK_RE.finditer(source))
    script_openings = list(re.finditer(r"<script\b", source, re.I))
    if len(script_openings) != len(matches):
        line_number = source.count("\n", 0, script_openings[0].start()) + 1 if script_openings else line
        raise DesignHtmlError(line_number, "malformed or unsupported script element")
    if not matches:
        return
    if len(matches) > 1:
        raise DesignHtmlError(source.count("\n", 0, matches[1].start()) + 1, "only one model script is allowed")
    match = matches[0]
    attrs = match.group(1)
    if not re.fullmatch(r"\s+type\s*=\s*(['\"])text/x-chimera\1\s*", attrs, re.I):
        raise DesignHtmlError(source.count("\n", 0, match.start()) + 1, 'script must have only type="text/x-chimera" and no src')
    code = match.group(2)
    code_line = source.count("\n", 0, match.start(2)) + 1
    if not re.search(r"\bclass\s+Component\b", code):
        raise DesignHtmlError(code_line, "script must declare class Component")
    if not re.search(r"\brender\s*\(\s*state\s*\)", code):
        raise DesignHtmlError(code_line, "Component must implement render(state)")
    for pattern, reason in _SCRIPT_BANNED:
        found = pattern.search(code)
        if found:
            found_line = code_line + code.count("\n", 0, found.start())
            raise DesignHtmlError(found_line, reason, echo=found.group(0))


class _AllowlistParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.svg_depth = 0
        self.in_style = False
        self.in_title = False
        self.style_text: list[str] = []
        self.in_model_script = False

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], self_closing: bool) -> None:
        lowered = tag.lower()
        line = self.getpos()[0]
        if lowered == "script":
            # The model block is accepted at one level only: outside <svg> and <title>. Inside <svg>
            # a browser parses the script's body as markup, not as text, so the body would be
            # markup this allowlist never saw (measured: a <div> and an <img> escaped it).
            if self.svg_depth:
                raise DesignHtmlError(line, "is not allowed inside <svg>", echo="<script>")
            if self.in_title:
                raise DesignHtmlError(line, "is not allowed inside <title>", echo="<script>")
            self.in_model_script = True
            return
        if lowered in _FORBIDDEN_TAGS:
            raise DesignHtmlError(line, "is not allowed", echo=f"<{lowered}>")
        if self.in_title:
            # Browsers read title text as markup-free text, so anything here is refused.
            raise DesignHtmlError(line, "is not allowed inside <title>", echo=f"<{lowered}>")
        if self.svg_depth:
            if lowered not in _SVG_TAGS:
                raise DesignHtmlError(line, "is not allowed inside <svg>", echo=f"<{lowered}>")
        elif lowered in _SVG_ONLY_TAGS:
            raise DesignHtmlError(line, "is only allowed inside <svg>", echo=f"<{lowered}>")
        elif lowered not in _HTML_TAGS:
            raise DesignHtmlError(line, "is not allowlisted", echo=f"<{lowered}>")
        seen: set[str] = set()
        for raw_name, raw_value in attrs:
            name = raw_name.lower()
            value = raw_value or ""
            if name in seen:
                raise DesignHtmlError(line, "duplicate attribute", echo=name)
            seen.add(name)
            if name.startswith("on") or name in _FORBIDDEN_ATTRS:
                raise DesignHtmlError(line, "attribute is not allowed", echo=name)
            allowed = name in _GLOBAL_ATTRS or name in _TAG_ATTRS.get(lowered, frozenset()) or name.startswith(("aria-", "data-"))
            if not allowed:
                raise DesignHtmlError(line, "attribute is not allowlisted", echo=name)
            if _SCHEME.search(_CONTROL.sub("", value)):
                raise DesignHtmlError(line, "unsafe URL scheme")
            if name == "style":
                _css(value, line)
            elif (name in _PAINT_ATTRS or _URL_CALL.search(value)) and not _paint_url_ok(value):
                raise DesignHtmlError(line, "url() here must be url(#<id>)", echo=name)
            if lowered == "meta" and name == "charset" and value.strip().lower() != "utf-8":
                raise DesignHtmlError(line, "meta charset must be utf-8")
            if lowered == "meta" and name != "charset":
                raise DesignHtmlError(line, "meta only permits charset")
            if lowered == "svg" and name == "xmlns" and value != _SVG_NAMESPACE:
                raise DesignHtmlError(line, "svg xmlns must be the SVG namespace", echo=value)
            if lowered == "input" and name == "type" and value.strip().lower() == "file":
                raise DesignHtmlError(line, "file inputs are not allowed")
            if lowered == "link":
                if name == "rel" and value.strip().lower() != "stylesheet":
                    raise DesignHtmlError(line, "link rel must be stylesheet")
                if name == "href" and not value.startswith("https://fonts.googleapis.com/css2?"):
                    raise DesignHtmlError(line, "link href must use Google Fonts CSS2")
            if lowered == "a" and name == "href" and not _anchor_ok(value):
                raise DesignHtmlError(line, "anchor href is not allowed", echo=value)
            if lowered == "img" and name == "src" and not (value.startswith("asset:") and _SAFE_ID.fullmatch(value[6:])):
                raise DesignHtmlError(line, "img src must use asset:<id>")
            if self.svg_depth and name == "href":
                raise DesignHtmlError(line, "SVG href is not allowed")
        if lowered == "meta" and "charset" not in seen:
            raise DesignHtmlError(line, "meta requires charset")
        if lowered == "link" and not {"rel", "href"} <= seen:
            raise DesignHtmlError(line, "link requires rel and href")
        # A self-closed <svg/> is not counted: the depth tracks only open <svg> elements.
        if lowered == "svg" and not self_closing:
            self.svg_depth += 1
        # Browsers read <style> and <title> as raw or RCDATA text whatever the `/`, so a
        # self-closed one still opens that text and it must be checked until its end tag.
        if lowered in ("style", "title"):
            self.in_style = lowered == "style"
            self.in_title = lowered == "title"
            self.style_text = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        line = self.getpos()[0]
        if lowered == "script" and self.in_model_script:
            self.in_model_script = False
            return
        if lowered in _FORBIDDEN_TAGS:
            raise DesignHtmlError(line, "is not allowed", echo=f"</{lowered}>")
        if lowered not in _ALLOWED_TAGS:
            raise DesignHtmlError(line, "closing tag is not allowlisted", echo=f"</{lowered}>")
        if lowered == "svg":
            self.svg_depth = max(0, self.svg_depth - 1)
        if lowered == "style" and self.in_style:
            self.in_style = False
            _css("".join(self.style_text), line)
            self.style_text = []
        if lowered == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        # Raw text reaches here, so a `<` is either inside a <style> body (refused), or a tag the
        # parser could not finish: `<img src=...` at the end of the source, or a stray `<`.
        if self.in_model_script:
            return
        if "<" in data:
            raise DesignHtmlError(self.getpos()[0],
                                  "stray < in text; write &lt; for a literal less-than sign")
        if self.in_style:
            self.style_text.append(data)

    def handle_decl(self, decl: str) -> None:
        if decl.lower() != "doctype html":
            raise DesignHtmlError(self.getpos()[0], "only <!doctype html> is allowed")

    def unknown_decl(self, data: str) -> None:
        raise DesignHtmlError(self.getpos()[0], "unknown declaration")

    def handle_comment(self, data: str) -> None:
        raise DesignHtmlError(self.getpos()[0], "HTML comments are not allowed")

    def handle_pi(self, data: str) -> None:
        raise DesignHtmlError(self.getpos()[0], "processing instructions are not allowed")


def _refuse_unterminated(parser: _AllowlistParser) -> None:
    # After feed() the parser still holds an unfinished tag or an open <style>/<title>; after
    # close() some of those are dropped silently, so both points are checked.
    if parser.rawdata or parser.cdata_elem is not None:
        raise DesignHtmlError(parser.getpos()[0], "unterminated tag or element")


def validate_artboard_html(source: str) -> None:
    """Validate a complete HTML source against the inert artboard allowlist."""
    if not isinstance(source, str):
        raise DesignHtmlError(1, "HTML source must be text")
    if len(source.encode("utf-8")) > MAX_ARTBOARD_HTML:
        raise DesignHtmlError(1, f"HTML exceeds {MAX_ARTBOARD_HTML} bytes")
    if not source.strip():
        raise DesignHtmlError(1, "empty HTML; use null to remove")
    _validate_model_script(source, 1)
    parser = _AllowlistParser()
    try:
        parser.feed(source)
        _refuse_unterminated(parser)
        parser.close()
        _refuse_unterminated(parser)
    except DesignHtmlError:
        raise
    except Exception as exc:
        raise DesignHtmlError(parser.getpos()[0], "malformed HTML source") from exc
