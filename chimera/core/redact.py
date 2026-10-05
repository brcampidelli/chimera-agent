"""Keep secrets out of anything written to disk.

The trace records what each tool was called with and what came back. On the 24/7 path those tools
talk to a broker, a database and a payment processor, so the text is exactly the kind that carries a
bearer token — and the trace is a file that lives for weeks on a machine nobody logs into.

**What this guarantees, and what it does not.** It guarantees that a secret *this process knows about*
never reaches the file *verbatim*: every environment value whose variable name looks like a
credential is replaced, as a literal string, wherever it appears — and so are the encodings of it
that a model reaches for unprompted (7 of 9 frontier models disguised a credential to "help" another
agent, and a monitor that did not know the credential missed most of them; arXiv 2609.39050). This
module DOES know the credential, so it computes them (study 30, S30-32, measured by
`bench/encoded_secrets`):

* base64, standard and URL-safe, alone or inside a longer blob at any byte offset (less at most one
  partial character at each end of the run) — **on one line**: base64 wrapped at 76 columns, as the
  `base64` command and PEM files write it, is a secret split across lines;
* hex in either case, contiguous or with a space, colon, comma or semicolon between bytes
  (``bytes.hex(" ")``, ``bytes.hex(":")``, `xxd`-style groups);
* decimal character codes separated by spaces, commas or semicolons;
* escapes: ``\\x70``, ``0x70``, ``\\u0070``, ``&#x70;``, ``&#112;`` (one kind or mixed);
* the reversed string;
* percent-encoding with any choice of which characters are escaped, in either case (``quote`` with
  its default ``safe="/"``, ``quote_plus``, ``encodeURIComponent``).

A secret that is not ASCII is matched both as UTF-8 bytes (``.encode()``) and as code points
(``ord()``, ``\\u``, ``&#``). That list is the whole of the guarantee. A secret split across lines
(wrapped base64 included), ROT13'd, compressed, keyed or encoded twice still passes through — the
corpus measures those at 0%, and says so rather than leaving it out; so do a code point outside the
Basic Multilingual Plane written as a JSON surrogate pair, and an escape mixed with plain characters
(``pl\\u0061in``). This text once called the literal match "a complete guarantee over the set that
matters most" (study 30, S30-21(c)); it never was, and the list above is not either.

It does **not** guarantee that no secret ever survives. A token minted at runtime by a remote API, a
password typed into a prompt, a key in a file the agent read — none of those are in the environment
and no pattern list finds all of them. The regexes below catch the shapes that are cheap to catch;
they are a second net, not the guarantee. Anyone reading this should treat a trace as sensitive,
which is why it is also size-capped and rotated rather than kept forever.
"""

from __future__ import annotations

import base64
import os
import re
from collections.abc import Callable
from functools import lru_cache
from urllib.parse import unquote_to_bytes

#: What makes a variable NAME a credential. One list, one meaning of "secret", and it lives here
#: rather than in the sandbox because this is the module the meaning belongs to — the sandbox strips
#: the child environment with it, and this file masks values with it.
#:
#: It used to live in `chimera.sandbox.local`, and importing it from there is what made THIS module
#: drag in the sandbox subsystem, `chimera.proc`, `chimera.telemetry` and finally `rich`. Nothing
#: noticed until `scripts/scan_artifact.py` — which imports the patterns below to check a built
#: wheel — ran for the first time in the publish job, where the package's runtime dependencies are
#: deliberately not installed, and died on `No module named 'rich'` before scanning a single byte.
#: Keep this module importable with the standard library alone.
_SECRET_MARKERS = ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL", "PRIVATE_KEY")

MASK = "[redacted]"

#: Below this, a value is too short to be a credential and too likely to be a common word — an env
#: var set to `1` or `true` would otherwise mask every digit in the file.
_MIN_SECRET_LEN = 8

#: Names that make a query parameter or a header a credential. Matched on the NAME, so the value
#: never has to be recognised — which is the point: a token minted by a remote API has no shape a
#: list can hold, and the parameter it arrives in does.
_SENSITIVE_NAME = r"(?:api[_-]?key|apikey|access[_-]?token|auth|authorization|token|secret|password|passwd|pwd|sig|signature|session)"

#: Structural leaks — a secret given away by WHERE it sits rather than by what it looks like.
#:
#: Six of seven shapes measured against the shape list below survived it intact: URL userinfo, a
#: query parameter named `api_key`, an `Authorization` header, a cookie, a database DSN, and a
#: Discord webhook path. None of them needs to be recognised; the place identifies them. That
#: matters here specifically — delivery on the 24/7 deployment goes through a webhook whose URL IS
#: the secret, and `steplog` writes every tool argument and result through this function.
#:
#: Every one keeps its context. `api_key=[redacted]` says a key was sent; a line reduced to
#: `[redacted]` says only that a request happened, and the file exists to answer more than that.
_PLACES = (
    # scheme://user:SECRET@host — the password half of URL userinfo, never the user or the host.
    re.compile(r"(?P<keep>://[^\s:/@]+:)(?P<hide>[^\s@/]+)(?P<tail>@)"),
    # ?name=SECRET or &name=SECRET, where the NAME is what marks it.
    re.compile(rf'(?P<keep>[?&]{_SENSITIVE_NAME}=)(?P<hide>[^\s&#"\']+)', re.IGNORECASE),
    # A credential header inside a quoted argument — `curl -H 'x-api-key: …'`. First, because the
    # line-anchored form below would otherwise swallow the closing quote and everything after it.
    # This is the shape a tool observation actually carries; a header on its own line is the rarer
    # one here, and writing only that missed the case the whole change is for.
    re.compile(
        rf"(?P<keep>['\"](?:{_SENSITIVE_NAME}|x-api-key|cookie|proxy-authorization)[ \t]*:[ \t]*)"
        r"(?P<hide>[^'\"]+)(?P<tail>['\"])",
        re.IGNORECASE,
    ),
    # A credential header on its own line, to the end of it. The value may contain spaces —
    # `Authorization: Basic dXNlcjpzZW5oYQ==` is one value, not a scheme and a separate token.
    re.compile(
        rf"(?P<keep>^[ \t]*(?:{_SENSITIVE_NAME}|x-api-key|cookie|proxy-authorization)[ \t]*:[ \t]*)"
        r"(?P<hide>\S.*)$",
        re.IGNORECASE | re.MULTILINE,
    ),
    # A chat webhook path: the id/token tail is the credential, and the host says which service.
    re.compile(
        r"(?P<keep>https://(?:discord(?:app)?\.com|hooks\.slack\.com)/[^\s?]*?/)(?P<hide>[A-Za-z0-9_-]{16,})",
        re.IGNORECASE,
    ),
)

#: High-confidence shapes, as a second net. Deliberately narrow: a greedy pattern that redacted
#: ordinary output would make the trace useless, and a useless trace gets turned off.
_PATTERNS = (
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"),  # OpenAI-style
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub
    re.compile(r"\bsbp_[A-Za-z0-9]{20,}"),  # Supabase personal token
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT
)


def known_secrets() -> list[str]:
    """Every environment value whose NAME marks it a credential, longest first.

    Longest first matters: when one secret is a prefix of another (a base token and the same token
    with a suffix), replacing the short one first would leave the tail of the long one in the file,
    which looks redacted and is not.
    """
    found = [
        value
        for name, value in os.environ.items()
        if value
        and len(value) >= _MIN_SECRET_LEN
        and any(marker in name.upper() for marker in _SECRET_MARKERS)
    ]
    return sorted(set(found), key=len, reverse=True)


#: Whether :func:`redact` also masks the encoded forms of each known secret (study 30, S30-32).
#: Decided by `bench/encoded_secrets` against the rule registered before it ran (ON only with zero
#: false positives); a test holds this equal to that run's decision, so changing it means re-running
#: the corpus, not editing a constant.
MASK_ENCODED = True

#: Separators between two encoded units. Decimal codes need at least one — `115107` is not two codes,
#: it is a number — while hex digits and escapes need none (`706c…`, `\x70\x6c…`). Hex also takes a
#: colon (`bytes.hex(":")`, a MAC-style dump); `xxd` groups and `bytes.hex(" ")` are the space.
_CODE_SEP = r"[\s,;]+"
_ESCAPE_SEP = r"[\s,;]*"
_HEX_SEP = r"[\s,;:]*"

#: One escaped character: `\x70`, `0x70`, `\u0070`, `\U00000070`, `&#x70;`, `&#112;` (the HTML
#: semicolon optional, as parsers allow). Groups: hex value for the first five, decimal for the last.
_ESCAPE_UNIT = (
    # `0x70` is two digits when the next unit follows with no separator (`0x700x6c`), else up to 8.
    r"\\x(?P<x>[0-9a-f]{2})|0x(?P<o>[0-9a-f]{2}(?=0x)|[0-9a-f]{1,8})|\\u(?P<u>[0-9a-f]{4})|\\U(?P<U>[0-9a-f]{8})"
    r"|&#x(?P<hx>[0-9a-f]{1,8});?|&#(?P<hd>[0-9]{1,8});?"
)
#: The same, without names, to repeat inside a run (a name may appear once per pattern).
_ESCAPE_ANY = re.sub(r"\?P<\w+>", "", _ESCAPE_UNIT)

#: Each family is found the same way: one scan for the RUNS the family can write (a stretch of hex
#: digits, of numbers, of escapes, of a URL-ish token holding `%HH` or `+`), each run decoded to
#: the characters it stands for, and every known secret looked up in that decoding with `str.find`.
#: The first cut compiled one regex alternation per secret instead; with the corpus's 250 secrets
#: that ran for over ten minutes where this takes seconds, because `re` tries every branch at every
#: position while a run scan is linear and the lookup per run is a C substring search.
#: A run is at least `_MIN_SECRET_LEN` units long, so ordinary text rarely holds one at all.
_RUNS = {
    "hex": re.compile(r"[0-9a-f](?:" + _HEX_SEP + r"[0-9a-f]){15,}", re.IGNORECASE),
    "decimal": re.compile(r"(?<!\d)\d+(?:" + _CODE_SEP + r"\d+){7,}(?!\d)"),
    "escape": re.compile(r"(?:" + _ESCAPE_ANY + r")(?:" + _ESCAPE_SEP + r"(?:" + _ESCAPE_ANY + r")){7,}", re.IGNORECASE),
    # A token, not "a token holding %HH": that form backtracks quadratically over a long token with
    # no `%` (a megabyte of URL-safe base64). Tokens without `%` or `+` are skipped before decoding.
    "percent": re.compile(rf"[^\s\"'<>]{{{_MIN_SECRET_LEN},}}"),
}
_UNIT = {
    "hex": re.compile(r"[0-9a-f]", re.IGNORECASE),
    "decimal": re.compile(r"\d+"),
    "escape": re.compile(_ESCAPE_UNIT, re.IGNORECASE),
    "percent": re.compile(r"%[0-9a-f]{2}|.", re.IGNORECASE | re.DOTALL),
}
#: Stands in for a number that is no character (above U+10FFFF): it can never be part of a secret.
_NO_CHAR = chr(0xFFFF)


def _b64_fragments(raw: bytes, *, urlsafe: bool) -> list[str]:
    """The base64 characters that carry ONLY bits of ``raw``, at each of the three alignments.

    A secret inside a longer encoded blob — ``base64(user:secret)``, a JSON body, a file — starts at
    a byte offset the encoder never aligned for it, so its standalone base64 is not a substring of
    the blob. Encoding it behind k = 0, 1, 2 zero bytes and keeping the characters whose six bits
    all come from the secret gives the run that IS in the blob, whatever the offset. The one or two
    characters cut at each end carry at most four bits of the first and last byte.
    """
    encode = base64.urlsafe_b64encode if urlsafe else base64.b64encode
    out = []
    for k in range(3):
        enc = encode(b"\0" * k + raw).decode("ascii")
        start = -(-8 * k // 6)  # ceil: the first character with no bit of the zero prefix
        end = 8 * (k + len(raw)) // 6  # floor: the last character with no bit of the padding
        out.append(enc[start:end])
    return out


def _encoded_literals(secret: str) -> set[str]:
    """The encoded forms of one secret that are fixed strings."""
    raw = secret.encode("utf-8")
    return {
        base64.b64encode(raw).decode("ascii"),
        base64.urlsafe_b64encode(raw).decode("ascii"),
        *_b64_fragments(raw, urlsafe=False),
        *_b64_fragments(raw, urlsafe=True),
        secret[::-1],
    }


def _needles(secret: str, family: str) -> set[str]:
    """What a decoded run of ``family`` holds when it holds ``secret``.

    Two readings when the secret is not ASCII: its UTF-8 bytes (what ``.encode()``, ``\\xc3\\xa7`` and
    ``%C3%A7`` give, decoded here one byte per character) and its code points (what ``ord()``, a JSON
    ``\\u`` escape and an HTML entity give). The first cut used the bytes alone, so ``ord()`` codes of
    ``ação`` passed (study 30 review). Hex digits are matched as digits: the needle is the hex string.
    """
    raw = secret.encode("utf-8")
    if family == "hex":
        return {raw.hex()}
    return {secret, raw.decode("latin-1")}


def _decode(family: str, run: str, *, plus_is_space: bool = False) -> tuple[str, list[tuple[int, int]]]:
    """``run`` decoded to the characters its units stand for, and each unit's span in ``run``."""
    chars: list[str] = []
    spans: list[tuple[int, int]] = []
    for unit in _UNIT[family].finditer(run):
        span = unit.span()
        if family == "hex":
            chars.append(unit.group().lower())
        elif family == "decimal":
            n = int(unit.group())
            chars.append(chr(n) if n <= 0x10FFFF else _NO_CHAR)
        elif family == "escape":
            hexa = next((v for k, v in unit.groupdict().items() if v and k != "hd"), None)
            n = int(hexa, 16) if hexa else int(unit.group("hd"))
            chars.append(chr(n) if n <= 0x10FFFF else _NO_CHAR)
        else:  # percent: `%HH` is one byte; a plain character is its UTF-8 bytes, one per char
            text = " " if plus_is_space and unit.group() == "+" else unit.group()
            for byte in bytes([int(text[1:], 16)]) if len(text) == 3 and text[0] == "%" else text.encode("utf-8"):
                chars.append(chr(byte))
                spans.append(span)
            continue
        spans.append(span)
    return "".join(chars), spans


_NOT_HEX = re.compile(r"[^0-9a-f]", re.IGNORECASE)

#: The decoding of :func:`_decode` without the spans, for the two families whose runs are common in
#: logs (every word of a URL, every line of a dump). Exactly the same characters, so a run that holds
#: no needle here holds none there; the per-unit decode then runs only on a hit. On a 10 kB log with
#: 30 known secrets it cut the encoded pass from ~6.7 ms to ~2.6 ms.
def _quick_hex(run: str, _plus_is_space: bool) -> str:
    return _NOT_HEX.sub("", run).lower()


def _quick_percent(run: str, plus_is_space: bool) -> str:
    return unquote_to_bytes(run.replace("+", " ") if plus_is_space else run).decode("latin-1")


_QUICK: dict[str, Callable[[str, bool], str]] = {"hex": _quick_hex, "percent": _quick_percent}


def _family_spans(text: str, family: str, needles: tuple[str, ...]) -> list[tuple[int, int]]:
    """Every span of ``text`` where a run of ``family`` decodes to a known secret."""
    out = []
    for run in _RUNS[family].finditer(text):
        readings = [False]
        if family == "percent":
            if "%" not in run.group() and "+" not in run.group():
                continue  # plain text: the verbatim pass already had it
            if "+" in run.group():
                # `quote_plus` writes a space as `+`; `quote` writes `+` as `%2B`. Which one wrote
                # this run is unknown, so both readings are searched.
                readings.append(True)
        for plus_is_space in readings:
            quick = _QUICK.get(family)
            if quick is not None:
                plain = quick(run.group(), plus_is_space)
                if not any(n in plain for n in needles):
                    continue  # the same decoding without spans, in C: most runs hold no secret
            decoded, spans = _decode(family, run.group(), plus_is_space=plus_is_space)
            for needle in needles:
                at = decoded.find(needle)
                while at != -1:
                    start, end = spans[at][0], spans[at + len(needle) - 1][1]
                    out.append((run.start() + start, run.start() + end))
                    at = decoded.find(needle, at + 1)
    return out


def _mask_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """``text`` with each span (merged where they overlap) replaced by one mask."""
    if not spans:
        return text
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    parts, last = [], 0
    for start, end in merged:
        parts += [text[last:start], MASK]
        last = end
    return "".join(parts) + text[last:]


@lru_cache(maxsize=8)
def _encoded(secrets: tuple[str, ...]) -> tuple[tuple[str, ...], dict[str, tuple[str, ...]]]:
    """Every encoded form of a SET of secrets, computed once per set.

    Keyed on the whole set, not on each secret: :func:`known_secrets` returns the same list on
    every call, so one entry serves a process. The first version cached per secret with a bound of
    128 — 200 secrets thrashed it (the bench corpus is what found it).
    """
    literals: set[str] = set()
    needles: dict[str, set[str]] = {family: set() for family in _RUNS}
    for secret in secrets:
        literals |= _encoded_literals(secret)
        for family in _RUNS:
            needles[family] |= _needles(secret, family)
    literals -= set(secrets)  # the verbatim pass already took them
    # Longest first across ALL secrets, the rule `known_secrets` keeps for the verbatim pass: a
    # short fragment replaced first would leave the rest of a longer one readable.
    ordered = tuple(sorted((x for x in literals if len(x) >= _MIN_SECRET_LEN), key=len, reverse=True))
    return ordered, {family: tuple(sorted(found, key=len, reverse=True)) for family, found in needles.items()}


def mask_known(text: str, secrets: list[str], *, encoded: bool = True) -> str:
    """The known-secret net alone: each value verbatim, then (``encoded``) its encoded forms.

    Separate from :func:`redact` so `bench/encoded_secrets` can read the encoded pass as the
    difference between ``encoded=False`` and ``encoded=True`` on the same text, and so a caller
    holding a secret that is NOT in the environment (the bridge token) can mask it the same way.
    """
    for secret in secrets:
        text = text.replace(secret, MASK)
    # The length floor `known_secrets` applies, applied here too: a caller may pass a short value
    # (a test token, a pin), and the hex of three characters sits inside any long enough hex dump.
    long_enough = tuple(s for s in secrets if len(s) >= _MIN_SECRET_LEN)
    if not encoded or not long_enough:
        return text
    literals, needles = _encoded(long_enough)
    for literal in literals:
        text = text.replace(literal, MASK)
    for family, found in needles.items():
        text = _mask_spans(text, _family_spans(text, family, found))
    return text


def redact(text: str) -> str:
    """Replace known secrets, structurally-placed secrets, and credential-shaped strings.

    Three nets, in order of confidence. The environment values are a guarantee against their verbatim
    copies and the encoded forms the module docstring lists (no others); the places are structural and need no
    knowledge of the value; the shapes are a guess at the string and are deliberately the narrowest
    of the three.
    """
    if not text:
        return text
    text = mask_known(text, known_secrets(), encoded=MASK_ENCODED)
    for pattern in _PLACES:
        # The surrounding context is kept and only the captured value replaced. A line that reads
        # `[redacted]` and nothing else cannot be diagnosed, which defeats the file's purpose.
        text = pattern.sub(lambda m: f"{m.group('keep')}{MASK}{m.groupdict().get('tail') or ''}", text)
    for pattern in _PATTERNS:
        text = pattern.sub(MASK, text)
    return text
