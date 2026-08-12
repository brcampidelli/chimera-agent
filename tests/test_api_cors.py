"""Cross-origin access: closed unless the operator opened it, and never mistaken for the gate.

The desktop app pointed at a REMOTE Chimera is served by its own loopback sidecar, so every call it
makes to that instance is cross-origin and the browser drops the response unless the instance names
the app's origin. Serving the bundled SPA is same-origin and needs none of this — which is why an
instance nobody configured has to behave exactly as it did before.

The distinction these tests exist to hold: **CORS is not authorization.** It decides which page may
read a response, not who may call. The gate is ``CHIMERA_SERVER_TOKEN``, and naming an origin does
not soften it — the last test is that pairing, because "I allowed my app's origin" is the sentence
someone says right before leaving an agent open to the internet.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402

APP = "http://127.0.0.1:45813"
OUTRA = "https://qualquer-site.example"


class _FakeAgent:
    def answer(self, message: str) -> str:  # pragma: no cover - trivial
        return f"eco: {message}"


def _client(tmp_path: Any, *, origins: str | None = None, token: str | None = None) -> TestClient:
    from chimera.api import build_api_app

    kwargs: dict[str, Any] = {"CHIMERA_HOME": str(tmp_path / "home")}
    if origins is not None:
        kwargs["CHIMERA_ALLOWED_ORIGINS"] = origins
    if token is not None:
        kwargs["CHIMERA_SERVER_TOKEN"] = token
    settings = Settings(**kwargs)
    return TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings))


def test_an_instance_nobody_configured_answers_no_origin(tmp_path: Any) -> None:
    """The default. Every existing install must be untouched by this feature existing."""
    r = _client(tmp_path).get("/api/health", headers={"Origin": APP})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_the_named_origin_gets_the_header(tmp_path: Any) -> None:
    r = _client(tmp_path, origins=APP).get("/api/health", headers={"Origin": APP})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == APP


def test_an_origin_that_was_not_named_does_not(tmp_path: Any) -> None:
    r = _client(tmp_path, origins=APP).get("/api/health", headers={"Origin": OUTRA})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_the_preflight_a_browser_sends_before_a_bearer_header(tmp_path: Any) -> None:
    # An `Authorization` header makes the request non-simple, so the browser asks first. If this
    # 400s, the app never gets to send a single authenticated request and the feature is dead.
    r = _client(tmp_path, origins=APP).options(
        "/api/config",
        headers={
            "Origin": APP,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == APP


@pytest.mark.parametrize("raw", [f" {APP} , ", f"{APP},,", f",{APP}"])
def test_the_list_survives_the_spacing_people_actually_type(tmp_path: Any, raw: str) -> None:
    r = _client(tmp_path, origins=raw).get("/api/health", headers={"Origin": APP})
    assert r.headers["access-control-allow-origin"] == APP


def test_credentials_are_not_allowed(tmp_path: Any) -> None:
    """The app authenticates with a bearer header, not a cookie.

    Turning credentials on would let a browser attach this instance's cookies to a request some
    other page made — buying nothing, since nothing here reads cookies.
    """
    r = _client(tmp_path, origins=APP).get("/api/health", headers={"Origin": APP})
    assert "access-control-allow-credentials" not in {k.lower() for k in r.headers}


def test_naming_an_origin_does_not_soften_the_token(monkeypatch: Any, tmp_path: Any) -> None:
    """The one that matters.

    "I allowed my app's origin" is the sentence someone says right before leaving an agent open to
    the internet. An allowed origin with a wrong token is still 401, and CORS never had a vote.

    The token goes through the environment, not the injected ``Settings``, because the two are read
    at different moments on purpose: CORS middleware is installed once at construction, while the
    token guard re-reads ``get_settings()`` on every call so that a token set at runtime takes
    effect immediately. Passing it to the constructor produced a green 200 on a wrong token — the
    guard was reading the process settings, which had none.
    """
    from chimera.api import build_api_app

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "o-certo")
    monkeypatch.setenv("CHIMERA_ALLOWED_ORIGINS", APP)
    from chimera.config import get_settings

    get_settings.cache_clear()
    client = TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=Settings()))

    negado = client.get("/api/config", headers={"Origin": APP, "Authorization": "Bearer o-errado"})
    assert negado.status_code == 401
    permitido = client.get("/api/config", headers={"Origin": APP, "Authorization": "Bearer o-certo"})
    assert permitido.status_code == 200
    # And the header is still there on the allowed one — the two mechanisms are independent.
    assert permitido.headers["access-control-allow-origin"] == APP
    get_settings.cache_clear()
