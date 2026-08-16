"""The versioned skill library in `skills/` validates itself.

`skills/` exists so that someone can contribute something useful without writing Python. That only
works if the review is cheap, and the review is only cheap if the mechanical half is automatic — a
maintainer reading a contributed card should be spending their attention on whether the *advice* is
good, not on whether the frontmatter parses.

None of this executes a skill. A `SKILL.md` is data. But it is data an agent will act on, so the
structural checks here are also the reason `provenance: clean` means something: it is conferred by a
reviewer who saw a well-formed card, not claimed by a file about itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.skills.skill_md import _CARD_SECTIONS, STAGES, TOPICS, parse_skill_md

LIBRARY = Path(__file__).resolve().parent.parent / "skills"
CARDS = sorted(LIBRARY.glob("*/SKILL.md"))
_IDS = [c.parent.name for c in CARDS]


def test_the_library_is_not_empty() -> None:
    """A glob over an empty directory would make every parametrized test below vacuous.

    It would also mean the contribution surface the docs advertise does not exist, which is the
    more interesting failure of the two.
    """
    assert CARDS, f"no SKILL.md found under {LIBRARY} — the skill library has gone missing"
    assert (LIBRARY / "README.md").exists(), "skills/README.md explains how to contribute one"


@pytest.mark.parametrize("card", CARDS, ids=_IDS)
def test_name_matches_its_directory(card: Path) -> None:
    """Two names for one thing drift apart. The directory is the address; the frontmatter is what
    the agent and the CLI use, and `chimera skill import skills/<dir>` assumes they agree."""
    skill = parse_skill_md(card.read_text(encoding="utf-8"))
    assert skill.manifest.name == card.parent.name


@pytest.mark.parametrize("card", CARDS, ids=_IDS)
def test_carries_the_metadata_retrieval_depends_on(card: Path) -> None:
    """`description` and `triggers` are the L1 disclosure level — the only thing read when deciding
    whether a skill is relevant at all. A card without them is invisible to the thing that would
    use it, however good its body is."""
    m = parse_skill_md(card.read_text(encoding="utf-8")).manifest
    assert m.description.strip(), "description is what the agent reads when judging relevance"
    assert m.triggers, "triggers are how the skill gets found"
    assert m.license, "a contributed card states its licence"
    assert m.kind in {"pattern", "anti_pattern"}
    assert m.status == "active", "a card in the library is active; pending is for runtime imports"


@pytest.mark.parametrize("card", CARDS, ids=_IDS)
def test_provenance_is_clean(card: Path) -> None:
    """Everything in this directory passed human review — that is what being in it means.

    A `tainted` card here would be a contradiction: taint marks something the agent picked up at
    runtime and nobody vetted, and such a card should be in the user's own store as `pending`, not
    committed to the repository as if it had been checked.
    """
    assert parse_skill_md(card.read_text(encoding="utf-8")).manifest.provenance == "clean"


@pytest.mark.parametrize("card", CARDS, ids=_IDS)
def test_has_all_five_sections_in_order(card: Path) -> None:
    """Trigger / Do / Avoid / Check / Risk.

    The order is not decoration. `Do` is the easy section and the one everybody writes; `Avoid` and
    `Check` are where the value usually is, and they are the ones quietly dropped when a card is
    written in a hurry. Requiring all five is what stops the library filling up with happy paths.
    """
    body = parse_skill_md(card.read_text(encoding="utf-8")).instructions
    positions = []
    for section in _CARD_SECTIONS:
        marker = f"## {section}"
        assert marker in body, f"{card.parent.name} is missing the '{section}' section"
        positions.append(body.index(marker))
    assert positions == sorted(positions), (
        f"{card.parent.name}: sections are out of order — expected {list(_CARD_SECTIONS)}"
    )


@pytest.mark.parametrize("card", CARDS, ids=_IDS)
def test_sections_have_content(card: Path) -> None:
    """A heading with nothing under it passes a structure check and helps nobody."""
    body = parse_skill_md(card.read_text(encoding="utf-8")).instructions
    thin = []
    for section in _CARD_SECTIONS:
        marker = f"## {section}"
        # A missing section is the other test's finding. Indexing it here would raise ValueError and
        # report a crash where the real answer is "that section is absent" — noise on top of a
        # failure that is already being reported clearly one test up.
        if marker not in body:
            continue
        start = body.index(marker) + len(marker)
        rest = body[start:]
        nxt = min((rest.index(f"## {s}") for s in _CARD_SECTIONS if f"## {s}" in rest), default=len(rest))
        if len(rest[:nxt].strip()) < 40:
            thin.append(section)
    assert not thin, f"{card.parent.name}: these sections are empty or near-empty: {thin}"


@pytest.mark.parametrize("card", CARDS, ids=_IDS)
def test_round_trips_through_the_renderer(card: Path) -> None:
    """Parse → render → parse gives the same manifest.

    This is what proves the file is genuinely consumable by the code that ships it, rather than
    merely being markdown that happens to look right to a human reader.
    """
    from chimera.skills.skill_md import render_skill_md

    once = parse_skill_md(card.read_text(encoding="utf-8"))
    twice = parse_skill_md(render_skill_md(once))
    assert twice.manifest == once.manifest
    assert twice.instructions == once.instructions


# --- browsing metadata: the hub groups by these, so a card without them is invisible ---------------


@pytest.mark.parametrize("card", CARDS, ids=_IDS)
def test_declares_a_stage_and_a_topic(card: Path) -> None:
    """A card with no stage lands in no group, and the hub renders only non-empty groups — so the
    failure is not an ugly "Other" bucket, it is a card that silently does not appear.

    The parser deliberately does NOT enforce this: an imported third-party card may carry a
    taxonomy that is not ours, and refusing to READ it over a display field would be the wrong
    trade. "Required" belongs on what we publish, which is here.
    """
    skill = parse_skill_md(card.read_text(encoding="utf-8"))

    assert skill.manifest.stage in STAGES, (
        f"{card.parent.name}: stage must be one of {STAGES} — a value outside the vocabulary is "
        "dropped to '' by the parser, so a typo removes the card from the hub without any error"
    )
    assert skill.manifest.topic in TOPICS, (
        f"{card.parent.name}: topic must be one of {TOPICS}"
    )


def test_every_stage_the_hub_can_show_has_at_least_one_card() -> None:
    """The taxonomy is longer than the library on purpose — topics light up as contributions
    arrive. Stages are different: five is the whole shape of a piece of work, and an empty one
    means the library has a hole in it rather than room to grow.
    """
    seen = {parse_skill_md(c.read_text(encoding="utf-8")).manifest.stage for c in CARDS}

    assert set(STAGES) <= seen, f"no card covers: {sorted(set(STAGES) - seen)}"


def test_the_vocabulary_check_would_catch_a_typo() -> None:
    """Proof the check is not vacuous. `_vocab` returning '' for an unknown value is what makes a
    typo silent, so this asserts the silence exists and that the test above is what breaks it."""
    from chimera.skills.skill_md import _vocab

    assert _vocab("Verify", STAGES) == "verify"  # case and spacing are normalised
    assert _vocab("verifyy", STAGES) == ""  # a typo becomes empty, not an error
    assert _vocab(None, STAGES) == ""
