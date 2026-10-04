"""Which ``Host`` a request to the loopback app may carry — the answer to DNS rebinding.

Without a server token (the desktop default) the API trusts whoever reaches 127.0.0.1, on the
reasoning that only this machine can. A browser on this machine can, on behalf of any page: the
page at ``attacker.example`` re-points that name to 127.0.0.1 (a short TTL is enough) and fetches
``http://attacker.example:8765/api/...``. To the browser that is the page's own origin — no
preflight, and the answer is readable. Nothing about the request is foreign except one header the
browser fills from the URL: ``Host: attacker.example:8765``.

So the rule is about that header, and it does not depend on the port or on ``Origin``:

* an IP literal (``127.0.0.1``, ``[::1]``) — the browser connected to that address, no DNS answer
  stands behind it;
* ``localhost`` — browsers resolve it themselves, to loopback, and do not ask DNS;
* a name the operator wrote in ``CHIMERA_ALLOWED_ORIGINS`` — they put it in front of this app.

Anything else is a DNS name somebody else may control. The port is deliberately NOT checked: the
Vite dev server proxies ``/api`` with ``Host: localhost:5173``, and a rebinding page could not pick
a loopback name for its own Host header anyway.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from typing import Any
from urllib.parse import urlsplit

# The access card's own rule for "reachable only from this machine" — one answer, not two.
from chimera.api.access_api import is_loopback_host

_Message = MutableMapping[str, Any]
_Receive = Callable[[], Awaitable[_Message]]
_Send = Callable[[_Message], Awaitable[None]]
_ASGIApp = Callable[[_Message, _Receive, _Send], Awaitable[None]]


def hostname_of(authority: str) -> str | None:
    """``host`` from ``host[:port]`` (``[::1]:8765`` → ``::1``), lowercased; ``None`` if unparseable."""
    try:
        name = urlsplit("//" + authority).hostname
    except ValueError:  # a malformed bracket or port
        return None
    return name.rstrip(".") if name else None


def named_hostnames(origins: Iterable[str]) -> frozenset[str]:
    """The host part of each origin the operator named."""
    out = set()
    for origin in origins:
        try:
            name = urlsplit(origin).hostname
        except ValueError:
            continue
        if name:
            out.add(name.rstrip("."))
    return frozenset(out)


def is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True


def unrebindable(hostname: str | None) -> bool:
    """Whether no DNS answer can stand behind this name: an IP literal, or ``localhost``."""
    return hostname is not None and (hostname == "localhost" or is_ip_literal(hostname))


def host_allowed(header: str | None, named: frozenset[str]) -> bool:
    """Whether a request to the loopback app may carry this ``Host`` header.

    A missing header is let through: every browser sends one, and a client that does not is not a
    page in the owner's browser, which is what this defends against.
    """
    if header is None:
        return True
    name = hostname_of(header)
    if name is None:
        return False
    if name in named:
        return True
    # On a loopback bind the only honest literal is a loopback one: `Host: 10.0.0.5` arriving at
    # 127.0.0.1 is not a browser that connected to 10.0.0.5.
    return name == "localhost" or (is_ip_literal(name) and ipaddress.ip_address(name).is_loopback)


class LoopbackHostGuard:
    """ASGI middleware: on a loopback bind, refuse a request whose ``Host`` is a DNS name.

    ``bound`` reports the address the server listens on — known only after the socket is bound,
    which is after the app is built, so it is read per request. While it is unknown (a test client,
    the schema dump) nothing is refused: the rule is about a listener, and there is none yet.
    Plain ASGI so a streamed response is never buffered, like :class:`MarkResponses` beside it.
    """

    def __init__(
        self,
        app: _ASGIApp,
        *,
        bound: Callable[[], tuple[str, int] | None],
        named: Callable[[], frozenset[str]],
    ) -> None:
        self.app = app
        self._bound = bound
        self._named = named

    async def __call__(self, scope: _Message, receive: _Receive, send: _Send) -> None:
        kind = scope.get("type")
        address = self._bound() if kind in ("http", "websocket") else None
        if address is None or not is_loopback_host(str(address[0])):
            await self.app(scope, receive, send)
            return
        header = None
        for key, value in scope.get("headers") or []:
            if key == b"host":
                header = value.decode("latin-1")
                break
        if host_allowed(header, self._named()):
            await self.app(scope, receive, send)
            return
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = b'{"detail":"refusing a request addressed to a name this app does not answer to"}'
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
