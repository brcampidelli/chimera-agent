"""The TCP ports this process listens on, for the one check that must never let the agent reach them.

The agent's browser can be told to open loopback ports the owner declared (``CHIMERA_BROWSER_LOCAL_PORTS``,
study 29 P5.2) so it can look at the dev server it is working on. Every port Chimera itself serves on
has to stay out of that, whatever the owner wrote: the app's API (where ``/api/approvals`` answers a
card), the guest listener (``0.0.0.0``, no token by default), and the ``chimera serve`` gateway. A page
that could steer the agent to any of them could approve its own actions.

The ports are not known in advance — the desktop sidecar binds port 0 and learns its number from the
OS — so each listener says what it got, here, the moment it binds. Nothing is ever released: a port
this process opened stays refused for the life of the process, which errs toward refusing a dev server
that later took the same number, never toward reaching a listener that is gone.

A port is not the whole answer, though, because a server on ANOTHER port can relay to one of these:
see :data:`INSTANCE_HEADER`.
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

#: Every response a Chimera listener sends carries this header. A port is not enough to know who
#: answers on it: the desktop's own dev server (Vite, ``npm --prefix apps/desktop run dev``) listens
#: on 5173 and proxies ``/api`` to the app's API, so a declared 5173 reaches ``/api/approvals``
#: through a port this process never held. A proxy relays response headers, so the browser's
#: loopback check asks the declared port before a request goes to it and refuses one that answers
#: with this (`chimera.tools.browser_reach`). Presence is the signal; the value says nothing.
INSTANCE_HEADER = "X-Chimera-Instance"

_Message = MutableMapping[str, Any]
_Receive = Callable[[], Awaitable[_Message]]
_Send = Callable[[_Message], Awaitable[None]]
_ASGIApp = Callable[[_Message, _Receive, _Send], Awaitable[None]]

_lock = threading.Lock()
_held: set[int] = set()


def claim(port: int) -> None:
    """Record that this process listens on ``port``. Idempotent; 0 (not bound yet) is ignored."""
    if port <= 0:
        return
    with _lock:
        _held.add(int(port))


def held() -> frozenset[int]:
    """Every port this process has listened on."""
    with _lock:
        return frozenset(_held)


class MarkResponses:
    """ASGI middleware: every HTTP response of the app it wraps carries :data:`INSTANCE_HEADER`.

    Plain ASGI rather than ``BaseHTTPMiddleware`` so a streamed response (the chat's SSE) is never
    buffered, and so the 404s and 405s routing answers by itself are marked too — the browser's
    probe is an ``OPTIONS``, which no route here declares."""

    def __init__(self, app: _ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: _Message, receive: _Receive, send: _Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def marked(message: _Message) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((INSTANCE_HEADER.lower().encode("latin-1"), b"1"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, marked)
