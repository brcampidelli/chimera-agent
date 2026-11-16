"""Reasoning effort per role (P1-10 slice c): ``CHIMERA_ROLE_EFFORTS``, and ``effort`` in an agent file.

Pinned here:

* with the setting empty nothing moves: the committed routing snapshot is the proof, this file adds
  the cheap direct checks;
* an entry reaches the table with the right origin, reaches the consumers that read it (a profile's
  roles, the fusion cast), and loses to a ``#effort`` the slug already carries — with a note;
* a malformed entry is a note, never an exception, including out of ``get_settings()``;
* an agent file that declares ``effort`` gets ``<model>#effort`` on its own calls, and ONLY then.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera import config
from chimera.api.roles import resolve as resolve_roles
from chimera.config import Settings
from chimera.core.escalation import ladder_of
from chimera.core.markdown_agents import discover_agents
from chimera.core.subagent import SubAgentTool
from chimera.fusion.factory import fusion_config
from chimera.providers import CompletionResult
from chimera.providers.catalog import CATALOG
from chimera.providers.routing import EFFORT_ROLES, parse_role_efforts, with_role_effort
from chimera.tools import EchoTool, ToolRegistry

MODELS = {
    "CHIMERA_WEAK_MODEL": "openrouter/t/weak",
    "CHIMERA_MID_MODEL": "openrouter/t/mid",
    "CHIMERA_ORCHESTRATOR_MODEL": "openrouter/t/top",
    "CHIMERA_DEFAULT_MODEL": "openrouter/t/mid",
}


def _settings(tmp_path: Path, **env: str) -> Settings:
    return Settings(_env_file=None, CHIMERA_HOME=str(tmp_path), **{**MODELS, **env})  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _no_startup_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_STARTUP_ENV", frozenset())
    monkeypatch.delenv("CHIMERA_ROLE_EFFORTS", raising=False)


# --- the setting -------------------------------------------------------------------------------


def test_the_setting_is_empty_by_default_and_the_table_has_nothing_to_say(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    table = settings.routing_table()

    assert settings.role_efforts == ""
    assert table.notes == ()
    assert all(route.effort is None for route in table.routes.values())


def test_an_entry_shows_its_effort_and_where_it_came_from(tmp_path: Path) -> None:
    settings = _settings(tmp_path, CHIMERA_ROLE_EFFORTS="code.plan=high, code.explore=LOW")
    table = settings.routing_table()

    plan, explore, edit = table.route("code.plan"), table.route("code.explore"), table.route("code.edit")
    assert (plan.effort, plan.effort_origin, plan.slug) == ("high", "config", "openrouter/t/mid#high")
    assert (explore.effort, explore.effort_origin) == ("low", "config")
    assert edit.effort is None and edit.effort_origin == "default"
    assert "CHIMERA_ROLE_EFFORTS" in plan.via
    assert table.notes == ()
    # The model did not move: only the effort did.
    assert plan.model == table.route("default").model and plan.origin == "default"


def test_the_origin_is_env_when_the_variable_was_in_the_starting_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "_STARTUP_ENV", frozenset({"CHIMERA_ROLE_EFFORTS"}))
    table = _settings(tmp_path, CHIMERA_ROLE_EFFORTS="code.plan=high").routing_table()

    assert table.route("code.plan").effort_origin == "env"


def test_a_slug_that_already_carries_an_effort_wins_and_the_table_says_so(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        CHIMERA_FUSION_JUDGE="openrouter/t/judge#low",
        CHIMERA_FUSION_PANEL="openrouter/t/one,openrouter/t/two#low",
        CHIMERA_ROLE_EFFORTS="fusion.judge=high,fusion.panel=high",
    )
    table = settings.routing_table()

    judge = table.route("fusion.judge")
    assert (judge.effort, judge.slug) == ("low", "openrouter/t/judge#low")
    members = {m.model: (m.effort, m.effort_origin) for m in table.lists["fusion.panel"]}
    assert members["openrouter/t/one"] == ("high", "config")
    assert members["openrouter/t/two"][0] == "low"
    assert any("fusion.judge=high not applied" in note and "#low" in note for note in table.notes)
    assert table.route("fusion.panel").model == "openrouter/t/one#high,openrouter/t/two#low"


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ("code.plan", "not role=effort"),
        ("nope=high", "not a role the table knows"),
        ("code.plan=extreme", "not low, medium or high"),
        ("code.plan=", "not low, medium or high"),
        ("default=high", "does not take a role effort yet"),
        ("tier.top=low", "does not take a role effort yet"),
        ("code.plan=low,code.plan=high", "set twice"),
    ],
)
def test_a_bad_entry_is_a_note_never_an_exception(tmp_path: Path, raw: str, fragment: str) -> None:
    settings = _settings(tmp_path, CHIMERA_ROLE_EFFORTS=raw)

    table = settings.routing_table()  # must not raise

    assert any(fragment in note for note in table.notes), table.notes


def test_the_good_entries_around_a_bad_one_still_count(tmp_path: Path) -> None:
    table = _settings(tmp_path, CHIMERA_ROLE_EFFORTS="oops, code.plan=high,,code.edit=sideways").routing_table()

    assert table.route("code.plan").effort == "high"
    assert table.route("code.edit").effort is None
    assert len(table.notes) == 2


def test_get_settings_never_raises_over_a_malformed_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIMERA_ROLE_EFFORTS", "===,,;;;code.plan=")
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
        assert settings.routing_table().notes
    finally:
        config.get_settings.cache_clear()


def test_parse_is_total_over_any_input() -> None:
    for raw in (None, "", "   ", ",", "=", "a=b=c", 42, ["code.plan=high"]):
        efforts, notes = parse_role_efforts(raw)
        assert isinstance(efforts, dict) and isinstance(notes, tuple)
    assert set(EFFORT_ROLES) == {
        "code.explore", "code.plan", "code.edit", "code.review",
        "fusion.panel", "fusion.judge", "fusion.synthesizer",
    }


def test_with_role_effort_returns_the_slug_untouched_when_nothing_applies(tmp_path: Path) -> None:
    settings = _settings(tmp_path, CHIMERA_ROLE_EFFORTS="code.plan=high")

    assert with_role_effort(settings, "code.edit", "a/b") == "a/b"
    assert with_role_effort(settings, "code.plan", "a/b#low") == "a/b#low"
    assert with_role_effort(settings, "code.plan", "a/b") == "a/b#high"
    assert with_role_effort(settings, "code.plan", None) is None
    assert with_role_effort(settings, "code.plan", "") == ""


# --- the consumers -----------------------------------------------------------------------------


def test_a_profile_carries_the_role_effort_to_the_roles_the_run_reads(tmp_path: Path) -> None:
    plain = resolve_roles("balanced", _settings(tmp_path)).models
    settings = _settings(tmp_path, CHIMERA_ROLE_EFFORTS="code.plan=high,code.explore=low")
    models = resolve_roles("balanced", settings).models

    assert models.plan == f"{plain.plan}#high"
    assert models.explore == f"{plain.explore}#low"
    assert (models.edit, models.review) == (plain.edit, plain.review)
    routes = settings.routing_table().profile_routes("balanced")
    assert (routes["code.plan"].effort, routes["code.plan"].effort_origin) == ("high", "config")
    assert routes["code.edit"].effort is None


def test_an_effort_on_the_rung_is_not_attributed_to_the_setting(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, CHIMERA_ORCHESTRATOR_MODEL="openrouter/t/top#low", CHIMERA_ROLE_EFFORTS="code.plan=high"
    )

    assert resolve_roles("balanced", settings).models.plan == "openrouter/t/top#low"
    plan = settings.routing_table().profile_routes("balanced")["code.plan"]
    assert (plan.effort, plan.effort_origin) == ("low", "default")


def test_a_reviewer_that_is_the_editor_stays_withheld_whatever_code_review_says(tmp_path: Path) -> None:
    from chimera.api.roles import review_model_for

    settings = _settings(tmp_path, CHIMERA_ROLE_EFFORTS="code.review=high")
    plan = resolve_roles("economy", settings)  # economy: review and edit are both the mid tier

    assert plan.models.review == plan.models.edit
    assert review_model_for(plan) is None


def test_an_explicit_override_is_applied_after_and_in_full(tmp_path: Path) -> None:
    from chimera.api.roles import RoleModels

    settings = _settings(tmp_path, CHIMERA_ROLE_EFFORTS="code.plan=high")
    plan = resolve_roles("balanced", settings, RoleModels(plan="vendor/custom"))

    assert plan.models.plan == "vendor/custom"


def test_the_fusion_cast_gets_the_efforts_of_its_roles(tmp_path: Path) -> None:
    plain = fusion_config(_settings(tmp_path))
    cast = fusion_config(_settings(tmp_path, CHIMERA_ROLE_EFFORTS="fusion.judge=high,fusion.panel=low"))

    assert cast.judge == f"{plain.judge}#high"
    assert cast.synthesizer == plain.synthesizer
    assert cast.panel == [f"{m}#low" for m in plain.panel]


def test_escalation_rungs_stay_plain_ids(tmp_path: Path) -> None:
    plain = ladder_of(_settings(tmp_path))
    settings = _settings(tmp_path, CHIMERA_ROLE_EFFORTS="code.plan=high,fusion.judge=high")

    assert ladder_of(settings) == plain == settings.routing_table().escalation_rungs()


# --- agent files -------------------------------------------------------------------------------


class _Backend:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.kwargs = kwargs
        return CompletionResult(content="finished", model="test")


def _run(front: str, tmp_path: Path, *, directory: str = "owner/agents", filename: str = "reviewer.md") -> str:
    folder = tmp_path / directory
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_text(f"---\n{front}\n---\nReview the change.\n", encoding="utf-8")
    catalog = discover_agents(tmp_path / "project", tmp_path / "owner", owner_claude=tmp_path / "claude")
    (agent,) = catalog.agents
    backend = _Backend()
    registry = ToolRegistry()
    registry.register(EchoTool())
    SubAgentTool(
        backend, registry, model="session/model", max_turns=3, markdown_agents={agent.id: agent}
    ).run(task="work", agent=agent.id)
    return str(backend.kwargs.get("model"))


NATIVE = "description: A reviewer\ntools:\n- echo\nsteps: 3\n"
CLAUDE = "name: reviewer\ndescription: A reviewer\ntools: Read"


def test_a_native_agent_with_effort_runs_on_the_session_model_at_that_effort(tmp_path: Path) -> None:
    assert _run(NATIVE + "effort: high", tmp_path) == "session/model#high"


def test_a_native_agent_with_a_pinned_model_and_effort_gets_both(tmp_path: Path) -> None:
    pinned = CATALOG[0].slug
    assert _run(f"{NATIVE}model: {pinned}\neffort: low", tmp_path) == f"{pinned}#low"


def test_a_claude_agent_with_effort_runs_at_that_effort(tmp_path: Path) -> None:
    assert _run(CLAUDE + "\neffort: high", tmp_path, directory="claude/agents") == "session/model#high"


def test_an_agent_that_declares_no_effort_gets_the_slug_it_always_got(tmp_path: Path) -> None:
    assert _run(NATIVE, tmp_path) == "session/model"
    assert _run(CLAUDE, tmp_path / "again", directory="claude/agents") == "session/model"


def _run_with(
    tmp_path: Path, front: str, *, session: str | None, directory: str = "owner/agents",
    trust: bool | None = None,
) -> Any:
    folder = tmp_path / directory
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a.md").write_text(f"---\n{front}\n---\nGo.\n", encoding="utf-8")
    (agent,) = discover_agents(tmp_path / "project", tmp_path / "owner", owner_claude=tmp_path / "claude").agents
    backend = _Backend()
    registry = ToolRegistry()
    registry.register(EchoTool())
    SubAgentTool(
        backend, registry, model=session, max_turns=3, markdown_agents={agent.id: agent}
    ).run(task="work", agent=agent.id)
    return backend.kwargs.get("model")


def test_the_owners_own_effort_on_the_model_wins_over_the_agents(tmp_path: Path) -> None:
    assert _run_with(tmp_path, NATIVE + "effort: low", session="session/model#high") == "session/model#high"


def test_an_agent_with_effort_and_no_model_anywhere_uses_the_default_not_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIMERA_DEFAULT_MODEL", "openrouter/t/dflt")
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    config.get_settings.cache_clear()
    try:
        model = _run_with(tmp_path, NATIVE + "effort: high", session=None)
    finally:
        config.get_settings.cache_clear()

    assert model == "openrouter/t/dflt#high"


def test_with_nothing_resolvable_no_effort_is_appended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Empty:
        def route(self, _role: str) -> Any:
            return type("R", (), {"slug": ""})()

    class _S:
        def routing_table(self) -> _Empty:
            return _Empty()

    monkeypatch.setattr("chimera.core.subagent.get_settings", lambda: _S())

    assert _run_with(tmp_path, NATIVE + "effort: high", session=None) is None


def test_a_repository_agent_cannot_raise_the_owners_effort(tmp_path: Path) -> None:
    claude = "name: a\ndescription: d\ntools: Read\neffort: medium"
    got = _run_with(tmp_path, claude, session="session/model#low", directory="project/.claude/agents")

    assert got == "session/model#low"


def test_a_repository_agent_is_ignored_for_effort_with_the_workspace_untrusted(tmp_path: Path) -> None:
    from chimera.core.agent import AgentConfig

    assert AgentConfig(trust_workspace=False).trust_workspace is False
    claude = "name: a\ndescription: d\ntools: Read\neffort: high"
    folder = tmp_path / "project" / ".claude" / "agents"
    folder.mkdir(parents=True)
    (folder / "a.md").write_text(f"---\n{claude}\n---\nGo.\n", encoding="utf-8")
    catalog = discover_agents(tmp_path / "project", tmp_path / "owner", owner_claude=tmp_path / "claude")

    (agent,) = catalog.agents
    assert agent.effort == "" and agent.from_project
    assert any("effort is chosen by the owner" in i.message for i in catalog.issues)
    assert _run_with(tmp_path / "x", claude, session="session/model", directory="project/.claude/agents") == "session/model"


def test_an_invalid_claude_effort_is_ignored_with_a_note_not_rounded_to_high(tmp_path: Path) -> None:
    folder = tmp_path / "claude" / "agents"
    folder.mkdir(parents=True)
    (folder / "x.md").write_text("---\n" + CLAUDE + "\neffort: max\n---\nGo.\n", encoding="utf-8")
    catalog = discover_agents(tmp_path / "project", tmp_path / "owner", owner_claude=tmp_path / "claude")

    (agent,) = catalog.agents
    assert agent.effort == ""
    assert any("effort 'max' is not low, medium or high" in issue.message for issue in catalog.issues)
    assert not any("recognised but not applied" in issue.message and "effort" in issue.message for issue in catalog.issues)


def test_a_native_agent_with_an_invalid_effort_is_skipped_with_the_reason(tmp_path: Path) -> None:
    folder = tmp_path / "owner" / "agents"
    folder.mkdir(parents=True)
    (folder / "x.md").write_text(f"---\n{NATIVE}effort: max\n---\nGo.\n", encoding="utf-8")

    catalog = discover_agents(tmp_path / "project", tmp_path / "owner")

    assert catalog.agents == ()
    assert any("effort" in issue.message for issue in catalog.issues)


def test_a_native_project_agent_cannot_choose_the_effort(tmp_path: Path) -> None:
    folder = tmp_path / "project" / ".chimera" / "agents"
    folder.mkdir(parents=True)
    (folder / "p.md").write_text(f"---\n{NATIVE}effort: high\n---\nGo.\n", encoding="utf-8")

    catalog = discover_agents(tmp_path / "project", tmp_path / "owner")

    (agent,) = catalog.agents
    assert agent.effort == ""
    assert any("effort is chosen by the owner" in issue.message for issue in catalog.issues)


def test_editing_the_effort_in_a_file_is_seen_on_the_next_listing(tmp_path: Path) -> None:
    import os

    folder = tmp_path / "owner" / "agents"
    folder.mkdir(parents=True)
    path = folder / "a.md"
    path.write_text(f"---\n{NATIVE}effort: low\n---\nGo.\n", encoding="utf-8")
    old = os.stat(path).st_mtime_ns - 10_000_000_000  # old enough to be cached
    os.utime(path, ns=(old, old))
    first = discover_agents(tmp_path / "project", tmp_path / "owner").agents[0]
    path.write_text(f"---\n{NATIVE}effort: high\n---\nGo.\n", encoding="utf-8")
    os.utime(path, ns=(old + 1, old + 1))

    second = discover_agents(tmp_path / "project", tmp_path / "owner").agents[0]

    assert (first.effort, second.effort) == ("low", "high")
