"""Reasoning variants route as request options, and a Code conversation keeps the model it was PINNED to.

The effort suffix (``provider/model#high``) is not a provider model id. Three things follow, and each
was a defect before this file:

- the route is sent the effort only where it accepts one, and the receipt names a variant only when
  it was sent — ``generate``/``stream`` once printed ``#high`` for a route that had ignored it;
- every lookup keyed by the model id (window, vision, price, family, a fusion slot) sees the id —
  a ``#high`` turn once fell to the 128k fallback window and read "unknown" for vision;
- a conversation's model is pinned only by a turn that says it is a choice (``model_pinned``). The
  first version stored "the first turn's model" and then preferred it to every later pick, so the
  model of the first turn — often just the install default — was frozen in forever.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.core.agent import AgentResult
from chimera.core.code_session import CodeSession, CodeSessionStore
from chimera.fusion.receipts import resolve_price
from chimera.interface import ChatSession
from chimera.providers.catalog import base_slug, split_effort
from chimera.providers.gateway import LLMGateway, Message


def _response() -> SimpleNamespace:
    message = SimpleNamespace(content="answer", tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def _gateway(monkeypatch: pytest.MonkeyPatch, provider: str) -> LLMGateway:
    # Through the environment the test owns: the gateway exports keys to `os.environ`, and a name
    # set here is one monkeypatch puts back.
    monkeypatch.setenv(f"{provider.upper()}_API_KEY", "test-key")
    return LLMGateway(Settings(_env_file=None))


# ------------------------------------------------------------------ the slug


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("openrouter/vendor/model#low", ("openrouter/vendor/model", "low")),
        ("openrouter/vendor/model#medium", ("openrouter/vendor/model", "medium")),
        ("openai/o4-mini#high", ("openai/o4-mini", "high")),
        # Not an effort: left exactly as it was, so a slug that happens to hold `#` is not rewritten.
        ("openrouter/vendor/model#turbo", ("openrouter/vendor/model#turbo", None)),
        ("#high", ("#high", None)),
        ("openrouter/vendor/model", ("openrouter/vendor/model", None)),
    ],
)
def test_only_a_known_effort_is_split_off_the_slug(slug: str, expected: tuple[str, str | None]) -> None:
    assert split_effort(slug) == expected
    assert base_slug(slug) == expected[0]


# ------------------------------------------------------------------ what goes on the wire


@pytest.mark.parametrize(
    ("model", "expected_kwargs"),
    [
        ("openrouter/vendor/model#low", {"extra_body": {"reasoning": {"effort": "low"}}}),
        ("openrouter/vendor/model#medium", {"extra_body": {"reasoning": {"effort": "medium"}}}),
        ("openrouter/vendor/model#high", {"extra_body": {"reasoning": {"effort": "high"}}}),
        ("openai/model#high", {"reasoning_effort": "high"}),
    ],
)
def test_effort_variant_is_sent_as_route_option_and_kept_in_receipt(
    monkeypatch: pytest.MonkeyPatch, model: str, expected_kwargs: dict[str, Any]
) -> None:
    import litellm

    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    captured: dict[str, Any] = {}

    def fake(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return _response()

    monkeypatch.setattr(litellm, "completion", fake)
    gateway = _gateway(monkeypatch, model.split("/", 1)[0])

    result = gateway.complete([Message(role="user", content="hello")], model=model)

    base, _, effort = model.rpartition("#")
    assert captured["model"] == base
    for key, value in expected_kwargs.items():
        assert captured[key] == value
    assert result.model == f"{base}#{effort}"


def test_plain_model_slug_keeps_existing_request_and_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    import litellm

    captured: dict[str, Any] = {}

    def fake(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return _response()

    monkeypatch.setattr(litellm, "completion", fake)
    result = _gateway(monkeypatch, "openrouter").complete(
        [Message(role="user", content="hello")], model="openrouter/vendor/model"
    )

    assert captured["model"] == "openrouter/vendor/model"
    assert "reasoning" not in captured.get("extra_body", {})
    assert "reasoning_effort" not in captured
    assert result.model == "openrouter/vendor/model"


def test_unsupported_route_ignores_effort_and_warns_only_once(caplog: pytest.LogCaptureFixture) -> None:
    gateway = LLMGateway(Settings(_env_file=None))

    for _ in range(2):
        kwargs = gateway._provider_kwargs("anthropic/vendor/model", reasoning_effort="high")
        assert "reasoning_effort" not in kwargs
        assert "reasoning" not in kwargs.get("extra_body", {})

    warnings = [r for r in caplog.records if "reasoning-effort variant" in r.getMessage()]
    assert len(warnings) == 1


def test_an_openai_slug_on_another_endpoint_is_not_sent_the_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LM Studio and vLLM speak the OpenAI shape; some refuse an unknown `reasoning_effort` with 400."""
    monkeypatch.setenv("OPENAI_API_BASE", "http://127.0.0.1:1234/v1")
    kwargs = _gateway(monkeypatch, "openai")._provider_kwargs("openai/local-model", reasoning_effort="high")
    assert "reasoning_effort" not in kwargs


@pytest.mark.parametrize("slug", ["openai/local-model", "openrouter/vendor/model"])
def test_a_custom_chimera_api_base_takes_the_effort_off_every_route(
    monkeypatch: pytest.MonkeyPatch, slug: str
) -> None:
    """`CHIMERA_API_BASE` sends every route to that one endpoint, whose dialect we do not know."""
    monkeypatch.setenv("CHIMERA_API_BASE", "http://127.0.0.1:8000/v1")
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    gateway = _gateway(monkeypatch, slug.split("/", 1)[0])
    kwargs = gateway._provider_kwargs(slug, reasoning_effort="high")
    assert "reasoning_effort" not in kwargs
    assert "reasoning" not in kwargs.get("extra_body", {})
    assert gateway._receipt_model(slug, "high") == slug


def test_a_route_that_ignored_the_effort_has_no_variant_on_any_paths_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``generate`` (acomplete) and ``stream_complete`` named `#high` for a route sent no effort."""
    import asyncio

    import litellm

    calls: list[dict[str, Any]] = []

    def fake(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if kwargs.get("stream"):
            delta = SimpleNamespace(content="answer", tool_calls=None, reasoning_content=None)
            return iter([SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason="stop")])])
        return _response()

    async def afake(**kwargs: Any) -> Any:
        return fake(**kwargs)

    monkeypatch.setattr(litellm, "completion", fake)
    monkeypatch.setattr(litellm, "acompletion", afake)
    gateway = _gateway(monkeypatch, "anthropic")
    model = "anthropic/claude-x#high"
    messages = [Message(role="user", content="hello")]

    blocking = gateway.complete(messages, model=model)
    generated = asyncio.run(gateway.acomplete(messages, model=model))
    streamed = gateway.stream_complete(messages, model=model, on_token=lambda _t: None)

    assert [blocking.model, generated.model, streamed.model] == ["anthropic/claude-x"] * 3
    assert all("reasoning_effort" not in c and "reasoning" not in c.get("extra_body", {}) for c in calls)
    assert all(c["model"] == "anthropic/claude-x" for c in calls)


def test_generate_and_stream_send_the_effort_and_name_it(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    import litellm

    calls: list[dict[str, Any]] = []

    def fake(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if kwargs.get("stream"):
            delta = SimpleNamespace(content="answer", tool_calls=None, reasoning_content=None)
            return iter([SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason="stop")])])
        return _response()

    async def afake(**kwargs: Any) -> Any:
        return fake(**kwargs)

    monkeypatch.setattr(litellm, "completion", fake)
    monkeypatch.setattr(litellm, "acompletion", afake)
    gateway = _gateway(monkeypatch, "openrouter")
    model = "openrouter/vendor/model#medium"
    messages = [Message(role="user", content="hello")]

    generated = asyncio.run(gateway.acomplete(messages, model=model))
    streamed = gateway.stream_complete(messages, model=model, on_token=lambda _t: None)

    assert [generated.model, streamed.model] == [model, model]
    assert [c["extra_body"]["reasoning"] for c in calls] == [{"effort": "medium"}] * 2


# ------------------------------------------------------------------ lookups keyed by the id


def test_reasoning_suffix_does_not_change_the_catalogue_price() -> None:
    from chimera.providers.catalog import CATALOG

    base = next(e.slug for e in CATALOG if e.input_per_m)
    # Not None first: two unknown prices compare equal and would prove nothing.
    assert resolve_price(base) is not None
    assert resolve_price(f"{base}#high") == resolve_price(base)


def test_window_vision_and_family_are_the_same_with_and_without_a_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chimera.api import attachments
    from chimera.core.context_budget import FALLBACK_CONTEXT_TOKENS, window_tokens
    from chimera.providers import listing
    from chimera.providers.catalog import CATALOG
    from chimera.review.family import model_family

    base = CATALOG[0].slug
    assert window_tokens(base) != FALLBACK_CONTEXT_TOKENS  # a real answer, or the check is empty
    assert window_tokens(f"{base}#high") == window_tokens(base)

    seen: list[str] = []
    monkeypatch.setattr(listing, "known_vision", lambda slug: seen.append(slug) or True)
    assert attachments.vision_support(f"{base}#low") == "yes"
    assert seen == [base]

    assert model_family(f"{base}#high") == model_family(base)


def test_a_fusion_panelist_keeps_its_temperature_slot_when_its_variant_was_ignored() -> None:
    from chimera.fusion.engine import FusionConfig

    config = FusionConfig(
        panel=["openrouter/a/one#high", "anthropic/two#high"], judge="openrouter/j",
        synthesizer="openrouter/s", panel_temperatures=[0.2, 0.9],
    )
    # The receipt names what ran: `anthropic/two`, its route having taken no variant.
    assert config.temperature_for("anthropic/two") == 0.9
    assert config.temperature_for("openrouter/a/one#high") == 0.2


# ------------------------------------------------------------------ the session file


class _Agent:
    def run(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the persistence test must not run an agent")


def test_selected_model_variant_survives_loading_a_code_conversation(tmp_path: Path) -> None:
    store = CodeSessionStore(tmp_path)
    original = CodeSession(
        _Agent(),
        session_id="saved-variant",
        selected_model="openrouter/vendor/model#high",
        messages=[{"role": "user", "content": "continue this task"}],
    )
    store.save(original)

    reopened = store.load("saved-variant", _Agent())

    assert reopened.selected_model == "openrouter/vendor/model#high"
    assert store.stored_model("saved-variant") == "openrouter/vendor/model#high"
    assert reopened.messages == original.messages


def test_refresh_takes_the_stored_pin_unless_this_turn_set_one(tmp_path: Path) -> None:
    """A turn waiting behind another must not save its stale pin over the newer one; a turn that
    unpinned must not get the old pin back from disk."""
    store = CodeSessionStore(tmp_path)
    store.save(CodeSession(_Agent(), session_id="s", selected_model="openrouter/new/model"))

    stale = CodeSession(_Agent(), session_id="s", selected_model="openrouter/old/model")
    store.refresh(stale)
    assert stale.selected_model == "openrouter/new/model"

    unpinned = CodeSession(_Agent(), session_id="s")
    unpinned.pin("")
    store.refresh(unpinned)
    assert unpinned.selected_model == ""


# ------------------------------------------------------------------ which model a turn runs on

BUILT: list[str | None] = []


class _RecordingAgent:
    def __init__(self, *args: Any, **_kw: Any) -> None:
        config = args[2] if len(args) > 2 else None
        if config is not None and hasattr(config, "model"):
            BUILT.append(config.model)

    def run(self, task: str, **kw: Any) -> AgentResult:
        history = list(kw.get("history") or [])
        return AgentResult(
            answer="ok", steps=1, stopped_reason="final", model="test/model",
            transcript=[*history, {"role": "user", "content": task}, {"role": "assistant", "content": "ok"}],
        )


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> tuple[TestClient, Path]:
    import chimera.core
    from chimera.api import build_api_app
    from chimera.config import get_settings

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _RecordingAgent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")  # type: ignore[call-arg]
    BUILT.clear()
    app = build_api_app(lambda: ChatSession(_RecordingAgent()), workspace=ws, settings=settings)
    return TestClient(app), home


def _turn(client: TestClient, **body: Any) -> str:
    response = client.post(
        "/api/code/turn", json={"message": "explain the parser", "stream": False, **body}
    )
    assert response.status_code == 200, response.text
    for line in response.text.splitlines():
        if line.startswith("data: ") and '"session_id"' in line:
            return str(json.loads(line[len("data: ") :])["session_id"])
    raise AssertionError(f"no session id in {response.text[:400]}")


def test_a_first_turn_model_is_not_a_pin_and_a_later_pick_still_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    sid = _turn(client, model="openrouter/first/model")
    _turn(client, session_id=sid, model="openrouter/second/model#high", model_pinned=True)
    _turn(client, session_id=sid)

    assert BUILT == ["openrouter/first/model", "openrouter/second/model#high", "openrouter/second/model#high"]
    assert client.get(f"/api/code/sessions/{sid}").json()["selected_model"] == "openrouter/second/model#high"


def test_a_pinned_conversation_ignores_a_changed_global_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _client(tmp_path, monkeypatch, CHIMERA_DEFAULT_MODEL="openrouter/old/default")
    sid = _turn(client, model="openrouter/pinned/model#low", model_pinned=True)
    unpinned = _turn(client)

    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_DEFAULT_MODEL", "openrouter/new/default")
    get_settings.cache_clear()
    _turn(client, session_id=sid)
    _turn(client, session_id=unpinned)

    # The pinned one stays; the one nobody pinned follows the default (None = the install's).
    assert BUILT[2] == "openrouter/pinned/model#low"
    assert BUILT[3] is None


def test_an_unflagged_model_is_a_one_turn_override_and_an_empty_pin_unpins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    sid = _turn(client, model="openrouter/pinned/model", model_pinned=True)
    _turn(client, session_id=sid, model="openrouter/once/model")
    _turn(client, session_id=sid)
    _turn(client, session_id=sid, model_pinned=True)
    _turn(client, session_id=sid)

    assert BUILT == [
        "openrouter/pinned/model", "openrouter/once/model", "openrouter/pinned/model", None, None,
    ]


def test_a_pin_does_not_move_the_voice_talk_model_or_a_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Protected choices keep their own model: the spoken talk model, and a refusal's retry."""
    client, _ = _client(tmp_path, monkeypatch, CHIMERA_VOICE_MODEL="openrouter/voice/model")
    sid = _turn(client, model="openrouter/pinned/model", model_pinned=True)
    _turn(client, session_id=sid, spoken=True, message="what is this file?")
    _turn(
        client, session_id=sid, model="openrouter/retry/model",
        retry_of={"blocked_model": "openrouter/pinned/model"},
    )
    _turn(client, session_id=sid)

    assert BUILT[1] == "openrouter/voice/model"
    assert BUILT[2] == "openrouter/retry/model"
    assert BUILT[3] == "openrouter/pinned/model"


def test_a_guest_turn_never_pins() -> None:
    from chimera.api.code_api import CodeTurnRequest, _conversation_model

    req = CodeTurnRequest(message="hi", model="openrouter/guest/model", model_pinned=True)
    assert _conversation_model(req, "openrouter/owner/model", guest=True) == ("openrouter/guest/model", None)
    assert _conversation_model(req, "openrouter/owner/model", guest=False) == (
        "openrouter/guest/model", "openrouter/guest/model",
    )
