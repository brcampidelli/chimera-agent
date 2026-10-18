"""OAuth 2.1 for remote MCP servers: discovery, dynamic registration, PKCE, refresh, revocation.

Implements the client side of the MCP Authorization specification, revision **2025-11-25**
(https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization), which 2025-06-18
also satisfies for everything used here, and the RFCs it names:

* **RFC 9728** (protected resource metadata) — the server's ``401`` carries ``WWW-Authenticate:
  Bearer resource_metadata="…"``; without it the client MUST try
  ``/.well-known/oauth-protected-resource<path>`` and then the root, in that order.
* **RFC 8414 / OpenID Connect Discovery** — the authorization server's metadata, tried in the
  spec's order for an issuer with and without a path. Its ``issuer`` MUST be the one the resource
  advertised (RFC 8414 §3.3): anything else is refused as ``issuer_mismatch``, the mix-up attack.
* **RFC 7591** (dynamic client registration) — only when no ``oauth_client_id`` is configured and
  no client was registered with this issuer before. Client ID metadata documents (the spec's
  SHOULD) need a client hosted at an https URL; Chimera has none, so the order is: configured
  client, stored registration, DCR, and a clear refusal.
* **RFC 7636 + OAuth 2.1** — PKCE with ``S256`` only, and the spec's rule that a server whose
  metadata does not list ``S256`` in ``code_challenge_methods_supported`` is refused.
* **RFC 8252** — the redirect is ``http://127.0.0.1:<port>/callback`` on a listener bound to
  127.0.0.1 only; a request to any other path or Host is answered 404 and ignored, a wrong
  ``state`` aborts the sign-in, and the wait is bounded.
* **RFC 8707** — ``resource`` (the canonical URI of the MCP server) on the authorization and every
  token request.
* **RFC 9207** — an ``iss`` on the callback must name the issuer; required when the metadata says
  the server sends it.
* **RFC 7009** — revocation on disconnect, when the server offers it.

Who may start a sign-in is decided by the callers, not here: only :func:`login` opens a browser, and
only two places call it — ``chimera mcp login`` in an interactive terminal and the MCP screen's
Connect, a request that must carry the screen credential. Everything that connects unattended (the
pool, autoload, ``serve``, the bots, cron, the screen's Test) calls :func:`access_token_for`, which
uses or refreshes a stored token and otherwise answers None — and the session then refuses with
:class:`SignInRequired`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.parse
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx

from chimera.integrations import mcp_oauth_store as store
from chimera.telemetry import get_logger

_log = get_logger("integrations.mcp_oauth")

#: The protocol revision sent on the unauthenticated probe.
PROTOCOL_VERSION = "2025-11-25"
_HTTP_TIMEOUT = 15.0
#: An access token this close to its expiry is refreshed before use rather than sent to fail.
_EXPIRY_MARGIN = 60.0
_CALLBACK_PATH = "/callback"
_LOOPBACK = ("127.0.0.1", "localhost", "::1")
_IDENTITY_MAX = 80

#: The codes a failed sign-in is reported with. An enum, not prose: the screen ships in ten
#: languages and translates the ones a person can act on.
CODES = frozenset(
    {
        "not_http", "not_required", "discovery", "insecure_endpoint", "resource_mismatch",
        "issuer_mismatch", "pkce_unsupported", "no_registration", "registration", "browser",
        "timeout", "state_mismatch", "denied", "token_exchange", "storage", "declined",
    }
)


class OAuthError(Exception):
    """A sign-in that did not complete, with a code from :data:`CODES`. The message carries no
    token, code, verifier or server response body."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code if code in CODES else "discovery"


class NotRequired(OAuthError):
    def __init__(self) -> None:
        super().__init__("not_required", "this MCP server answered without asking for a sign-in")


class SignInRequired(PermissionError):
    """An unattended connect found no usable sign-in. Nothing here may open a browser."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"MCP server {name!r} precisa conectar no app — needs to be connected first: "
            f"press Connect on the app's MCP screen, or run `chimera mcp login {name}` in a terminal"
        )


# --- URLs ------------------------------------------------------------------------------------------


def check_url(label: str, url: str | None) -> str:
    """``url`` when it is https, or http to loopback, with no fragment and no user info."""
    parts = urllib.parse.urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if parts.fragment or parts.username or parts.password or not host:
        raise OAuthError("insecure_endpoint", f"MCP OAuth {label} is not a usable absolute URL")
    if parts.scheme == "https" or (parts.scheme == "http" and host in _LOOPBACK):
        return str(url)
    raise OAuthError("insecure_endpoint", f"MCP OAuth {label} must be https (or http on loopback)")


def _origin(url: str) -> tuple[str, str, int]:
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    port = parts.port or (443 if scheme == "https" else 80)
    return scheme, (parts.hostname or "").lower(), port


def canonical_resource(url: str) -> str:
    """The canonical URI of an MCP server (RFC 8707 §2): lowercase scheme and host, no query or
    fragment, no trailing slash unless the path is only one."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    path = parts.path.rstrip("/")
    return urllib.parse.urlunsplit((parts.scheme.lower(), netloc, path, "", ""))


def _path_within(resource_path: str, server_path: str) -> bool:
    base = resource_path.rstrip("/")
    return not base or server_path.rstrip("/") == base or server_path.startswith(base + "/")


def parse_www_authenticate(header: str) -> dict[str, str]:
    """The parameters of the ``Bearer`` challenge in a ``WWW-Authenticate`` value; ``{}`` if none."""
    text = header.strip()
    index = text.lower().find("bearer")
    if index < 0:
        return {}
    rest = text[index + len("bearer") :]
    out: dict[str, str] = {}
    i = 0
    while i < len(rest):
        while i < len(rest) and rest[i] in " ,\t":
            i += 1
        start = i
        while i < len(rest) and rest[i] not in "= ,":
            i += 1
        key = rest[start:i].strip().lower()
        if i >= len(rest) or rest[i] != "=":
            # A token with no `=` starts the next scheme: the Bearer challenge is over.
            break
        i += 1
        if i < len(rest) and rest[i] == '"':
            i += 1
            value_chars: list[str] = []
            while i < len(rest) and rest[i] != '"':
                if rest[i] == "\\" and i + 1 < len(rest):
                    i += 1
                value_chars.append(rest[i])
                i += 1
            i += 1
            value = "".join(value_chars)
        else:
            start = i
            while i < len(rest) and rest[i] not in " ,":
                i += 1
            value = rest[start:i]
        if key:
            out.setdefault(key, value)
    return out


# --- discovery -------------------------------------------------------------------------------------


@dataclass
class Discovery:
    resource: str
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None
    revocation_endpoint: str | None = None
    scope: str | None = None
    iss_required: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@contextmanager
def _client(http: httpx.Client | None) -> Iterator[httpx.Client]:
    """The caller's client, or one made (and closed) here. No redirects: a metadata or token
    endpoint that redirects is sending the request somewhere the checks above never saw."""
    if http is not None:
        yield http
        return
    with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=False) as made:
        yield made


def _get_json(http: httpx.Client, url: str) -> dict[str, Any] | None:
    try:
        response = http.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def probe(server_url: str, http: httpx.Client) -> dict[str, str] | None:
    """Send an unauthenticated MCP ``initialize``. The Bearer challenge's parameters when the
    server answers 401 (``{}`` with no usable header), None when it answers without asking."""
    body = {
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                   "clientInfo": {"name": "chimera", "version": "oauth-probe"}},
    }
    try:
        response = http.post(
            server_url, json=body,
            headers={"Accept": "application/json, text/event-stream",
                     "MCP-Protocol-Version": PROTOCOL_VERSION},
        )
    except httpx.HTTPError as exc:
        raise OAuthError("discovery", f"MCP server did not answer ({type(exc).__name__})") from None
    if response.status_code == 401:
        return parse_www_authenticate(response.headers.get("www-authenticate", ""))
    if 200 <= response.status_code < 300:
        return None
    # Some servers answer an unauthenticated POST with 400/403/405; the well-known document is
    # still the authority on whether they want a sign-in.
    return {}


def _protected_resource(server_url: str, challenge: dict[str, str], http: httpx.Client) -> dict[str, Any]:
    scheme, host, port = _origin(server_url)
    parts = urllib.parse.urlsplit(server_url)
    root = f"{parts.scheme}://{parts.netloc}"
    candidates: list[str] = []
    advertised = challenge.get("resource_metadata")
    if advertised:
        check_url("resource metadata URL", advertised)
        # Same origin only: a server that points the client at another host's document is asking it
        # to trust that host about which authorization server to sign in with.
        if _origin(advertised) != (scheme, host, port):
            raise OAuthError("resource_mismatch", "MCP resource metadata is on another origin")
        candidates.append(advertised)
    else:
        path = parts.path.rstrip("/")
        if path:
            candidates.append(f"{root}/.well-known/oauth-protected-resource{path}")
        candidates.append(f"{root}/.well-known/oauth-protected-resource")
    for url in candidates:
        document = _get_json(http, url)
        if document is not None:
            return document
    raise OAuthError("discovery", "MCP server publishes no protected resource metadata")


def _authorization_server(issuer: str, http: httpx.Client) -> dict[str, Any]:
    parts = urllib.parse.urlsplit(issuer)
    root = f"{parts.scheme}://{parts.netloc}"
    path = parts.path.rstrip("/")
    if path:
        candidates = [
            f"{root}/.well-known/oauth-authorization-server{path}",
            f"{root}/.well-known/openid-configuration{path}",
            f"{root}{path}/.well-known/openid-configuration",
        ]
    else:
        candidates = [
            f"{root}/.well-known/oauth-authorization-server",
            f"{root}/.well-known/openid-configuration",
        ]
    for url in candidates:
        document = _get_json(http, url)
        if document is not None:
            return document
    raise OAuthError("discovery", "the authorization server publishes no metadata")


def _same_issuer(a: str, b: str) -> bool:
    # RFC 8414 asks for identical strings; a trailing slash is the one difference real servers show
    # between their resource metadata and their own `issuer`, and it names the same server.
    return a.rstrip("/") == b.rstrip("/")


def discover(server_url: str, *, http: httpx.Client | None = None) -> Discovery:
    """Find where to sign in for ``server_url``, checking every link of the chain."""
    check_url("server URL", server_url)
    with _client(http) as client:
        return _discover(server_url, client)


def _discover(server_url: str, client: httpx.Client) -> Discovery:
    challenge = probe(server_url, client)
    if challenge is None:
        raise NotRequired()
    prm = _protected_resource(server_url, challenge, client)
    resource = prm.get("resource")
    if not isinstance(resource, str) or not resource:
        raise OAuthError("resource_mismatch", "MCP resource metadata names no resource")
    check_url("resource", resource)
    # RFC 9728 §3.3 / §7.3: the metadata must be about the server that was asked.
    if _origin(resource) != _origin(server_url) or not _path_within(
        urllib.parse.urlsplit(resource).path, urllib.parse.urlsplit(server_url).path
    ):
        raise OAuthError("resource_mismatch", "MCP resource metadata describes another resource")
    servers = prm.get("authorization_servers")
    if not isinstance(servers, list) or not servers or not isinstance(servers[0], str):
        raise OAuthError("discovery", "MCP resource metadata names no authorization server")
    issuer = check_url("issuer", servers[0])
    asm = _authorization_server(issuer, client)
    if not isinstance(asm.get("issuer"), str) or not _same_issuer(asm["issuer"], issuer):
        raise OAuthError(
            "issuer_mismatch",
            "the authorization server's issuer is not the one the MCP server advertised",
        )
    methods = asm.get("code_challenge_methods_supported")
    if not isinstance(methods, list) or "S256" not in methods:
        raise OAuthError("pkce_unsupported", "the authorization server does not offer PKCE S256")
    authorization = check_url("authorization endpoint", asm.get("authorization_endpoint"))
    token = check_url("token endpoint", asm.get("token_endpoint"))
    registration = asm.get("registration_endpoint")
    revocation = asm.get("revocation_endpoint")
    scope = challenge.get("scope")
    if not scope:
        supported = prm.get("scopes_supported")
        if isinstance(supported, list) and all(isinstance(s, str) for s in supported) and supported:
            scope = " ".join(supported)
    return Discovery(
        resource=canonical_resource(resource),
        issuer=issuer,
        authorization_endpoint=authorization,
        token_endpoint=token,
        registration_endpoint=check_url("registration endpoint", registration) if registration else None,
        revocation_endpoint=check_url("revocation endpoint", revocation) if revocation else None,
        scope=scope or None,
        iss_required=asm.get("authorization_response_iss_parameter_supported") is True,
        metadata=asm,
    )


def requires_oauth(server_url: str, *, http: httpx.Client | None = None) -> bool:
    """Whether ``server_url`` asks for an OAuth sign-in. One unauthenticated request; never raises."""
    try:
        discover(server_url, http=http)
    except OAuthError:
        return False
    return True


# --- the loopback redirect -------------------------------------------------------------------------


class _Loopback:
    """A one-sign-in HTTP listener on 127.0.0.1, answering exactly one path and one Host."""

    def __init__(self, port: int = 0, path: str = _CALLBACK_PATH, host: str = "127.0.0.1") -> None:
        if host not in ("127.0.0.1", "localhost"):
            raise OAuthError("insecure_endpoint", "the OAuth redirect must be loopback")
        self.result: dict[str, str] = {}
        self.refused: str | None = None
        self.done = threading.Event()
        listener = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                listener._handle(self)

            def log_message(self, *_args: Any) -> None:
                return  # the query carries the code; nothing about it is logged

        self.server = HTTPServer(("127.0.0.1", port), Handler)
        self.server.timeout = 0.25
        self.port = int(self.server.server_port)
        self.path = path
        self.host = host
        self.redirect_uri = f"http://{host}:{self.port}{path}"
        self.state = ""
        self.issuer: str | None = None

    def _handle(self, request: BaseHTTPRequestHandler) -> None:
        split = urllib.parse.urlsplit(request.path)
        host_header = (request.headers.get("Host") or "").lower()
        if split.path != self.path or host_header not in (
            f"{self.host}:{self.port}", f"127.0.0.1:{self.port}"
        ) or self.done.is_set():
            request.send_response(404)
            request.end_headers()
            return
        query = {k: v[0] for k, v in urllib.parse.parse_qs(split.query).items() if v}
        if not secrets.compare_digest(query.get("state", ""), self.state):
            self.refused = "state_mismatch"
        elif (
            query.get("iss") is not None
            and self.issuer is not None
            and not _same_issuer(query["iss"], self.issuer)
        ):
            # RFC 9207: a callback naming another issuer is the mix-up attack. With typed endpoints
            # (the pre-registered path) there is no issuer to compare against.
            self.refused = "issuer_mismatch"
        else:
            self.result = query
        ok = bool(self.result.get("code")) and self.refused is None
        request.send_response(200 if ok else 400)
        request.send_header("Content-Type", "text/plain; charset=utf-8")
        request.end_headers()
        request.wfile.write(
            b"Chimera: sign-in received. You can close this window."
            if ok else b"Chimera: this sign-in was refused. You can close this window."
        )
        self.done.set()

    def wait(self, state: str, issuer: str | None, timeout: float) -> dict[str, str]:
        self.state, self.issuer = state, issuer
        deadline = time.monotonic() + timeout
        try:
            while not self.done.is_set() and time.monotonic() < deadline:
                self.server.handle_request()
        finally:
            self.server.server_close()
        if self.refused is not None:
            raise OAuthError(self.refused, "the authorization callback was refused")
        if not self.done.is_set():
            raise OAuthError("timeout", "the sign-in did not complete in time")
        if self.result.get("error"):
            raise OAuthError("denied", "the authorization server refused the sign-in")
        if not self.result.get("code"):
            raise OAuthError("denied", "the authorization callback carried no code")
        return self.result

    def close(self) -> None:
        self.server.server_close()


# --- tokens ----------------------------------------------------------------------------------------


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode()
    return verifier, challenge.rstrip("=")


def _token_request(
    http: httpx.Client, endpoint: str, data: dict[str, str], client: dict[str, Any]
) -> dict[str, Any]:
    data = dict(data)
    extra: dict[str, Any] = {}
    secret = client.get("client_secret")
    method = client.get("token_endpoint_auth_method") or ("client_secret_basic" if secret else "none")
    if secret and method == "client_secret_basic":
        extra["auth"] = httpx.BasicAuth(str(client["client_id"]), str(secret))
    else:
        data["client_id"] = str(client["client_id"])
        if secret and method == "client_secret_post":
            data["client_secret"] = str(secret)
    try:
        response = http.post(endpoint, data=data, headers={"Accept": "application/json"}, **extra)
    except httpx.HTTPError as exc:
        raise OAuthError("token_exchange", f"token endpoint unreachable ({type(exc).__name__})") from None
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code != 200 or not isinstance(body, dict):
        error = body.get("error") if isinstance(body, dict) else None
        # The error code is a fixed RFC 6749 word; the description is the server's text and stays out.
        raise OAuthError("token_exchange", f"token endpoint refused ({error or response.status_code})")
    if not isinstance(body.get("access_token"), str) or not body["access_token"]:
        raise OAuthError("token_exchange", "token endpoint returned no access token")
    token_type = body.get("token_type")
    if isinstance(token_type, str) and token_type.lower() != "bearer":
        raise OAuthError("token_exchange", "token endpoint returned a token that is not Bearer")
    return body


def _tokens(body: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    expires_in = body.get("expires_in")
    expires_at: float | None = None
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        expires_at = time.time() + float(expires_in)
    refresh = body.get("refresh_token")
    if not isinstance(refresh, str) or not refresh:
        # A refresh response may leave the refresh token out, meaning "keep the one you have".
        refresh = (previous or {}).get("refresh_token")
    return {
        "access_token": body["access_token"],
        "refresh_token": refresh,
        "expires_at": expires_at,
        "scope": body.get("scope") if isinstance(body.get("scope"), str) else None,
    }


def _clean(text: Any) -> str | None:
    if not isinstance(text, str):
        return None
    cleaned = "".join(ch for ch in text if ch.isprintable()).strip()
    return cleaned[:_IDENTITY_MAX] or None


def _jwt_claims(token: Any) -> dict[str, Any]:
    if not isinstance(token, str) or token.count(".") != 2:
        return {}
    try:
        payload = token.split(".")[1]
        decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, TypeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def identity_from(body: dict[str, Any]) -> str | None:
    """Who the server says signed in, for "connected as …". Display only, never a decision: an
    ``id_token`` is read without verifying its signature, because nothing here trusts it."""
    sources: list[dict[str, Any]] = [body, _jwt_claims(body.get("id_token"))]
    for container in ("user", "owner", "account"):
        nested = body.get(container)
        if isinstance(nested, dict):
            sources.append(nested)
            inner = nested.get("user")
            if isinstance(inner, dict):
                sources.append(inner)
    for key in ("email", "preferred_username", "username", "login", "name", "workspace_name"):
        for source in sources:
            found = _clean(source.get(key))
            if found:
                return found
    return None


# --- client registration ---------------------------------------------------------------------------


def _register(http: httpx.Client, endpoint: str, redirect_uri: str, scope: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "client_name": "Chimera",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "application_type": "native",
    }
    if scope:
        payload["scope"] = scope
    try:
        response = http.post(endpoint, json=payload, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise OAuthError("registration", f"registration endpoint unreachable ({type(exc).__name__})") from None
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code not in (200, 201) or not isinstance(body, dict) or not isinstance(
        body.get("client_id"), str
    ):
        raise OAuthError("registration", f"dynamic client registration refused ({response.status_code})")
    return {
        "client_id": body["client_id"],
        "client_secret": body.get("client_secret") if isinstance(body.get("client_secret"), str) else None,
        "token_endpoint_auth_method": body.get("token_endpoint_auth_method") or "none",
        "redirect_uri": redirect_uri,
        "source": "dynamic",
    }


def _bind(port: int, path: str, host: str = "127.0.0.1") -> _Loopback | None:
    try:
        return _Loopback(port, path, host)
    except OSError:
        return None


# --- the sign-in -----------------------------------------------------------------------------------


@dataclass
class _Plan:
    resource: str
    issuer: str | None
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str | None
    registration_endpoint: str | None
    scope: str | None
    iss_required: bool


def _plan(cfg: Any, http: httpx.Client) -> _Plan:
    if cfg.oauth_authorization_url and cfg.oauth_token_url:
        # The pre-registered path: the owner typed the endpoints, so there is nothing to discover
        # and no issuer to compare against. Kept exactly as it worked before discovery existed.
        return _Plan(
            resource=canonical_resource(cfg.url),
            issuer=None,
            authorization_endpoint=check_url("authorization URL", cfg.oauth_authorization_url),
            token_endpoint=check_url("token URL", cfg.oauth_token_url),
            revocation_endpoint=None,
            registration_endpoint=None,
            scope=cfg.oauth_scope,
            iss_required=False,
        )
    found = _discover(cfg.url, http)
    return _Plan(
        resource=found.resource,
        issuer=found.issuer,
        authorization_endpoint=found.authorization_endpoint,
        token_endpoint=found.token_endpoint,
        revocation_endpoint=found.revocation_endpoint,
        registration_endpoint=found.registration_endpoint,
        scope=cfg.oauth_scope or found.scope,
        iss_required=found.iss_required,
    )


def _listener_and_client(
    cfg: Any, plan: _Plan, stored: dict[str, Any] | None, http: httpx.Client
) -> tuple[_Loopback, dict[str, Any]]:
    if cfg.oauth_client_id:
        client = {"client_id": cfg.oauth_client_id, "source": "configured",
                  "token_endpoint_auth_method": "none"}
        if cfg.oauth_redirect_uri:
            parts = urllib.parse.urlsplit(cfg.oauth_redirect_uri)
            if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost"):
                raise OAuthError("insecure_endpoint", "MCP OAuth redirect URI must be a loopback HTTP URL")
            listener = _bind(parts.port or 80, parts.path or "/", parts.hostname or "127.0.0.1")
            if listener is None:
                raise OAuthError("browser", "the configured loopback redirect port is in use")
            return listener, client
        listener = _bind(0, _CALLBACK_PATH)
        if listener is None:
            raise OAuthError("browser", "could not open a loopback listener")
        return listener, client
    previous = (stored or {}).get("client")
    if (
        isinstance(previous, dict)
        and previous.get("source") == "dynamic"
        and plan.issuer is not None
        and (stored or {}).get("issuer") == plan.issuer
        and isinstance(previous.get("redirect_uri"), str)
    ):
        # A client registered with THIS issuer is reused on the port it was registered for; a
        # registration made with another issuer is never shown to this one.
        parts = urllib.parse.urlsplit(previous["redirect_uri"])
        listener = _bind(parts.port or 0, parts.path or _CALLBACK_PATH)
        if listener is not None and listener.redirect_uri == previous["redirect_uri"]:
            return listener, dict(previous)
        if listener is not None:
            listener.close()
    if not plan.registration_endpoint:
        raise OAuthError(
            "no_registration",
            "this server offers no dynamic client registration; configure oauth_client_id",
        )
    listener = _bind(0, _CALLBACK_PATH)
    if listener is None:
        raise OAuthError("browser", "could not open a loopback listener")
    try:
        client = _register(http, plan.registration_endpoint, listener.redirect_uri, plan.scope)
    except BaseException:
        listener.close()
        raise
    return listener, client


def login(
    cfg: Any,
    home: Path,
    *,
    open_browser: Callable[[str], bool],
    confirm: Callable[[str], bool] | None = None,
    timeout: float = 180.0,
    http: httpx.Client | None = None,
) -> dict[str, Any]:
    """Sign in to ``cfg``'s server through the browser and store the result. ATTENDED ONLY.

    ``confirm`` is shown the authorization server's host before anything opens; answering no ends
    the sign-in with ``declined``. Returns :func:`state` of the stored record.
    """
    if not getattr(cfg, "url", None):
        raise OAuthError("not_http", "only streamable-HTTP MCP servers sign in with OAuth")
    check_url("server URL", cfg.url)
    with _client(http) as client_http:
        return _login(cfg, home, client_http, open_browser=open_browser, confirm=confirm, timeout=timeout)


def _login(
    cfg: Any,
    home: Path,
    client_http: httpx.Client,
    *,
    open_browser: Callable[[str], bool],
    confirm: Callable[[str], bool] | None,
    timeout: float,
) -> dict[str, Any]:
    plan = _plan(cfg, client_http)
    with store.LOCK:
        stored = store.load(home, cfg.name)
    listener, client = _listener_and_client(cfg, plan, stored, client_http)
    try:
        host = urllib.parse.urlsplit(plan.authorization_endpoint).hostname or ""
        if confirm is not None and not confirm(host):
            raise OAuthError("declined", "the sign-in was declined")
        verifier, challenge = _pkce()
        state = secrets.token_urlsafe(32)
        params = {
            "response_type": "code",
            "client_id": str(client["client_id"]),
            "redirect_uri": listener.redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "resource": plan.resource,
        }
        if plan.scope:
            params["scope"] = plan.scope
        separator = "&" if urllib.parse.urlsplit(plan.authorization_endpoint).query else "?"
        url = f"{plan.authorization_endpoint}{separator}{urllib.parse.urlencode(params)}"
        if not open_browser(url):
            raise OAuthError("browser", "the authorization page could not be opened")
        result = listener.wait(state, plan.issuer, timeout)
    finally:
        listener.close()
    if plan.iss_required and "iss" not in result:
        raise OAuthError("issuer_mismatch", "the callback did not say which issuer sent it")
    body = _token_request(
        client_http, plan.token_endpoint,
        {"grant_type": "authorization_code", "code": result["code"],
         "redirect_uri": listener.redirect_uri, "code_verifier": verifier, "resource": plan.resource},
        client,
    )
    record = {
        "version": 1,
        "resource_url": canonical_resource(cfg.url),
        "resource": plan.resource,
        "issuer": plan.issuer,
        "token_endpoint": plan.token_endpoint,
        "revocation_endpoint": plan.revocation_endpoint,
        "client": client,
        "tokens": _tokens(body),
        "identity": identity_from(body),
        "connected_at": time.time(),
    }
    with store.LOCK:
        try:
            store.save(home, cfg.name, record)
        except OSError as exc:
            raise OAuthError("storage", f"the sign-in could not be stored ({type(exc).__name__})") from None
    _log.info("MCP OAuth sign-in stored for a configured server")
    return store.summary(record)


# --- unattended use --------------------------------------------------------------------------------


def _bound(cfg: Any, record: dict[str, Any]) -> bool:
    """Whether ``record`` was obtained for the server ``cfg`` points at NOW. A server re-pointed at
    another URL under the same name must not receive the first one's token."""
    try:
        return record.get("resource_url") == canonical_resource(cfg.url)
    except (TypeError, ValueError):
        return False


def has_record(cfg: Any, home: Path) -> bool:
    record = store.load(home, cfg.name)
    return bool(record) and _bound(cfg, record or {})


def access_token_for(
    cfg: Any, home: Path, *, force_refresh: bool = False, http: httpx.Client | None = None
) -> str | None:
    """A usable access token for ``cfg`` without any person: the stored one, or a refreshed one.

    None when there is no sign-in for this URL or it can no longer be refreshed — never a browser.
    """
    with store.LOCK:
        record = store.load(home, cfg.name)
        if not record or not _bound(cfg, record):
            return None
        raw_tokens = record.get("tokens")
        tokens: dict[str, Any] = raw_tokens if isinstance(raw_tokens, dict) else {}
        access = tokens.get("access_token")
        expires_at = tokens.get("expires_at")
        fresh = isinstance(access, str) and access and (
            not isinstance(expires_at, (int, float)) or time.time() < expires_at - _EXPIRY_MARGIN
        )
        if fresh and not force_refresh:
            return str(access)
        refresh = tokens.get("refresh_token")
        client = record.get("client")
        endpoint = record.get("token_endpoint")
        if not isinstance(refresh, str) or not refresh or not isinstance(client, dict) or not endpoint:
            return str(access) if fresh else None
        try:
            with _client(http) as client_http:
                body = _token_request(
                    client_http, str(endpoint),
                    {"grant_type": "refresh_token", "refresh_token": refresh,
                     "resource": str(record.get("resource") or canonical_resource(cfg.url))},
                    client,
                )
        except OAuthError as exc:
            if "invalid_grant" in str(exc):
                # The refresh token is dead (revoked, rotated elsewhere, expired). Keep the client,
                # drop the tokens: the next connect says "connect in the app" instead of retrying.
                record["tokens"] = {}
                store.save(home, cfg.name, record)
                return None
            raise
        record["tokens"] = _tokens(body, tokens)
        new_identity = identity_from(body)
        if new_identity:
            record["identity"] = new_identity
        store.save(home, cfg.name, record)
        return str(record["tokens"]["access_token"])


class StoredTokenAuth(httpx.Auth):
    """Puts the stored token on every request to the MCP server, refreshing it as it expires.

    A pool session lives for the whole process and an access token for an hour; a header fixed at
    connect time would stop working mid-run. On a 401 the token is refreshed once and the request
    retried. The header goes only to the server's own origin.
    """

    def __init__(self, cfg: Any, home: Path) -> None:
        self.cfg = cfg
        self.home = Path(home)
        self.origin = _origin(cfg.url)

    def _token(self, force: bool) -> str | None:
        try:
            return access_token_for(self.cfg, self.home, force_refresh=force)
        except OAuthError:
            return None

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        if _origin(str(request.url)) != self.origin:
            yield request
            return
        token = self._token(False)
        if token is None:
            raise SignInRequired(self.cfg.name)
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request
        if response.status_code == 401:
            renewed = self._token(True)
            if renewed and renewed != token:
                request.headers["Authorization"] = f"Bearer {renewed}"
                yield request


def disconnect(cfg: Any, home: Path, *, http: httpx.Client | None = None) -> dict[str, bool]:
    """Revoke the stored tokens where the server offers revocation, then forget them."""
    revoked = False
    with store.LOCK:
        record = store.load(home, cfg.name)
        tokens = (record or {}).get("tokens")
        endpoint = (record or {}).get("revocation_endpoint")
        client = (record or {}).get("client")
        if isinstance(tokens, dict) and endpoint and isinstance(client, dict):
            for hint in ("refresh_token", "access_token"):
                value = tokens.get(hint)
                if not isinstance(value, str) or not value:
                    continue
                try:
                    with _client(http) as client_http:
                        revoked = _revoke(client_http, str(endpoint), value, hint, client) or revoked
                except httpx.HTTPError:
                    continue
        deleted = False
        if record is not None:
            record["tokens"] = {}
            record["identity"] = None
            store.save(home, cfg.name, record)
            deleted = True
    from chimera import config_vault

    # The single-token entry the pre-registered path wrote before this module existed.
    kr = config_vault._keyring()
    if kr is not None:
        try:
            kr.delete_password(config_vault.SERVICE, config_vault._mcp_vault_account(cfg.name))
            deleted = True
        except Exception:  # noqa: BLE001 — absent is the common case
            pass
    return {"revoked": revoked, "deleted": deleted}


def _revoke(http: httpx.Client, endpoint: str, token: str, hint: str, client: dict[str, Any]) -> bool:
    data = {"token": token, "token_type_hint": hint}
    extra: dict[str, Any] = {}
    secret = client.get("client_secret")
    if secret and client.get("token_endpoint_auth_method") == "client_secret_basic":
        extra["auth"] = httpx.BasicAuth(str(client["client_id"]), str(secret))
    else:
        data["client_id"] = str(client["client_id"])
        if secret:
            data["client_secret"] = str(secret)
    # RFC 7009 §2.2: 200 whether or not the token was valid; anything else revoked nothing.
    return http.post(endpoint, data=data, **extra).status_code == 200


def state(cfg: Any, home: Path) -> dict[str, Any]:
    """``{connected, identity}`` for a route. Carries no token."""
    record = store.load(home, cfg.name)
    if record is None or not _bound(cfg, record):
        return store.summary(None)
    return store.summary(record)
