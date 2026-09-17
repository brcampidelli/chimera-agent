"""The diagnostic that found the half second: a raw-socket client inside the app's own boot.

Runs `chimera app` in-process with three hooks — the moment `_bind_app_socket` returns (the port
file is written right after), uvicorn's `Server.startup`, and an ASGI wrapper that stamps the first
HTTP request — and, from a thread, connects to the port the instant it is bound and times the
connect and the request separately. Before the fix the connect took ~0.5 s on Windows (a SYN to a
bound-but-not-listening socket is dropped and retransmitted after 500 ms) and the request 6 ms;
after it both are milliseconds.

    python bench/startup/probe_connect.py <port-file>      # prints the timeline to stderr
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from typing import Any

T0 = time.perf_counter()


def mark(label: str) -> None:
    print(f"[{time.perf_counter() - T0:7.3f}s] {label}", file=sys.stderr, flush=True)


import uvicorn  # noqa: E402

_startup = uvicorn.Server.startup


async def startup(self: Any, *a: Any, **k: Any) -> None:
    mark("Server.startup begin")
    await _startup(self, *a, **k)
    mark("Server.startup end (create_server done)")


uvicorn.Server.startup = startup  # type: ignore[method-assign]

import chimera.api as capi  # noqa: E402
import chimera.cli.main as cli  # noqa: E402

_bind = cli._bind_app_socket


def bind(*a: Any, **k: Any) -> Any:
    sock, port = _bind(*a, **k)
    mark(f"socket bound port {port}")

    def client() -> None:
        began = time.perf_counter()
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        mark(f"client connected after {time.perf_counter() - began:.3f}s, sending")
        began = time.perf_counter()
        s.sendall(b"GET /api/health HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        s.recv(4096)
        s.close()
        mark(f"client got the response after {time.perf_counter() - began:.3f}s")

    threading.Thread(target=client, daemon=True).start()
    return sock, port


cli._bind_app_socket = bind  # type: ignore[assignment]

_build = capi.build_api_app


def build(*a: Any, **k: Any) -> Any:
    app = _build(*a, **k)

    async def timing(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            mark(f"ASGI http request enters {scope['path']}")
        elif scope["type"] == "lifespan":
            mark("ASGI lifespan begins")
        await app(scope, receive, send)

    return timing


capi.build_api_app = build  # type: ignore[assignment]

if __name__ == "__main__":
    sys.argv = ["chimera", "app", "--no-open", "--port", "0", "--emit-port-file", sys.argv[1]]
    cli.app()
