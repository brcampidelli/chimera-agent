"""A content-policy refusal says what refused it, and only the owner tries another model.

Study 29 P5.7. CONTENT_POLICY is ABORT, deliberately: another key of the same provider refuses the
same text, and moving to the fallback model silently would route around a safeguard to whichever
model has the weakest one — the plan's ``not_doing`` rules that out by name. That part stays.

What was wrong is what came after. The coding turn's error frame said "the coding turn failed":
`_native_failure` forwards only the provider sentences it has markers for, and none of them reads
a policy refusal, so a refusal was indistinguishable from a crash in this repository. The screen
had no model to name and no request id to quote, so it could not offer the one honest next step —
the owner choosing another model. And on Discord the exception escaped the gateway, the adapter
sent nothing, and the message read as ignored.

Every refusal below is built locally on the shapes LiteLLM and the providers use (the same ones
``test_a_safety_word_does_not_abort_the_turn.py`` uses); no call was made to draw them.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import litellm
import pytest

from chimera.config import get_settings
from chimera.providers.failover import (
    PolicyBlock,
    failed_model,
    mark_model,
    policy_block,
)


def _openai_refusal() -> Exception:
    return litellm.ContentPolicyViolationError(
        message=(
            "ContentPolicyViolationError: OpenAIException - Your request was rejected as a"
            " result of our safety system."
        ),
        model="gpt-4o",
        llm_provider="openai",
    )


def _router_refusal() -> Exception:
    # OpenRouter passes an upstream refusal on as a 400 with the route named in its metadata.
    return litellm.BadRequestError(
        message=(
            'OpenrouterException - {"error":{"message":"Provider returned error","code":400,'
            '"metadata":{"raw":"Invalid prompt: your prompt was flagged as potentially'
            ' violating our usage policy.","provider_name":"OpenAI"}}}'
        ),
        model="openrouter/openai/gpt-4o",
        llm_provider="openrouter",
    )


class _WithHeaders(Exception):
    """A refusal that came back over HTTP, with the provider's own id in the headers."""

    status_code = 400

    def __init__(self) -> None:
        super().__init__("request blocked by the content policy")
        self.response = SimpleNamespace(
            status_code=400, headers={"x-request-id": "req_9f2c41"}
        )


# --- what the refusal is said to be -----------------------------------------------------------


def test_a_refusal_is_read_with_its_route_and_its_request_id() -> None:
    routed = policy_block(_router_refusal())
    assert routed is not None and routed.provider == "OpenAI"

    with_id = policy_block(_WithHeaders())
    assert with_id is not None and with_id.request_id == "req_9f2c41"

    # The model is the gateway's to say; nothing recorded it here, and nothing is guessed.
    assert routed.model is None


def test_only_a_refusal_becomes_a_policy_block() -> None:
    # A 503 that quotes "safety" is an outage; a 500 the same. Neither is a refusal to offer a
    # model choice for — the fallback chain already handles them.
    outage = litellm.ServiceUnavailableError(
        message="the safety team's endpoint is down", model="m", llm_provider="openrouter"
    )
    assert policy_block(outage) is None
    assert policy_block(RuntimeError("worker crashed")) is None


def test_the_sentence_names_the_model_and_says_nothing_was_retried() -> None:
    text = PolicyBlock("openrouter/openai/gpt-4o", "OpenAI", "req_9f2c41").sentence()
    assert "openrouter/openai/gpt-4o" in text and "OpenAI" in text and "req_9f2c41" in text
    assert "Nothing was retried" in text
    # With nothing known, it still says what happened rather than a blank.
    assert "content policy" in PolicyBlock(None).sentence()


def test_a_mark_on_an_exception_that_refuses_attributes_is_not_a_crash() -> None:
    class Slotted(Exception):
        __slots__ = ()

    exc = ValueError("x")
    mark_model(exc, "prov/m")
    assert failed_model(exc) == "prov/m"
    # `BaseException` instances always take attributes; the guard is for a stranger type. Either
    # way the call returns and the original exception is what gets raised.
    mark_model(Slotted(), "prov/m")


# --- the gateway names the model that refused, past a fallback -----------------------------------


def test_the_gateway_names_the_fallback_that_refused_not_the_model_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("CHIMERA_FALLBACK_MODELS", "prov/backup")
    get_settings.cache_clear()
    seen: list[str] = []

    def fake(*, model: str, **_: Any) -> Any:
        seen.append(model)
        if model == "prov/primary":
            raise litellm.ServiceUnavailableError(
                message="overloaded", model=model, llm_provider="openrouter"
            )
        raise _openai_refusal()

    monkeypatch.setattr(litellm, "completion", fake)
    from chimera.providers import LLMGateway
    from chimera.providers.gateway import Message

    with pytest.raises(litellm.ContentPolicyViolationError) as caught:
        LLMGateway().complete([Message(role="user", content="hi")], model="prov/primary")
    assert seen == ["prov/primary", "prov/backup"]
    block = policy_block(caught.value)
    assert block is not None and block.model == "prov/backup"


def test_the_streaming_path_names_its_model_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()

    def fake(**_: Any) -> Any:
        raise _openai_refusal()

    monkeypatch.setattr(litellm, "completion", fake)
    from chimera.providers import LLMGateway
    from chimera.providers.gateway import Message

    with pytest.raises(litellm.ContentPolicyViolationError) as caught:
        LLMGateway().stream_complete([Message(role="user", content="hi")], model="prov/stream")
    assert failed_model(caught.value) == "prov/stream"


# --- the coding turn says so, and the retry is written on the receipt ---------------------------

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.core.agent import AgentResult  # noqa: E402
from chimera.core.context_budget import RunState  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402


class _Refuses:
    """Refuses every task that says "refuse"; answers the rest."""

    def __init__(self, *_a: Any, **_k: Any) -> None:
        self.run_state = RunState()

    def run(self, task: str, **_kw: Any) -> AgentResult:
        if "refuse" in task:
            exc = _router_refusal()
            mark_model(exc, "openrouter/openai/gpt-4o")
            raise exc
        if "crash" in task:
            raise RuntimeError("upstream said no")
        return AgentResult(
            answer="done", steps=1, stopped_reason="final", model="prov/other",
            transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "done"}],
        )


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    monkeypatch.setattr(chimera.core, "Agent", _Refuses, raising=True)
    return TestClient(build_api_app(lambda: ChatSession(_Refuses()), workspace=ws, settings=settings))


def _frames(text: str) -> dict[str, dict[str, Any]]:
    """The last payload of each event in an SSE body."""
    event, out = "", {}
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            out[event] = json.loads(line[len("data: ") :])
    return out


def test_a_refused_turn_says_it_was_refused_and_by_what(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)

    error = _frames(client.post("/api/code/turn", json={"message": "refuse this"}).text)["error"]

    assert error["reason"] == "content_policy"
    assert error["model"] == "openrouter/openai/gpt-4o"
    assert error["provider"] == "OpenAI"
    # It used to read "the coding turn failed", which is what a bug in this repository reads as.
    assert error["message"] != "the coding turn failed"
    assert "content policy" in error["message"] and "openrouter/openai/gpt-4o" in error["message"]


def test_any_other_failure_carries_no_policy_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)

    error = _frames(client.post("/api/code/turn", json={"message": "crash now"}).text)["error"]

    # No reason means no "Try with another model" button: an outage is not a refusal.
    assert "reason" not in error
    assert error["message"] == "the coding turn failed"


def test_the_owners_retry_is_written_on_the_receipt_and_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    body = {
        "message": "answer it",
        "model": "prov/other",
        "retry_of": {"blocked_model": "openrouter/openai/gpt-4o", "request_id": "req_9f2c41"},
    }

    frames = _frames(client.post("/api/code/turn", json=body).text)

    retry = frames["done"]["policy_retry"]
    assert retry == {"blocked_model": "openrouter/openai/gpt-4o", "request_id": "req_9f2c41"}
    # Kept: a reopened conversation still says the answer is from the model the owner chose.
    sid = frames["session"]["session_id"]
    stored = client.get(f"/api/code/sessions/{sid}").json()["exchanges"][-1]["done"]
    assert stored["policy_retry"] == retry


def test_a_turn_that_is_no_retry_carries_no_retry_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)

    done = _frames(client.post("/api/code/turn", json={"message": "answer it"}).text)["done"]

    assert "policy_retry" not in done


def test_a_retry_naming_no_model_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/api/code/turn", json={"message": "answer it", "retry_of": {"blocked_model": ""}}
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "extra",
    [{"fuse": True}, {"provider": "claude"}],
    ids=["fused", "external"],
)
def test_a_retry_that_would_not_run_on_its_model_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: dict[str, Any]
) -> None:
    # A fused turn answers with its panel and judge whatever `model` says, and an external agent
    # picks its own model: the receipt would read "redone on Y by the owner's choice" over an
    # answer Y never wrote. Before, the desktop sent `fuse` with the retry and this was a 200.
    client = _client(tmp_path, monkeypatch)
    body = {
        "message": "answer it",
        "model": "prov/other",
        "retry_of": {"blocked_model": "openrouter/openai/gpt-4o"},
        **extra,
    }

    response = client.post("/api/code/turn", json=body)

    assert response.status_code == 422


def test_the_opening_frame_says_how_many_files_the_turn_carried_and_not_which(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A screen that FOLLOWS a turn (it came back mid-turn, or another window sent it) builds the
    # row from this frame alone. Without the count, a refusal's retry from there went out with no
    # files and nothing said so: the new model answered without the document the turn was about.
    client = _client(tmp_path, monkeypatch)
    ids = ["a" * 32, "b" * 32]

    frames = _frames(
        client.post("/api/code/turn", json={"message": "refuse this", "attachments": ids}).text
    )

    bus = client.app.state.session_bus  # type: ignore[attr-defined]  # set by build_api_app
    started = [f for f in bus.replay(frames["session"]["session_id"]) if f["event"] == "turn_started"]
    assert started[-1]["payload"]["attachment_count"] == 2
    # The ids themselves stay off the bus: guests read it too.
    assert not any(i in json.dumps(started[-1]["payload"]) for i in ids)


# --- the bot: only the sentence ------------------------------------------------------------------


class _RefusingSession:
    max_turns = None

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def send(self, message: str) -> str:
        raise self.exc


def _gateway(exc: Exception, *, chat: bool) -> Any:
    from chimera.server.gateway import MessageGateway

    return MessageGateway(lambda: _RefusingSession(exc), warnings_in_reply=chat)  # type: ignore[arg-type,return-value]  # a two-method fake session


def test_a_chat_bot_answers_a_refusal_with_one_sentence() -> None:
    from chimera.server.gateway import InboundMessage

    exc = _router_refusal()
    mark_model(exc, "openrouter/openai/gpt-4o")

    reply = _gateway(exc, chat=True).on_message(InboundMessage(text="x", platform="discord"))

    # The Discord adapter sends whatever comes back; before, nothing came back and nothing was sent.
    # The chat form: the app's sentence invited a model choice the bot cannot offer.
    assert reply == PolicyBlock("openrouter/openai/gpt-4o", "OpenAI", None).chat_sentence()


def test_the_chat_sentence_does_not_invite_a_choice_the_chat_cannot_offer() -> None:
    block = PolicyBlock("openrouter/openai/gpt-4o", "OpenAI", "req_9f2c41")

    chat = block.chat_sentence()

    # A chat has no model list, and the person writing may not be the owner whose call it is.
    assert "a choice for you to make" not in chat
    assert "owner" in chat and "app" in chat
    # Everything that identifies the refusal is still said.
    assert "openrouter/openai/gpt-4o" in chat and "OpenAI" in chat and "req_9f2c41" in chat
    assert "Nothing was retried" in chat
    # The app keeps its own sentence: there the card does offer the choice.
    assert "a choice for you to make" in block.sentence()


def test_the_http_route_and_other_failures_keep_their_exception() -> None:
    from chimera.server.gateway import InboundMessage

    # A program reads the HTTP route's reply as the answer; a refusal there stays an error.
    with pytest.raises(litellm.BadRequestError):
        _gateway(_router_refusal(), chat=False).on_message(InboundMessage(text="x"))
    # And on a chat platform, anything that is not a refusal is not dressed as one.
    with pytest.raises(RuntimeError):
        _gateway(RuntimeError("down"), chat=True).on_message(InboundMessage(text="x"))
