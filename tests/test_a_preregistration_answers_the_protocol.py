"""A pre-registration written after `bench/PROTOCOL.md` §11-§14 answers each of them (study 30, S30-34).

The protocol already said "a PREREGISTRATION.md that does not say which of these it satisfies, and
how, is not finished", and nothing checked it. §11 (the interval), §12 (the equivalence margin), §13
(the controls) and §14 (the model scope) are each a way a number came out of one of our benches
looking like a result, so a new registration names all four — "not applicable, because ..." counts,
silence does not. The ones written before the rules existed are frozen in a list that may only
shrink.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "bench"
GRANDFATHERED = BENCH / "PREREGISTRATIONS-before-protocol-11.txt"
REQUIRED = ("§11", "§12", "§13", "§14")

#: The list's length when it was frozen. Lower it when a registration leaves the list; never raise it.
FROZEN_AT = 115


def _grandfathered() -> list[str]:
    lines = GRANDFATHERED.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _missing(text: str) -> list[str]:
    return [section for section in REQUIRED if section not in text]


def test_every_new_preregistration_names_the_four_protocol_sections() -> None:
    old = set(_grandfathered())
    unanswered = {}
    for path in sorted(BENCH.glob("**/PREREGISTRATION*.md")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in old:
            continue
        missing = _missing(path.read_text(encoding="utf-8"))
        if missing:
            unanswered[rel] = missing
    assert unanswered == {}, (
        "these pre-registrations do not answer bench/PROTOCOL.md (name each section, even to say it "
        f"does not apply): {unanswered}"
    )


def test_the_grandfathered_list_only_shrinks_and_names_real_files() -> None:
    names = _grandfathered()
    assert len(names) == len(set(names)), "a registration is listed twice"
    assert len(names) <= FROZEN_AT, "the grandfathered list grew — a new registration answers §11-§14 instead"
    gone = [name for name in names if not (ROOT / name).is_file()]
    assert gone == [], f"listed but absent (remove the line and lower FROZEN_AT): {gone}"


def test_the_check_refuses_a_registration_that_is_silent_on_a_section() -> None:
    assert _missing("## Interval (§11)\nWilson.\n## §12\nnone\n## §13 controls\n") == ["§14"]
    assert _missing("§11 §12 §13 §14") == []
