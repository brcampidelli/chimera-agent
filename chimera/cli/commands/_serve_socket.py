"""Bind the desktop listener before publishing its port."""

from __future__ import annotations

from typing import Any


def _bind_app_socket(host: str, port: int) -> tuple[Any, int]:
    """Bind the app's listening socket, falling back to a free port if ``port`` is taken.

    Returns the bound, **listening** socket (handed straight to uvicorn so there is no
    close-then-rebind race) and the actual port. ``port=0`` asks the OS for any free port. A fixed
    port that is already in use no longer crashes the app — it drops to an OS-assigned free port (so
    a second `chimera app`, or a Tauri sidecar, just works). No ``SO_REUSEADDR`` on purpose: on
    Windows that would let the bind succeed on a port another server already holds, defeating the
    busy-detection.

    ``listen()`` is called HERE and not left to uvicorn, because the port file — the sidecar's
    discovery channel — is written between this return and uvicorn's own ``listen()``. Measured on
    2026-09-17 (`bench/startup`): a connection attempted in that window met a bound socket that was
    not listening, and on Windows the SYN is dropped rather than refused, so the client's first
    connect sat on the 500 ms retransmit timer — a half-second of every desktop launch, paid between
    "the backend reported its port" and "the window loaded". Listening first means a connect made
    the moment the port file exists is queued in the backlog and served as soon as uvicorn accepts.
    """
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except OSError:
        if port == 0:  # asked for any free port and still failed — surface it
            sock.close()
            raise
        sock.bind((host, 0))  # requested port busy → OS picks a free one
    sock.listen(128)
    # The desktop sidecar binds port 0, so only the OS knows this number until now. The browser's
    # loopback check reads it from here: no declared local port may ever be the app's own API.
    from chimera.core.listeners import claim

    claim(int(sock.getsockname()[1]))
    return sock, sock.getsockname()[1]
