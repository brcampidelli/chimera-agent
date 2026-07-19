"""The OpenAI-compatible endpoint — the seam that lets LLM benchmarks measure the agent loop."""

from __future__ import annotations

import json
from typing import Any

import pytest

from chimera.api.openai_compat import (
    ChatMessage,
    build_completion,
    flatten_content,
    resolve_model,
    split_messages,
)
from chimera.interface.session import TurnReport

fastapi = pytest.importorskip("fastapi", reason="the [desktop] extra is optional")


def _report(**kw: Any) -> TurnReport:
    base: dict[str, Any] = {
        "answer": "42",
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "model": "openrouter/some-model",
        "steps": 3,
        "tool_names": ["run_shell"],
    }
    base.update(kw)
    return TurnReport(**base)


# --- model resolution -------------------------------------------------------------------------


def test_resolve_model_aliases_mean_configured_default() -> None:
    for alias in ("chimera", "chimera-agent", "default", "", "  "):
        assert resolve_model(alias) is None


def test_resolve_model_strips_the_chimera_prefix() -> None:
    assert resolve_model("chimera/openrouter/qwen/qwen3-coder") == "openrouter/qwen/qwen3-coder"
    assert resolve_model("chimera/") is None  # prefix with nothing after it = default


def test_resolve_model_passes_a_bare_slug_through() -> None:
    assert resolve_model("openrouter/deepseek/deepseek-chat-v3.1") == "openrouter/deepseek/deepseek-chat-v3.1"


# --- message handling -------------------------------------------------------------------------


def test_flatten_content_handles_string_and_parts() -> None:
    assert flatten_content("hi") == "hi"
    assert flatten_content([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "a\nb"


def test_flatten_content_drops_images_rather_than_inventing_a_placeholder() -> None:
    # Emitting "[image]" would let a caller believe an image was considered. This endpoint is text-only.
    parts = [{"type": "image_url", "image_url": {"url": "x"}}, {"type": "text", "text": "what is this"}]
    assert flatten_content(parts) == "what is this"


def test_split_messages_separates_system_history_and_final_user() -> None:
    msgs = [
        ChatMessage(role="system", content="be terse"),
        ChatMessage(role="user", content="first"),
        ChatMessage(role="assistant", content="answer one"),
        ChatMessage(role="user", content="second"),
    ]
    system, history, final = split_messages(msgs)
    assert system == "be terse"
    assert history == [("first", "answer one")]
    assert final == "second"


def test_split_messages_keeps_an_unanswered_user_turn() -> None:
    # Two users in a row: the first got no reply. Dropping it would silently lose context the caller sent.
    msgs = [ChatMessage(role="user", content="a"), ChatMessage(role="user", content="b")]
    _system, history, final = split_messages(msgs)
    assert history == [("a", "")]
    assert final == "b"


# --- response shape ---------------------------------------------------------------------------


def test_build_completion_is_openai_shaped() -> None:
    body = build_completion(_report(), model="chimera", completion_id="chatcmpl-x", created=1)
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "42"}
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"] == {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    assert body["model"] == "openrouter/some-model"  # the model that ACTUALLY answered, not the alias


def test_usage_reports_the_whole_turn_not_one_call() -> None:
    # The load-bearing honesty property: a 5-step loop's usage is the sum, so a cost comparison
    # against a raw model is not silently flattered by reporting only the final call.
    body = build_completion(
        _report(prompt_tokens=8000, completion_tokens=1500, steps=5),
        model="chimera",
        completion_id="c",
        created=1,
    )
    assert body["usage"]["total_tokens"] == 9500
    assert body["chimera"]["steps"] == 5


def test_unknown_price_stays_none_never_zero() -> None:
    body = build_completion(_report(usd=None), model="chimera", completion_id="c", created=1)
    assert body["chimera"]["usd"] is None  # a guessed 0.0 would understate cost


def test_budget_exhaustion_is_length_everything_else_is_stop() -> None:
    truncated = build_completion(
        _report(stopped_reason="token_budget exhausted"), model="m", completion_id="c", created=1
    )
    assert truncated["choices"][0]["finish_reason"] == "length"
    refused = build_completion(
        _report(stopped_reason="model declined"), model="m", completion_id="c", created=1
    )
    assert refused["choices"][0]["finish_reason"] == "stop"  # a refusal is a normal stop


# --- the endpoint end-to-end (no provider key: run_turn is injected) --------------------------


class _FakeSession:
    """Stands in for ChatSession; records what the endpoint put into it."""

    def __init__(self) -> None:
        self.profile = ""
        self.turns: list[Any] = []
        self.model: str | None = "UNSET"

    def set_model(self, model: str | None) -> bool:
        self.model = model
        return True


class _FakeManager:
    def __init__(self) -> None:
        self.made: list[_FakeSession] = []

    def ephemeral(self) -> Any:
        session = _FakeSession()
        self.made.append(session)
        return session


def _client(run_turn: Any) -> tuple[Any, _FakeManager]:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from chimera.api.openai_compat import register_openai_compat

    app = FastAPI()
    manager = _FakeManager()

    def _noguard() -> None:
        return None

    register_openai_compat(app, fastapi.Depends(_noguard), manager, run_turn=run_turn)  # type: ignore[arg-type]
    return TestClient(app), manager


def test_endpoint_returns_a_valid_completion() -> None:
    client, manager = _client(lambda _s, msg: _report(answer=f"echo:{msg}"))
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "chimera/openrouter/x", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "echo:hello"
    assert manager.made[0].model == "openrouter/x"  # the prefix was stripped and applied


def test_requests_are_independent_no_history_leaks() -> None:
    # THE property a benchmark depends on: item 2 must not see item 1's transcript, or later items get
    # quietly easier and the score is inflated. Each request must get its own fresh session.
    client, manager = _client(lambda _s, msg: _report(answer=msg))
    for text in ("first", "second"):
        client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": text}]})
    assert len(manager.made) == 2
    assert manager.made[0] is not manager.made[1]
    assert manager.made[1].turns == []  # the second request starts with an empty transcript


def test_system_message_becomes_the_session_profile() -> None:
    client, manager = _client(lambda _s, _m: _report())
    client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {"role": "system", "content": "answer in one word"},
                {"role": "user", "content": "hi"},
            ]
        },
    )
    assert manager.made[0].profile == "answer in one word"


def test_prior_turns_are_replayed_into_the_ephemeral_session() -> None:
    client, manager = _client(lambda _s, _m: _report())
    client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {"role": "user", "content": "my name is Bruno"},
                {"role": "assistant", "content": "noted"},
                {"role": "user", "content": "what is my name?"},
            ]
        },
    )
    turns = manager.made[0].turns
    assert len(turns) == 1
    assert (turns[0].user, turns[0].assistant) == ("my name is Bruno", "noted")


def test_unsupported_sampling_params_are_accepted_not_rejected() -> None:
    # Real harnesses send temperature/top_p/seed/max_tokens. A 422 here would break every one of them.
    client, _ = _client(lambda _s, _m: _report())
    resp = client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "x"}],
            "temperature": 0.2,
            "top_p": 0.9,
            "seed": 7,
            "max_tokens": 512,
            "stream_options": {"include_usage": True},
        },
    )
    assert resp.status_code == 200


def test_empty_messages_is_a_400() -> None:
    client, _ = _client(lambda _s, _m: _report())
    assert client.post("/v1/chat/completions", json={"messages": []}).status_code == 400


def test_turn_failure_is_a_500_without_leaking_internals() -> None:
    def boom(_s: Any, _m: str) -> TurnReport:
        raise RuntimeError("provider key sk-secret-123 rejected")

    client, _ = _client(boom)
    resp = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x"}]})
    assert resp.status_code == 500
    assert "sk-secret" not in resp.text


def test_stream_emits_openai_chunks_and_a_done_sentinel() -> None:
    client, _ = _client(lambda _s, _m: _report(answer="streamed"))
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "x"}], "stream": True},
    )
    assert resp.status_code == 200
    lines = [ln for ln in resp.text.splitlines() if ln.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"  # the sentinel every OpenAI client waits for
    chunks = [json.loads(ln[len("data: ") :]) for ln in lines[:-1]]
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert "".join(c["choices"][0]["delta"].get("content", "") for c in chunks) == "streamed"
    assert chunks[-1]["usage"]["total_tokens"] == 120  # usage rides the terminal chunk


def test_models_endpoint_lists_the_catalog() -> None:
    client, _ = _client(lambda _s, _m: _report())
    body = client.get("/v1/models").json()
    assert body["object"] == "list"
    ids = [m["id"] for m in body["data"]]
    assert ids[0] == "chimera"
    assert any(i.startswith("chimera/openrouter/") for i in ids)
