"""A credential in a decision's state never reaches a hosted backend and never lands in the log.

Study 27, phase 3. Mapping the phase found the gap: nothing in `chimera/decisions/` used `redact`, the
decision log wrote the first 500 characters of the state raw, and the hosted backends sent it whole. A
governance decision is asked about a shell command, and a shell command is exactly where a bearer token
sits. The net is `chimera.core.redact`, the one that already keeps secrets out of the trace.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from chimera.core.redact import redact
from chimera.decisions.contract import Choice
from chimera.decisions.hosted import HostedVerbalizedBackend
from chimera.decisions.log import DecisionLog, state_hash
from chimera.decisions.openrouter import OpenRouterDecisionsBackend

TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"  # a GitHub-shaped token
ENV_SECRET = "hunter2-but-much-longer-9f8e7d"
QUESTION = Choice(key="danger", options=("yes", "no"), instructions="Is this dangerous?")


@pytest.fixture(autouse=True)
def _known_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPLOY_API_TOKEN", ENV_SECRET)


def dirty() -> str:
    return f"curl -H 'Authorization: Bearer {ENV_SECRET}' https://api.example/x?api_key=zzz && echo {TOKEN}"


def test_the_net_actually_catches_what_these_tests_plant() -> None:
    cleaned = redact(dirty())

    assert ENV_SECRET not in cleaned and TOKEN not in cleaned and "zzz" not in cleaned


# --- the hosted gateway backend ---------------------------------------------------------------


class _Result:
    content = '{"danger": "yes"}'
    model = "vendor/x"
    prompt_tokens = 10
    completion_tokens = 5
    cache_read_tokens = 0
    cache_write_tokens = 0
    answer_in_reasoning = False
    reasoning = ""
    tool_calls: list[Any] = []


class _Gateway:
    def __init__(self) -> None:
        self.sent: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]], **kw: Any) -> _Result:
        self.sent.append(messages)
        return _Result()


def test_a_hosted_gateway_never_sees_the_secrets_in_the_state() -> None:
    gateway = _Gateway()

    HostedVerbalizedBackend(gateway, "vendor/x").ask(dirty(), QUESTION)

    wire = json.dumps(gateway.sent)
    assert ENV_SECRET not in wire and TOKEN not in wire and "zzz" not in wire
    assert "curl" in wire and "[redacted]" in wire  # the command survives, with the secrets masked


def test_a_state_with_nothing_to_hide_reaches_the_hosted_backend_unchanged() -> None:
    gateway = _Gateway()
    state = "rm -rf ./build && git status --short"

    HostedVerbalizedBackend(gateway, "vendor/x").ask(state, QUESTION)

    assert [m["content"] for m in gateway.sent[0] if m["role"] == "user"] == [state]


# --- the OpenRouter decisions backend ---------------------------------------------------------


def test_the_openrouter_request_body_never_carries_the_secrets() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200, json={"answers": {"danger": {"choice": "yes", "probabilities": {"yes": 0.9, "no": 0.1}}}}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = OpenRouterDecisionsBackend("sk-or-test", "typesafe/jev-1.13", client=client)

    try:
        backend.ask(dirty(), QUESTION)
    except ValueError:
        pass  # the reading of the canned reply is not what this test is about

    wire = json.dumps(bodies)
    assert bodies and ENV_SECRET not in wire and TOKEN not in wire and "zzz" not in wire


# --- the log ----------------------------------------------------------------------------------


def test_the_decision_log_never_writes_the_secrets(tmp_path: Path) -> None:
    log = DecisionLog.for_home(tmp_path)

    log.answer({"decision": "d", "backend": "b", "model": "m"}, dirty(), raw_p=0.5)

    text = log.path.read_text(encoding="utf-8")
    assert ENV_SECRET not in text and TOKEN not in text and "zzz" not in text and "[redacted]" in text


def test_a_token_straddling_the_cap_is_masked_not_left_as_a_fragment(tmp_path: Path) -> None:
    log = DecisionLog.for_home(tmp_path)
    state = "x" * 470 + " " + TOKEN  # the cap falls inside the token

    log.answer({"decision": "d"}, state, raw_p=0.5)

    line = json.loads(log.path.read_text(encoding="utf-8").splitlines()[-1])
    assert TOKEN[:8] not in line["state"] and "[redacted]" in line["state"]


def test_the_state_hash_is_still_the_hash_of_what_was_decided_on(tmp_path: Path) -> None:
    log = DecisionLog.for_home(tmp_path)

    log.answer({"decision": "d"}, dirty(), raw_p=0.5)

    line = json.loads(log.path.read_text(encoding="utf-8").splitlines()[-1])
    assert line["state_hash"] == state_hash(dirty())  # a refit joins rows on this, so it must not move
