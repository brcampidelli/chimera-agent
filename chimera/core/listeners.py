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
"""

from __future__ import annotations

import threading

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
