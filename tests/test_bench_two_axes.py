"""Two benches that could only report the flattering half.

The injection suite counted attacks blocked and nothing else, so the trivial maximum — refuse
everything — scored perfectly. The skill-card A/B compared cards against nothing, so "a block of
plausible prose before the question helps" was indistinguishable from "this card was relevant".

Both are the same mistake in different clothes: a single axis a defense or a feature can be optimised
along without doing the thing it claims to do.
"""

from __future__ import annotations

from chimera.eval.injection import (
    PostureReport,
    default_attacks,
    default_benign,
    run_benign,
    run_posture,
    run_redteam,
)
from chimera.eval.skillcard_ab import CardABReport, CardABRow

# --- the injection bench's second axis ----------------------------------------------------------


def test_the_benign_corpus_taints_the_way_production_does() -> None:
    """The correction that mattered. `record_fetch` has ONE production caller — `ledger_tool`, for
    fetch-class tools — so a workspace read does not taint while `trust_workspace` is True.

    The first version of this corpus tainted all six rows and reported 100% over-block. That was a
    property of the harness, not of the defense, and this test is what keeps it from coming back.
    """
    sources = {task.source for task in default_benign()}

    assert sources == {"workspace", "fetch"}, "a corpus with one door measures one configuration"


def test_workspace_reads_are_never_refused() -> None:
    """The control. A refusal here would mean the taint default moved and every ordinary run just
    acquired a gate it did not have yesterday — a louder problem than an over-strict gate."""
    report = run_benign(default_benign(), defended=True)

    assert report.summary()["over_block_workspace"] == 0.0


def test_reading_something_external_first_costs_the_run_its_writes() -> None:
    """The measurement, and it is not subtle: with `approve=None` there is nothing to approve, so
    every dangerous-class call after any external read is refused. An agent that fetches a page and
    then edits a file completes zero of its writes."""
    report = run_benign(default_benign(), defended=True)

    assert report.summary()["over_block_fetch"] == 1.0


def test_undefended_refuses_nothing() -> None:
    # The floor: without the defense the benign corpus must pass entirely, or the corpus itself is
    # broken and every number above it is about the corpus.
    report = run_benign(default_benign(), defended=False)

    assert report.summary()["over_block_rate"] == 0.0


def test_the_gate_needs_both_halves() -> None:
    """A stack that refuses every call scores a perfect block rate. That is the exact failure a
    single-axis gate rewards, so the gate reads both or neither."""
    perfect_defense_useless_product = PostureReport(
        attacks=run_redteam(default_attacks(), defended=True),
        benign=run_benign(default_benign(), defended=True),
    )
    passed, why = perfect_defense_useless_product.gate()

    assert passed is False
    assert "over-block" in why


def test_the_gate_names_the_half_that_failed() -> None:
    # Undefended: attacks sail through, benign work is untouched. The complaint must be about the
    # block rate, not about over-blocking.
    passed, why = run_posture(defended=False).gate()

    assert passed is False
    assert "block rate" in why and "over-block" not in why


def test_a_thresholds_pair_is_registered_not_invented() -> None:
    # Both live on the class so that changing one is a diff someone reviews, not a literal buried in
    # a function. `bench/injection/PREREGISTRATION.md` is where they are justified.
    assert 0 < PostureReport.MAX_OVER_BLOCK_RATE < PostureReport.MIN_BLOCK_RATE <= 1


# --- the skill-card bench's placebo arm ---------------------------------------------------------


def _row(task: str, base: bool, card: bool, placebo: bool | None) -> CardABRow:
    return CardABRow(
        task_id=task, base_ok=base, base_tokens=10, card_ok=card, card_tokens=20,
        hit=True, placebo_ok=placebo, placebo_tokens=20,
    )


def test_cards_that_only_beat_nothing_do_not_beat_the_placebo() -> None:
    """The case the arm exists for: adding ANY block of text helps, and the card is not doing the
    work. Against no-cards it looks like a win; against the placebo it is a wash — and only the
    second is a claim about the skill library."""
    report = CardABReport(rows=[_row(f"t{i}", base=False, card=True, placebo=True) for i in range(10)])

    assert report.paired().treatment_only == 10, "it beats no-cards outright"
    vs_placebo = report.paired_vs_placebo()
    assert vs_placebo is not None
    assert vs_placebo.treatment_only == 0 and vs_placebo.baseline_only == 0


def test_a_card_that_really_works_still_wins_against_the_placebo() -> None:
    # The counterpart. A control that also kills the true positives is not a control.
    report = CardABReport(rows=[_row(f"t{i}", base=False, card=True, placebo=False) for i in range(10)])

    vs_placebo = report.paired_vs_placebo()
    assert vs_placebo is not None
    assert vs_placebo.treatment_only == 10


def test_no_placebo_arm_reports_none_rather_than_a_fabricated_tie() -> None:
    """The arm costs a third model call per task, so it is opt-in — and an un-run arm must read as
    absent, never as a measured null. Those license opposite decisions."""
    report = CardABReport(rows=[_row("t", base=False, card=True, placebo=None)])

    assert report.paired_vs_placebo() is None
    assert report.paired().treatment_only == 1, "the original comparison is unaffected"


def test_the_decoys_are_real_cards_the_task_did_not_retrieve() -> None:
    """A placebo of obvious nonsense would ask a different question. The block has to be plausible
    library prose so that what is being tested is RELEVANCE, not the presence of text."""
    from chimera.eval.skillcard_ab import _decoys
    from chimera.evolution.card_retrieval import CardIndex
    from chimera.evolution.learned_skill import LearnedSkill

    cards = [
        LearnedSkill(name="about_parsing", description="parsing text", prompt_template="x"),
        LearnedSkill(name="about_baking", description="baking bread", prompt_template="y"),
    ]

    decoys = _decoys(CardIndex(cards), cards, "how do I parse text", 1, k=3, min_overlap=1)

    assert [c.name for c in decoys] == ["about_baking"]


def test_a_decoy_never_duplicates_the_card_under_test() -> None:
    # Repeating the retrieved card would make the placebo a second dose of the treatment.
    from chimera.eval.skillcard_ab import _decoys
    from chimera.evolution.card_retrieval import CardIndex
    from chimera.evolution.learned_skill import LearnedSkill

    cards = [LearnedSkill(name="only_card", description="parsing text", prompt_template="x")]

    decoys = _decoys(CardIndex(cards), cards, "how do I parse text", 1, k=3, min_overlap=1)

    assert decoys == [], "it padded the placebo with the card it was controlling for"
