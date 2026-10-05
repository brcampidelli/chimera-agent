"""Keep secrets out of anything written to disk.

The trace records what each tool was called with and what came back. On the 24/7 path those tools
talk to a broker, a database and a payment processor, so the text is exactly the kind that carries a
bearer token — and the trace is a file that lives for weeks on a machine nobody logs into.

**What this guarantees, and what it does not.** It guarantees that a secret *this process knows about*
never reaches the file *verbatim*: every environment value whose variable name looks like a
credential is replaced, as a literal string, wherever it appears — and so are the encodings of it
that a model reaches for unprompted (7 of 9 frontier models disguised a credential to "help" another
agent, and a monitor that did not know the credential missed most of them; arXiv 2609.39050). This
module DOES know the credential, so it computes them: base64 (standard and URL-safe, alone or inside
a longer blob at any byte offset, less at most one partial character at each end of the run), hex in
either case, decimal character codes, ``\\x``/``0x`` escapes, the reversed string, and the
percent-encoded one (study 30, S30-32; `bench/encoded_secrets` measured every form at 100% and zero
false positives over 7,240 texts that carry no secret). That list is the whole of the guarantee. A
secret split across lines, ROT13'd, compressed, keyed or encoded twice still passes through — the
same corpus measures those at 0%, and says so rather than leaving it out. This text once called the
literal match "a complete guarantee over the set that matters most" (study 30, S30-21(c)); it never
was, and the list above is not either.

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
from functools import lru_cache
from urllib.parse import quote, quote_plus

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

#: Characters between two character codes: `115, 107`, `115 107`, `[115,107]`, `0x73;0x6b`. Decimal
#: codes need at least one — `115107` is not two codes, it is a number — while `\x73\x6b` has none.
_CODE_SEP = r"[\s,;]+"
_ESCAPE_SEP = r"[\s,;]*"


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
        quote(secret, safe=""),
        quote_plus(secret, safe=""),
    }


def _encoded_patterns(secret: str) -> list[str]:
    """The encoded forms of one secret that vary in case or separator, as regex sources."""
    raw = secret.encode("utf-8")
    return [
        "(?i:" + re.escape(raw.hex()) + ")",
        # Decimal codes, digits not adjacent so `115` is never the tail of `2115`.
        r"(?<!\d)" + _CODE_SEP.join(str(b) for b in raw) + r"(?!\d)",
        # `\x73\x6b...` escapes and `0x73, 0x6b, ...` lists.
        "(?i:" + r"(?:\\x|0x)" + (_ESCAPE_SEP + r"(?:\\x|0x)").join(f"{b:02x}" for b in raw) + r"(?![0-9a-f]))",
    ]


@lru_cache(maxsize=8)
def _encoded(secrets: tuple[str, ...]) -> tuple[tuple[str, ...], re.Pattern[str] | None]:
    """Every encoded form of a SET of secrets, computed once per set.

    Keyed on the whole set, not on each secret: :func:`known_secrets` returns the same list on
    every call, so one entry serves a process, and the patterns are ONE compiled alternation. The
    first version cached per secret with a bound of 128 and compiled each pattern apart — 200
    secrets thrashed both that cache and `re`'s own, and every call recompiled hundreds of
    patterns (the bench corpus is what found it).
    """
    literals: set[str] = set()
    sources: list[str] = []
    for secret in secrets:
        literals |= _encoded_literals(secret)
        sources += _encoded_patterns(secret)
    literals -= set(secrets)  # the verbatim pass already took them
    # Longest first across ALL secrets, the rule `known_secrets` keeps for the verbatim pass: a
    # short fragment replaced first would leave the rest of a longer one readable.
    ordered = tuple(sorted((x for x in literals if len(x) >= _MIN_SECRET_LEN), key=len, reverse=True))
    return ordered, (re.compile("|".join(sources)) if sources else None)


def mask_known(text: str, secrets: list[str], *, encoded: bool = True) -> str:
    """The known-secret net alone: each value verbatim, then (``encoded``) its encoded forms.

    Separate from :func:`redact` so `bench/encoded_secrets` can read the encoded pass as the
    difference between ``encoded=False`` and ``encoded=True`` on the same text.
    """
    for secret in secrets:
        text = text.replace(secret, MASK)
    if not encoded or not secrets:
        return text
    literals, pattern = _encoded(tuple(secrets))
    for literal in literals:
        text = text.replace(literal, MASK)
    if pattern is not None:
        text = pattern.sub(MASK, text)
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
