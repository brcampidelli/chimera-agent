"""Minimal HTTP transport for the messaging gateway (stdlib only, no extra deps).

``GET /health`` and ``POST /chat`` ({"text", "chat_id"?}) -> {"reply"}. The request
logic lives in the pure :func:`handle` function (easy to unit-test); the
:class:`http.server` handler is a thin wrapper.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from chimera.server.gateway import InboundMessage, MessageGateway
from chimera.telemetry import get_logger

_log = get_logger("server.http")

# Given a hook name and the POST payload, fire the matching jobs and return their results.
WebhookHandler = Callable[[str, dict[str, Any]], list[str]]


def _bearer_ok(headers: Mapping[str, str] | None, token: str) -> bool:
    """Constant-time check of an ``Authorization: Bearer <token>`` header against ``token``."""
    auth = ""
    for key, value in (headers or {}).items():
        if key.lower() == "authorization":
            auth = value
            break
    prefix = "Bearer "
    return auth.startswith(prefix) and hmac.compare_digest(auth[len(prefix) :], token)


def _needs_bearer(method: str, route: str) -> bool:
    """Routes that drive the agent or change state, and so require the bearer when one is set.

    ``/whatsapp`` is excluded on purpose — Meta cannot send our bearer, so it is authenticated by
    HMAC signature instead (see the ``x-hub-signature-256`` check below).
    """
    return method == "POST" and (route in ("/a2a", "/chat") or route.startswith("/webhook/"))


def authorized(
    method: str, path: str, headers: Mapping[str, str] | None, token: str | None
) -> bool:
    """The single authorisation decision for this server. No transport may route around it.

    This exists as one function rather than an inline condition because it previously *was* an
    inline condition inside :func:`handle`, and the A2A ``message/stream`` path — which cannot use
    ``handle`` (SSE streams many bodies, ``handle`` returns one) — short-circuited ahead of it and
    served an unauthenticated agent to anyone who used the streaming method name. Any new transport
    branch must call this first; that is the whole point of it being here.
    """
    if not token:  # auth is opt-in: no token configured means no check
        return True
    if not _needs_bearer(method, urlparse(path).path):
        return True
    return _bearer_ok(headers, token)


def handle(
    gateway: MessageGateway,
    method: str,
    path: str,
    body: bytes,
    *,
    headers: Mapping[str, str] | None = None,
    token: str | None = None,
    webhooks: WebhookHandler | None = None,
    whatsapp: Any = None,
    a2a: Any = None,  # (A2AServer, agent_card dict) — exposes A2A when provided
) -> tuple[int, dict[str, Any] | str]:
    """Pure request handler returning ``(status, body)`` — body is a dict (JSON) or a str (text)."""
    route = urlparse(path).path
    if not authorized(method, path, headers, token):
        return 401, {"error": "unauthorized"}
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
            sig = None
            for key, value in (headers or {}).items():
                if key.lower() == "x-hub-signature-256":
                    sig = value
                    break
            verifier = getattr(whatsapp, "verify_signature", None)
            if callable(verifier) and not verifier(body, sig):  # opt-in HMAC (no-op w/o an app_secret)
                return 403, {"error": "invalid signature"}
            try:
                payload = json.loads(body or b"{}")
            except json.JSONDecodeError:
                payload = {}
            handled = whatsapp.on_message(payload if isinstance(payload, dict) else {})
            return 200, {"received": handled}
    if method == "POST" and route.startswith("/webhook/") and webhooks is not None:
        hook = route[len("/webhook/") :]  # use the parsed path, not the raw path (strips ?query)
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError:
            payload = {}
        try:
            results = webhooks(hook, payload if isinstance(payload, dict) else {})
        except Exception:  # noqa: BLE001 — surface as 500 WITHOUT leaking internals to the caller
            _log.exception("webhook %r failed", hook)
            return 500, {"error": "internal error"}
        if not results:
            return 404, {"error": f"no webhook job registered for {hook!r}"}
        return 200, {"hook": hook, "fired": len(results), "results": results}
    if method == "POST" and route == "/chat":
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
        except Exception:  # noqa: BLE001 — surface as 500 WITHOUT leaking internals (paths/secrets)
            _log.exception("chat handler failed")
            return 500, {"error": "internal error"}
        return 200, {"reply": reply, "chat_id": message.chat_id}
    return 404, {"error": "not found"}


def make_server(
    gateway: MessageGateway,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    token: str | None = None,
    webhooks: WebhookHandler | None = None,
    whatsapp: Any = None,
    a2a: Any = None,
) -> ThreadingHTTPServer:
    """Build (but don't start) an HTTP server wrapping ``gateway`` (and optional webhooks).

    ``token``: when set, state-changing POST endpoints (/a2a, /chat, /webhook/*) require an
    ``Authorization: Bearer <token>`` header. /whatsapp is authenticated by HMAC (app secret) instead.
    """

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict[str, Any] | str) -> None:
            if isinstance(payload, str):
                data, content_type = payload.encode("utf-8"), "text/plain"
            else:
                data, content_type = json.dumps(payload).encode("utf-8"), "application/json"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _respond(self, method: str) -> None:
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:  # malformed Content-Length -> 400, not a dropped connection
                self._send(400, {"error": "invalid Content-Length"})
                return
            body = self.rfile.read(length) if length else b""
            # Authorise BEFORE choosing a transport. The SSE branch below cannot go through
            # `handle`, so if the check lived only there, picking the streaming method name would
            # skip it entirely — which is exactly the hole this ordering closes.
            if not authorized(method, self.path, dict(self.headers.items()), token):
                self._send(401, {"error": "unauthorized"})
                return
            # A2A message/stream is Server-Sent Events — it can't go through the pure `handle`
            # (which returns one body), so stream it directly off the A2AServer's iterator.
            if method == "POST" and a2a is not None and self._is_a2a_stream(body):
                self._respond_sse(a2a[0].stream(json.loads(body or b"{}")))
                return
            status, payload = handle(
                gateway, method, self.path, body,
                headers=dict(self.headers.items()), token=token,
                webhooks=webhooks, whatsapp=whatsapp, a2a=a2a,
            )
            self._send(status, payload)

        def _is_a2a_stream(self, body: bytes) -> bool:
            if urlparse(self.path).path != "/a2a":
                return False
            try:
                request = json.loads(body or b"{}")
            except json.JSONDecodeError:
                return False
            return isinstance(request, dict) and request.get("method") == "message/stream"

        def _respond_sse(self, events: Any) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            for event in events:
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()

        def do_GET(self) -> None:  # noqa: N802 — http.server's required name
            self._respond("GET")

        def do_POST(self) -> None:  # noqa: N802 — http.server's required name
            self._respond("POST")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — overrides stdlib
            pass  # silence default stderr access logging

    return ThreadingHTTPServer((host, port), Handler)
