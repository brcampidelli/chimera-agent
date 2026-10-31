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

**A service the app attaches to (S1, PR 3).** A background service outlives the window that
started it, so it cannot be handed a screen credential once, at launch, and keep it. Instead the
desktop shell *leases* one to it while a window is connected (``POST /api/service/window``,
:mod:`chimera.api.service_windows`), and the lease ends when the window disconnects or stops
renewing it. Holding the owner's token is not enough to take the lease — that token is on disk. The
lease also needs the **launcher key**: a secret the shell draws per launch, passes to the service it
spawns in :data:`LAUNCHER_ENV` (removed from the environment as it is read, like the screen
credential) and keeps in its own memory. A service the shell did not start — ``chimera service
start`` from a terminal, or a previous run of the app — holds no launcher key the current shell
knows, so no lease is granted there and a yes takes the code until the app restarts the service.

The webview sends it in :data:`HEADER_NAME`, beside — not instead of — the bearer token. The
middleware compares it in constant time and marks the request's ASGI scope (:data:`SCOPE_KEY`), the
same way the desktop bridge marks its own in-process requests; nothing arriving over a socket can
set a scope key.
"""

from __future__ import annotations

import hmac
import os
import secrets
import time
from dataclasses import dataclass
from typing import Literal

#: The variable the desktop shell starts the backend with. Read once and removed.
ENV_VAR = "CHIMERA_SCREEN_TOKEN"
#: The request header the screen sends it in.
HEADER_NAME = "x-chimera-screen"
#: Set on a request's ASGI scope when it carried the screen credential. Only this process sets it.
SCOPE_KEY = "chimera.via_screen"
#: The variable the desktop shell starts a background SERVICE with: the launcher key that lets that
#: shell — and no other holder of the owner's token — lease a screen credential to it. Named TOKEN so
#: ``_child_env`` strips it too; read once and removed, like :data:`ENV_VAR`.
LAUNCHER_ENV = "CHIMERA_SCREEN_LAUNCHER_TOKEN"
#: The request header the shell sends the launcher key in, on ``/api/service/window`` only.
LAUNCHER_HEADER_NAME = "x-chimera-launcher"
#: Shorter than this is not a secret the shell drew (it draws 32 random bytes, hex: 64 characters).
MIN_SECRET = 32

Mode = Literal["app", "service", "standalone"]


@dataclass
class ScreenCredential:
    """This process's screen credential, or ``None`` when it has no screen to vouch for."""

    value: str | None = None

    #: Whether the page this process serves may carry it (``chimera app`` in a browser). False for
    #: the desktop shell's backend, whose webview gets it from the shell, and for a service.
    in_page: bool = False

    #: ``time.monotonic()`` past which :attr:`value` no longer counts — a lease a window holds on a
    #: service and has to renew. ``None``: no expiry (the value lives as long as the process).
    expires_at: float | None = None

    #: The desktop shell's launcher key, when it started this process (:func:`take_launcher_key`,
    #: read by :func:`for_mode` in every mode so no mode leaves it in the environment). Only a
    #: service uses it — to let that shell, and no other holder of the owner's token, lease it a
    #: screen credential (``chimera/api/service_windows.py``). Kept by :meth:`revoke`.
    launcher_key: str | None = None

    #: ``time.monotonic()`` of the last request that carried the credential. The one sign that a
    #: screen is connected and not just holding a value: a browser tab keeps the value after it closes.
    seen_at: float | None = None

    def revoke(self) -> None:
        """Hold none from now on. ``chimera service`` calls it before it serves anything
        (``publish_service``): the app is built before the mode's publication, and a service has
        no screen to vouch for until a window leases it one."""
        self.value = None
        self.in_page = False
        self.expires_at = None

    def lease(self, value: str, until: float) -> None:
        """Hold ``value`` until ``until`` (monotonic). Never offered in a page: a service has none."""
        self.value = value
        self.in_page = False
        self.expires_at = until

    def held(self, now: float | None = None) -> bool:
        """Whether a value counts right now — false once a lease ran out."""
        if not self.value:
            return False
        return self.expires_at is None or (time.monotonic() if now is None else now) < self.expires_at

    def matches(self, offered: str | None) -> bool:
        if not offered or not self.held():
            return False
        value = self.value or ""
        matched = hmac.compare_digest(offered.encode("utf-8"), value.encode("utf-8"))
        if matched:
            self.seen_at = time.monotonic()
        return matched

    def seen_within(self, seconds: float, now: float | None = None) -> bool:
        """Whether a request carried the credential in the last ``seconds``: a screen is talking."""
        if self.seen_at is None:
            return False
        return (time.monotonic() if now is None else now) - self.seen_at < seconds


#: What a browser's refusal to switch "Ignore permissions" on says to the owner, as the API's 403.
BROWSER_BYPASS_REFUSAL = (
    'In the browser mode "Ignore permissions" can only be turned on in the desktop app.'
)


def may_switch_bypass_on(credential: ScreenCredential | None) -> bool:
    """Whether this process's screen credential may turn "Ignore permissions" on.

    ``in_page`` means the credential was drawn for a page a browser loads (``chimera app``). That page
    is served to any loopback caller, and a process of the agent's own shell — ``run_shell`` with a
    ``curl`` — is one. So in that mode the credential proves nothing about who asked, and the
    switches that ENABLE bypass are refused there. Turning them OFF is never refused: it only narrows.
    The desktop shell's backend, whose webview gets the value from the shell and no page, is not
    ``in_page`` and keeps the switch.

    No credential at all is refused, not allowed: the guard is only ever asked after the route has
    checked for a screen, so ``None`` here means no screen was vouched for. Failing open on ``None``
    would turn a missing check into a permission. Turning OFF never reaches this guard.
    """
    return credential is not None and not credential.in_page


def _take_from_environment() -> str:
    """The launcher's value, removed from the environment so no child — and no later reader — sees it."""
    return (os.environ.pop(ENV_VAR, None) or "").strip()


def take_launcher_key() -> str | None:
    """The launcher key the desktop shell started this process with, removed from the environment.

    Adopted only from the desktop shell (``CHIMERA_TAURI_SIDECAR=1``) and only when it is long
    enough to be one the shell drew. Read in every mode, so no mode leaves it for a child to find;
    only a service keeps what it returns (``publish_service``).
    """
    offered = (os.environ.pop(LAUNCHER_ENV, None) or "").strip()
    if os.environ.get("CHIMERA_TAURI_SIDECAR") != "1" or len(offered) < MIN_SECRET:
        return None
    return offered


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
    launcher = take_launcher_key()
    if mode == "service":
        return ScreenCredential(launcher_key=launcher)
    native = os.environ.get("CHIMERA_TAURI_SIDECAR") == "1"
    if native:
        value = offered if offered and offered != (server_token or "").strip() else None
        return ScreenCredential(value=value, launcher_key=launcher)
    return ScreenCredential(value=secrets.token_urlsafe(32), in_page=True, launcher_key=launcher)
