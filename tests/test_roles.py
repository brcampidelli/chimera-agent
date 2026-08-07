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


def test_max_keeps_the_editor_on_the_tool_calling_tier_not_the_top_one() -> None:
    """The one place `max` deliberately does not take the best model.

    The ladder ranks by REASONING strength; `edit` is the only role that carries tools every turn AND
    has to finish. `bench/role_routing/PILOT.md` measured what happens when those are conflated: with
    the top tier on edit, 3 of 3 solves burned the full 1800 s wall and produced an empty patch, at
    US$ 0.16 each — cheap because the arm was waiting, not working.

    Asserted against the ladder rather than a slug so it keeps meaning when the catalogue changes.
    """
    from chimera.providers.catalog import resolve_tiers

    ladder = resolve_tiers(_settings())  # type: ignore[arg-type]
    models = resolve("max", _settings()).models

    assert models.edit == ladder.mid
    assert models.edit != ladder.top
    assert models.plan == ladder.top and models.review == ladder.top  # reasoning roles still escalate


def test_max_now_gets_an_independent_reviewer_as_a_side_effect() -> None:
    """This test replaces one that asserted the opposite, and the reason is worth keeping.

    `max` used to put the top tier on edit AND review, which tripped the collapse guard — the
    reviewer fell back to the run's default because it must not be the model that wrote the patch.
    Moving edit off the top tier removes the collision, so `max` now routes a genuinely independent
    reviewer instead of silently degrading to the default. That is strictly better, and it means the
    old assertion (`review_model_for(...) is None`) is now false for a good reason rather than a bad
    one. The guard itself stays covered by the override-based test above, which is where it belongs:
    a guard should be tested on the condition it guards, not on a profile that happens to hit it.
    """
    assert review_model_for(resolve("max", _settings())) is not None


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


def test_one_model_means_no_role_routing_and_says_so() -> None:
    """The ordinary state for a user with a single provider key.

    With one model on every rung there is nothing to route: all four roles would resolve to the same
    slug, `review_model_for` would return None, and the Manager would review with the very model
    that wrote the patch — the self-grading collapse the reviewer rule exists to prevent. Offering
    three profiles here would charge the user attention for a distinction that does not exist, and a
    `max` that costs exactly what `economy` costs while reporting itself as different is the kind of
    claim this project keeps deleting.
    """
    from chimera.api.roles import resolve

    settings = _settings()
    settings.weak_model = "solo/model"
    settings.mid_model = "solo/model"
    settings.orchestrator_model = "solo/model"

    plan = resolve("max", settings)

    assert plan.single_model == "solo/model"
    assert plan.models.explore is None and plan.models.plan is None
    assert plan.models.edit is None and plan.models.review is None
    # And no fusion: a one-model "panel" is one model asked three times.
    assert not plan.models.fuse_plan and not plan.models.fuse_review


def test_a_single_model_still_honours_an_explicit_per_role_override() -> None:
    """Someone naming a second model is precisely the person who has one."""
    from chimera.api.roles import RoleModels, resolve

    settings = _settings()
    settings.weak_model = settings.mid_model = settings.orchestrator_model = "solo/model"

    plan = resolve("balanced", settings, RoleModels(review="other/reviewer"))

    assert plan.models.review == "other/reviewer"
    assert plan.single_model == "solo/model"
