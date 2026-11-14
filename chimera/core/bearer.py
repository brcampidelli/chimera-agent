"""The one place a bearer token is compared.

Four doors used to carry their own copy of this check (the owner API's dependency and middleware,
the stdlib gateway, the desktop bridge). Three compared ``str`` with ``hmac.compare_digest``, which
raises ``TypeError`` for any non-ASCII character, so ``Authorization: Bearer é`` was a 500 on a
route whose whole job is to say 401. Comparing bytes removes the exception, and one function means
the next door cannot grow a fourth variant.
"""

from __future__ import annotations

import hmac


def bearer_matches(header: str | None, token: str | None) -> bool:
    """Whether ``header`` is exactly ``Authorization: Bearer <token>``, in constant time.

    False when either side is empty: an unset token never matches, and a missing header never does.
    ``replace`` keeps a header with an unpaired surrogate (which no HTTP stack hands us, but a test
    or a future transport might) a plain mismatch instead of a ``UnicodeEncodeError``.
    """
    if not header or not token:
        return False
    return hmac.compare_digest(
        header.encode("utf-8", "replace"), f"Bearer {token}".encode("utf-8", "replace")
    )
