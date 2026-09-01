"""Keep secrets out of anything written to disk.

The trace records what each tool was called with and what came back. On the 24/7 path those tools
talk to a broker, a database and a payment processor, so the text is exactly the kind that carries a
bearer token — and the trace is a file that lives for weeks on a machine nobody logs into.

**What this guarantees, and what it does not.** It guarantees that a secret *this process knows about*
never reaches the file: every environment value whose variable name looks like a credential is
replaced verbatim wherever it appears. That is a complete guarantee over the set that matters most,
because those are the strings the agent could plausibly echo.

It does **not** guarantee that no secret ever survives. A token minted at runtime by a remote API, a
password typed into a prompt, a key in a file the agent read — none of those are in the environment
and no pattern list finds all of them. The regexes below catch the shapes that are cheap to catch;
they are a second net, not the guarantee. Anyone reading this should treat a trace as sensitive,
which is why it is also size-capped and rotated rather than kept forever.
"""

from __future__ import annotations

import os
import re

#: Same markers the sandbox uses to strip the child environment. One list, one meaning of "secret".
from chimera.sandbox.local import _SECRET_MARKERS

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


def redact(text: str) -> str:
    """Replace known secrets, structurally-placed secrets, and credential-shaped strings.

    Three nets, in order of confidence. The environment values are a guarantee over the set that
    matters most; the places are structural and need no knowledge of the value; the shapes are a
    guess at the string and are deliberately the narrowest of the three.
    """
    if not text:
        return text
    for secret in known_secrets():
        text = text.replace(secret, MASK)
    for pattern in _PLACES:
        # The surrounding context is kept and only the captured value replaced. A line that reads
        # `[redacted]` and nothing else cannot be diagnosed, which defeats the file's purpose.
        text = pattern.sub(lambda m: f"{m.group('keep')}{MASK}{m.groupdict().get('tail') or ''}", text)
    for pattern in _PATTERNS:
        text = pattern.sub(MASK, text)
    return text
