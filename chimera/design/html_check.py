"""What an HTML artboard says about colour, type, spacing and legibility, measured from its own CSS.

The node checks in :mod:`.export_html` walk ``board.node_ids``, and an HTML artboard has none: the
page *is* the artboard. Before this module the walk returned an empty list, and an empty list read as
"nothing wrong" when it meant "nothing measured". This reads the ``<style>`` blocks and ``style``
attributes the artboard validator already vetted, cascades them over a small element tree and reports
in the :class:`TokenIssue` shape the node checks use.

The contract is **a measured fact or "not checked"**: never a clean result for something that was not
modelled, never an exception. It runs on the server over model-written text, so parsing and expansion
are linear and budgeted (:class:`~chimera.design.html_css.Budget`); an artboard that exceeds a budget,
or whose markup leans on implied closing tags, is reported whole as not checked.

The modelled subset (anything outside it is declared, at the element or the artboard level):

* **Structure**: ``html > body > fragment``, the preview's own shape (no wrapper); a complete
  document (``<!doctype>``, ``<html>``, ``<head>``, ``<body>``) is read, and the attributes of those
  tags (``style``, ``class``, ``id``) join the cascade as the elements' own. Every non-void element is
  closed explicitly and in order; a missing, extra, implied (``<li>``, ``<p>``) or mismatched closing
  tag, content the browser would move (a ``<div>`` or text directly in ``table``/``tr``/``tbody``, a
  ``td`` outside a ``tr``), nested headings, ``a``, ``button`` or ``form``, make the artboard
  ``structure_unbalanced``. Depth and element count are capped.
* **Selectors**: ``:root``, ``*``, ``tag``, ``.class``, ``#id`` and compounds of those, comma lists.
  Anything else is ``selector`` / ``at_rule``.
* **Cascade**: ``!important``, specificity, source order, inline style, presentation attributes
  (``fill``, ``stroke``, ``stop-color``). A declaration using ``inherit``, ``initial``, ``unset``,
  ``revert`` or the ``all`` property taints that element: its colour, size, weight and background are
  unknown, and so are its descendants'.
* **Variables**: custom properties inherit; ``var()`` is resolved with the element's own values. A
  custom property whose value uses ``var()`` (an alias) is not resolved: whatever uses it is unknown.
* **Colour**: hex, ``rgb()/rgba()`` (all commas, or all spaces with ``/`` before alpha; any other mix
  is invalid, as in the browser), the 148 named colours, ``transparent``. Unreadable colours are
  ``color_unresolved``. The page background is ``board.background`` (opaque), painted over by what
  ``html`` and ``body`` declare; a translucent artboard background, an image or gradient behind text,
  and a background on a ``display:contents`` element are ``background_alpha`` /
  ``contrast_unmeasurable`` / ``display_contents``.
* **Type**: px, pt, rem and em sizes, token sizes through ``--ds-font-*``, weights; browser defaults
  for headings, ``small``, ``b``/``strong``/``th``. An unreadable size is held to 4.5:1.
* **Text colour defaults**: a link without its own colour, ``mark`` and form controls without their
  own colour and background take the browser's, so they and their descendants are ``ua_default`` /
  ``form_control``; text with no colour anywhere is not guessed (the preview and the export start from
  different defaults).
* **Visibility**: ``hidden``, ``display:none`` and ``visibility:hidden`` are modelled; ``hidden`` with a
  declared ``display``, ``opacity``, ``filter``, ``transform``, ``clip`` and ``clip-path`` are not, and
  the text under them is "visibility not checked", neither measured nor listed as visible.
* **Spacing and fonts**: px/rem/pt lengths and font families against the tokens; ``%``, ``em``,
  ``calc()``, the ``font`` shorthand and invalid numbers are declared.

Limits, stated: token colours are compared by RGB, ignoring alpha; the artboard background is assumed
to be what the preview shows behind the page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from chimera.design.export_html import TokenIssue, Unchecked
from chimera.design.html_css import (
    ALIAS,
    NUM,
    UNKNOWN,
    Budget,
    Decl,
    NotChecked,
    Rule,
    Scope,
    TooComplex,
    Unbalanced,
    drops_declaration,
    expand,
    parse_color,
    parse_css,
    parse_declarations,
    parse_length,
    selector_matches,
    split_values,
    substitute,
)
from chimera.design.html_source import tokens_css
from chimera.design.model import Artboard, DesignTokens
from chimera.design.tokens import AA_LARGE, AA_NORMAL, composite, contrast_ratio

MAX_ELEMENTS = 4000
MAX_DEPTH = 200
_VOID = frozenset(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                   "param", "source", "track", "wbr"])
_DOCUMENT_TAGS = frozenset(["html", "head", "body"])
_NO_TEXT = frozenset(["style", "script", "title", "template"])
_FORM = frozenset(["input", "textarea", "select", "option", "button"])
_CONTENT = frozenset(["img", "svg", "canvas", "video", "picture", "input", "textarea", "select", "button"])
_P_CLOSERS = frozenset(
    ["address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset", "figcaption", "figure",
     "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "main", "menu", "nav",
     "ol", "p", "pre", "section", "table", "ul"])
_OPTIONAL_END = frozenset(["li", "dt", "dd", "tr", "td", "th", "thead", "tbody", "tfoot", "option",
                           "optgroup", "caption", "colgroup"])
_SCOPE = frozenset(["ul", "ol", "dl", "menu", "table", "select", "datalist", "template"])
_UA_SIZE = {"h1": 2.0, "h2": 1.5, "h3": 1.17, "h4": 1.0, "h5": 0.83, "h6": 0.67, "small": 0.83}
_UA_BOLD = frozenset(["h1", "h2", "h3", "h4", "h5", "h6", "b", "strong", "th"])
_SPACING = frozenset(
    ["padding", "padding-top", "padding-right", "padding-bottom", "padding-left", "padding-inline",
     "padding-block", "padding-inline-start", "padding-inline-end", "padding-block-start",
     "padding-block-end", "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
     "margin-inline", "margin-block", "margin-inline-start", "margin-inline-end", "margin-block-start",
     "margin-block-end", "gap", "row-gap", "column-gap"])
_NO_COLOUR_MODEL = frozenset(
    ["box-shadow", "text-shadow", "text-decoration", "column-rule", "border-image", "-webkit-text-stroke"])
_EFFECTS = frozenset(["opacity", "filter", "mix-blend-mode", "backdrop-filter", "-webkit-text-fill-color",
                      "transform", "clip", "clip-path"])
_KEYWORDS = frozenset(["inherit", "initial", "unset", "revert", "revert-layer"])
_BUTTON_TYPES = frozenset(["submit", "reset", "button", "image"])
_NO_TEXT_INPUT = frozenset(["hidden", "checkbox", "radio", "file", "range", "color"])
_HEADINGS = frozenset(["h1", "h2", "h3", "h4", "h5", "h6"])
_ONCE_OPEN = frozenset(["a", "button", "form"])
_ONLY_CHILDREN = {
    "table": frozenset(["caption", "colgroup", "thead", "tbody", "tfoot", "tr", "style", "script", "template"]),
    "thead": frozenset(["tr", "style", "script", "template"]),
    "tbody": frozenset(["tr", "style", "script", "template"]),
    "tfoot": frozenset(["tr", "style", "script", "template"]),
    "tr": frozenset(["td", "th", "style", "script", "template"]),
    "colgroup": frozenset(["col", "template"]),
    "select": frozenset(["option", "optgroup", "script", "template"]),
    "optgroup": frozenset(["option", "script", "template"]),
}
_ONLY_UNDER = {
    "td": frozenset(["tr"]), "th": frozenset(["tr"]), "tr": frozenset(["table", "thead", "tbody", "tfoot"]),
    "thead": frozenset(["table"]), "tbody": frozenset(["table"]), "tfoot": frozenset(["table"]),
    "caption": frozenset(["table"]), "colgroup": frozenset(["table"]), "col": frozenset(["colgroup"]),
}
_HEAD_DISPLAY = ("a display declared on <head> or inside it makes head content visible, which the checker "
                 "does not model; it was not checked")
_Key = tuple[bool, bool, tuple[int, int, int], int, int]


@dataclass
class _El:
    tag: str
    attrs: dict[str, str]
    parent: _El | None
    texts: list[str] = field(default_factory=list)
    own: dict[str, str] = field(default_factory=dict)
    customs: Scope = field(default_factory=lambda: Scope(None))
    color: str | None = None
    bg: str | None = None
    size: float | None = 16.0
    weight: int | None = 400
    display_none: bool = False
    hidden: bool = False
    vis_unknown: bool = False
    blocked: bool = False
    in_svg: bool = False
    tainted: bool = False
    in_head: bool = False

    @property
    def has_text(self) -> bool:
        if self.tag == "input":
            kind = self.attrs.get("type", "text").lower()
            return kind not in _NO_TEXT_INPUT and bool(
                self.attrs.get("value", "").strip() or self.attrs.get("placeholder", "").strip())
        return any(self.texts)


class _Notes:
    def __init__(self, artboard_id: str) -> None:
        self.artboard_id = artboard_id
        self.reasons: dict[str, str] = {}

    def add(self, reason: str, message: str) -> None:
        self.reasons.setdefault(reason, message)

    def entries(self) -> list[Unchecked]:
        return [Unchecked(self.artboard_id, reason, message) for reason, message in self.reasons.items()]


class _Tree(HTMLParser):
    """``html > body > fragment``, built only from markup whose structure the browser would agree on."""

    def __init__(self, notes: _Notes, budget: Budget) -> None:
        super().__init__(convert_charrefs=True)
        self.notes, self.budget = notes, budget
        html = _El("html", {}, None)
        body = _El("body", {}, html)
        self.elements = [html, body]
        self.css: list[str] = []
        self._stack = [body]
        self._open: dict[str, int] = {}
        self._seen: set[str] = set()
        self._in_head = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DOCUMENT_TAGS:
            self._document_tag(tag, attrs)
            return
        self.budget.spend()
        self._refuse_implied_close(tag)
        el = _El(tag, {k.lower(): v or "" for k, v in attrs}, self._stack[-1])
        el.in_head = self._in_head
        self.elements.append(el)
        if len(self.elements) > MAX_ELEMENTS:
            raise TooComplex
        if tag not in _VOID:
            self._stack.append(el)
            self._open[tag] = self._open.get(tag, 0) + 1
            if len(self._stack) > MAX_DEPTH:
                raise TooComplex

    def _document_tag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """``<html>``, ``<head>``, ``<body>``: a complete document. Attributes join the synthetic element."""
        if tag in self._seen or len(self._stack) > 1:
            raise Unbalanced
        self._seen.add(tag)
        self._in_head = tag == "head"
        if tag == "head" and "display" in (dict(attrs).get("style") or "").lower():
            self.notes.add("head_display", _HEAD_DISPLAY)
        if tag != "head":
            target = self.elements[0 if tag == "html" else 1]
            target.attrs.update({k.lower(): v or "" for k, v in attrs})

    def _refuse_implied_close(self, tag: str) -> None:
        parent = self._stack[-1].tag
        if tag in _HEADINGS and parent in _HEADINGS:
            raise Unbalanced
        if tag in _ONCE_OPEN and self._open.get(tag):
            raise Unbalanced
        if (parent in _ONLY_CHILDREN and tag not in _ONLY_CHILDREN[parent]) or (
                tag in _ONLY_UNDER and parent not in _ONLY_UNDER[tag]):
            raise Unbalanced  # the browser would foster-parent or drop it
        if tag in _P_CLOSERS and self._open.get("p"):
            raise Unbalanced
        if tag in _OPTIONAL_END and self._open.get(tag):
            for el in reversed(self._stack[1:]):
                if el.tag in _SCOPE:
                    break
                if el.tag == tag:
                    raise Unbalanced

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        # ``<div/>`` opens a div in HTML; only svg content is genuinely self-closing.
        if tag not in _VOID | _DOCUMENT_TAGS and self._open.get("svg") and self._stack[-1].tag == tag:
            self._pop()

    def _pop(self) -> None:
        self._open[self._stack.pop().tag] -= 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _DOCUMENT_TAGS | _VOID:
            self._in_head = self._in_head and tag != "head"
            return
        if len(self._stack) < 2 or self._stack[-1].tag != tag:
            raise Unbalanced
        self._pop()

    def handle_data(self, data: str) -> None:
        top = self._stack[-1]
        if top.tag == "style":
            self.css.append(data)
        elif data.strip() and top.tag not in _NO_TEXT:
            if top.tag in _ONLY_CHILDREN:
                raise Unbalanced  # text directly in a table is foster-parented out of it
            top.texts.append(data.strip())

    def finish(self) -> None:
        self.close()
        if len(self._stack) > 1:
            raise Unbalanced


def _entries(el: _El, rules: list[Rule], budget: Budget) -> list[tuple[_Key, Decl]]:
    budget.spend(5 * len(rules) + 1)
    out: list[tuple[_Key, Decl]] = [
        ((False, False, (-1, 0, 0), -1, 0), Decl(a, el.attrs[a], False))
        for a in ("fill", "stroke", "stop-color") if a in el.attrs]
    classes, element_id = set(el.attrs.get("class", "").split()), el.attrs.get("id")
    for rule in rules:
        if selector_matches(rule.selector, el.tag, classes, element_id):
            budget.spend(10 * len(rule.decls))
            out += [((d.important, False, rule.specificity, rule.order, i), d) for i, d in enumerate(rule.decls)]
    inline = parse_declarations(el.attrs.get("style", ""))
    out += [((d.important, True, (0, 0, 0), 0, i), d) for i, d in enumerate(inline)]
    out.sort(key=lambda entry: entry[0])
    return out


def _compute(el: _El, rules: list[Rule], root_px: float | None, notes: _Notes, budget: Budget) -> None:
    parent = el.parent
    el.customs = Scope(parent.customs if parent else None)
    entries = _entries(el, rules, budget)
    memo: dict[str, str | None] = {}
    for _key, decl in entries:
        if decl.prop.startswith("--"):
            alias = "var(" in decl.value.lower()
            if alias:
                notes.add("variable_alias", "variables defined through other variables were not resolved; "
                                            "whatever uses them was not checked")
            el.customs.own[decl.prop] = ALIAS if alias else decl.value
    for _key, decl in entries:
        if not decl.prop.startswith("--"):
            value = substitute(decl.value, el.customs, budget, memo)
            if value is not None and "var(" not in decl.value.lower() and drops_declaration(decl.prop, value):
                continue  # invalid at parse time: the browser keeps the earlier declaration
            el.own.update(expand(decl.prop, value, notes.add))
    budget.spend(len(el.customs.own) + len(el.own) + 1)
    if el.own.get("display", "").strip().lower() == "contents" and (
            el.own.get("background-image", "none") != "none" or el.own.get("background-color", "none") != "none"):
        notes.add("display_contents", "an element with display:contents paints no background; what it would "
                                      "have painted was not measured")
    if el.in_head and "display" in el.own:
        notes.add("head_display", _HEAD_DISPLAY)
    el.tainted = "all" in el.own or any(v.strip().lower() in _KEYWORDS for v in el.own.values())
    if el.tainted:
        notes.add("keyword_unmodelled", "inherit, initial, unset, revert and the all property are not "
                                        "modelled; text styled with them was not checked")
    _compute_text_style(el, parent, root_px, notes)
    _compute_visibility(el, parent)


def _compute_text_style(el: _El, parent: _El | None, root_px: float | None, notes: _Notes) -> None:
    up_color, up_size, up_weight = (parent.color, parent.size, parent.weight) if parent else (None, 16.0, 400)
    own_color = el.own.get("color")
    if own_color is None:
        el.color = up_color
    else:
        parsed = None if own_color == UNKNOWN else parse_color(own_color)
        el.color = parsed if parsed not in (None, "none", "current") else None
    own_size = el.own.get("font-size")
    if own_size is None:
        el.size = None if up_size is None else up_size * _UA_SIZE.get(el.tag, 1.0)
    else:
        el.size = _font_size(own_size, up_size, root_px)
    own_weight = el.own.get("font-weight", "").strip().lower()
    if not own_weight:
        el.weight = 700 if el.tag in _UA_BOLD else up_weight
    elif own_weight in ("bold", "normal"):
        el.weight = 700 if own_weight == "bold" else 400
    else:
        el.weight = int(own_weight) if re.fullmatch(r"\d{1,4}", own_weight) else None
    if (el.tag == "a" and "href" in el.attrs and "color" not in el.own) or (
            el.tag in _FORM | {"mark"} and "color" not in el.own):
        el.color = None  # the browser's own colour for these, which the extractor does not know
    if el.tainted:
        el.color, el.size, el.weight = None, None, None
    el.in_svg = el.tag == "svg" or bool(parent and parent.in_svg)
    el.blocked = bool(parent and parent.blocked) or any(
        prop in el.own and el.own[prop].strip().lower() not in ("1", "none", "normal", "auto") for prop in _EFFECTS)
    if el.blocked:
        notes.add("effect_unmodelled", "opacity, filters, transforms and clipping change what is visible and "
                                       "are not modelled; text under them was not checked")
    for prop in _NO_COLOUR_MODEL & el.own.keys():
        notes.add("property_unmodelled", f"colours in {prop} were not compared with the tokens")


def _font_size(raw: str, parent_px: float | None, root_px: float | None) -> float | None:
    raw = raw.strip().lower()
    if raw == UNKNOWN or len(raw) > 40:
        return None
    if (m := re.fullmatch(rf"({NUM})(em|%)", raw)) is not None:
        scale = float(m.group(1)) / (100 if m.group(2) == "%" else 1)
        return None if parent_px is None else scale * parent_px
    return parse_length(raw, root_px)


def _compute_visibility(el: _El, parent: _El | None) -> None:
    declared_display = el.own.get("display", "").strip().lower()
    always = el.tag in ("style", "script", "head", "title", "template", "meta", "link")
    attr_hidden = "hidden" in el.attrs and not declared_display
    el.display_none = (bool(parent and parent.display_none) or always or attr_hidden
                       or declared_display == "none"
                       or (el.tag == "input" and el.attrs.get("type") == "hidden"))
    el.vis_unknown = bool(parent and parent.vis_unknown) or ("hidden" in el.attrs and bool(declared_display))
    own = el.own.get("visibility", "").strip().lower()
    if own in ("hidden", "collapse"):
        invisible = True
    elif own == "visible":
        invisible = False
    else:
        invisible = bool(parent and parent.hidden and not parent.display_none)
    el.hidden = el.display_none or invisible


def _background(el: _El, root: str | None) -> str | None:
    """The opaque colour behind ``el``, or None when an image, gradient or unreadable colour is."""
    if el.tainted:
        return None
    parent_bg = el.parent.bg if el.parent else root
    colour = el.own.get("background-color")
    if el.own.get("background-image", "none") != "none":
        return None
    if el.own.get("display", "").strip().lower() == "contents" and colour not in (None, "none"):
        return None
    if el.tag in _FORM | {"mark"} and colour is None:
        return None
    if colour is None:
        return parent_bg
    parsed = None if colour == UNKNOWN else parse_color(colour)
    if parsed is None or parsed == "current":
        return None
    if parsed == "none":
        return parent_bg
    if parent_bg is None:
        return None
    r, g, b = composite(parsed, parent_bg)
    return f"#{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}"


def _root_background(board: Artboard, notes: _Notes) -> str | None:
    """What sits behind the page: ``board.background``, assumed opaque, as the preview shows it."""
    parsed = parse_color(board.background)
    if parsed is None or parsed in ("none", "current") or (len(parsed) == 9 and parsed[7:] != "FF"):
        notes.add("background_alpha", "the artboard background is transparent, so contrast against what is "
                                      "behind it was not measured")
        return None
    return parsed[:7]


class _Collector:
    """The findings of one artboard, deduplicated by what they are about."""

    def __init__(self, board: Artboard, tokens: DesignTokens, notes: _Notes) -> None:
        self.board, self.notes = board, notes
        self.palette = {c.upper()[:7] for c in tokens.colors.values()}
        self.fonts = {t.family.split(",")[0].strip().strip("\"'").lower() for t in tokens.typography.values()}
        self.spaces = {float(v) for v in tokens.spacing.values()}
        self.issues: list[TokenIssue] = []
        self._seen: set[tuple[str, str]] = set()

    def add(self, kind: str, key: str, message: str) -> None:
        if (kind, key) not in self._seen:
            self._seen.add((kind, key))
            self.issues.append(TokenIssue(kind, None, message, self.board.id))  # type: ignore[arg-type]

    def check_element(self, el: _El, root_px: float | None) -> None:
        for prop, value in el.own.items():
            if prop.endswith("color") or prop in ("fill", "stroke"):
                self._colour(el, prop, value)
            elif prop == "font-family":
                self._font(el, value)
            elif prop in _SPACING:
                self._spacing(el, prop, value, root_px)

    def _colour(self, el: _El, prop: str, value: str) -> None:
        parsed = None if value == UNKNOWN else parse_color(value)
        if parsed is None:
            self.notes.add("color_unresolved", "colours written as hsl(), color-mix(), gradients, system "
                                               "colours or unresolved variables were not compared with the tokens")
        elif parsed not in ("none", "current") and self.palette and parsed[:7] not in self.palette:
            self.add("color_not_in_tokens", parsed[:7],
                     f"{prop} {parsed[:7]} is not a token colour (used on <{el.tag}>)")

    def _font(self, el: _El, value: str) -> None:
        first = value.split(",")[0].strip().strip("\"'").lower()
        if value == UNKNOWN:
            self.notes.add("font_unresolved", "font families written through unresolved variables were not "
                                              "compared with the tokens")
        elif first not in _KEYWORDS and first not in self.fonts:
            self.add("font_not_from_tokens", first, f"font {first!r} is not a type token family (used on <{el.tag}>)")

    def _spacing(self, el: _El, prop: str, value: str, root_px: float | None) -> None:
        for part in split_values(value) if value != UNKNOWN else [UNKNOWN]:
            if part.lower() in ("auto", "normal") or part.lower() in _KEYWORDS:
                continue
            px = None if part == UNKNOWN else parse_length(part, root_px)
            if px is None:
                self.notes.add("spacing_unresolved", "spacings in %, em, vw, calc() or unresolved variables, "
                                                     "and values that are not valid lengths, were not compared "
                                                     "with the tokens")
            elif px != 0 and abs(px) not in self.spaces:
                self.add("spacing_not_in_tokens", f"{abs(px):g}",
                         f"{prop} {abs(px):g}px is not a spacing token (used on <{el.tag}>)")


def _unmeasurable(el: _El) -> tuple[str, str] | None:
    if el.in_svg:
        return "svg_text", "text inside svg is painted with fill and was not measured"
    if el.vis_unknown or el.blocked:
        return "visibility_unchecked", "text under an unmodelled visibility rule was not measured"
    if el.tag == "input" and (el.attrs.get("type", "").lower() in _BUTTON_TYPES or el.attrs.get("placeholder")):
        return "form_control", "button inputs and placeholders use browser colours and were not measured"
    if el.color is None or el.bg is None:
        if el.tag in _FORM:
            return "form_control", ("form controls without their own colour and background use browser "
                                    "defaults, so their contrast was not measured")
        if el.tag in ("a", "mark") or _inside_ua_default(el):
            return "ua_default", ("links, <mark> and what sits inside them take the browser's colours; "
                                  "not measured")
        return "contrast_unmeasurable", ("text over an image, a gradient, an unset, inherited-by-keyword or "
                                         "unreadable colour could not have its contrast measured")
    return None


def _inside_ua_default(el: _El) -> bool:
    cursor = el.parent
    while cursor is not None:
        if (cursor.tag == "a" and "href" in cursor.attrs and "color" not in cursor.own) or cursor.tag == "mark":
            return True
        cursor = cursor.parent
    return False


def _contrast(els: list[_El], out: _Collector) -> int:
    measured = 0
    for el in els:
        if el.hidden or not el.has_text:
            continue
        measured += 1
        gap = _unmeasurable(el)
        if gap is not None:
            out.notes.add(*gap)
            continue
        if el.color is None or el.bg is None:  # _unmeasurable has already said so
            continue
        large = el.size is not None and (el.size >= 24 or (el.size >= 18.66 and (el.weight or 0) >= 700))
        if el.size is None:
            out.notes.add("font_size_unresolved", "text whose size could not be read was held to the strict "
                                                  "4.5:1 threshold")
        needed = AA_LARGE if large else AA_NORMAL
        ratio = contrast_ratio(el.color, el.bg)
        if ratio < needed:
            out.add("low_contrast", f"{el.color}|{el.bg}|{large}",
                    f"<{el.tag}> text {el.color[:7]} on {el.bg}: contrast {ratio:.2f}:1, needs {needed}:1")
    return measured


def _analyse(board: Artboard, tokens: DesignTokens, notes: _Notes) -> tuple[list[_El], float | None]:
    budget = Budget()
    tree = _Tree(notes, budget)
    tree.feed(board.html or "")
    tree.finish()
    rules = parse_css(tokens_css(tokens) + "\n" + "\n".join(tree.css), notes.add, budget)
    if any(r.selector == "head" and any(d.prop == "display" for d in r.decls) for r in rules):
        notes.add("head_display", _HEAD_DISPLAY)
    root_px: float | None = 16.0
    root_bg = _root_background(board, notes)
    # Every _entries call charges this amount before selector matching, even for rules that do
    # not match. If that unavoidable work alone exceeds the remaining shared budget, the result
    # must be too_complex; avoid processing declarations merely to reach the same refusal.
    if len(tree.elements) * (5 * len(rules) + 1) > budget.left:
        raise TooComplex
    for el in tree.elements:
        _compute(el, rules, root_px, notes, budget)
        if el.tag == "html":
            root_px = el.size
        el.bg = _background(el, root_bg)
    return tree.elements, root_px


def check_html_artboard(board: Artboard, tokens: DesignTokens) -> tuple[list[TokenIssue], list[Unchecked]]:
    """Findings and declared gaps for one HTML artboard; ``([], [])`` for one that has no HTML."""
    if board.html is None:
        return [], []
    notes = _Notes(board.id)
    try:
        els, root_px = _analyse(board, tokens, notes)
        out = _Collector(board, tokens, notes)
        if not out.palette:
            notes.add("no_palette", "the design has no colour tokens to compare the artboard's colours with")
        for el in els:
            out.check_element(el, root_px)
        if _contrast(els, out) == 0:
            has_content = any(el.tag in _CONTENT for el in els)
            notes.add("no_text" if has_content else "empty",
                      "the artboard has no visible text, so contrast was not measured" if has_content
                      else "the artboard has no content to check")
        return out.issues, notes.entries()
    except NotChecked as gap:
        return [], [Unchecked(board.id, gap.reason, gap.message)]
    except Exception:  # the contract is "not checked", never an error that hides the other artboards
        return [], [Unchecked(board.id, "checker_error", "this artboard could not be read by the checker")]


def summarize_html(board: Artboard, tokens: DesignTokens, *, max_texts: int = 12) -> dict[str, object]:
    """Facts about an HTML artboard for a prompt: structure counts, visible texts, colours in use.

    Only text the checker could verify is visible is listed in ``texts``. Hidden text is counted;
    text under ``opacity``, ``filter``, ``transform``, ``clip`` or a ``hidden``/``display`` conflict is
    listed apart as ``visibility_unchecked_texts``. Runs under the same isolation as the check.
    """
    if board.html is None:
        return {}
    try:
        els, _ = _analyse(board, tokens, _Notes(board.id))
        return _summary(board, els, max_texts)
    except NotChecked as gap:
        return {"artboard_id": board.id, "not_checked": gap.reason}
    except Exception:
        return {"artboard_id": board.id, "not_checked": "checker_error"}


def _summary(board: Artboard, els: list[_El], max_texts: int) -> dict[str, object]:
    counts: dict[str, int] = {}
    colours: set[str] = set()
    texts: list[str] = []
    unchecked: list[str] = []
    hidden = 0
    for el in els[2:]:
        label = " ".join(" ".join(el.texts or [el.attrs.get("value", "") or el.attrs.get("placeholder", "")]).split())[:80]
        if el.has_text and (el.hidden or _aria_hidden(el)):
            hidden += 1
        elif el.has_text and (el.vis_unknown or el.blocked):
            unchecked.append(label)
        elif el.has_text:
            texts.append(label)
        if not el.display_none and el.tag in ("h1", "h2", "h3", "h4", "h5", "h6", "button", "a", "img", "input", "label", "p"):
            counts[el.tag] = counts.get(el.tag, 0) + 1
        for prop, value in el.own.items():
            if prop.endswith("color") or prop in ("fill", "stroke"):
                parsed = None if value == UNKNOWN else parse_color(value)
                if parsed not in (None, "none", "current"):
                    colours.add(str(parsed)[:7])
    return {"artboard_id": board.id, "elements": len(els) - 2, "counts": counts,
            "colours_used": sorted(colours)[:30], "texts": texts[:max_texts], "hidden_texts": hidden,
            "visibility_unchecked_texts": unchecked[:max_texts]}


def _aria_hidden(el: _El) -> bool:
    cursor: _El | None = el
    while cursor is not None:
        if cursor.attrs.get("aria-hidden", "").lower() == "true":
            return True
        cursor = cursor.parent
    return False
