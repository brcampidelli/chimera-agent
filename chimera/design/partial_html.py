"""Extract a complete prefix of an ``html`` JSON string from a streamed model reply.

The HTML flow writes the page inside an operation: ``artboard.add`` carries it as
``artboard.html`` and ``artboard.set_html`` as ``html``. Both end in a JSON string keyed ``"html"``,
which is what :func:`partial_html` follows; :func:`partial_target` also names the artboard the
operation is about, so the preview can say which screen it is drawing.
"""

from __future__ import annotations

import re

_KEY = re.compile(r'"html"\s*:\s*"')
_OP = re.compile(r'"type"\s*:\s*"artboard\.(add|set_html)"')
_ID_VALUE = r'"([^"' + chr(92) * 2 + r']{1,128})"'  # no quote or backslash: a plain id, never an escape
_ADD_ID = re.compile(r'"artboard"\s*:\s*\{[^{}]*?"id"\s*:\s*' + _ID_VALUE)
_SET_ID = re.compile(r'"artboardId"\s*:\s*' + _ID_VALUE)
_SIMPLE = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_REPLACEMENT = "�"
_ESCAPE_U = chr(92) + "u"  # backslash + u, built so no editor or heredoc rewrites it


def _hex4(text: str, at: int) -> int | None:
    digits = text[at : at + 4]
    if len(digits) < 4 or any(c not in "0123456789abcdefABCDEF" for c in digits):
        return None
    return int(digits, 16)


def partial_html(buffer: str) -> str | None:
    """Return the decoded prefix of the last ``html`` string, even before its JSON quote closes.

    A trailing incomplete JSON escape is omitted so the caller never displays a half-character,
    and a high surrogate waits for its low half (``\\ud83d`` + ``\\ude00`` is one character). The
    result is always encodable as UTF-8: a lone surrogate becomes U+FFFD. Invalid escapes end the
    usable prefix rather than being interpreted permissively.
    """
    found = _last_html_start(buffer)
    if found is None:
        return None
    return _decode_from(buffer, found.end())


def _last_html_start(buffer: str) -> re.Match[str] | None:
    matches = list(_KEY.finditer(buffer))
    return matches[-1] if matches else None


def partial_target(buffer: str) -> tuple[str | None, str | None]:
    """``(artboard id, html prefix)`` of the operation whose ``html`` is being written.

    The id is ``None`` when the reply has not said it before the HTML (the model may put ``type``
    or the id after it); the caller then falls back to its own default. Only ids of the two
    operations that carry a whole page count, so an id from an unrelated earlier operation is never
    attributed to this HTML.
    """
    found = _last_html_start(buffer)
    if found is None:
        return None, None
    html = _decode_from(buffer, found.end())
    ops = list(_OP.finditer(buffer, 0, found.start()))
    if not ops:
        return None, html
    op = ops[-1]
    head = buffer[op.start() : found.start()]
    if '"type"' in head[len(op.group(0)) :] or _KEY.search(head):
        return None, html  # another operation began after this `type`: its id is not ours
    pattern = _ADD_ID if op.group(1) == "add" else _SET_ID
    ident = pattern.search(head)
    return (ident.group(1) if ident else None), html


def _decode_from(buffer: str, index: int) -> str:
    output: list[str] = []
    while index < len(buffer):
        char = buffer[index]
        index += 1
        if char == '"':
            break
        if char != "\\":
            output.append(char)
            continue
        if index >= len(buffer):
            break
        escaped = buffer[index]
        index += 1
        if escaped in _SIMPLE:
            output.append(_SIMPLE[escaped])
            continue
        if escaped != "u":
            break
        code = _hex4(buffer, index)
        if code is None:
            break  # cut mid-escape, or not hex: stop at the last whole character
        index += 4
        if 0xDC00 <= code <= 0xDFFF:
            output.append(_REPLACEMENT)
        elif 0xD800 <= code <= 0xDBFF:
            rest = buffer[index : index + 6]
            hex_digits = "0123456789abcdefABCDEF"
            if len(rest) < 6 and _ESCAPE_U.startswith(rest[:2]) and all(c in hex_digits for c in rest[2:]):
                break  # the low half may still be arriving; never show half a pair
            low = _hex4(buffer, index + 2) if rest[:2] == _ESCAPE_U else None
            if low is not None and 0xDC00 <= low <= 0xDFFF:
                output.append(chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)))
                index += 6
            else:
                output.append(_REPLACEMENT)
        else:
            output.append(chr(code))
    return "".join(output)
