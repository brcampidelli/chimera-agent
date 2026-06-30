"""Minimal HTTP transport for the messaging gateway (stdlib only, no extra deps).

``GET /health`` and ``POST /chat`` ({"text", "chat_id"?}) -> {"reply"}. The request
logic lives in the pure :func:`handle` function (easy to unit-test); the
:class:`http.server` handler is a thin wrapper.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from chimera.server.gateway import InboundMessage, MessageGateway


def handle(gateway: MessageGateway, method: str, path: str, body: bytes) -> tuple[int, dict[str, Any]]:
    """Pure request handler returning ``(status, json_body)``."""
    if method == "GET" and path == "/health":
        return 200, {"status": "ok", "active_chats": gateway.active_chats}
    if method == "POST" and path == "/chat":
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return 400, {"error": "invalid JSON"}
        text = str(data.get("text", "")).strip()
        if not text:
            return 400, {"error": "missing 'text'"}
        message = InboundMessage(
            text=text,
            chat_id=str(data.get("chat_id", "default")),
            platform=str(data.get("platform", "http")),
            user=str(data.get("user", "user")),
        )
        try:
            reply = gateway.on_message(message)
        except Exception as exc:  # noqa: BLE001 — surface the failure as a 500, don't crash the server
            return 500, {"error": str(exc)}
        return 200, {"reply": reply, "chat_id": message.chat_id}
    return 404, {"error": "not found"}


def make_server(
    gateway: MessageGateway, host: str = "127.0.0.1", port: int = 8765
) -> ThreadingHTTPServer:
    """Build (but don't start) an HTTP server wrapping ``gateway``."""

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, method: str) -> None:
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            status, payload = handle(gateway, method, self.path, body)
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 — http.server's required name
            self._respond("GET")

        def do_POST(self) -> None:  # noqa: N802 — http.server's required name
            self._respond("POST")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — overrides stdlib
            pass  # silence default stderr access logging

    return ThreadingHTTPServer((host, port), Handler)
