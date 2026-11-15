"""The routing table answers every role, and says where each answer came from.

The equivalence with the resolvers that decided before the table existed is pinned in
``test_routing_table_is_what_ran_before.py``. This file is about what the table ADDS: a route for
every role in the registry, an origin that tells the environment from the ``.env`` from a value
saved after startup, and the order in which a closer scope's override wins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera import config
from chimera.config import Settings
from chimera.providers import routing
from chimera.providers.routing import ROLE_IDS, ROLES, RoutingTable

_PINNED = "openrouter/test/pinned"


@pytest.fixture(autouse=True)
def _no_discovered_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    # `configured_providers` also reads provider-shaped variables out of os.environ; the answer must
    # not depend on the machine the test runs on.
    monkeypatch.setattr("chimera.providers.discovery.generic_providers", lambda environ=None: [])


def _settings(**kwargs: str) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


def test_every_role_in_the_registry_resolves() -> None:
    table = _settings().routing_table()
    assert isinstance(table, RoutingTable)
    assert tuple(table.routes) == ROLE_IDS
    assert [r.role for r in table.rows()] == list(ROLE_IDS)
    assert len(set(ROLE_IDS)) == len(ROLE_IDS)
    for spec in ROLES:
        route = table.route(spec.id)
        assert route.role == spec.id
        assert route.via, f"{spec.id} must say how it was reached"
        if spec.kind == "list":
            assert route.kind == "list"
            assert spec.id in table.lists


def test_an_unknown_role_is_an_error_that_lists_the_known_ones() -> None:
    with pytest.raises(KeyError, match=r"tier\.weak"):
        _settings().routing_table().route("nonsense")


def test_the_product_defaults_say_default() -> None:
    table = _settings().routing_table()
    assert table.route("default").origin == "default"
    assert table.route("tier.weak").origin == "default"
    assert "preset" in table.route("tier.weak").via
    assert table.ladder_source == "preset"
    assert table.tiers() == _settings().tier_ladder()


def test_a_variable_exported_before_startup_is_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_WEAK_MODEL", _PINNED)
    monkeypatch.setattr(config, "_STARTUP_ENV", frozenset({"CHIMERA_WEAK_MODEL"}))
    route = Settings(_env_file=None).routing_table().route("tier.weak")  # type: ignore[call-arg]
    assert (route.model, route.origin) == (_PINNED, "env")


def test_a_value_in_the_dotenv_file_is_config(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"CHIMERA_WEAK_MODEL={_PINNED}\n", encoding="utf-8")
    route = Settings(_env_file=env_file).routing_table().route("tier.weak")  # type: ignore[call-arg]
    assert (route.model, route.origin) == (_PINNED, "config")


def test_a_value_saved_after_startup_is_config_not_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # `patch_config` writes os.environ too, so the saved name IS in the environment now; what tells
    # it from an export is that it was not there when the process started.
    monkeypatch.setenv("CHIMERA_WEAK_MODEL", _PINNED)
    monkeypatch.setattr(config, "_STARTUP_ENV", frozenset())
    route = Settings(_env_file=None).routing_table().route("tier.weak")  # type: ignore[call-arg]
    assert (route.model, route.origin) == (_PINNED, "config")


def test_origin_is_per_role_not_per_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_STARTUP_ENV", frozenset({"CHIMERA_DEFAULT_MODEL"}))
    table = _settings(
        CHIMERA_DEFAULT_MODEL=_PINNED, CHIMERA_VOICE_MODEL="openrouter/test/voice"
    ).routing_table()
    assert table.route("default").origin == "env"
    assert table.route("voice").origin == "config"
    assert table.route("tier.mid").origin == "default"
    # Roles with no model of their own follow the conversation, and say whom.
    assert table.route("chat").model == _PINNED
    assert table.route("chat").inherits == "default"


def test_a_role_without_a_model_of_its_own_says_so() -> None:
    table = _settings().routing_table()
    for role in ("chat", "code.plan", "subagent", "cron", "design"):
        assert table.route(role).inherits == "default"
    assert table.route("orchestrator").inherits == "tier.top"
    assert table.route("worker").inherits == "tier.mid"
    assert table.route("voice").inherits == "conversation"
    assert table.route("voice").model == table.route("default").model
    assert table.route("review").inherits == "reviewer_for"


def test_the_effort_suffix_is_split_and_the_slug_round_trips() -> None:
    table = _settings(CHIMERA_DEFAULT_MODEL="openrouter/test/m#high").routing_table()
    route = table.route("default")
    assert (route.model, route.effort) == ("openrouter/test/m", "high")
    assert route.slug == "openrouter/test/m#high"
    # A '#' that is not a known effort is left alone.
    assert _settings(CHIMERA_DEFAULT_MODEL="x/y#fast").routing_table().route("default").slug == "x/y#fast"


def test_a_closer_scope_wins_in_the_documented_order() -> None:
    table = _settings().routing_table()
    base = table.route("code.edit")
    assert table.route("code.edit", agent="a/model").origin == "agent"
    assert table.route("code.edit", job="j/model", agent="a/model").model == "j/model"
    chosen = table.route("code.edit", request="r/model", job="j/model", agent="a/model")
    assert (chosen.model, chosen.origin) == ("r/model", "request")
    assert table.route("code.edit", request="  ").slug == base.slug  # blank means no override
    assert routing.PRECEDENCE == ("request", "job", "agent", "env", "config", "default")


def test_model_and_effort_are_chosen_independently() -> None:
    table = _settings(CHIMERA_DEFAULT_MODEL="openrouter/test/m#low").routing_table()
    only_model = table.route("default", agent="a/model")
    assert (only_model.model, only_model.effort) == ("a/model", "low")
    assert only_model.origin == "agent"
    both = table.route("default", job="j/model#high")
    assert (both.model, both.effort, both.origin, both.effort_origin) == ("j/model", "high", "job", "job")


def test_the_list_roles_spell_out_their_members() -> None:
    table = _settings(
        CHIMERA_FUSION_PANEL="openrouter/a/one,openrouter/b/two#high",
        CHIMERA_FALLBACK_MODELS="openrouter/c/three",
    ).routing_table()
    panel = table.lists["fusion.panel"]
    assert [r.slug for r in panel] == ["openrouter/a/one", "openrouter/b/two#high"]
    assert panel[1].effort == "high"
    assert table.route("fusion.panel").model == "openrouter/a/one,openrouter/b/two#high"
    assert [r.slug for r in table.lists["fallback"]] == ["openrouter/c/three"]
    assert table.escalation_rungs() == [r.model for r in table.lists["escalation"]]


def test_the_reviewer_the_table_names_is_the_one_reviewer_for_picks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chimera.review.reviewer_for import reviewer_for

    settings = _settings(CHIMERA_REVIEW_MODEL="openrouter/test/reviewer")
    monkeypatch.setattr("chimera.config.get_settings", lambda: settings)
    route = settings.routing_table().route("review")
    assert route.slug == reviewer_for("openrouter/test/author").model


def test_a_profile_is_what_the_consumer_uses_not_the_intermediate_step() -> None:
    from chimera.api.roles import resolve, review_model_for

    settings = _settings(OPENROUTER_API_KEY="k", CHIMERA_DEFAULT_MODEL="openrouter/test/run-model")
    table = settings.routing_table()
    default = table.route("default").slug
    for profile in ("economy", "balanced", "max"):
        plan = resolve(profile, settings)  # type: ignore[arg-type]
        got = table.profile_routes(profile)
        expected = {
            "explore": plan.models.explore, "plan": plan.models.plan, "edit": plan.models.edit,
            "review": review_model_for(plan),
        }
        for name, slug in expected.items():
            assert got[f"code.{name}"].slug == (slug or default)
    # Under `economy` the reviewer would be the editor, so the run's own model reviews.
    economy = resolve("economy", settings)  # type: ignore[arg-type]
    assert economy.models.review == economy.models.edit and economy.models.review != default
    review = table.profile_routes("economy")["code.review"]
    assert (review.slug, review.inherits) == (default, "default")


def test_an_empty_fusion_panel_is_a_panel_not_an_error() -> None:
    table = _settings(CHIMERA_FUSION_PANEL="").routing_table()
    assert table.lists["fusion.panel"] == ()
    panel = table.route("fusion.panel")
    assert (panel.model, panel.origin) == ("", "config")
    assert table.route("default").slug  # every other role still answers


def test_a_role_the_process_is_not_using_never_hides_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("not resolvable")

    monkeypatch.setattr("chimera.fusion.factory.fusion_config", boom)
    monkeypatch.setattr("chimera.fusion.verified.escalation_model", boom)
    monkeypatch.setattr("chimera.decisions.factory.default_model_for", boom)
    table = _settings().routing_table()
    for role in ("fusion.panel", "fusion.judge", "fusion.synthesizer", "verify_escalation", "decisions"):
        assert table.route(role).via == "unavailable: RuntimeError"
    assert table.route("default").slug
    assert tuple(table.routes) == ROLE_IDS
