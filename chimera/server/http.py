"""Minimal HTTP transport for the messaging gateway (stdlib only, no extra deps).

``GET /health`` and ``POST /chat`` ({"text", "chat_id"?}) -> {"reply"}. The request
logic lives in the pure :func:`handle` function (easy to unit-test); the
:class:`http.server` handler is a thin wrapper.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from chimera.server.gateway import InboundMessage, MessageGateway

# Given a hook name and the POST payload, fire the matching jobs and return their results.
WebhookHandler = Callable[[str, dict[str, Any]], list[str]]


def handle(
    gateway: MessageGateway,
    method: str,
    path: str,
    body: bytes,
    *,
    webhooks: WebhookHandler | None = None,
    whatsapp: Any = None,
    a2a: Any = None,  # (A2AServer, agent_card dict) — exposes A2A when provided
) -> tuple[int, dict[str, Any] | str]:
    """Pure request handler returning ``(status, body)`` — body is a dict (JSON) or a str (text)."""
    route = urlparse(path).path
    if method == "GET" and route == "/health":
        return 200, {"status": "ok", "active_chats": gateway.active_chats}
    if a2a is not None:
        server, card = a2a
        if method == "GET" and route in ("/.well-known/agent.json", "/.well-known/agent-card.json"):
            return 200, card
        if method == "POST" and route == "/a2a":
            try:
                request = json.loads(body or b"{}")
            except json.JSONDecodeError:
                return 400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
            if not isinstance(request, dict):
                return 400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
            return 200, server.dispatch(request)
    if whatsapp is not None and route == "/whatsapp":
        if method == "GET":  # Meta subscription verification: echo the challenge as plain text
            params = {key: values[0] for key, values in parse_qs(urlparse(path).query).items()}
            challenge = whatsapp.verify(params)
            return (200, challenge) if challenge is not None else (403, {"error": "verification failed"})
        if method == "POST":
            try:
                payload = json.loads(body or b"{}")
            except json.JSONDecodeError:
                payload = {}
            handled = whatsapp.on_message(payload if isinstance(payload, dict) else {})
            return 200, {"received": handled}
    if method == "POST" and path.startswith("/webhook/") and webhooks is not None:
        hook = path[len("/webhook/") :]
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError:
            payload = {}
        try:
            results = webhooks(hook, payload if isinstance(payload, dict) else {})
        except Exception as exc:  # noqa: BLE001 — surface as 500, don't crash the server
            return 500, {"error": str(exc)}
        if not results:
            return 404, {"error": f"no webhook job registered for {hook!r}"}
        return 200, {"hook": hook, "fired": len(results), "results": results}
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
    gateway: MessageGateway,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    webhooks: WebhookHandler | None = None,
    whatsapp: Any = None,
    a2a: Any = None,
) -> ThreadingHTTPServer:
    """Build (but don't start) an HTTP server wrapping ``gateway`` (and optional webhooks)."""

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, method: str) -> None:
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            status, payload = handle(
                gateway, method, self.path, body, webhooks=webhooks, whatsapp=whatsapp, a2a=a2a
            )
            if isinstance(payload, str):  # plain text (e.g. the WhatsApp verification challenge)
                data, content_type = payload.encode("utf-8"), "text/plain"
            else:
                data, content_type = json.dumps(payload).encode("utf-8"), "application/json"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
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
