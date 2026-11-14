"""The CSS an HTML artboard check can read with fidelity: values, variables, shorthands, rules.

The contract is "a measured fact or not checked". Everything here returns ``None`` (or the
:data:`UNKNOWN` marker inside a property map) for what it cannot model, and never raises on a value:
a malformed number is an unknown, not an error. Callers turn unknowns into ``Unchecked`` entries.

This runs on the server over text a model wrote, so every scanner is linear (no backtracking regex over
unbounded input) and the expensive paths (variable expansion, rule matching, rule bodies) spend from a
:class:`Budget`; running out raises :class:`TooComplex`, which the caller reports as "not checked".
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

#: Stored in a property map where a value exists but could not be read.
UNKNOWN = "\x00unknown"
#: Stored for a custom property whose value contains ``var()``: not resolved where it was declared.
ALIAS = "\x00alias"
Note = Callable[[str, str], None]
MAX_VALUE = 4096
MAX_RULE_BYTES = 512 * 1024

NUM = r"[+-]?(?:\d{1,12}(?:\.\d{1,12})?|\.\d{1,12})"
_NUMBER = re.compile(rf"{NUM}%?")
_HEX = re.compile(r"^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$", re.I)
_LENGTH = re.compile(rf"^({NUM})(px|rem|pt)?$", re.I)
_SIMPLE = re.compile(r"^(\*|[A-Za-z][A-Za-z0-9]*)?((?:\.[\w-]+|#[\w-]+)*)$")
_VAR_CALL = re.compile(r"var\(", re.I)
_BOUNDARY = re.compile(r"[{};]")
_KEYWORD_COLORS = {"inherit": "current", "initial": "current", "unset": "current", "auto": "current",
                   "currentcolor": "current", "transparent": "none", "none": "none"}


class Scope:
    """Custom properties as a chain: an element holds only what it declares and points at its parent's.

    Copying the parent's map into every element is O(elements x variables); this is O(declarations).
    """

    __slots__ = ("own", "parent")

    def __init__(self, parent: Scope | None) -> None:
        self.own: dict[str, str] = {}
        self.parent = parent

    def get(self, name: str) -> str | None:
        scope: Scope | None = self
        while scope is not None:
            if name in scope.own:
                return scope.own[name]
            scope = scope.parent
        return None


class NotChecked(Exception):
    """An artboard the checker declines to measure, with the reason code and a message."""

    reason = "not_checked"
    message = "this artboard was not checked"


class TooComplex(NotChecked):
    reason = "too_complex"
    message = "this artboard is too large or too intricate for the checker; it was not checked"


class Unbalanced(NotChecked):
    reason = "structure_unbalanced"
    message = ("the markup relies on implied or missing closing tags, which the checker does not model; "
               "it was not checked")


class Budget:
    """A cap on work, so a small hostile input cannot cost the server a large amount of CPU."""

    def __init__(self, units: int = 2_000_000) -> None:
        self.left = units

    def spend(self, units: int = 1) -> None:
        self.left -= units
        if self.left < 0:
            raise TooComplex


_NAMED = dict(pair.split(":") for pair in ["aliceblue:f0f8ff", "antiquewhite:faebd7", "aqua:00ffff", "aquamarine:7fffd4", "azure:f0ffff", "beige:f5f5dc", "bisque:ffe4c4", "black:000000", "blanchedalmond:ffebcd", "blue:0000ff", "blueviolet:8a2be2", "brown:a52a2a", "burlywood:deb887", "cadetblue:5f9ea0", "chartreuse:7fff00", "chocolate:d2691e", "coral:ff7f50", "cornflowerblue:6495ed", "cornsilk:fff8dc", "crimson:dc143c", "cyan:00ffff", "darkblue:00008b", "darkcyan:008b8b", "darkgoldenrod:b8860b", "darkgray:a9a9a9", "darkgreen:006400", "darkgrey:a9a9a9", "darkkhaki:bdb76b", "darkmagenta:8b008b", "darkolivegreen:556b2f", "darkorange:ff8c00", "darkorchid:9932cc", "darkred:8b0000", "darksalmon:e9967a", "darkseagreen:8fbc8f", "darkslateblue:483d8b", "darkslategray:2f4f4f", "darkslategrey:2f4f4f", "darkturquoise:00ced1", "darkviolet:9400d3", "deeppink:ff1493", "deepskyblue:00bfff", "dimgray:696969", "dimgrey:696969", "dodgerblue:1e90ff", "firebrick:b22222", "floralwhite:fffaf0", "forestgreen:228b22", "fuchsia:ff00ff", "gainsboro:dcdcdc", "ghostwhite:f8f8ff", "gold:ffd700", "goldenrod:daa520", "gray:808080", "green:008000", "greenyellow:adff2f", "grey:808080", "honeydew:f0fff0", "hotpink:ff69b4", "indianred:cd5c5c", "indigo:4b0082", "ivory:fffff0", "khaki:f0e68c", "lavender:e6e6fa", "lavenderblush:fff0f5", "lawngreen:7cfc00", "lemonchiffon:fffacd", "lightblue:add8e6", "lightcoral:f08080", "lightcyan:e0ffff", "lightgoldenrodyellow:fafad2", "lightgray:d3d3d3", "lightgreen:90ee90", "lightgrey:d3d3d3", "lightpink:ffb6c1", "lightsalmon:ffa07a", "lightseagreen:20b2aa", "lightskyblue:87cefa", "lightslategray:778899", "lightslategrey:778899", "lightsteelblue:b0c4de", "lightyellow:ffffe0", "lime:00ff00", "limegreen:32cd32", "linen:faf0e6", "magenta:ff00ff", "maroon:800000", "mediumaquamarine:66cdaa", "mediumblue:0000cd", "mediumorchid:ba55d3", "mediumpurple:9370db", "mediumseagreen:3cb371", "mediumslateblue:7b68ee", "mediumspringgreen:00fa9a", "mediumturquoise:48d1cc", "mediumvioletred:c71585", "midnightblue:191970", "mintcream:f5fffa", "mistyrose:ffe4e1", "moccasin:ffe4b5", "navajowhite:ffdead", "navy:000080", "oldlace:fdf5e6", "olive:808000", "olivedrab:6b8e23", "orange:ffa500", "orangered:ff4500", "orchid:da70d6", "palegoldenrod:eee8aa", "palegreen:98fb98", "paleturquoise:afeeee", "palevioletred:db7093", "papayawhip:ffefd5", "peachpuff:ffdab9", "peru:cd853f", "pink:ffc0cb", "plum:dda0dd", "powderblue:b0e0e6", "purple:800080", "rebeccapurple:663399", "red:ff0000", "rosybrown:bc8f8f", "royalblue:4169e1", "saddlebrown:8b4513", "salmon:fa8072", "sandybrown:f4a460", "seagreen:2e8b57", "seashell:fff5ee", "sienna:a0522d", "silver:c0c0c0", "skyblue:87ceeb", "slateblue:6a5acd", "slategray:708090", "slategrey:708090", "snow:fffafa", "springgreen:00ff7f", "steelblue:4682b4", "tan:d2b48c", "teal:008080", "thistle:d8bfd8", "tomato:ff6347", "turquoise:40e0d0", "violet:ee82ee", "wheat:f5deb3", "white:ffffff", "whitesmoke:f5f5f5", "yellow:ffff00", "yellowgreen:9acd32"])
NAMED_COLORS = {name: "#" + digits.upper() for name, digits in _NAMED.items()}

_BORDER_STYLES = frozenset(["none", "hidden", "dotted", "dashed", "solid", "double", "groove", "ridge",
                            "inset", "outset", "thin", "medium", "thick"])
_BG_KEYWORDS = frozenset(["none", "repeat", "repeat-x", "repeat-y", "no-repeat", "space", "round",
                          "scroll", "fixed", "local", "border-box", "padding-box", "content-box", "text",
                          "center", "top", "bottom", "left", "right", "cover", "contain", "auto", "/"])
_SIDES = ("top", "right", "bottom", "left")


def _clamp(value: float, high: float) -> float:
    return max(0.0, min(high, value))


def _channel(raw: str, scale: float) -> float:
    return float(raw[:-1]) / 100 * scale if raw.endswith("%") else float(raw)


def _rgb(value: str) -> str | None:
    """``rgb()``/``rgba()`` with the browser's grammar: all commas, or all spaces with ``/`` before alpha.

    Any other mix is invalid and the declaration is dropped by the browser, so it is unknown here.
    """
    low = value.lower()
    if not low.endswith(")") or not (low.startswith("rgb(") or low.startswith("rgba(")):
        return None
    inner = value[value.index("(") + 1:-1].strip()
    if "," in inner:
        parts = [p.strip() for p in inner.split(",")]
        ok = len(parts) in (3, 4) and "/" not in inner
    elif "/" in inner:
        left, _slash, alpha = inner.partition("/")
        parts = [*left.split(), alpha.strip()]
        ok = len(parts) == 4 and "/" not in alpha and len(alpha.split()) == 1
    else:
        parts = inner.split()
        ok = len(parts) == 3
    if not ok or any(_NUMBER.fullmatch(p) is None for p in parts):
        return None
    r, g, b = (round(_clamp(_channel(p, 255), 255)) for p in parts[:3])
    out = f"#{r:02X}{g:02X}{b:02X}"
    if len(parts) == 4:
        out += f"{round(_clamp(_channel(parts[3], 1), 1) * 255):02X}"
    return out


def parse_color(value: str) -> str | None:
    """``#RRGGBB[AA]``; ``"none"`` for transparent; ``"current"`` for inherit-like keywords; else None."""
    value = value.strip()
    if len(value) > 100:
        return None
    low = value.lower()
    if low in _KEYWORD_COLORS:
        return _KEYWORD_COLORS[low]
    if low in NAMED_COLORS:
        return NAMED_COLORS[low]
    if (hexed := _HEX.match(value)) is not None:
        digits = hexed.group(1)
        if len(digits) in (3, 4):
            digits = "".join(c * 2 for c in digits)
        return "#" + digits.upper()
    return _rgb(value)


_COLOUR_PROPS = re.compile(r"^(?:color|fill|stroke|[\w-]*-color)$")


def drops_declaration(prop: str, value: str) -> bool:
    """True where the browser rejects a colour value as invalid and keeps the earlier declaration.

    Only for what is certainly invalid (``rgb()`` whose arguments are bare numbers in a bad grammar, or a
    malformed hex); a colour the checker merely cannot read (``calc()``, ``min()``, ``var()``, ``none``,
    ``hsl()``, ``color-mix()``) is never dropped, it stays unknown.
    """
    if _COLOUR_PROPS.match(prop) is None:
        return False
    low = value.strip().lower()
    if parse_color(value) is not None or len(low) > 100:
        return False
    if low.startswith("#"):
        return low[1:].isalnum()  # a malformed hex colour
    if low.startswith(("rgb(", "rgba(")) and low.endswith(")"):
        # Provably invalid only when the arguments are bare numbers: anything with a letter or a nested
        # function (calc, min, clamp, var, none, from) may be valid, so it stays unknown, not dropped.
        return re.fullmatch(r"[\d.%+\-,/\s]*", low[low.index("(") + 1:-1]) is not None
    return False


def parse_length(value: str, root_px: float | None) -> float | None:
    """``px``/``pt``/``rem``/``0`` as pixels. ``rem`` needs the root size; unknown without it."""
    match = _LENGTH.match(value.strip()) if len(value) <= 40 else None
    if match is None:
        return None
    number, unit = float(match.group(1)), (match.group(2) or "").lower()
    if unit == "px":
        return number
    if unit == "pt":
        return number * 4 / 3
    if unit == "rem":
        return None if root_px is None else number * root_px
    return 0.0 if number == 0 else None


def substitute(value: str, customs: Scope, budget: Budget, memo: dict[str, str | None]) -> str | None:
    """``value`` with every ``var()`` replaced.

    A defined variable wins over its fallback; an undefined one with no fallback, a cycle, an alias
    (a variable whose own value uses ``var()``), a malformed call or an oversized result is None
    (unknown), never a guess. ``memo`` caches each variable's expansion for one element, and every call
    spends from ``budget``, so a chain of variables that each use the previous one twenty times cannot
    blow up.
    """
    return _substitute(value, customs, budget, memo, 0)


def _substitute(value: str, customs: Scope, budget: Budget, memo: dict[str, str | None], depth: int) -> str | None:
    budget.spend(len(value) // 16 + 1)
    if depth > 8 or len(value) > MAX_VALUE:
        return None
    out: list[str] = []
    size = cursor = 0
    while (call := _VAR_CALL.search(value, cursor)) is not None:
        out.append(value[cursor:call.start()])
        size += call.start() - cursor
        level, end = 1, call.end()
        while end < len(value) and level:
            level += (value[end] == "(") - (value[end] == ")")
            end += 1
        if level:
            return None
        name, comma, fallback = value[call.end():end - 1].partition(",")
        name = name.strip()
        if not re.fullmatch(r"--[\w-]+", name):
            return None
        chosen = customs.get(name)
        if chosen == ALIAS:
            return None
        if chosen is not None:
            if name not in memo:
                memo[name] = _substitute(chosen, customs, budget, memo, depth + 1)
            replaced = memo[name]
        else:
            replaced = _substitute(fallback.strip(), customs, budget, memo, depth + 1) if comma else None
        if replaced is None:
            return None
        size += len(replaced)
        if size > MAX_VALUE:
            return None
        out.append(replaced)
        cursor = end
    out.append(value[cursor:])
    return "".join(out)


def split_values(value: str) -> list[str]:
    """Whitespace-separated tokens, keeping a parenthesised group whole. One linear pass."""
    out: list[str] = []
    current: list[str] = []
    depth = 0
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        if char.isspace() and not depth:
            if current:
                out.append("".join(current))
                current = []
        else:
            current.append(char)
    if current:
        out.append("".join(current))
    return out


def strip_comments(css: str) -> str:
    """CSS without ``/* ... */`` comments (an unterminated one runs to the end), in one pass."""
    if "/*" not in css:
        return css
    out: list[str] = []
    cursor = 0
    while (start := css.find("/*", cursor)) >= 0:
        out.append(css[cursor:start])
        end = css.find("*/", start + 2)
        if end < 0:
            return " ".join(out)
        out.append(" ")
        cursor = end + 2
    out.append(css[cursor:])
    return "".join(out)


def _split_declarations(block: str) -> list[str]:
    """Split on ``;`` outside parentheses and quotes, in one pass."""
    parts: list[str] = []
    start = depth = 0
    quote = ""
    for i, char in enumerate(block):
        if quote:
            quote = "" if char == quote else quote
        elif char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        elif char == ";" and not depth:
            parts.append(block[start:i])
            start = i + 1
    parts.append(block[start:])
    return parts


def _colour_tokens(tokens: list[str], skip: Callable[[str], bool]) -> str:
    """The one colour in a shorthand's tokens; ``currentcolor`` if none; UNKNOWN if a token is unread."""
    found = "currentcolor"
    for token in tokens:
        colour = parse_color(token)
        if colour is not None:
            found = colour
        elif not skip(token):
            return UNKNOWN
    return found


def _skip_border(token: str) -> bool:
    return token.lower() in _BORDER_STYLES or _LENGTH.match(token) is not None


_BORDER_SHORTHAND = re.compile(r"^border(?:-(?:top|right|bottom|left|inline|block)(?:-(?:start|end))?)?$")


def expand(prop: str, value: str | None, note: Note) -> dict[str, str]:
    """The longhands a declaration sets. ``value`` None means its ``var()`` could not be resolved."""
    low = (value or "").strip().lower()
    unread = value is None
    if prop == "background":
        colour, image, ok = "none", "none", not unread and low not in ("inherit", "initial", "unset")
        for token in split_values(value or "") if ok else []:
            if re.match(r"(?:url|[\w-]*gradient|image-set)\(", token, re.I):
                image = "image"
            elif (parsed := parse_color(token)) is not None and parsed != "current":
                colour = parsed
            elif not (token.lower() in _BG_KEYWORDS or (len(token) < 30 and re.fullmatch(rf"{NUM}(%|[a-z]+)?", token, re.I))):
                ok = False
        return {"background-color": colour, "background-image": image} if ok else {
            "background-color": UNKNOWN, "background-image": UNKNOWN}
    if prop == "background-image":
        return {prop: UNKNOWN if unread else ("none" if low == "none" else "image")}
    if prop == "background-color":
        return {prop: UNKNOWN if unread or low in ("inherit", "initial", "unset") else (value or "")}
    if prop == "font":
        note("font_shorthand", "the font shorthand was not read: type size, weight and family were "
                               "not compared")
        return {"font-size": UNKNOWN, "font-weight": UNKNOWN, "font-family": UNKNOWN}
    if _BORDER_SHORTHAND.match(prop):
        sides = _SIDES if prop == "border" else (prop[len("border-"):],)
        colour = UNKNOWN if unread else _colour_tokens(split_values(value or ""), _skip_border)
        return {f"border-{side}-color": colour for side in sides}
    if prop == "outline":
        return {"outline-color": UNKNOWN if unread else _colour_tokens(split_values(value or ""), _skip_border)}
    if prop == "border-color":
        parts = split_values(value or "")
        if unread or not 1 <= len(parts) <= 4:
            return {f"border-{side}-color": UNKNOWN for side in _SIDES}
        top = parts[0]
        right = parts[1] if len(parts) > 1 else top
        bottom = parts[2] if len(parts) > 2 else top
        left = parts[3] if len(parts) > 3 else right
        return dict(zip((f"border-{side}-color" for side in _SIDES), (top, right, bottom, left), strict=True))
    return {prop: UNKNOWN if unread else (value or "")}


@dataclass(frozen=True)
class Decl:
    prop: str
    value: str
    important: bool


@dataclass(frozen=True)
class Rule:
    selector: str
    specificity: tuple[int, int, int]
    order: int
    decls: tuple[Decl, ...]


def parse_declarations(block: str) -> tuple[Decl, ...]:
    out: list[Decl] = []
    for part in _split_declarations(strip_comments(block)):
        name, colon, raw = part.partition(":")
        if not colon or not name.strip():
            continue
        value = raw.strip()
        important = False
        bang = value.rfind("!")
        if bang >= 0 and value[bang + 1:].strip().lower() == "important":
            important, value = True, value[:bang].strip()
        prop = name.strip()
        out.append(Decl(prop if prop.startswith("--") else prop.lower(), value, important))
    return tuple(out)


def _block_end(css: str, start: int) -> int:
    """The index just past the ``}`` that closes the ``{`` before ``start`` (nesting counted), or -1."""
    level = 1
    for i in range(start, len(css)):
        char = css[i]
        if char == "{":
            level += 1
        elif char == "}":
            level -= 1
            if not level:
                return i + 1
    return -1


def parse_css(css: str, note: Note, budget: Budget) -> list[Rule]:
    """Rules with simple selectors. Everything else is noted, never guessed at. One linear pass."""
    if len(css) > MAX_RULE_BYTES:
        raise TooComplex
    css = strip_comments(css)
    budget.spend(len(css) // 8 + 1)
    rules: list[Rule] = []
    i, order = 0, 0
    while i < len(css):
        found = _BOUNDARY.search(css, i)
        if found is None:
            break
        prelude, mark = css[i:found.start()].strip(), found.group()
        if mark == "}":
            i = found.end()
            continue
        if mark == ";":
            if prelude.startswith("@"):
                note("at_rule", "@-rules are not evaluated")
            i = found.end()
            continue
        end = _block_end(css, found.end())
        if end < 0:
            note("selector", "an unterminated CSS block was not evaluated")
            break
        body = css[found.end():end - 1]
        i = end
        if prelude.startswith("@"):
            if not prelude.lower().startswith("@font-face"):
                note("at_rule", f"{prelude.split()[0].lower()} blocks are not evaluated")
            continue
        if "{" in body:
            note("selector", "nested CSS rules are not evaluated")
            continue
        order += 1
        decls = parse_declarations(body)
        for selector in (s.strip() for s in prelude.split(",")):
            simple = _SIMPLE.match(selector) if len(selector) < 200 else None
            if selector == ":root":
                rules.append(Rule(selector, (0, 1, 0), order, decls))
            elif simple is not None and selector:
                has_tag = bool(simple.group(1) and simple.group(1) != "*")
                rules.append(Rule(selector, (selector.count("#"), selector.count("."), int(has_tag)),
                                  order, decls))
            elif selector:
                note("selector", "selectors with combinators, pseudo-classes or attributes are not "
                                 "evaluated; colours, contrast and spacing they set were not checked")
    return rules


def selector_matches(selector: str, tag: str, classes: set[str], element_id: str | None) -> bool:
    if selector == ":root":
        return tag == "html"
    simple = _SIMPLE.match(selector)
    if simple is None:
        return False
    want = simple.group(1)
    if want and want != "*" and want.lower() != tag:
        return False
    for part in re.findall(r"[.#][\w-]+", simple.group(2)):
        if (part[0] == "." and part[1:] not in classes) or (part[0] == "#" and part[1:] != element_id):
            return False
    return True
