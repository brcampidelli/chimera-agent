"""M1: OAuth 2.1 with discovery, dynamic client registration and PKCE for remote MCP servers.

Everything runs against a fake authorization server and resource server held in this process
(``httpx.MockTransport``): no request leaves the machine. The one real socket is the loopback
redirect listener on 127.0.0.1, which is the thing under test, and a fake "browser" that answers the
authorization page the way a server would and then follows the redirect to it.

Each guard is shown both ways, so none of them can pass by being inert: the flow that should work
works (the positive control), and the one change that should break it — a wrong ``state``, another
issuer, metadata about another resource, a server without S256 — is refused, with nothing stored.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from chimera.config import get_settings
from chimera.integrations import mcp_oauth
from chimera.integrations import mcp_oauth_store as store
from chimera.integrations.mcp_config import McpServerConfig, add_server, load_servers, remove_server

RS = "https://mcp.example.test/mcp"
ISSUER = "https://auth.example.test"


class FakeKeyring:
    """The OS vault, in memory, with Windows' per-entry limit enforced."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.entries.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        if len(value.encode("utf-16-le")) > 5 * 512:
            raise RuntimeError("CredWrite: blob too large")
        self.entries[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        if (service, account) not in self.entries:
            raise KeyError(account)
        del self.entries[(service, account)]


class FakeOAuth:
    """A resource server and its authorization server, answering through ``httpx.MockTransport``."""

    def __init__(self, **options: Any) -> None:
        self.options: dict[str, Any] = {
            "header": True, "resource": RS, "metadata_url": "https://mcp.example.test/.well-known/oauth-protected-resource/mcp",
            "issuer_advertised": ISSUER, "issuer_actual": ISSUER, "pkce": ["S256"],
            "registration": True, "revocation": True, "scope": "read write", "iss_param": False,
            "access_ttl": 3600, "identity": {"email": "bruno@example.test"}, "access_pad": "",
        }
        self.options.update(options)
        self.clients: dict[str, list[str]] = {}
        self.codes: dict[str, dict[str, str]] = {}
        self.refresh_tokens: dict[str, str] = {}
        self.issued: list[str] = []
        self.calls: list[str] = []
        self.token_requests: list[dict[str, str]] = []
        self.authorize_params: list[dict[str, str]] = []
        self.revoked: list[str] = []
        self.counter = 0

    # --- transport ---------------------------------------------------------------------------------

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle), follow_redirects=False)

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        self.calls.append(f"{request.method} {url}")
        o = self.options
        if url == RS and request.method == "POST":
            auth = request.headers.get("authorization", "")
            if auth.removeprefix("Bearer ") in self.issued:
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": 0, "result": {}})
            headers = {}
            if o["header"]:
                headers["WWW-Authenticate"] = (
                    f'Bearer resource_metadata="{o["metadata_url"]}", scope="{o["scope"]}"'
                )
            return httpx.Response(401, headers=headers)
        if url in (o["metadata_url"], "https://mcp.example.test/.well-known/oauth-protected-resource/mcp"):
            return httpx.Response(200, json={
                "resource": o["resource"], "authorization_servers": [o["issuer_advertised"]],
                "scopes_supported": ["read"],
            })
        if url == f"{ISSUER}/.well-known/oauth-authorization-server":
            meta: dict[str, Any] = {
                "issuer": o["issuer_actual"],
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
            }
            if o["pkce"] is not None:
                meta["code_challenge_methods_supported"] = o["pkce"]
            if o["registration"]:
                meta["registration_endpoint"] = f"{ISSUER}/register"
            if o["revocation"]:
                meta["revocation_endpoint"] = f"{ISSUER}/revoke"
            if o["iss_param"]:
                meta["authorization_response_iss_parameter_supported"] = True
            return httpx.Response(200, json=meta)
        if url == f"{ISSUER}/register" and request.method == "POST":
            body = json.loads(request.content)
            self.counter += 1
            client_id = f"client-{self.counter}"
            self.clients[client_id] = list(body["redirect_uris"])
            return httpx.Response(201, json={"client_id": client_id, "token_endpoint_auth_method": "none"})
        if url == f"{ISSUER}/token" and request.method == "POST":
            return self._token(dict(urllib.parse.parse_qsl(request.content.decode())))
        if url == f"{ISSUER}/revoke" and request.method == "POST":
            form = dict(urllib.parse.parse_qsl(request.content.decode()))
            self.revoked.append(form["token"])
            return httpx.Response(200)
        return httpx.Response(404)

    def _issue(self, client_id: str) -> dict[str, Any]:
        self.counter += 1
        access = f"at-{self.counter}-secret{self.options['access_pad']}"
        refresh = f"rt-{self.counter}-secret"
        self.issued.append(access)
        self.refresh_tokens[refresh] = client_id
        return {"access_token": access, "token_type": "Bearer", "refresh_token": refresh,
                "expires_in": self.options["access_ttl"], **self.options["identity"]}

    def _token(self, form: dict[str, str]) -> httpx.Response:
        self.token_requests.append(form)
        if form.get("grant_type") == "authorization_code":
            grant = self.codes.pop(form.get("code", ""), None)
            if grant is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
            challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
            if (challenge != grant["code_challenge"] or form.get("redirect_uri") != grant["redirect_uri"]
                    or form.get("client_id") != grant["client_id"] or form.get("resource") != grant["resource"]):
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json=self._issue(grant["client_id"]))
        if form.get("grant_type") == "refresh_token":
            client_id = self.refresh_tokens.pop(form.get("refresh_token", ""), None)
            if client_id is None or form.get("resource") != RS:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "rt-dead-secret"})
            return httpx.Response(200, json=self._issue(client_id))
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    # --- the authorization page --------------------------------------------------------------------

    def authorize(self, url: str) -> str:
        """What the authorization server does when the browser opens ``url``: the redirect it sends."""
        parsed = urllib.parse.urlsplit(url)
        assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == f"{ISSUER}/authorize"
        params = dict(urllib.parse.parse_qsl(parsed.query))
        self.authorize_params.append(params)
        client_id = params["client_id"]
        redirect = params["redirect_uri"]
        if client_id in self.clients:
            # The AS validates the redirect EXACTLY against what was registered (open-redirect rule).
            assert redirect in self.clients[client_id]
        assert params["code_challenge_method"] == "S256"
        code = f"code-{len(self.codes) + len(self.issued)}"
        self.codes[code] = {"client_id": client_id, "redirect_uri": redirect,
                            "code_challenge": params["code_challenge"], "resource": params["resource"]}
        query = {"code": code, "state": params["state"]}
        if self.options["iss_param"]:
            query["iss"] = self.options.get("iss_sent", ISSUER)
        return f"{redirect}?{urllib.parse.urlencode(query)}"


def browser(fake: FakeOAuth, tamper: Callable[[str], str] | None = None) -> tuple[Callable[[str], bool], list[str]]:
    opened: list[str] = []

    def open_(url: str) -> bool:
        opened.append(url)
        redirect = fake.authorize(url)
        if tamper is not None:
            redirect = tamper(redirect)
        threading.Thread(target=lambda: httpx.get(redirect, timeout=5), daemon=True).start()
        return True

    return open_, opened


@pytest.fixture
def home() -> Path:
    return Path(get_settings().home)


@pytest.fixture(autouse=True)
def no_real_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never the developer's real keychain: the file store unless a test hands in a fake vault."""
    from chimera import config_vault

    monkeypatch.setattr(config_vault, "_keyring", lambda: None)


@pytest.fixture(autouse=True)
def no_real_browser(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    import webbrowser

    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    return opened


def _cfg(**kw: Any) -> McpServerConfig:
    return McpServerConfig(name="notion", url=RS, **kw)


def _login(fake: FakeOAuth, home: Path, cfg: McpServerConfig | None = None, **kw: Any) -> dict[str, Any]:
    open_, _ = browser(fake, kw.pop("tamper", None))
    with fake.client() as http:
        return mcp_oauth.login(cfg or _cfg(), home, open_browser=open_, timeout=5, http=http, **kw)


# --- discovery -------------------------------------------------------------------------------------


def test_discovery_follows_the_header_then_the_authorization_server_metadata() -> None:
    fake = FakeOAuth()
    with fake.client() as http:
        found = mcp_oauth.discover(RS, http=http)
    assert found.issuer == ISSUER
    assert found.resource == RS
    assert found.token_endpoint == f"{ISSUER}/token"
    assert found.registration_endpoint == f"{ISSUER}/register"
    # The challenge's scope is authoritative over scopes_supported (spec 2025-11-25).
    assert found.scope == "read write"


def test_discovery_falls_back_to_the_well_known_document_without_a_header() -> None:
    fake = FakeOAuth(header=False)
    with fake.client() as http:
        found = mcp_oauth.discover(RS, http=http)
    assert "GET https://mcp.example.test/.well-known/oauth-protected-resource/mcp" in fake.calls
    assert found.scope == "read"  # no challenge scope: scopes_supported


def test_a_server_that_answers_without_a_sign_in_is_not_required() -> None:
    fake = FakeOAuth()
    fake.issued.append("x")
    with fake.client() as http, pytest.raises(mcp_oauth.NotRequired):
        http.headers["Authorization"] = "Bearer x"
        mcp_oauth.discover(RS, http=http)


def test_the_header_parser_reads_quoted_parameters() -> None:
    params = mcp_oauth.parse_www_authenticate(
        'Bearer realm="x", resource_metadata="https://a.test/.well-known/oauth-protected-resource", '
        'scope="files:read files:write", error="invalid_token"'
    )
    assert params["resource_metadata"] == "https://a.test/.well-known/oauth-protected-resource"
    assert params["scope"] == "files:read files:write"


# --- the whole sign-in -----------------------------------------------------------------------------


def test_dcr_pkce_and_resource_from_discovery_to_a_stored_sign_in(home: Path) -> None:
    fake = FakeOAuth()
    state = _login(fake, home)
    assert state == {"connected": True, "identity": "bruno@example.test"}
    # Registered dynamically, with the exact loopback redirect it then used.
    assert list(fake.clients) == ["client-1"]
    redirect = fake.clients["client-1"][0]
    assert redirect.startswith("http://127.0.0.1:") and redirect.endswith("/callback")
    params = fake.authorize_params[0]
    assert params["redirect_uri"] == redirect
    assert params["resource"] == RS and params["code_challenge_method"] == "S256"
    exchange = fake.token_requests[0]
    assert exchange["resource"] == RS and exchange["code_verifier"]
    # The fake AS only issues when the verifier hashes to the challenge: PKCE was really checked.
    assert mcp_oauth.access_token_for(_cfg(), home) == fake.issued[0]


def test_a_wrong_verifier_gets_no_token(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The positive control above proves the verifier is sent; this proves the fake checks it."""
    fake = FakeOAuth()
    monkeypatch.setattr(mcp_oauth, "_pkce", lambda: ("not-the-verifier", "c" * 43))
    with pytest.raises(mcp_oauth.OAuthError) as caught:
        _login(fake, home)
    assert caught.value.code == "token_exchange"
    assert store.load(home, "notion") is None


def test_a_configured_client_id_skips_registration(home: Path) -> None:
    fake = FakeOAuth()
    _login(fake, home, _cfg(oauth_client_id="pre-registered"))
    assert fake.clients == {}
    assert not any("register" in call for call in fake.calls)
    assert fake.authorize_params[0]["client_id"] == "pre-registered"


def test_the_pre_registered_path_with_typed_endpoints_still_works(home: Path) -> None:
    fake = FakeOAuth()
    cfg = _cfg(oauth_client_id="pre", oauth_authorization_url=f"{ISSUER}/authorize",
               oauth_token_url=f"{ISSUER}/token", oauth_scope="read")
    _login(fake, home, cfg)
    assert not any(".well-known" in call for call in fake.calls)  # nothing discovered
    assert fake.authorize_params[0]["resource"] == RS
    assert mcp_oauth.access_token_for(cfg, home) == fake.issued[0]


def test_a_registration_is_reused_with_the_same_issuer(home: Path) -> None:
    fake = FakeOAuth()
    _login(fake, home)
    _login(fake, home)
    assert list(fake.clients) == ["client-1"]


def test_no_registration_and_no_client_id_is_a_clear_refusal(home: Path, no_real_browser: list[str]) -> None:
    fake = FakeOAuth(registration=False)
    open_, opened = browser(fake)
    with fake.client() as http, pytest.raises(mcp_oauth.OAuthError) as caught:
        mcp_oauth.login(_cfg(), home, open_browser=open_, timeout=5, http=http)
    assert caught.value.code == "no_registration"
    assert opened == []


# --- the guards, each shown biting -----------------------------------------------------------------


def _wrong_state(redirect: str) -> str:
    return redirect.replace("state=", "state=forged")


def test_a_callback_with_the_wrong_state_is_refused_and_nothing_is_exchanged(home: Path) -> None:
    fake = FakeOAuth()
    with pytest.raises(mcp_oauth.OAuthError) as caught:
        _login(fake, home, tamper=_wrong_state)
    assert caught.value.code == "state_mismatch"
    assert fake.token_requests == []
    assert store.load(home, "notion") is None


def test_a_callback_to_another_path_is_ignored_not_accepted(home: Path) -> None:
    fake = FakeOAuth()
    with pytest.raises(mcp_oauth.OAuthError) as caught:
        mcp_oauth_timeout = 1.0
        open_, _ = browser(fake, lambda r: r.replace("/callback", "/elsewhere"))
        with fake.client() as http:
            mcp_oauth.login(_cfg(), home, open_browser=open_, timeout=mcp_oauth_timeout, http=http)
    assert caught.value.code == "timeout"
    assert fake.token_requests == []


def test_an_issuer_other_than_the_advertised_one_is_refused_before_any_browser(home: Path) -> None:
    fake = FakeOAuth(issuer_actual="https://evil.example.test")
    open_, opened = browser(fake)
    with fake.client() as http, pytest.raises(mcp_oauth.OAuthError) as caught:
        mcp_oauth.login(_cfg(), home, open_browser=open_, timeout=5, http=http)
    assert caught.value.code == "issuer_mismatch"
    assert opened == [] and fake.clients == {}


def test_a_callback_naming_another_issuer_is_refused(home: Path) -> None:
    good = FakeOAuth(iss_param=True)
    assert _login(good, home)["connected"]  # control: the right `iss` passes
    store.delete(home, "notion")
    bad = FakeOAuth(iss_param=True, iss_sent="https://evil.example.test")
    with pytest.raises(mcp_oauth.OAuthError) as caught:
        _login(bad, home)
    assert caught.value.code == "issuer_mismatch"
    assert bad.token_requests == []


def test_metadata_about_another_resource_is_refused() -> None:
    fake = FakeOAuth(resource="https://other.example.test/mcp")
    with fake.client() as http, pytest.raises(mcp_oauth.OAuthError) as caught:
        mcp_oauth.discover(RS, http=http)
    assert caught.value.code == "resource_mismatch"


def test_resource_metadata_on_another_origin_is_refused() -> None:
    fake = FakeOAuth(metadata_url="https://attacker.example.test/.well-known/oauth-protected-resource")
    with fake.client() as http, pytest.raises(mcp_oauth.OAuthError) as caught:
        mcp_oauth.discover(RS, http=http)
    assert caught.value.code == "resource_mismatch"
    assert not any("attacker" in call for call in fake.calls)


def test_a_server_without_s256_is_refused() -> None:
    for methods in (None, ["plain"]):
        fake = FakeOAuth(pkce=methods)
        with fake.client() as http, pytest.raises(mcp_oauth.OAuthError) as caught:
            mcp_oauth.discover(RS, http=http)
        assert caught.value.code == "pkce_unsupported"


def test_cleartext_endpoints_are_refused() -> None:
    with pytest.raises(mcp_oauth.OAuthError):
        mcp_oauth.check_url("token endpoint", "http://auth.example.test/token")
    assert mcp_oauth.check_url("x", "http://127.0.0.1:9/token")
    from chimera.integrations.mcp_oauth import login

    cfg = _cfg(oauth_client_id="c", oauth_authorization_url=f"{ISSUER}/authorize",
               oauth_token_url="http://auth.example.test/token")
    with pytest.raises(mcp_oauth.OAuthError, match="token URL"):
        login(cfg, Path("."), open_browser=lambda _u: True, timeout=0.1)


# --- refresh ---------------------------------------------------------------------------------------


def test_an_expired_token_is_refreshed_with_the_resource_and_rotated(home: Path) -> None:
    fake = FakeOAuth(access_ttl=1)  # expires inside the margin: refreshed on first use
    _login(fake, home)
    first_refresh = store.load(home, "notion")["tokens"]["refresh_token"]  # type: ignore[index]
    with fake.client() as http:
        token = mcp_oauth.access_token_for(_cfg(), home, http=http)
    assert token == fake.issued[-1] and token != fake.issued[0]
    refresh = fake.token_requests[-1]
    assert refresh["grant_type"] == "refresh_token" and refresh["resource"] == RS
    assert store.load(home, "notion")["tokens"]["refresh_token"] != first_refresh  # type: ignore[index]


def test_a_dead_refresh_token_drops_the_tokens_and_asks_for_a_sign_in(home: Path) -> None:
    fake = FakeOAuth(access_ttl=1)
    _login(fake, home)
    fake.refresh_tokens.clear()  # revoked at the server
    with fake.client() as http:
        assert mcp_oauth.access_token_for(_cfg(), home, http=http) is None
    assert store.load(home, "notion")["tokens"] == {}  # type: ignore[index]
    from chimera.integrations.mcp_client import StreamableHTTPMCPSession

    with pytest.raises(mcp_oauth.SignInRequired, match="precisa conectar no app"):
        StreamableHTTPMCPSession.from_config(_cfg(), home=home)


def test_the_auth_refreshes_on_a_401_and_only_talks_to_its_own_origin(home: Path) -> None:
    fake = FakeOAuth()
    _login(fake, home)
    auth = mcp_oauth.StoredTokenAuth(_cfg(), home)
    other = httpx.Request("GET", "https://elsewhere.example.test/x")
    assert "authorization" not in next(auth.auth_flow(other)).headers
    flow = auth.auth_flow(httpx.Request("POST", RS))
    sent = next(flow)
    assert sent.headers["authorization"] == f"Bearer {fake.issued[0]}"


# --- storage ---------------------------------------------------------------------------------------


def test_tokens_never_reach_mcp_json_logs_or_the_routes(
    home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    add_server(home / "mcp.json", _cfg())
    fake = FakeOAuth()
    _login(fake, home)
    secrets_seen = [*fake.issued, *fake.refresh_tokens]
    assert secrets_seen
    mcp_json = (home / "mcp.json").read_text(encoding="utf-8")
    from chimera.api.mcp_api import list_servers

    listing = json.dumps(list_servers(home))
    for secret in secrets_seen:
        assert secret not in mcp_json
        assert secret not in caplog.text
        assert secret not in listing
    row = list_servers(home)["servers"][0]
    assert row["oauth"] == {"connected": True, "identity": "bruno@example.test"}
    assert row["url_host"] == "mcp.example.test"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_without_a_vault_the_record_is_a_0600_file_in_a_0700_folder(home: Path) -> None:
    _login(FakeOAuth(), home)
    files = list(store.store_folder(home).glob("*.json"))
    assert len(files) == 1 and "notion" not in files[0].name
    assert (files[0].stat().st_mode & 0o777) == 0o600
    assert (store.store_folder(home).stat().st_mode & 0o777) == 0o700


def test_with_a_vault_the_record_is_chunked_under_the_windows_limit(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera import config_vault

    vault = FakeKeyring()
    monkeypatch.setattr(config_vault, "_keyring", lambda: vault)
    fake = FakeOAuth(access_pad="x" * 4000)  # a JWT-sized token: no single entry can hold it
    _login(fake, home)
    assert not store.store_folder(home).exists() or not list(store.store_folder(home).glob("*.json"))
    assert len(vault.entries) > 2  # head + several chunks: one entry could not hold it
    assert mcp_oauth.access_token_for(_cfg(), home) == fake.issued[0]
    assert all("notion" not in account for _service, account in vault.entries)
    mcp_oauth.disconnect(_cfg(), home)
    record = store.load(home, "notion")
    assert record is not None and record["tokens"] == {}  # tokens gone, registration kept
    remove_server(home / "mcp.json", "notion")
    assert vault.entries == {}


def test_a_name_re_pointed_at_another_url_never_gets_the_first_token(home: Path) -> None:
    add_server(home / "mcp.json", _cfg())
    _login(FakeOAuth(), home)
    moved = McpServerConfig(name="notion", url="https://attacker.example.test/mcp")
    # The binding refuses it even before the cleanup...
    assert mcp_oauth.access_token_for(moved, home) is None
    # ...and re-adding under another URL clears the record.
    add_server(home / "mcp.json", moved)
    assert store.load(home, "notion") is None


def test_disconnect_revokes_and_forgets(home: Path) -> None:
    fake = FakeOAuth()
    _login(fake, home)
    with fake.client() as http:
        outcome = mcp_oauth.disconnect(_cfg(), home, http=http)
    assert outcome["revoked"] is True
    assert set(fake.revoked) == {fake.issued[0], *[t for t in fake.refresh_tokens]}
    assert mcp_oauth.state(_cfg(), home) == {"connected": False, "identity": None}


# --- unattended surfaces never sign in -------------------------------------------------------------


def test_unattended_connects_never_open_a_browser(home: Path, no_real_browser: list[str]) -> None:
    """The pool, autoload, serve, the bots and the Test all come through `_session_for`."""
    from chimera.integrations.mcp_config import _session_for, probe_tools

    add_server(home / "mcp.json", _cfg(oauth_client_id="c", oauth_authorization_url=f"{ISSUER}/a",
                                       oauth_token_url=f"{ISSUER}/t"))
    cfg = load_servers(home / "mcp.json")[0]
    with pytest.raises(mcp_oauth.SignInRequired):
        _session_for(cfg, 1.0)
    with pytest.raises(mcp_oauth.SignInRequired):
        probe_tools(cfg, connect_timeout=1.0)
    assert no_real_browser == []


def test_a_stored_sign_in_is_used_without_any_person(home: Path, no_real_browser: list[str]) -> None:
    from chimera.integrations.mcp_config import _session_for

    _login(FakeOAuth(), home)
    session = _session_for(_cfg(), 1.0)
    assert isinstance(session.auth, mcp_oauth.StoredTokenAuth)
    assert "Authorization" not in session.headers  # per request, never fixed at connect
    assert no_real_browser == []


def test_the_test_route_says_sign_in_is_needed_without_opening_anything(
    home: Path, no_real_browser: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api import mcp_api

    add_server(home / "mcp.json", _cfg())
    monkeypatch.setattr(mcp_oauth, "requires_oauth", lambda url, http=None: True)

    def refused(_cfg: Any) -> list[dict[str, Any]]:
        raise RuntimeError("MCP HTTP connection failed (HTTPStatusError)")

    monkeypatch.setattr(mcp_api, "_live_test", refused)
    result = mcp_api.test_server(home, "notion")
    assert result["ok"] is False and result["needs_sign_in"] is True
    assert mcp_api.list_servers(home)["servers"][0]["oauth"] == {"connected": False, "identity": None}
    assert no_real_browser == []


# --- who may start a sign-in -----------------------------------------------------------------------

SCREEN = "s" * 48


def _client(monkeypatch: pytest.MonkeyPatch) -> Any:
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.api.screen_credential import ScreenCredential
    from chimera.interface import ChatSession

    app = build_api_app(lambda: ChatSession(None), screen=ScreenCredential(value=SCREEN))  # type: ignore[arg-type]
    return TestClient(app)


def test_connect_needs_the_owners_screen(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.api import mcp_api

    add_server(home / "mcp.json", _cfg())
    calls: list[str] = []

    def fake_login(cfg: Any, _home: Path, **_kw: Any) -> dict[str, Any]:
        calls.append(cfg.name)
        return {"connected": True, "identity": "bruno@example.test"}

    monkeypatch.setattr(mcp_oauth, "login", fake_login)
    client = _client(monkeypatch)
    refused = client.post("/api/mcp/notion/oauth/connect")
    assert refused.status_code == 403 and calls == []
    wrong = client.post("/api/mcp/notion/oauth/connect", headers={"x-chimera-screen": "t" * 48})
    assert wrong.status_code == 403 and calls == []
    ok = client.post("/api/mcp/notion/oauth/connect", headers={"x-chimera-screen": SCREEN})
    assert ok.status_code == 200 and calls == ["notion"]
    assert ok.json() == {"ok": True, "error": None,
                         "oauth": {"connected": True, "identity": "bruno@example.test"}}
    assert mcp_api  # imported for the monkeypatch target's module


def test_the_desktop_bridge_cannot_reach_sign_in_or_sign_out() -> None:
    from chimera.api.bridge_routes import ROUTES

    assert not any("oauth" in route.path for route in ROUTES.values())


def test_the_agents_shell_cannot_start_or_read_a_sign_in(home: Path, tmp_path: Path) -> None:
    from chimera.tools.workspace import (
        ProtectedPathError,
        hides_own_env,
        queue_refusal,
        refuse_own_env_read,
    )

    _login(FakeOAuth(), home)
    record = next(store.store_folder(home).glob("*.json"))
    for command in (
        "chimera mcp login notion",
        "python -m chimera mcp login notion",
        f"cat {record}",
        f"type {store.store_folder(home)}",
        "python -c \"from chimera.integrations.mcp_oauth_store import load\"",
        "python -c \"import keyring; keyring.get_password('chimera-agent', 'mcp-oauth2-0123abcd')\"",
    ):
        assert queue_refusal("run_shell", command, tmp_path) is not None, command
    # Control: the fence is not refusing everything.
    assert queue_refusal("run_shell", "chimera mcp list", tmp_path) is None
    # The read tools: refused by name (read_file), and left out of a listing or a search, whatever
    # the owner's setting about Chimera's .env says.
    with pytest.raises(ProtectedPathError, match="sign-in store"):
        refuse_own_env_read(record)
    assert hides_own_env(record) is True
    assert hides_own_env(tmp_path / "notes.json") is False


def test_the_login_command_refuses_without_a_terminal(home: Path, no_real_browser: list[str]) -> None:
    from typer.testing import CliRunner

    from chimera.cli.main import app

    add_server(home / "mcp.json", _cfg())
    result = CliRunner().invoke(app, ["mcp", "login", "notion"])
    assert result.exit_code == 1
    assert "interactive terminal" in result.output
    assert no_real_browser == []


def test_codes_are_a_closed_set() -> None:
    assert mcp_oauth.OAuthError("made-up", "x").code == "discovery"
    assert "state_mismatch" in mcp_oauth.CODES and "issuer_mismatch" in mcp_oauth.CODES


@pytest.fixture
def remote_mcp_with_oauth() -> Iterator[tuple[str, list[str]]]:
    pytest.importorskip("mcp")
    uvicorn = pytest.importorskip("uvicorn")
    import importlib
    import socket

    fast_mcp = importlib.import_module("mcp.server.fastmcp").FastMCP
    server = fast_mcp("oauth-test")

    @server.tool()
    def echo(value: str) -> str:
        """Echo test data."""
        return value

    accepted: list[str] = []

    class Gate:
        def __init__(self, app: Any) -> None:
            self.app = app

        async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
            if scope["type"] == "http":
                token = dict(scope.get("headers", [])).get(b"authorization", b"").decode()
                if token.removeprefix("Bearer ") not in accepted:
                    await send({"type": "http.response.start", "status": 401, "headers": []})
                    await send({"type": "http.response.body", "body": b""})
                    return
            await self.app(scope, receive, send)

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(Gate(server.streamable_http_app()), host="127.0.0.1", port=port,
                            log_level="critical", lifespan="on", timeout_graceful_shutdown=1)
    app_server = uvicorn.Server(config)
    worker = threading.Thread(target=app_server.run, daemon=True)
    worker.start()
    deadline = time.monotonic() + 5
    while not app_server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}/mcp", accepted
    finally:
        app_server.should_exit = True
        app_server.force_exit = True
        worker.join(timeout=5)


def test_the_stored_token_reaches_a_real_mcp_server(
    home: Path, remote_mcp_with_oauth: tuple[str, list[str]]
) -> None:
    """End to end over the MCP SDK: the token the sign-in stored is what the server is sent."""
    url, accepted = remote_mcp_with_oauth
    from chimera.integrations.mcp_config import probe_tools

    cfg = McpServerConfig(name="local", url=url)
    record = {
        "version": 1, "resource_url": mcp_oauth.canonical_resource(url), "resource": url,
        "issuer": ISSUER, "token_endpoint": f"{ISSUER}/token", "revocation_endpoint": None,
        "client": {"client_id": "c", "source": "dynamic"},
        "tokens": {"access_token": "at-live-secret", "refresh_token": None, "expires_at": None},
        "identity": None,
    }
    store.save(home, "local", record)
    accepted.append("at-live-secret")
    assert [t["name"] for t in probe_tools(cfg, connect_timeout=10)] == ["echo"]
    accepted.clear()  # control: without the token the same server refuses
    with pytest.raises(RuntimeError):
        probe_tools(cfg, connect_timeout=10)
