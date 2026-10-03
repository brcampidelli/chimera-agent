"""An OpenRouter request can say what the route that serves it may keep (study 29, P5.6).

OpenRouter forwards a request to whichever upstream provider serves the model, under that provider's
data policy, and nothing in the project could ask for a stricter one — the VPS's default route sent
every turn, memory recall included, wherever was cheapest. `CHIMERA_OPENROUTER_DATA_COLLECTION=deny`
and `CHIMERA_OPENROUTER_ZDR=true` ask for it.

Both ship OFF, and OFF has to mean the request did not change at all: the first half of this file
reads the bytes that leave the process (litellm's real request builder, with only the socket
replaced) and holds that a default request carries no routing preference whatsoever. The second half
holds that the preference, once set, reaches every call path, merges with a route pin instead of
replacing it, survives a caller's own pin, and is never sent to a provider that does not know it.
No network: the transport is faked.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from chimera.config import Settings, get_settings
from chimera.providers.gateway import Message

OR_MODEL = "openrouter/deepseek/deepseek-chat"
HI = [Message(role="user", content="hi")]
_OK = {
    "id": "x",
    "object": "chat.completion",
    "created": 0,
    "model": "deepseek/deepseek-chat",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


def _wire(monkeypatch: pytest.MonkeyPatch, **env: str) -> list[bytes]:
    """Every request body litellm puts on the socket, with the socket answered by a canned reply."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    bodies: list[bytes] = []

    def send(self: httpx.Client, request: httpx.Request, *_a: Any, **_k: Any) -> httpx.Response:
        # litellm's first import fetches its price table through the same client; it gets a 404 and
        # uses the copy it ships, so nothing here touches the network and only completions count.
        if not request.url.path.endswith("/chat/completions"):
            return httpx.Response(404, request=request)
        bodies.append(request.content)
        return httpx.Response(200, json=_OK, request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    return bodies


def _sent(monkeypatch: pytest.MonkeyPatch, **env: str) -> dict[str, Any]:
    from chimera.providers import LLMGateway

    bodies = _wire(monkeypatch, **env)
    LLMGateway().complete(HI, model=OR_MODEL, temperature=0.2)
    assert len(bodies) == 1, "expected exactly one request on the wire"
    parsed: dict[str, Any] = json.loads(bodies[0])
    return parsed


# --------------------------------------------------------------------------- off means unchanged


def test_a_default_request_carries_no_routing_preference_on_the_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _sent(monkeypatch)
    assert "provider" not in body
    assert b"data_collection" not in json.dumps(body).encode()
    assert b"zdr" not in json.dumps(body).encode()


def test_writing_the_defaults_out_sends_the_same_bytes_as_not_writing_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`allow` and `false` are OpenRouter's own defaults. Sending them explicitly would change every
    request's bytes and its completion-cache key for no effect, so the explicit default is no field."""
    from chimera.providers import LLMGateway

    bodies = _wire(monkeypatch)
    LLMGateway().complete(HI, model=OR_MODEL, temperature=0.2)
    monkeypatch.setenv("CHIMERA_OPENROUTER_DATA_COLLECTION", "allow")
    monkeypatch.setenv("CHIMERA_OPENROUTER_ZDR", "false")
    get_settings.cache_clear()
    LLMGateway().complete(HI, model=OR_MODEL, temperature=0.2)
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]


def test_the_settings_ship_off() -> None:
    fields = Settings.model_fields
    assert fields["openrouter_data_collection"].default == "allow"
    assert fields["openrouter_zdr"].default is False


# --------------------------------------------------------------------------- on reaches the wire


def test_deny_reaches_the_wire_as_openrouters_own_field(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _sent(monkeypatch, CHIMERA_OPENROUTER_DATA_COLLECTION="deny")
    assert body["provider"] == {"data_collection": "deny"}


def test_zero_data_retention_reaches_the_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _sent(monkeypatch, CHIMERA_OPENROUTER_ZDR="true")
    assert body["provider"] == {"zdr": True}


def test_a_route_pin_and_the_preference_share_one_provider_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenRouter reads one routing object. Beside the pin instead of inside it, a pinned route that
    keeps data would have been served under `deny`."""
    body = _sent(
        monkeypatch,
        CHIMERA_PROVIDER_ORDER="DeepSeek",
        CHIMERA_OPENROUTER_DATA_COLLECTION="deny",
        CHIMERA_OPENROUTER_ZDR="1",
    )
    assert body["provider"] == {
        "order": ["DeepSeek"],
        "allow_fallbacks": False,
        "data_collection": "deny",
        "zdr": True,
    }


# --------------------------------------------------------------------------- every call path


def _kwargs_gateway(
    monkeypatch: pytest.MonkeyPatch, seen: list[dict[str, Any]], **env: str
) -> Any:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    import litellm

    message = SimpleNamespace(content="ok", tool_calls=None)
    reply = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)

    def fake(**kwargs: Any) -> Any:
        seen.append(kwargs)
        return [] if kwargs.get("stream") else reply

    async def afake(**kwargs: Any) -> Any:
        seen.append(kwargs)
        return reply

    def fake_embed(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs)
        return {"data": [{"embedding": [0.1]}]}

    monkeypatch.setattr(litellm, "completion", fake)
    monkeypatch.setattr(litellm, "acompletion", afake)
    monkeypatch.setattr(litellm, "embedding", fake_embed)
    from chimera.providers import LLMGateway

    return LLMGateway()


DENY = {"provider": {"data_collection": "deny"}}


def test_every_call_path_carries_the_preference(monkeypatch: pytest.MonkeyPatch) -> None:
    """The streaming primitive and the embedder asked `_provider_kwargs()` without the model, so a
    route-scoped field could never reach them: the live terminal, the A2A stream and semantic-memory
    recall would have gone out without the `deny` the owner set."""
    seen: list[dict[str, Any]] = []
    gateway = _kwargs_gateway(monkeypatch, seen, CHIMERA_OPENROUTER_DATA_COLLECTION="deny")
    gateway.complete(HI, model=OR_MODEL)
    asyncio.run(gateway.acomplete(HI, model=OR_MODEL))
    list(gateway.stream(HI, model=OR_MODEL))
    gateway.stream_complete(HI, model=OR_MODEL)
    gateway.embed(["remember this"], model="openrouter/openai/text-embedding-3-small")
    assert len(seen) == 5
    assert [call.get("extra_body") for call in seen] == [DENY] * 5


def test_every_call_path_sends_nothing_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    gateway = _kwargs_gateway(monkeypatch, seen)
    gateway.complete(HI, model=OR_MODEL)
    asyncio.run(gateway.acomplete(HI, model=OR_MODEL))
    list(gateway.stream(HI, model=OR_MODEL))
    gateway.stream_complete(HI, model=OR_MODEL)
    gateway.embed(["remember this"], model="openrouter/openai/text-embedding-3-small")
    assert len(seen) == 5
    assert all("extra_body" not in call for call in seen)


def test_another_provider_is_never_sent_the_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only OpenRouter knows `provider.data_collection`; another provider may refuse the request."""
    seen: list[dict[str, Any]] = []
    gateway = _kwargs_gateway(
        monkeypatch, seen, CHIMERA_OPENROUTER_DATA_COLLECTION="deny", CHIMERA_OPENROUTER_ZDR="true"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    gateway.complete(HI, model="openai/gpt-4.1-mini")
    gateway.complete(HI, model="ollama_chat/llama3")
    assert all("extra_body" not in call for call in seen)


def test_a_fallback_on_another_route_is_not_sent_the_field(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    gateway = _kwargs_gateway(
        monkeypatch,
        seen,
        CHIMERA_OPENROUTER_DATA_COLLECTION="deny",
        CHIMERA_FALLBACK_MODELS="openai/gpt-4.1-mini",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    import litellm

    reply = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None), finish_reason="stop")],
        usage=None,
    )

    def failing_primary(**kwargs: Any) -> Any:
        seen.append(kwargs)
        if kwargs["model"] == OR_MODEL:
            raise RuntimeError("upstream exploded")  # an unknown error falls back
        return reply

    monkeypatch.setattr(litellm, "completion", failing_primary)
    gateway.complete(HI, model=OR_MODEL)
    assert [call["model"] for call in seen] == [OR_MODEL, "openai/gpt-4.1-mini"]
    assert seen[0]["extra_body"] == DENY
    assert "extra_body" not in seen[1]


def test_a_callers_own_route_pin_keeps_the_owners_preference(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every bench pins its route with its own `{"provider": {...}}`. A whole-object replace dropped
    the owner's `deny` from each of those calls; the merge now goes one level into `provider`."""
    seen: list[dict[str, Any]] = []
    gateway = _kwargs_gateway(monkeypatch, seen, CHIMERA_OPENROUTER_DATA_COLLECTION="deny")
    pin = {"order": ["DeepInfra"], "allow_fallbacks": False}
    gateway.complete(HI, model=OR_MODEL, extra_body={"provider": pin})
    assert seen[-1]["extra_body"] == {"provider": {**pin, "data_collection": "deny"}}


def test_a_caller_that_names_the_field_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    gateway = _kwargs_gateway(monkeypatch, seen, CHIMERA_OPENROUTER_DATA_COLLECTION="deny")
    gateway.complete(HI, model=OR_MODEL, extra_body={"provider": {"data_collection": "allow"}})
    assert seen[-1]["extra_body"] == {"provider": {"data_collection": "allow"}}


# --------------------------------------------------------------------------- reading the setting


def test_an_empty_line_is_the_default() -> None:
    assert Settings(CHIMERA_OPENROUTER_DATA_COLLECTION="").openrouter_data_collection == "allow"  # type: ignore[call-arg]


def test_a_misspelt_value_reads_as_deny_not_as_allow() -> None:
    """The only reason to write the line is to ask for `deny`. A typo falling back to `allow` would
    send prompts to routes that keep them while the owner believes otherwise — invisibly."""
    for word in ("deney", "no", "DENY ", "off"):
        settings = Settings(CHIMERA_OPENROUTER_DATA_COLLECTION=word)  # type: ignore[call-arg]
        assert settings.openrouter_data_collection == "deny", word


# --------------------------------------------------------------------------- the settings screen


def test_the_screen_may_write_both_and_they_apply_from_the_next_call() -> None:
    from chimera.api.config_api import APPLIES_WHEN, is_editable

    for key in ("CHIMERA_OPENROUTER_DATA_COLLECTION", "CHIMERA_OPENROUTER_ZDR"):
        assert is_editable(key)
        assert key not in APPLIES_WHEN


def test_a_saved_deny_reaches_the_next_call_without_a_relaunch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gateway reads settings per call; what the screen saves is what the next request carries."""
    from chimera.api.config_api import patch_config

    seen: list[dict[str, Any]] = []
    gateway = _kwargs_gateway(monkeypatch, seen, CHIMERA_OPENROUTER_DATA_COLLECTION="allow")
    gateway.complete(HI, model=OR_MODEL)
    patch_config({"CHIMERA_OPENROUTER_DATA_COLLECTION": "deny"}, env_path=tmp_path / ".env")
    gateway.complete(HI, model=OR_MODEL)
    assert "extra_body" not in seen[0]
    assert seen[1]["extra_body"] == DENY


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("CHIMERA_OPENROUTER_DATA_COLLECTION", "maybe"),
        ("CHIMERA_OPENROUTER_DATA_COLLECTION", ""),
        ("CHIMERA_OPENROUTER_ZDR", "sometimes"),
    ],
)
def test_the_screen_cannot_save_a_third_word(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, value: str
) -> None:
    """The settings validator reads a stray word as `deny` for a hand-edited file; a screen that
    offers two choices has no business saving a third, so the endpoint refuses before writing."""
    from chimera.api.config_api import patch_config

    # Owned, so a regression that writes fails here and does not leak the value into later tests.
    monkeypatch.setenv(key, "")
    env = tmp_path / ".env"
    with pytest.raises(ValueError, match=key):
        patch_config({key: value}, env_path=env)
    assert not env.exists()
