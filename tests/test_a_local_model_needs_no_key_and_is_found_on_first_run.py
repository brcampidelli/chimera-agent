"""A machine with a local model needs no key — and the first-run screen finds the model itself.

Audited on 2026-09-16 against the marketing sentence "the agent scans your machine for Ollama or LM
Studio and configures itself": what existed was a credential gate that let `ollama_chat/…` through
INSIDE the gateway, while six CLI commands, the ACP server and the desktop's first-run gate answered
the same question by looking for a key alone — so `CHIMERA_DEFAULT_MODEL=ollama_chat/llama3` with no
key was "No provider key configured" everywhere the user could see, and the app opened a wizard
demanding a key it did not need. LM Studio was on the local-prefix list and nowhere else: no probe,
and LiteLLM's `lm_studio/` provider has no default base URL, so the model was a request to OpenAI.

Three things pinned here: the one answer to "can this install run a model" (`Settings.can_answer`),
the LM Studio probe with the Ollama probe's contract, and the endpoint the first-run screen reads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from chimera.config import Settings
from chimera.providers import lmstudio
from chimera.providers.discovery import is_local_model
from chimera.providers.gateway import _is_local_model


def _settings(tmp_path: Path, **kw: Any) -> Settings:
    return Settings(CHIMERA_HOME=str(tmp_path / "home"), **kw)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("CHIMERA_DEFAULT_MODEL", raising=False)


# ------------------------------------------------------------------ one answer to "can it run a model"


def test_a_local_default_model_can_answer_without_any_key(tmp_path: Path) -> None:
    keyless = _settings(tmp_path, CHIMERA_DEFAULT_MODEL="ollama_chat/llama3")
    assert keyless.has_any_key() is False
    assert keyless.can_answer() is True
    assert _settings(tmp_path, CHIMERA_DEFAULT_MODEL="lm_studio/qwen2.5-7b").can_answer() is True
    assert _settings(tmp_path, CHIMERA_DEFAULT_MODEL="openrouter/deepseek/x").can_answer() is False


def test_the_rule_moved_and_the_gateway_still_reads_the_same_one() -> None:
    assert is_local_model("LM_STUDIO/anything") and is_local_model("ollama_chat/x")
    assert not is_local_model("openrouter/x") and not is_local_model("")
    assert _is_local_model is is_local_model


def test_the_cli_commands_that_run_a_model_let_a_local_one_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`run` used to print "No provider key configured" and exit 1 before the gateway could say the
    model needs none. With a local default model the gate opens; the (fake) gateway is then what
    answers, which is where the question always belonged."""
    from chimera.cli import main as cli

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_DEFAULT_MODEL", "ollama_chat/llama3")
    from chimera.config import get_settings

    get_settings.cache_clear()
    reached: list[str] = []

    class _Gateway:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            reached.append("gateway built")

        def complete(self, *_a: Any, **_k: Any) -> Any:
            raise RuntimeError("stop here — the gate is what this test is about")

    monkeypatch.setattr("chimera.providers.LLMGateway", _Gateway)
    result = CliRunner().invoke(cli.app, ["run", "hi"])
    get_settings.cache_clear()
    assert "No provider key configured" not in result.output, result.output
    assert reached == ["gateway built"], "the command stopped before building a gateway"


# ------------------------------------------------------------------ the LM Studio probe


class _Resp:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        return self._body


def test_lm_studio_lists_what_it_has_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    asked: list[str] = []

    def _get(url: str, *, timeout: Any) -> _Resp:
        asked.append(url)
        return _Resp(
            200,
            {
                "object": "list",
                "data": [{"id": "qwen2.5-7b-instruct"}, {"id": "gemma-3-4b"}, {"id": ""}],
            },
        )

    monkeypatch.setattr(httpx, "get", _get)
    found = lmstudio.loaded_models("http://localhost:1234/v1/")
    assert asked == ["http://localhost:1234/v1/models"], (
        "the OpenAI-compatible list, under the /v1 the setting carries"
    )
    assert found.reachable is True
    assert found.models == ("gemma-3-4b", "qwen2.5-7b-instruct")
    assert found.reason == ""


@pytest.mark.parametrize(
    ("get", "reachable", "reason", "models"),
    [
        (
            lambda url, timeout: (_ for _ in ()).throw(ConnectionError("refused")),
            False,
            "unreachable",
            (),
        ),
        (lambda url, timeout: _Resp(500, {}), False, "http_error", ()),
        (lambda url, timeout: _Resp(200, {"models": []}), False, "not_lm_studio", ()),
        (lambda url, timeout: _Resp(200, {"data": []}), True, "", ()),
    ],
)
def test_the_probe_keeps_reachable_with_nothing_apart_from_nothing_answered(
    monkeypatch: pytest.MonkeyPatch, get: Any, reachable: bool, reason: str, models: tuple[str, ...]
) -> None:
    """The two states have opposite remedies — "load a model" and "start the server" — exactly as
    the Ollama probe says, and this probe is the same probe pointed at a second server."""
    import httpx

    monkeypatch.setattr(httpx, "get", get)
    found = lmstudio.loaded_models("http://localhost:1234/v1")
    assert (found.reachable, found.reason, found.models) == (reachable, reason, models)


def test_no_url_asks_nobody(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked"))
    )
    assert lmstudio.loaded_models("").reason == "no_url"


# ------------------------------------------------------------------ what the first-run screen reads


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kw: Any) -> TestClient:
    from chimera.api import build_api_app
    from chimera.interface import ChatSession

    class _Agent:
        def run(self, task: str, **_: Any) -> Any:
            raise AssertionError("no model call belongs in this test")

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return TestClient(
        build_api_app(
            lambda: ChatSession(_Agent()), workspace=ws, settings=_settings(tmp_path, **kw)
        )
    )


def test_the_endpoint_asks_both_runtimes_and_names_the_prefix_for_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.providers import ollama

    monkeypatch.setattr(
        ollama,
        "installed_models",
        lambda base, **_: ollama.InstalledModels(base, True, ("llama3:latest",)),
    )
    monkeypatch.setattr(
        lmstudio,
        "loaded_models",
        lambda base, **_: lmstudio.LoadedModels(base, False, reason="unreachable"),
    )
    client = _client(tmp_path, monkeypatch)

    body = client.get("/api/models/local").json()

    by_name = {r["name"]: r for r in body["runtimes"]}
    assert (
        by_name["ollama"]["models"] == ["llama3:latest"]
        and by_name["ollama"]["prefix"] == "ollama_chat/"
    )
    assert (
        by_name["lm_studio"]["reachable"] is False
        and by_name["lm_studio"]["reason"] == "unreachable"
    )
    assert by_name["lm_studio"]["prefix"] == "lm_studio/"
    assert by_name["lm_studio"]["base_url"] == "http://localhost:1234/v1", (
        "the default LiteLLM needs, /v1 included"
    )


def test_the_doctor_says_a_local_model_can_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The doctor reads the live settings (`get_settings()`), not the app's boot photograph — so the
    model is set the way a user sets it, in the environment, and the cache is cleared as the
    Settings screen's PATCH does."""
    from chimera.config import get_settings

    client = _client(tmp_path, monkeypatch)
    remote = client.get("/api/doctor").json()
    assert remote["local_model"] is False and remote["can_answer"] is False

    monkeypatch.setenv("CHIMERA_DEFAULT_MODEL", "ollama_chat/llama3")
    get_settings.cache_clear()
    keyless = client.get("/api/doctor").json()
    assert keyless["has_any_key"] is False
    assert keyless["local_model"] is True and keyless["can_answer"] is True


def test_the_config_read_carries_the_lm_studio_url_and_the_gateway_exports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from chimera.config import get_settings
    from chimera.providers.gateway import LLMGateway

    settings = _settings(tmp_path, CHIMERA_LM_STUDIO_BASE_URL="http://10.0.0.2:1234/v1")
    LLMGateway(settings=settings)._export_keys_to_env()
    assert os.environ.get("LM_STUDIO_API_BASE") == "http://10.0.0.2:1234/v1", (
        "LiteLLM's lm_studio/ provider reads this and has no default of its own"
    )

    monkeypatch.setenv("CHIMERA_LM_STUDIO_BASE_URL", "http://10.0.0.2:1234/v1")
    get_settings.cache_clear()
    body = _client(tmp_path, monkeypatch).get("/api/config").json()
    assert body["models"]["lm_studio_base_url"] == "http://10.0.0.2:1234/v1"
