"""Named OpenAI-compatible endpoints (#1133): config, routing, receipts, the API and the bridge."""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.config import Settings
from chimera.providers.endpoints import endpoint_host, keys_for, validate_endpoints
from chimera.providers.gateway import LLMGateway
from chimera.providers.privacy import prompt_routes

_MSG: list[Any] = [{"role": "user", "content": "hi"}]


def _entry(name: str = "lab", url: str = "https://llm.example/v1", key_env: str = "LAB_API_KEY") -> dict[str, str]:
    return {"name": name, "base_url": url, "key_env": key_env}


def _settings(*entries: dict[str, str], **extra: Any) -> Settings:
    return Settings(CHIMERA_ENDPOINTS=json.dumps(list(entries)), **extra)


class _FakeLiteLLM:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_with: Exception | None = None
        module = ModuleType("litellm")
        module.completion = self._completion  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "litellm", module)

    def _completion(self, **kw: Any) -> Any:
        self.calls.append(kw)
        if self.fail_with is not None:
            raise self.fail_with
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")],
            usage=None, provider="openai", id="x",
        )


# --- config ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["a", "lab-1", "0x", "a" * 32])
def test_valid_names_are_accepted(name: str) -> None:
    assert validate_endpoints([_entry(name)])[0]["name"] == name


@pytest.mark.parametrize("name", ["", "Lab", "-lab", "has space", "a" * 33, "under_score", "a/b"])
def test_invalid_names_are_refused(name: str) -> None:
    with pytest.raises(ValueError):
        validate_endpoints([_entry(name)])


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/v1",
        "ftp://localhost/v1",
        "https://user:pw@h.example/v1",
        "https://h.example/v1?x=1",
        "llm.example/v1",
        "",
    ],
)
def test_unsafe_base_urls_are_refused(url: str) -> None:
    with pytest.raises(ValueError):
        validate_endpoints([_entry(url=url)])


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8000/v1", "http://localhost:1234/v1", "http://[::1]:9000/v1", "https://llm.example/v1/"],
)
def test_https_and_loopback_http_are_accepted(url: str) -> None:
    assert validate_endpoints([_entry(url=url)])[0]["base_url"] == url.rstrip("/")


def test_duplicates_and_extra_fields_are_refused() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        validate_endpoints([_entry(), _entry()])
    with pytest.raises(ValueError) as caught:
        validate_endpoints([{**_entry(), "api_key": "sk-must-not-appear"}])
    assert "sk-must-not-appear" not in str(caught.value)


def test_settings_hold_only_the_variable_name_never_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAB_API_KEY", "sk-secret-value")
    settings = _settings(_entry())
    assert "sk-secret-value" not in repr(settings) + json.dumps(settings.endpoints)


def test_a_key_pool_overrides_the_single_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAB_API_KEY", "single")
    monkeypatch.setenv("LAB_API_KEYS", "pool-one, pool-two")
    assert keys_for(_entry()) == ["pool-one", "pool-two"]
    monkeypatch.delenv("LAB_API_KEYS")
    assert keys_for(_entry()) == ["single"]


# --- routing ---------------------------------------------------------------------------------------


def test_each_endpoint_gets_its_own_base_and_key_and_openrouter_gets_neither(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeLiteLLM(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("ONE_API_KEY", "secret-one")
    monkeypatch.setenv("TWO_API_KEY", "secret-two")
    gateway = LLMGateway(
        _settings(_entry("one", "https://one.example/v1", "ONE_API_KEY"), _entry("two", "https://two.example/v1", "TWO_API_KEY"))
    )
    gateway.complete(_MSG, model="endpoint/one/m-1")
    gateway.complete(_MSG, model="endpoint/two/vendor/m-2")
    gateway.complete(_MSG, model="openrouter/vendor/m")
    one, two, router = fake.calls
    assert (one["model"], one["api_base"], one["api_key"]) == ("openai/m-1", "https://one.example/v1", "secret-one")
    assert (two["model"], two["api_base"], two["api_key"]) == (
        "openai/vendor/m-2",
        "https://two.example/v1",
        "secret-two",
    )
    assert "api_base" not in router and router["model"] == "openrouter/vendor/m"


def test_an_endpoint_ignores_the_global_api_base_and_other_routes_still_use_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeLiteLLM(monkeypatch)
    monkeypatch.setenv("LAB_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    gateway = LLMGateway(_settings(_entry(), CHIMERA_API_BASE="https://global.example/v1"))
    gateway.complete(_MSG, model="endpoint/lab/m")
    gateway.complete(_MSG, model="openrouter/vendor/m")
    endpoint_call, router_call = fake.calls
    assert endpoint_call["api_base"] == "https://llm.example/v1"
    assert router_call["api_base"] == "https://global.example/v1"  # the old behaviour, unchanged


def test_a_keyless_endpoint_never_falls_back_to_the_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeLiteLLM(monkeypatch)
    monkeypatch.delenv("LAB_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-must-not-travel")
    LLMGateway(_settings(_entry(url="http://127.0.0.1:8000/v1"))).complete(_MSG, model="endpoint/lab/m")
    assert fake.calls[0]["api_key"]
    assert fake.calls[0]["api_key"] != "sk-openai-must-not-travel"


def test_the_key_pool_rotates_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeLiteLLM(monkeypatch)
    monkeypatch.setenv("LAB_API_KEYS", "k1,k2")
    gateway = LLMGateway(_settings(_entry()))
    for _ in range(2):
        gateway.complete(_MSG, model="endpoint/lab/m")
    assert {call["api_key"] for call in fake.calls} == {"k1", "k2"}


@pytest.mark.parametrize("slug", ["endpoint/missing/m", "endpoint/lab", "endpoint/Lab/m"])
def test_an_unknown_or_malformed_endpoint_is_a_clear_error_not_a_global_fallthrough(
    monkeypatch: pytest.MonkeyPatch, slug: str
) -> None:
    fake = _FakeLiteLLM(monkeypatch)
    gateway = LLMGateway(_settings(_entry(), CHIMERA_API_BASE="https://global.example/v1"))
    with pytest.raises(ValueError, match="endpoint"):
        gateway.complete(_MSG, model=slug)
    assert fake.calls == []


def test_an_error_that_echoes_the_key_is_scrubbed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake = _FakeLiteLLM(monkeypatch)
    monkeypatch.setenv("LAB_API_KEY", "sk-echoed-by-the-server")
    fake.fail_with = ValueError("bad key sk-echoed-by-the-server")
    with caplog.at_level("DEBUG"), pytest.raises(ValueError) as caught:
        LLMGateway(_settings(_entry())).complete(_MSG, model="endpoint/lab/m")
    assert "sk-echoed-by-the-server" not in str(caught.value)
    assert "sk-echoed-by-the-server" not in caplog.text


def test_a_result_names_the_endpoint_and_host_never_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeLiteLLM(monkeypatch)
    monkeypatch.setenv("LAB_API_KEY", "k")
    result = LLMGateway(_settings(_entry())).complete(_MSG, model="endpoint/lab/m")
    assert (result.provider, result.route_host) == ("endpoint:lab", "llm.example")
    assert result.model == "endpoint/lab/m"


def test_a_reasoning_variant_is_not_sent_to_an_arbitrary_endpoint() -> None:
    gateway = LLMGateway(_settings(_entry()))
    assert gateway._effort_for_route("endpoint/lab/m", "high") is None  # noqa: SLF001


# --- privacy ---------------------------------------------------------------------------------------


def test_the_privacy_card_names_the_endpoint_and_its_host() -> None:
    routes = prompt_routes(_settings(_entry(), CHIMERA_DEFAULT_MODEL="endpoint/lab/m"))
    route = next(r for r in routes if r["provider"] == "endpoint:lab")
    assert route["host"] == "llm.example" and route["local"] is False
    assert not any(r["provider"] == "openai" for r in routes)


# --- API -------------------------------------------------------------------------------------------


def _client(monkeypatch: pytest.MonkeyPatch, handler: Any, tmp_path: Any) -> TestClient:
    monkeypatch.setenv("LAB_API_KEY", "sk-never-returned")
    from chimera.api import screen_credential

    settings = _settings(_entry(), CHIMERA_HOME=str(tmp_path / "home"))
    # The test route is the owner's screen's: the client carries the screen credential beside the request.
    screen = screen_credential.for_mode("app", server_token="owner-token-for-the-probe-tests")
    app = build_api_app(lambda: None, settings=settings, screen=screen)  # type: ignore[arg-type,return-value]
    app.state.endpoint_test_transport = httpx.MockTransport(handler)
    client = TestClient(app)
    client.headers[screen_credential.HEADER_NAME] = str(screen.value)
    return client


def test_the_list_route_never_carries_a_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    client = _client(monkeypatch, lambda request: httpx.Response(200), tmp_path)
    response = client.get("/api/endpoints")
    assert response.json() == {"endpoints": [{"name": "lab", "host": "llm.example", "key_configured": True}]}
    assert "sk-never-returned" not in response.text


def test_the_test_route_calls_get_models_on_the_configured_host_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{}, {}]})

    body = _client(monkeypatch, handler, tmp_path).post("/api/endpoints/lab/test").json()
    assert body["ok"] is True and body["models"] == 2
    assert [(r.method, str(r.url)) for r in seen] == [("GET", "https://llm.example/v1/models")]
    assert seen[0].headers["authorization"] == "Bearer sk-never-returned"
    assert "sk-never-returned" not in json.dumps(body)


def test_the_test_route_does_not_follow_a_redirect_to_another_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://elsewhere.example/steal"})

    body = _client(monkeypatch, handler, tmp_path).post("/api/endpoints/lab/test").json()
    assert body["ok"] is False and hosts == ["llm.example"]
    assert "sk-never-returned" not in json.dumps(body)


def test_the_test_route_refuses_a_name_that_is_not_configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    client = _client(monkeypatch, lambda request: httpx.Response(200), tmp_path)
    assert client.post("/api/endpoints/other/test").status_code == 404


def test_the_test_route_reports_a_failure_without_the_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused {request.headers['authorization']}")

    body = _client(monkeypatch, handler, tmp_path).post("/api/endpoints/lab/test").json()
    assert body["ok"] is False and "sk-never-returned" not in json.dumps(body)


# --- the bridge ------------------------------------------------------------------------------------


def test_the_bridge_may_not_write_the_endpoint_list() -> None:
    from chimera.api.bridge_routes import FULL_CONTROL_NEVER_SETTINGS, SUGGESTABLE_SETTINGS

    assert "CHIMERA_ENDPOINTS" in FULL_CONTROL_NEVER_SETTINGS
    assert "CHIMERA_ENDPOINTS" not in SUGGESTABLE_SETTINGS


def test_the_host_of_a_malformed_entry_is_empty_not_an_exception() -> None:
    assert endpoint_host({"name": "x", "base_url": "http://[bad", "key_env": "K"}) == ""


# --- the owner's screen adds and removes an endpoint (#1133) ----------------------------------------

_OWNER_TOKEN = "owner-token-for-the-endpoint-screen-tests"


@pytest.fixture
def live_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    """The app as the desktop runs it: no injected settings, so the list reads what a write just saved.

    An injected ``Settings`` is frozen on purpose (``live_settings``), so a test that injects one would
    read a stale list after an add and say nothing about the real app.
    """
    from chimera.api import screen_credential
    from chimera.config import get_settings

    monkeypatch.chdir(tmp_path)  # the write lands in this directory's .env, never the repo's
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", _OWNER_TOKEN)
    monkeypatch.setenv("CHIMERA_MEMORY_BACKEND", "json")
    monkeypatch.setenv("LAB_API_KEY", "sk-never-returned")
    # Restored at teardown: the write also sets it in this process's environment.
    monkeypatch.setenv("CHIMERA_ENDPOINTS", "")
    get_settings.cache_clear()
    app = build_api_app(lambda: None, screen=screen_credential.for_mode("app", server_token=_OWNER_TOKEN))  # type: ignore[arg-type,return-value]
    yield app
    get_settings.cache_clear()


def _bearer() -> dict[str, str]:
    return {"authorization": f"Bearer {_OWNER_TOKEN}"}


def _from_screen(app: Any) -> dict[str, str]:
    from chimera.api.screen_credential import HEADER_NAME

    return {**_bearer(), HEADER_NAME: str(app.state.screen_credential.value)}


_NEW = {"name": "vllm", "base_url": "https://llm.example/v1", "key_env": "LAB_API_KEY"}


def test_adding_an_endpoint_is_refused_to_the_bearer_token_alone(live_app: Any, tmp_path: Any) -> None:
    client = TestClient(live_app)
    refused = client.post("/api/endpoints", json=_NEW, headers=_bearer())
    assert refused.status_code == 403, refused.text
    assert "app's screen" in refused.json()["detail"]
    assert not (tmp_path / ".env").exists()
    assert client.get("/api/endpoints", headers=_bearer()).json() == {"endpoints": []}


def test_the_screen_adds_an_endpoint_and_the_list_names_its_host_never_its_key(live_app: Any, tmp_path: Any) -> None:
    client = TestClient(live_app)
    added = client.post("/api/endpoints", json=_NEW, headers=_from_screen(live_app))
    assert added.status_code == 200, added.text
    assert added.json() == {"endpoints": [{"name": "vllm", "host": "llm.example", "key_configured": True}]}
    assert "sk-never-returned" not in added.text
    assert "CHIMERA_ENDPOINTS=" in (tmp_path / ".env").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "body",
    [
        {**_NEW, "base_url": "http://llm.example/v1"},  # plain http to a remote host
        {**_NEW, "base_url": "https://user:pw@llm.example/v1"},  # credentials in the URL
        {**_NEW, "name": "Bad Name"},  # not a slug
        {**_NEW, "key_env": "lab key"},  # not an environment variable name
        {**_NEW, "base_url": "ftp://llm.example"},
    ],
)
def test_the_backend_validator_refuses_a_bad_endpoint_and_writes_nothing(
    live_app: Any, tmp_path: Any, body: dict[str, str]
) -> None:
    client = TestClient(live_app)
    refused = client.post("/api/endpoints", json=body, headers=_from_screen(live_app))
    assert refused.status_code == 400, refused.text
    assert not (tmp_path / ".env").exists()


def test_a_duplicate_name_is_refused(live_app: Any) -> None:
    client = TestClient(live_app)
    assert client.post("/api/endpoints", json=_NEW, headers=_from_screen(live_app)).status_code == 200
    again = client.post("/api/endpoints", json={**_NEW, "base_url": "http://127.0.0.1:8000/v1"}, headers=_from_screen(live_app))
    assert again.status_code == 400 and "already exists" in again.json()["detail"]


def test_removing_an_endpoint_needs_the_screen_and_a_name_that_exists(live_app: Any) -> None:
    client = TestClient(live_app)
    client.post("/api/endpoints", json=_NEW, headers=_from_screen(live_app))
    assert client.delete("/api/endpoints/vllm", headers=_bearer()).status_code == 403
    assert client.delete("/api/endpoints/nope", headers=_from_screen(live_app)).status_code == 404
    removed = client.delete("/api/endpoints/vllm", headers=_from_screen(live_app))
    assert removed.status_code == 200 and removed.json() == {"endpoints": []}


def test_the_test_route_is_bounded_by_a_timeout_and_calls_only_the_configured_host(
    live_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(live_app)
    client.post("/api/endpoints", json=_NEW, headers=_from_screen(live_app))
    seen_kwargs: list[dict[str, Any]] = []
    urls: list[str] = []
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json={"data": [{}]})

    class Spy(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            seen_kwargs.append(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", Spy)
    live_app.state.endpoint_test_transport = httpx.MockTransport(handler)
    body = client.post("/api/endpoints/vllm/test", headers=_from_screen(live_app)).json()
    assert body["ok"] is True and body["models"] == 1
    timeout = seen_kwargs[0]["timeout"]
    assert isinstance(timeout, httpx.Timeout) and timeout.read is not None and timeout.read <= 5
    assert seen_kwargs[0]["follow_redirects"] is False
    assert urls == ["https://llm.example/v1/models"]


def test_no_route_of_the_bridge_reaches_the_endpoint_list() -> None:
    from chimera.api.bridge_routes import FULL_CONTROL_ROUTES, ROUTES

    reachable = {(r.method, r.path) for r in [*ROUTES.values(), *FULL_CONTROL_ROUTES.values()]}
    assert not {p for p in reachable if p[1].startswith("/api/endpoints")}


# --- which variable an endpoint may name as its key (review of #1133) -------------------------------


@pytest.mark.parametrize(
    "key_env",
    [
        "LAB_KEY",  # no _API_KEY suffix
        "lab_api_key",  # lower case
        "CHIMERA_API_KEY",  # the app's own names
        "CHIMERA_SERVER_TOKEN",
        "OPENROUTER_API_KEY",  # first-class providers
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "TAVILY_API_KEY",  # a search credential, not a model provider
        "GITHUB_TOKEN",  # a credential that is not named *_API_KEY
        "A" * 57 + "_API_KEY",  # 65 characters
    ],
)
def test_an_endpoint_may_not_name_another_credential_as_its_key(key_env: str) -> None:
    with pytest.raises(ValueError):
        validate_endpoints([_entry(key_env=key_env)])


def test_a_key_named_for_an_endpoint_is_accepted_and_the_app_saves_it() -> None:
    from chimera.api.config_api import check_updates, is_editable

    assert validate_endpoints([_entry(key_env="MYLAB_API_KEY")])[0]["key_env"] == "MYLAB_API_KEY"
    assert validate_endpoints([_entry(key_env="A" * 56 + "_API_KEY")])  # exactly 64 characters
    # The same name must be one the key screen can write, or a card would name a key it cannot save.
    assert is_editable("MYLAB_API_KEY")
    check_updates({"MYLAB_API_KEY": "sk-not-real"})  # raises if the save path would refuse it


def test_every_credential_the_app_already_stores_is_refused_as_an_endpoint_key() -> None:
    from chimera.api.config_api import ALLOWED_KEYS

    shaped = sorted(name for name in ALLOWED_KEYS if name.endswith("_API_KEY"))
    assert shaped, "the allowlist names provider keys; an empty list means this test checks nothing"
    for name in shaped:
        with pytest.raises(ValueError):
            validate_endpoints([_entry(key_env=name)])


def test_a_private_address_is_accepted_and_a_file_url_is_refused() -> None:
    assert validate_endpoints([_entry(url="https://192.168.1.20:8000/v1")])
    with pytest.raises(ValueError):
        validate_endpoints([_entry(url="file:///etc/passwd")])


def test_the_test_route_refuses_the_bearer_token_alone(live_app: Any) -> None:
    client = TestClient(live_app)
    client.post("/api/endpoints", json=_NEW, headers=_from_screen(live_app))
    refused = client.post("/api/endpoints/vllm/test", headers=_bearer())
    assert refused.status_code == 403, refused.text


# --- the probe is bounded in time and in bytes, against a real socket -------------------------------


@contextmanager
def _loopback_server(answer: Callable[[BaseHTTPRequestHandler], None]) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — the stdlib's name
            answer(self)

        def log_message(self, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()


def _endpoint_at(base_url: str) -> dict[str, str]:
    return {"name": "local", "base_url": base_url, "key_env": "LOCAL_API_KEY"}


@pytest.fixture
def direct_network(monkeypatch: pytest.MonkeyPatch) -> None:
    # A proxy from the machine's environment must not carry a loopback test off the box.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


def test_a_model_list_is_counted_over_a_real_socket(direct_network: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.providers import endpoints
    from chimera.providers.endpoints import probe_models

    # The first connection of a process is slow on this machine (several seconds of loopback warm-up);
    # the deadline is what the other tests measure, so this one only checks the count.
    monkeypatch.setattr(endpoints, "TEST_DEADLINE_SECONDS", 30.0)

    body = json.dumps({"data": [{"id": "a"}, {"id": "b"}]}).encode()

    def answer(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    with _loopback_server(answer) as base_url:
        assert probe_models(_endpoint_at(base_url)) == 2


def test_a_server_that_trickles_bytes_is_cut_off_at_the_deadline(direct_network: None) -> None:
    from chimera.providers.endpoints import (
        TEST_DEADLINE_SECONDS,
        TEST_READ_TIMEOUT_SECONDS,
        EndpointDeadlineExceeded,
        probe_models,
    )

    def trickle(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", "100000")
        handler.end_headers()
        try:
            for _ in range(400):  # far longer than the deadline
                handler.wfile.write(b" ")
                handler.wfile.flush()
                time.sleep(0.1)
        except OSError:
            return  # the probe has gone, as it should

    started = time.monotonic()
    with _loopback_server(trickle) as base_url, pytest.raises(EndpointDeadlineExceeded):
        probe_models(_endpoint_at(base_url))
    # The deadline is checked between reads, so a stall adds at most one read timeout.
    assert time.monotonic() - started < TEST_DEADLINE_SECONDS + TEST_READ_TIMEOUT_SECONDS + 1.0


def test_an_answer_past_the_byte_cap_is_refused_before_it_is_read_whole(direct_network: None) -> None:
    from chimera.providers.endpoints import TEST_BYTE_CAP, EndpointResponseTooLarge, probe_models

    size = TEST_BYTE_CAP * 3

    def huge(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(size))
        handler.end_headers()
        try:
            handler.wfile.write(b" " * size)
        except OSError:
            return

    with _loopback_server(huge) as base_url, pytest.raises(EndpointResponseTooLarge):
        probe_models(_endpoint_at(base_url))


@pytest.mark.parametrize(
    "key_env",
    [
        "TOGETHER_API_KEY",  # read by LiteLLM's Python code, not listed in its JSON registry
        "XAI_API_KEY",
        "FIREWORKS_API_KEY",
        "PERPLEXITY_API_KEY",
        "COHERE_API_KEY",
        "AZURE_API_KEY",
        "GOOGLE_API_KEY",  # also read by chimera/acp/agents.py
        "SARVAM_API_KEY",  # listed in LiteLLM's providers.json, so read from the installed package
    ],
)
def test_a_key_litellm_reads_for_a_provider_is_refused_as_an_endpoint_key(key_env: str) -> None:
    with pytest.raises(ValueError, match="provider's own key"):
        validate_endpoints([_entry(key_env=key_env)])


def test_the_litellm_registry_is_read_from_the_installed_package() -> None:
    from chimera.providers.endpoints import _litellm_key_envs

    names = _litellm_key_envs()
    assert "SARVAM_API_KEY" in names  # only the registry file lists it
    assert "MYLAB_API_KEY" not in names
