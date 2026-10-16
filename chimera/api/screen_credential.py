"""The screen credential: what lets the approval card approve without a code.

``POST /api/approvals/{id}`` with ``approved: true`` approves a question THIS process asked from the
code it keeps in memory (``governance/pending.py``, ``approvable_here``) — that is the app's card,
one click. Until 2026-10-16 the only thing the route asked of the caller was an owner credential,
and since S1 (#937) one of those is ``<home>/service.token``: a file on disk, published in
``service-<channel>.json``. Any local process that could read it — the agent's own shell, with a
``cat`` — could approve the agent's own questions with no code at all, which is the bypass the
per-question code (study 30, S30-30) exists to close.

So the owner's bearer token says *who may call the API*, and this says *the request came from the
owner's screen*. Its properties are the whole of the argument:

* **Drawn per start, kept in memory.** Never written to a file, a record, a log or ``.env``, never
  returned by a route (``/api/info`` included), never inherited by a child (the variable that
  carries it in is removed from this process's environment as it is read, and ``_child_env`` strips
  every name that says TOKEN anyway).
* **Never the owner's token.** An owner-chosen ``CHIMERA_SERVER_TOKEN`` sits in ``.env`` — on disk —
  so a value equal to the bearer token is refused as a screen credential, and the desktop shell
  draws this one fresh on every launch even when it reuses the owner's token.
* **Delivered only where a screen is.** The desktop shell (Tauri) passes it in
  :data:`ENV_VAR` and hands it to the webview through its initialization script, the same way it
  hands over the bearer token: no URL, no disk, no page-callable IPC. A browser served by
  ``chimera app`` receives it in the page's ``<meta name="chimera-screen">`` on a loopback request —
  exactly where and when that page already receives the bearer token, so that mode is no more
  exposed than it was (see :func:`for_mode`). ``chimera service`` has no screen and holds none —
  ``publish_service`` revokes the one the app was built with, before the record exists: there a
  yes takes the per-question code.

The webview sends it in :data:`HEADER_NAME`, beside — not instead of — the bearer token. The
middleware compares it in constant time and marks the request's ASGI scope (:data:`SCOPE_KEY`), the
same way the desktop bridge marks its own in-process requests; nothing arriving over a socket can
set a scope key.
"""

from __future__ import annotations

import hmac
import os
import secrets
from dataclasses import dataclass
from typing import Literal

#: The variable the desktop shell starts the backend with. Read once and removed.
ENV_VAR = "CHIMERA_SCREEN_TOKEN"
#: The request header the screen sends it in.
HEADER_NAME = "x-chimera-screen"
#: Set on a request's ASGI scope when it carried the screen credential. Only this process sets it.
SCOPE_KEY = "chimera.via_screen"

Mode = Literal["app", "service", "standalone"]


@dataclass
class ScreenCredential:
    """This process's screen credential, or ``None`` when it has no screen to vouch for."""

    value: str | None = None

    #: Whether the page this process serves may carry it (``chimera app`` in a browser). False for
    #: the desktop shell's backend, whose webview gets it from the shell, and for a service.
    in_page: bool = False

    def revoke(self) -> None:
        """Hold none from now on. ``chimera service`` calls it before it serves anything
        (``publish_service``): the app is built before the mode's publication, and a service has
        no screen to vouch for."""
        self.value = None
        self.in_page = False

    def matches(self, offered: str | None) -> bool:
        if not self.value or not offered:
            return False
        return hmac.compare_digest(offered.encode("utf-8"), self.value.encode("utf-8"))


def _take_from_environment() -> str:
    """The launcher's value, removed from the environment so no child — and no later reader — sees it."""
    return (os.environ.pop(ENV_VAR, None) or "").strip()


def for_mode(mode: Mode | None, *, server_token: str | None = None) -> ScreenCredential:
    """The credential a backend started in ``mode`` holds.

    ``service``: none. A long-lived process with a token on disk and no window has nobody to hand a
    screen credential to, and a value somebody left in the environment is discarded, not adopted.
    (``chimera service`` builds its app like ``chimera app`` and then calls
    :meth:`ScreenCredential.revoke`; this branch is the same answer for a caller that knows the
    mode up front.)

    The desktop shell's backend (``CHIMERA_TAURI_SIDECAR=1``): the shell's value, which it draws on
    every launch. Without one — an older shell — none, so a click there takes the code rather than
    a credential the page could not have been given.

    Anything else (``chimera app`` in a browser, ``--standalone``, a test, ``schema_dump``): drawn
    here, in memory, and offered on the loopback page. Not adopted from the environment: outside the
    shell nobody is entitled to choose it.

    In every case a value equal to the owner's bearer token is refused — that one may be in ``.env``.
    """
    offered = _take_from_environment()
    if mode == "service":
        return ScreenCredential()
    native = os.environ.get("CHIMERA_TAURI_SIDECAR") == "1"
    if native:
        value = offered if offered and offered != (server_token or "").strip() else None
        return ScreenCredential(value=value)
    return ScreenCredential(value=secrets.token_urlsafe(32), in_page=True)
