"""Tests for SKILL.md interop + progressive disclosure (M15-A2)."""

from __future__ import annotations

from chimera.evolution.learned_skill import LearnedSkill
from chimera.skills.skill_md import (
    Disclosure,
    SkillManifest,
    SkillMd,
    from_learned,
    parse_skill_md,
    render_skill_md,
    to_learned,
)


def _skill() -> LearnedSkill:
    return LearnedSkill(
        name="fix-percentile",
        description="Interpolate percentiles between order statistics.",
        version="0.2.0",
        trigger="asked for a percentile",
        do="use linear interpolation between the two neighbors",
        avoid="rounding to the nearest rank",
        check="p50 of [1,2,3,4] == 2.5",
        risk="off-by-one on the index",
        triggers=["percentile", "interpolation"],
        prompt_template="Compute the {p}th percentile of {data}.",
    )


# --- round-trip --------------------------------------------------------------------------


def test_render_then_parse_preserves_metadata() -> None:
    md = render_skill_md(from_learned(_skill()))
    assert md.startswith("---\n")
    parsed = parse_skill_md(md)
    m = parsed.manifest
    assert m.name == "fix-percentile"
    assert m.version == "0.2.0"
    assert m.triggers == ["percentile", "interpolation"]
    assert m.provenance == "clean"


def test_learned_roundtrip_recovers_card_and_template() -> None:
    original = _skill()
    restored = to_learned(parse_skill_md(render_skill_md(from_learned(original))))
    assert restored.name == original.name
    assert restored.do == "use linear interpolation between the two neighbors"
    assert restored.check == "p50 of [1,2,3,4] == 2.5"
    assert restored.prompt_template == "Compute the {p}th percentile of {data}."
    assert restored.triggers == ["percentile", "interpolation"]


# --- progressive disclosure --------------------------------------------------------------


def test_disclosure_levels_are_cumulative_and_cheapest_first() -> None:
    md = from_learned(_skill())
    md.resources = ["scripts/pct.py"]
    l1 = md.disclose(Disclosure.METADATA)
    l2 = md.disclose(Disclosure.INSTRUCTIONS)
    l3 = md.disclose(Disclosure.RESOURCES)
    # L1 = metadata only (name + description + triggers), no how-to body.
    assert "fix-percentile" in l1 and "linear interpolation" not in l1
    # L2 adds the instructions; L3 adds the resource pointers.
    assert "linear interpolation" in l2
    assert "scripts/pct.py" not in l2 and "scripts/pct.py" in l3
    assert len(l1) < len(l2) < len(l3)  # cheapest first — the token-cost lever


# --- security lineage --------------------------------------------------------------------


def test_provenance_travels_in_frontmatter() -> None:
    skill = _skill()
    skill.provenance = "tainted"
    md = render_skill_md(from_learned(skill))
    assert "provenance: tainted" in md


def test_tainted_import_is_held_pending() -> None:
    md = SkillMd(SkillManifest(name="x", description="d", provenance="tainted"), instructions="## Do\nstuff")
    imported = to_learned(md)
    # A tainted imported skill must not silently enter retrieval — it's pending until approved.
    assert imported.provenance == "tainted"
    assert imported.status == "pending"


def test_clean_import_stays_active() -> None:
    md = SkillMd(SkillManifest(name="x", description="d"), instructions="## Do\nstuff")
    assert to_learned(md).status == "active"


def test_provisional_status_survives_import() -> None:
    # A clean skill on probation must round-trip as `provisional`, not be promoted to `active`.
    md = SkillMd(SkillManifest(name="x", description="d", status="provisional"), instructions="## Do\nstuff")
    assert to_learned(md).status == "provisional"


def test_unknown_status_defaults_to_pending() -> None:
    # A mistyped/unknown status must never silently become full `active` retrieval.
    md = SkillMd(SkillManifest(name="x", description="d", status="bogus"), instructions="## Do\nstuff")
    assert to_learned(md).status == "pending"


def test_malformed_frontmatter_is_treated_as_body() -> None:
    # Broken YAML frontmatter (e.g. from an untrusted import) must not crash the parser.
    parsed = parse_skill_md("---\nname: [unclosed\n---\n\nthe body")
    assert parsed.manifest.name == "unnamed"  # frontmatter ignored
    assert "the body" in parsed.instructions


# --- parsing robustness ------------------------------------------------------------------


def test_body_without_frontmatter_is_all_instructions() -> None:
    parsed = parse_skill_md("just some instructions, no frontmatter")
    assert parsed.manifest.name == "unnamed"
    assert "just some instructions" in parsed.instructions


def test_anti_pattern_kind_survives() -> None:
    md = render_skill_md(SkillMd(SkillManifest(name="a", description="d", kind="anti_pattern")))
    assert parse_skill_md(md).manifest.kind == "anti_pattern"


def test_allowed_tools_and_license_roundtrip() -> None:
    src = SkillMd(SkillManifest(
        name="a", description="d", license="Apache-2.0", allowed_tools=["read_file", "edit_file"]
    ))
    parsed = parse_skill_md(render_skill_md(src))
    assert parsed.manifest.license == "Apache-2.0"
    assert parsed.manifest.allowed_tools == ["read_file", "edit_file"]


# --- store integration (export/import path) ----------------------------------------------


def test_store_get_and_skill_md_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from chimera.evolution import SkillStore

    store = SkillStore(tmp_path / "skills.json")
    store.add(_skill())
    fetched = store.get("fix-percentile")
    assert fetched is not None and fetched.name == "fix-percentile"

    # Export → import through a fresh store recovers the skill.
    md = render_skill_md(from_learned(fetched))
    other = SkillStore(tmp_path / "other.json")
    other.add(to_learned(parse_skill_md(md)))
    assert "fix-percentile" in other
    assert other.get("fix-percentile").do.startswith("use linear interpolation")  # type: ignore[union-attr]


def test_store_get_missing_is_none(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from chimera.evolution import SkillStore

    assert SkillStore(tmp_path / "skills.json").get("ghost") is None
