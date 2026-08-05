"""A model per role — and the two claims that would be lies if they were not checked.

The first is about fusion. `RoutedBackend.complete` sends any turn carrying tool schemas to a single
model, and `FusionEngine` ignores tool schemas outright, so in a coding loop — where every turn
carries tools — a "fuse the loop" switch would never fire and would report that it had. The tests
here pin fusion to the two turns that genuinely have no tools, planning and review.

The second is about the reviewer. Generate-and-verify collapses when the reviewer IS the model that
wrote the patch: it grades its own work and agrees with itself. That has to be prevented by the
code, not by whoever fills in the form.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.api.roles import RoleModels, resolve, review_model_for
from chimera.config import Settings


def _settings(**over: Any) -> Settings:
    return Settings(CHIMERA_HOME="/tmp/chimera-roles-test", **over)


def test_no_profile_and_no_overrides_means_no_role_routing() -> None:
    """Every caller that predates roles keeps running on one model, exactly as before."""
    plan = resolve(None, _settings())
    assert plan.profile is None
    assert plan.models == RoleModels()  # every field None / False


@pytest.mark.parametrize("profile", ["economy", "balanced", "max"])
def test_every_profile_fills_every_role(profile: Any) -> None:
    models = resolve(profile, _settings()).models
    assert models.explore and models.plan and models.edit and models.review


@pytest.mark.parametrize("profile", ["economy", "balanced", "max"])
def test_fusion_is_confined_to_the_tool_free_turns(profile: Any) -> None:
    """There is no `fuse_edit` and no `fuse_explore`, and that absence is the design.

    Asserted structurally rather than by value: a future profile that wanted to fuse the editor
    could not express it, which is the point.
    """
    models = resolve(profile, _settings()).models
    fields = set(RoleModels.model_fields)
    assert {"fuse_plan", "fuse_review"} <= fields
    assert not {"fuse_edit", "fuse_explore"} & fields
    assert isinstance(models.fuse_plan, bool) and isinstance(models.fuse_review, bool)


def test_verify_has_no_model_field_at_all() -> None:
    """Not a nullable field — an absent one. Offering a choice would imply one exists, and the whole
    value of an executable verifier is that the thing deciding whether the work was good has no
    opinion to route."""
    assert "verify" not in RoleModels.model_fields


def test_economy_never_reaches_for_the_top_tier() -> None:
    from chimera.providers.catalog import resolve_tiers

    ladder = resolve_tiers(_settings())  # type: ignore[arg-type]
    models = resolve("economy", _settings()).models
    assert ladder.top not in {models.explore, models.plan, models.edit, models.review}
    assert not models.fuse_plan and not models.fuse_review  # economy pays for no panels


def test_max_fuses_both_deliberative_turns() -> None:
    models = resolve("max", _settings()).models
    assert models.fuse_plan and models.fuse_review


def test_an_override_wins_field_by_field_without_blanking_the_rest() -> None:
    """A partially-filled override must not reset the untouched roles to "the default model" —
    that is the difference between "change the editor" and "abandon the profile"."""
    base = resolve("balanced", _settings()).models
    plan = resolve("balanced", _settings(), RoleModels(edit="vendor/custom"))

    assert plan.models.edit == "vendor/custom"
    assert plan.models.plan == base.plan and plan.models.explore == base.explore
    assert plan.models.fuse_plan == base.fuse_plan


def test_overrides_alone_work_without_a_profile() -> None:
    plan = resolve(None, _settings(), RoleModels(review="vendor/reviewer"))
    assert plan.models.review == "vendor/reviewer"


def test_the_reviewer_refuses_to_be_the_model_that_wrote_the_patch() -> None:
    """The one place where "the best model" is the wrong answer: a weaker INDEPENDENT check beats a
    stronger dependent one, and falling back to the run's default is how that is expressed."""
    same = resolve(None, _settings(), RoleModels(edit="vendor/x", review="vendor/x"))
    assert review_model_for(same) is None

    different = resolve(None, _settings(), RoleModels(edit="vendor/x", review="vendor/y"))
    assert review_model_for(different) == "vendor/y"


def test_max_does_not_accidentally_make_the_reviewer_the_editor() -> None:
    """`max` puts the top tier on both edit and review, which is exactly the collapse above — so the
    guard has to catch it on a real profile, not only on a hand-written override."""
    assert review_model_for(resolve("max", _settings())) is None


def test_the_solve_agent_routes_each_role_to_its_own_model(tmp_path: Any) -> None:
    """Asserted through the real builder: a role table the agent construction does not read is a
    table, and a table is what people trust when they stop checking."""
    from chimera.api.app import RunRequest, _build_solve_agent

    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    req = RunRequest(
        task="t",
        model="vendor/default",
        roles=RoleModels(edit="vendor/editor", plan="vendor/planner", review="vendor/reviewer"),
    )
    agent = _build_solve_agent(req, ws, lambda _e: None, settings)

    assert agent.worker.config.model == "vendor/editor"
    assert agent.planner.model == "vendor/planner"
    assert agent.manager.model == "vendor/reviewer"


def test_a_role_left_unset_falls_back_to_the_requests_own_model(tmp_path: Any) -> None:
    from chimera.api.app import RunRequest, _build_solve_agent

    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    req = RunRequest(task="t", model="vendor/default", roles=RoleModels(edit="vendor/editor"))
    agent = _build_solve_agent(req, ws, lambda _e: None, settings)

    assert agent.worker.config.model == "vendor/editor"
    assert agent.planner.model == "vendor/default"


def test_fuse_review_puts_a_panel_behind_the_manager_and_not_behind_the_editor(tmp_path: Any) -> None:
    from chimera.api.app import RunRequest, _build_solve_agent
    from chimera.fusion import FusionEngine

    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    req = RunRequest(task="t", roles=RoleModels(fuse_review=True, fuse_plan=True))
    agent = _build_solve_agent(req, ws, lambda _e: None, settings)

    assert isinstance(agent.manager.backend, FusionEngine)
    assert isinstance(agent.planner.backend, FusionEngine)
    # The editor is the one that must NEVER be a panel: three synthesised patches produce a patch
    # that applies cleanly and means nothing.
    assert not isinstance(agent.worker.backend, FusionEngine)
