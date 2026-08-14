"""Refuse to serve an unauthenticated agent to the internet.

The shipped `docker-compose.yml` published `8765:8765` on every interface, `.env.example` left
`CHIMERA_SERVER_TOKEN=` empty, and auth is opt-in — no token configured means no check. Follow the
README's one-command deployment and the result is an agent gateway, with tools, answering anyone who
finds the port. Nothing in that chain is a bug on its own; together they are the default.

The check is on the BIND, not on the request, and that distinction is the design. Refusing per
request would mean the port is open, the service is up, and every probe gets a polite 401 — which
still tells an attacker there is a Chimera here and still depends on every future transport
remembering to ask. Refusing to start means the failure happens once, on the operator's terminal,
with the fix in the message.

`0.0.0.0` behind an authenticating proxy is a legitimate deployment, so there is an explicit escape.
Without one this correction would be a regression for everyone already running that way — and a
guard people are forced to work around is a guard they learn to disable everywhere.
"""

from __future__ import annotations

import ipaddress

#: Hosts that cannot receive a connection from another machine. `localhost` is resolved by name
#: rather than assumed: it is the spelling almost everyone types, and on a normal machine it is
#: exactly as private as the two addresses beside it.
_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", ""}


class InsecureBindError(RuntimeError):
    """Raised when a non-loopback bind is requested with no token and no explicit escape."""


def is_loopback(host: str) -> bool:
    """True when nothing outside this machine can reach a socket bound to ``host``."""
    name = (host or "").strip().lower()
    if name in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        # A hostname we cannot classify. Treated as reachable — the conservative direction, because
        # the cost of being wrong here is an unauthenticated agent on a public interface, and the
        # cost of being wrong the other way is one flag on the command line.
        return False


def check_bind(host: str, *, token: str | None, allow_insecure: bool = False) -> None:
    """Raise :class:`InsecureBindError` for a reachable bind with no token.

    Order matters: the escape is checked last so the message a person sees first is the problem
    rather than the way around it.
    """
    if is_loopback(host) or (token or "").strip() or allow_insecure:
        return
    raise InsecureBindError(
        f"refusing to serve on {host} with no CHIMERA_SERVER_TOKEN set.\n"
        "  Anyone who can reach that address would get an agent with tools, unauthenticated.\n"
        "  Fix it one of three ways:\n"
        "    · set CHIMERA_SERVER_TOKEN (any long random string) and pass it as a bearer token\n"
        "    · bind to 127.0.0.1 and reach it through a proxy that authenticates\n"
        "    · pass --allow-insecure-bind if the network in front of this is already trusted"
    )
