"""Tests for the TRS reasoning-card schema on LearnedSkill (no network)."""

from __future__ import annotations

from chimera.evolution.learned_skill import LearnedSkill


def _card() -> LearnedSkill:
    return LearnedSkill(
        name="two_pointer_scan",
        description="Two-pointer technique for sorted-array pair problems",
        trigger="sorted array, find a pair summing to a target",
        do="start pointers at both ends; move inward by comparing the sum to the target",
        avoid="nested loops (O(n^2)); re-sorting an already-sorted input",
        check="pointers never cross; each element visited at most once",
        risk="unsorted input breaks the invariant",
        triggers=["sorted", "pair", "two pointer", "target sum"],
    )


def test_card_roundtrip_preserves_all_fields() -> None:
    original = _card()
    restored = LearnedSkill.from_dict(original.to_dict())
    for field in ("name", "description", "trigger", "do", "avoid", "check", "risk", "kind"):
        assert getattr(restored, field) == getattr(original, field)
    assert restored.triggers == original.triggers


def test_anti_pattern_kind_roundtrips() -> None:
    skill = LearnedSkill(name="off_by_one", description="fencepost error", kind="anti_pattern", do="x", check="y")
    assert LearnedSkill.from_dict(skill.to_dict()).kind == "anti_pattern"


def test_card_text_is_bulleted_and_capped() -> None:
    text = _card().card_text(max_lines=3)
    lines = text.splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("- Trigger:")
    assert all(line.startswith("- ") for line in lines)


def test_card_text_falls_back_to_description_when_no_card() -> None:
    skill = LearnedSkill(name="t", description="just a template skill", prompt_template="do {x}")
    assert skill.card_text() == "- just a template skill"


def test_old_format_dict_loads_without_card_fields() -> None:
    # A skills.json written before the card schema existed must still load.
    legacy = {"name": "legacy", "description": "old skill", "prompt_template": "summarize {text}"}
    skill = LearnedSkill.from_dict(legacy)
    assert skill.name == "legacy"
    assert skill.prompt_template == "summarize {text}"
    assert skill.kind == "pattern"
    assert skill.triggers == []
    assert not skill.has_card()


def test_advisory_card_is_not_executable() -> None:
    skill = LearnedSkill(name="c", description="advisory", kind="anti_pattern", do="x", check="y")
    assert skill.has_card()
    result = skill.run()
    assert result.ok is False and "advisory" in (result.error or "")
