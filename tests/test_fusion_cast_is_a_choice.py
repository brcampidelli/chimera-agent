"""Who plays each part in a fused turn, chosen by the person paying for it.

The engine has taken ``panel`` / ``judge`` / ``synthesizer`` per instance since it was written, and
the config has carried all three since then — but nothing between the engine and a user could set
them. The only panel anyone could actually run was whichever one shipped, which put the product's
central claim (several INDEPENDENT models answering, graded by a model that is not one of them) at
the mercy of a default nobody could change without hand-editing an env file.

Two seams, and they are deliberately different:

* the **config** is the standing default, and writing it needs the three keys to be editable;
* the **turn** is a per-conversation override, because "which models should argue about THIS
  question" is a property of the question, not of the installation.

Neither seam refuses a judge that also sits on the panel. That is reported — never enforced — and
the reason is in ``role_kinship``'s own docstring: a user with one provider key cannot avoid the
overlap, and refusing a configuration they have no way to fix would just remove the feature.
"""

from __future__ import annotations

from typing import Any

from chimera.api.code_api import CodeTurnRequest, _cast_for_turn
from chimera.api.config_api import _fusion_kinship, is_editable
from chimera.fusion.engine import FusionConfig, FusionEngine


class _Gateway:
    """Stands in for the LLM gateway: the per-turn engine must reuse it, not build another."""


def _engine(panel: list[str] | None = None, judge: str = "j", synth: str = "s") -> FusionEngine:
    return FusionEngine(
        _Gateway(),
        FusionConfig(panel=list(panel or ["a", "b", "c"]), judge=judge, synthesizer=synth),
    )


def _turn(**kwargs: Any) -> CodeTurnRequest:
    return CodeTurnRequest(message="does this design hold up?", fuse=True, **kwargs)


# --- the standing default ------------------------------------------------------------------------


def test_the_three_roles_are_writable_from_the_app() -> None:
    # Until this landed, `chimera/api/config_api.py` listed 25 editable settings and none of them was
    # the fusion cast — so the app could show a fused turn's receipt and never let anyone change who
    # produced it.
    assert is_editable("CHIMERA_FUSION_PANEL")
    assert is_editable("CHIMERA_FUSION_JUDGE")
    assert is_editable("CHIMERA_FUSION_SYNTHESIZER")


def test_the_config_reports_how_independent_the_judge_is() -> None:
    """The two degrees, and the second is the one that gets missed."""
    clean = _fusion_kinship(["vendor_a/one", "vendor_b/two"], "vendor_c/three")
    assert clean["independent"] is True

    grading_itself = _fusion_kinship(["vendor_a/one", "vendor_b/two"], "vendor_a/one")
    assert grading_itself["judge_is_panelist"] is True
    assert grading_itself["independent"] is False

    # Not the same model — the same lab. Two answers from one vendor are not two votes, and this is
    # the case a user would never notice from the slugs alone.
    same_lab = _fusion_kinship(["openrouter/anthropic/claude-opus-5"], "openrouter/anthropic/haiku")
    assert same_lab["judge_is_panelist"] is False
    assert same_lab["judge_shares_vendor_with"] == ["openrouter/anthropic/claude-opus-5"]
    assert same_lab["independent"] is False


def test_the_kinship_shown_is_the_engine_s_own_answer() -> None:
    # Reimplementing the vendor comparison in the API layer would give two rules that drift, and the
    # copy on screen would be the one people believe. This asserts they are one rule.
    panel, judge = ["openrouter/anthropic/opus", "openrouter/openai/gpt"], "openrouter/anthropic/haiku"
    engine_says = FusionConfig(panel=panel, judge=judge, synthesizer=judge).role_kinship()
    assert _fusion_kinship(panel, judge) == dict(engine_says)


# --- the per-conversation override ---------------------------------------------------------------


def test_a_turn_with_no_choice_keeps_the_object_it_already_had() -> None:
    # The shared engine is handed to every turn. A turn that expresses no preference must get that
    # exact instance back — not an equal copy — so nothing about the default path changes shape.
    shared = _engine()
    assert _cast_for_turn(shared, _turn()) is shared


def test_a_chosen_cast_runs_on_the_same_gateway() -> None:
    shared = _engine(panel=["a", "b"], judge="j", synth="s")
    turn = _cast_for_turn(
        shared, _turn(fusion_panel=["x", "y"], fusion_judge="jx", fusion_synthesizer="sx")
    )

    assert turn is not shared
    assert turn.config.panel == ["x", "y"]
    assert turn.config.judge == "jx"
    assert turn.config.synthesizer == "sx"
    # The point of `replace` over a fresh config: one gateway, one credential pool, one cache.
    assert turn.backend is shared.backend


def test_choosing_one_role_leaves_the_others_alone() -> None:
    shared = _engine(panel=["a", "b"], judge="j", synth="s")
    turn = _cast_for_turn(shared, _turn(fusion_judge="jx"))

    assert turn.config.judge == "jx"
    assert turn.config.panel == ["a", "b"]
    assert turn.config.synthesizer == "s"


def test_the_shared_engine_is_never_mutated() -> None:
    """The swap in the caller restores `agent.backend` afterwards — but only the reference. If the
    override edited the config in place, every later turn in every other conversation would inherit
    a cast nobody chose there."""
    shared = _engine(panel=["a", "b"], judge="j", synth="s")

    _cast_for_turn(shared, _turn(fusion_panel=["x", "y"], fusion_judge="jx"))

    assert shared.config.panel == ["a", "b"]
    assert shared.config.judge == "j"
    assert shared.config.synthesizer == "s"


def test_a_panel_of_one_is_refused_back_to_the_default() -> None:
    # Fusion over a single opinion is not fusion; it is one model plus the cost of a judge and a
    # synthesizer. Refused rather than run, and refused back to something that works.
    shared = _engine(panel=["a", "b", "c"])
    assert _cast_for_turn(shared, _turn(fusion_panel=["only-me"])).config.panel == ["a", "b", "c"]


def test_blank_and_whitespace_choices_are_not_choices() -> None:
    shared = _engine(panel=["a", "b"], judge="j")
    assert _cast_for_turn(shared, _turn(fusion_judge="   ")) is shared
    assert _cast_for_turn(shared, _turn(fusion_panel=["", "  "])) is shared


def test_a_judge_on_its_own_panel_is_allowed_and_visible() -> None:
    """Allowed, because a one-key user has no alternative. Visible, because it is the case where the
    independent signal fusion sells is not independent."""
    shared = _engine(panel=["a", "b"])
    turn = _cast_for_turn(shared, _turn(fusion_panel=["a", "b"], fusion_judge="a"))

    assert turn.config.judge == "a"
    assert turn.config.role_kinship()["judge_is_panelist"] is True


def test_a_backend_that_is_not_a_fusion_engine_is_passed_through() -> None:
    # `fuse_backend` is injected, and a deployment can hand something else in. Reading `.config` off
    # whatever arrives would turn a wrong assumption into an AttributeError mid-turn.
    class NotAnEngine:
        pass

    other = NotAnEngine()
    assert _cast_for_turn(other, _turn(fusion_panel=["x", "y"])) is other
