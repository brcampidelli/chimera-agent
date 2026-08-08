"""The agents you dispatch work to, persisted.

Everything needed to define one was already written twice — `Role` and `AgentBlueprint` — and
neither was stored, so an agent existed only for the length of the call that built it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from chimera.core.registry import AgentDef, as_role, get, load, remove, save, upsert


def test_an_absent_registry_is_empty_not_an_error(tmp_path: Path) -> None:
    assert load(tmp_path) == []
    (tmp_path / "agents.json").write_text("{ not json", encoding="utf-8")
    assert load(tmp_path) == []
    (tmp_path / "agents.json").write_text('{"not": "a list"}', encoding="utf-8")
    assert load(tmp_path) == []


def test_one_bad_entry_does_not_take_the_others_with_it(tmp_path: Path) -> None:
    """A registry that emptied itself over one malformed row would look exactly like data loss."""
    (tmp_path / "agents.json").write_text(
        json.dumps([{"id": "good"}, {"id": "NOT A SLUG"}, {"id": "also-good"}]),
        encoding="utf-8",
    )
    assert [a.id for a in load(tmp_path)] == ["good", "also-good"]


def test_ids_are_handles_not_prose() -> None:
    """The id names a Kanban lane and appears in logs, so it is a slug rather than free text."""
    assert AgentDef(id="  Reviewer  ").id == "reviewer"  # trimmed and lowered, not rejected
    for bad in ("has space", "-leading", "a" * 41, "", "Ünïcode"):
        with pytest.raises(ValidationError):
            AgentDef(id=bad)


def test_upsert_replaces_rather_than_merges(tmp_path: Path) -> None:
    """Keeping fields the caller omitted would make clearing a model impossible — and "I removed
    that and it came back" is the shape of bug nobody reports, because they assume they did it
    wrong."""
    upsert(tmp_path, AgentDef(id="reviewer", model="openrouter/x", allowed_tools=["read_file"]))
    upsert(tmp_path, AgentDef(id="reviewer", name="Reviewer"))

    stored = get(tmp_path, "reviewer")
    assert stored is not None
    assert stored.name == "Reviewer"
    assert stored.model == "" and stored.allowed_tools == []


def test_upsert_keeps_the_others_and_the_order(tmp_path: Path) -> None:
    upsert(tmp_path, AgentDef(id="a"))
    upsert(tmp_path, AgentDef(id="b"))
    upsert(tmp_path, AgentDef(id="a", name="First"))
    assert [x.id for x in load(tmp_path)] == ["b", "a"]  # replaced entry moves to the end
    assert get(tmp_path, "b") is not None


def test_removing_an_agent_says_nothing_about_its_work(tmp_path: Path) -> None:
    """Cards filed under its lane keep their lane and stop being dispatched, which is recoverable.
    Deleting the work is not, and nothing about "remove this agent" says "and throw away what it was
    asked to do"."""
    save(tmp_path, [AgentDef(id="a"), AgentDef(id="b")])
    assert [x.id for x in remove(tmp_path, "a")] == ["b"]
    assert get(tmp_path, "a") is None


def test_an_entry_is_never_nameless(tmp_path: Path) -> None:
    assert AgentDef(id="reviewer").label == "reviewer"
    assert AgentDef(id="reviewer", name="  Quinn  ").label == "Quinn"


def test_saving_trims_and_caps(tmp_path: Path) -> None:
    stored = save(
        tmp_path,
        [AgentDef(id="a", name="  N  ", instructions="x" * 5000, allowed_tools=["  read_file  ", " "])],
    )
    assert stored[0].name == "N"
    assert len(stored[0].instructions) == 4000
    assert stored[0].allowed_tools == ["read_file"]


def test_an_empty_allowlist_means_no_restriction_not_no_tools() -> None:
    """The one place the two conventions in this codebase are translated.

    Every list in this project's configuration reads empty as "no allowlist in force" — the env
    allowlist, the request seams. `Role` spells that `None`, and an empty LIST there is a fully
    locked worker. A registry that let the two meet in the middle would hand someone a toolless
    agent for leaving a field blank.
    """
    unrestricted = as_role(AgentDef(id="a"))
    assert unrestricted.allowed_tools is None

    restricted = as_role(AgentDef(id="b", allowed_tools=["read_file"]))
    assert restricted.allowed_tools == ["read_file"]


def test_the_role_carries_the_label_and_an_unpinned_model() -> None:
    role = as_role(AgentDef(id="reviewer", name="Quinn", instructions="be strict"))
    assert role.name == "Quinn"
    assert role.system_prompt == "be strict"
    # Empty means "inherit the ladder", and `Role` spells that None too.
    assert role.model is None


def test_the_registry_is_reachable_over_http(tmp_path: Path) -> None:
    """Asserted through the API, because a registry nothing can reach is a data structure."""
    from fastapi.testclient import TestClient

    from chimera.api.app import build_api_app
    from chimera.config import Settings

    client = TestClient(
        build_api_app(lambda: None, settings=Settings(CHIMERA_HOME=str(tmp_path)))  # type: ignore[arg-type]
    )

    assert client.get("/api/agents/registry").json() == []

    listed = client.put(
        "/api/agents/registry",
        json={"id": "reviewer", "name": "Quinn", "instructions": "be strict"},
    ).json()
    assert [a["id"] for a in listed] == ["reviewer"]
    assert listed[0]["allowed_tools"] == []  # absent means unrestricted, not locked

    # A handle that could not name a lane is refused with a reason, not a 500.
    bad = client.put("/api/agents/registry", json={"id": "not a slug"})
    assert bad.status_code == 400
    assert "slug" in bad.json()["detail"]

    assert client.delete("/api/agents/registry/reviewer").json() == []
