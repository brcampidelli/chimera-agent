"""Named OpenAI-compatible endpoints (#1133): config, routing, receipts, the API and the bridge."""

from __future__ import annotations

import json
import sys
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


def _entry(name: str = "lab", url: str = "https://llm.example/v1", key_env: str = "LAB_KEY") -> dict[str, str]:
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
    monkeypatch.setenv("LAB_KEY", "sk-secret-value")
    settings = _settings(_entry())
    assert "sk-secret-value" not in repr(settings) + json.dumps(settings.endpoints)


def test_a_key_pool_overrides_the_single_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAB_KEY", "single")
    monkeypatch.setenv("LAB_KEYS", "pool-one, pool-two")
    assert keys_for(_entry()) == ["pool-one", "pool-two"]
    monkeypatch.delenv("LAB_KEYS")
    assert keys_for(_entry()) == ["single"]


# --- routing ---------------------------------------------------------------------------------------


def test_each_endpoint_gets_its_own_base_and_key_and_openrouter_gets_neither(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeLiteLLM(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("ONE_KEY", "secret-one")
    monkeypatch.setenv("TWO_KEY", "secret-two")
    gateway = LLMGateway(
        _settings(_entry("one", "https://one.example/v1", "ONE_KEY"), _entry("two", "https://two.example/v1", "TWO_KEY"))
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
    monkeypatch.setenv("LAB_KEY", "k")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    gateway = LLMGateway(_settings(_entry(), CHIMERA_API_BASE="https://global.example/v1"))
    gateway.complete(_MSG, model="endpoint/lab/m")
    gateway.complete(_MSG, model="openrouter/vendor/m")
    endpoint_call, router_call = fake.calls
    assert endpoint_call["api_base"] == "https://llm.example/v1"
    assert router_call["api_base"] == "https://global.example/v1"  # the old behaviour, unchanged


def test_a_keyless_endpoint_never_falls_back_to_the_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeLiteLLM(monkeypatch)
    monkeypatch.delenv("LAB_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-must-not-travel")
    LLMGateway(_settings(_entry(url="http://127.0.0.1:8000/v1"))).complete(_MSG, model="endpoint/lab/m")
    assert fake.calls[0]["api_key"]
    assert fake.calls[0]["api_key"] != "sk-openai-must-not-travel"


def test_the_key_pool_rotates_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeLiteLLM(monkeypatch)
    monkeypatch.setenv("LAB_KEYS", "k1,k2")
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
    monkeypatch.setenv("LAB_KEY", "sk-echoed-by-the-server")
    fake.fail_with = ValueError("bad key sk-echoed-by-the-server")
    with caplog.at_level("DEBUG"), pytest.raises(ValueError) as caught:
        LLMGateway(_settings(_entry())).complete(_MSG, model="endpoint/lab/m")
    assert "sk-echoed-by-the-server" not in str(caught.value)
    assert "sk-echoed-by-the-server" not in caplog.text


def test_a_result_names_the_endpoint_and_host_never_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeLiteLLM(monkeypatch)
    monkeypatch.setenv("LAB_KEY", "k")
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
    monkeypatch.setenv("LAB_KEY", "sk-never-returned")
    settings = _settings(_entry(), CHIMERA_HOME=str(tmp_path / "home"))
    app = build_api_app(lambda: None, settings=settings)  # type: ignore[arg-type,return-value]
    app.state.endpoint_test_transport = httpx.MockTransport(handler)
    return TestClient(app)


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
