"""Designing a subagent from a sentence.

`chimera meta` has been able to do this for a long time and printed the blueprint into a table —
designed, shown, and thrown away every single time. Nothing kept it, so nobody could use it.

The proposal now lands in the registry form, which already edits an agent. Reviewing a design and
editing an agent are the same act, so there is no second surface to keep in step with the first.

**Why the review is not a formality.** Measured on three real descriptions through the real model,
an agent asked only to *say what is weak about a marketing text* was designed holding `edit_file` —
a tool that rewrites files. Over-granting is the tendency, and the person reading the list is the
one who knows the agent was never meant to touch anything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.providers import CompletionResult

BLUEPRINT = json.dumps(
    {
        "name": "revisor de textos",
        "role_prompt": "You review marketing copy and say what is weak.",
        "tools": ["read_document", "edit_file"],
    }
)


class _Canned:
    reply = BLUEPRINT
    calls = 0

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> CompletionResult:
        type(self).calls += 1
        return CompletionResult(
            content=type(self).reply, model="fake", prompt_tokens=1, completion_tokens=1
        )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from chimera.api.app import build_api_app
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    _Canned.reply = BLUEPRINT
    _Canned.calls = 0
    monkeypatch.setattr("chimera.providers.LLMGateway", _Canned)
    return TestClient(
        build_api_app(  # type: ignore[arg-type]
            lambda: None, settings=Settings(CHIMERA_HOME=str(tmp_path / "home"))
        )
    )


def test_a_sentence_comes_back_as_an_agent(client: TestClient) -> None:
    body = client.post(
        "/api/agents/design", json={"description": "um agente que revisa meus textos"}
    ).json()

    assert body["note"] == ""
    assert body["name"] == "revisor de textos"
    assert body["instructions"].startswith("You review")
    assert body["allowed_tools"] == ["read_document", "edit_file"]


def test_the_id_is_a_slug_the_registry_will_accept(client: TestClient) -> None:
    """The id names a Kanban lane and shows up in logs. Handing back a display name would make the
    form's first field wrong by default."""
    body = client.post("/api/agents/design", json={"description": "x"}).json()
    assert body["id"] == "revisor-de-textos"


def test_designing_saves_nothing(client: TestClient) -> None:
    """A design is a proposal. Writing it into the registry on arrival would make the review a
    formality performed after the fact."""
    client.post("/api/agents/design", json={"description": "x"})

    assert client.get("/api/agents/registry").json() == []


def test_it_costs_exactly_one_model_call(client: TestClient) -> None:
    client.post("/api/agents/design", json={"description": "x"})
    assert _Canned.calls == 1


def test_an_empty_description_never_reaches_the_model(client: TestClient) -> None:
    body = client.post("/api/agents/design", json={"description": "   "}).json()

    assert body["note"] and body["name"] == ""
    assert _Canned.calls == 0


def test_an_empty_tool_list_is_reported_as_the_grant_it_is(client: TestClient) -> None:
    """The inversion that once let a subagent run the full loop outside its owner's denylist:
    `AgentDef.allowed_tools` reads empty as NO RESTRICTION — the opposite of `Role.allowed_tools`.
    A design that named nothing must not arrive looking like a locked-down agent."""
    _Canned.reply = json.dumps({"name": "x", "role_prompt": "y", "tools": []})
    body = client.post("/api/agents/design", json={"description": "x"}).json()

    assert body["allowed_tools"] == []
    assert "every tool" in body["note"]


def test_a_tool_the_deployment_does_not_have_is_dropped(client: TestClient) -> None:
    """`MetaAgent.design` filters against the real registry. Pinned here because the alternative is
    an agent saved with a tool name nothing answers to — which fails at dispatch, long after the
    person who approved it has stopped looking."""
    _Canned.reply = json.dumps(
        {"name": "x", "role_prompt": "y", "tools": ["read_file", "launch_missiles"]}
    )
    body = client.post("/api/agents/design", json={"description": "x"}).json()

    assert body["allowed_tools"] == ["read_file"]


def test_an_unparseable_answer_is_a_sentence_not_a_500(client: TestClient) -> None:
    _Canned.reply = "desculpa, nao entendi"
    r = client.post("/api/agents/design", json={"description": "x"})

    assert r.status_code == 200
    assert r.json()["note"] and r.json()["name"] == ""


def test_a_broken_provider_is_a_sentence_too(client: TestClient, monkeypatch) -> None:
    class _Broken:
        def complete(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("provider down")

    monkeypatch.setattr("chimera.providers.LLMGateway", _Broken)
    r = client.post("/api/agents/design", json={"description": "x"})

    assert r.status_code == 200 and r.json()["note"]


def test_the_designed_agent_can_be_saved_as_it_stands(client: TestClient) -> None:
    """The end of the road, and the only test proving the two routes are one flow rather than two
    endpoints that happen to exist."""
    design = client.post("/api/agents/design", json={"description": "x"}).json()

    saved = client.put(
        "/api/agents/registry",
        json={
            "id": design["id"],
            "name": design["name"],
            "instructions": design["instructions"],
            "model": "",
            "allowed_tools": design["allowed_tools"],
        },
    )

    assert saved.status_code == 200
    assert [a["id"] for a in saved.json()] == ["revisor-de-textos"]
