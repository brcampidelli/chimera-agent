"""The judge must not grade its own answer.

Fusion is this project's claim to an *independent* signal rather than a self-report, and the shipped
default contradicted it: `_DEFAULT_JUDGE` was `_DEFAULT_PANEL[0]` — the same slug, character for
character — so the default panel's first answer was graded by the model that wrote it. Nothing in
the codebase noticed, because nothing looked.

The check is a test rather than a validator that raises. Someone holding a single provider key
cannot assemble a four-vendor panel, and crashing their config to protect a property they have no
way to satisfy would trade a silent flaw for a loud one. So the *default* is held independent here,
and the runtime reports overlap through `FusionConfig.role_kinship()` for the configs that cannot
avoid it.
"""

from __future__ import annotations

from chimera.config import _DEFAULT_JUDGE, _DEFAULT_PANEL, _DEFAULT_SYNTHESIZER
from chimera.fusion.engine import FusionConfig, _vendor_of


def test_the_default_judge_is_not_a_panelist() -> None:
    """The regression this file exists for. Reverting the default makes it fail."""
    assert _DEFAULT_JUDGE not in _DEFAULT_PANEL, (
        f"the judge ({_DEFAULT_JUDGE}) is on the panel it grades — fusion would be scoring its "
        "own answer in the one place this project claims an independent signal"
    )


def test_the_default_judge_is_not_even_a_panelist_s_sibling() -> None:
    """Two models from one lab are not two independent votes."""
    vendors = {_vendor_of(m) for m in _DEFAULT_PANEL}
    assert _vendor_of(_DEFAULT_JUDGE) not in vendors, (
        f"judge vendor {_vendor_of(_DEFAULT_JUDGE)!r} also appears on the panel {sorted(vendors)}"
    )


def test_changing_the_judge_does_not_move_the_synthesiser() -> None:
    """The synthesiser used to be spelled `_DEFAULT_JUDGE`.

    Fixing the judge would have relocated the synthesiser too, silently — a second behaviour change
    riding along on a one-line fix, which is exactly the class of defect the audit that produced
    this file was looking for.
    """
    assert _DEFAULT_SYNTHESIZER != _DEFAULT_JUDGE
    assert _DEFAULT_SYNTHESIZER in _DEFAULT_PANEL, (
        "the synthesiser is deliberately still a panelist (composition, not evaluation) — if that "
        "changes it should be a decision with its own reason, not a side effect"
    )


def test_kinship_names_both_degrees_of_overlap() -> None:
    same_model = FusionConfig(panel=["a/x/1", "a/x/2"], judge="a/x/1", synthesizer="a/x/1")
    assert same_model.role_kinship() == {
        "judge_is_panelist": True,
        "judge_shares_vendor_with": ["a/x/2"],
        "independent": False,
    }

    same_lab = FusionConfig(panel=["openrouter/anthropic/o", "openrouter/openai/g"],
                            judge="openrouter/anthropic/h", synthesizer="x")
    kin = same_lab.role_kinship()
    assert kin["judge_is_panelist"] is False, "a different slug is not the same model"
    assert kin["judge_shares_vendor_with"] == ["openrouter/anthropic/o"]
    assert kin["independent"] is False, "same lab is the weaker overlap, and still not independent"


def test_kinship_calls_the_shipped_default_independent() -> None:
    assert FusionConfig(
        panel=list(_DEFAULT_PANEL), judge=_DEFAULT_JUDGE, synthesizer=_DEFAULT_SYNTHESIZER
    ).role_kinship()["independent"] is True


def test_vendor_extraction_gives_up_rather_than_guessing() -> None:
    """A wrong vendor guess would label a receipt with a confidence nobody measured."""
    assert _vendor_of("openrouter/anthropic/claude-opus-4-8") == "anthropic"
    assert _vendor_of("anthropic/claude-opus-4-8") == "anthropic"
    assert _vendor_of("gpt-4o") == "", "a bare model name names no vendor — say so, do not invent one"
    assert _vendor_of("") == ""
