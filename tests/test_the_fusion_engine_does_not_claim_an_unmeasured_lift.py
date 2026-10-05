"""The fusion engine's own text does not claim a gain the repository never measured (S30-15).

The module docstring said "the lift comes from the synthesis step itself", the selective path said
the synthesis step "is where the lift comes from", and ``panel_diversity`` called the panel's answers
"independent". None of it was measured here: ``docs/multi-agent-policy.md`` lists "does fusion beat
one model at equal budget?" as not measured, and ``bench/panel_correlation`` found three panelists
carrying 1.46 effective votes. A docstring is what a contributor reads before deciding what fusion is
for, so the claim is held out of it by a test rather than by memory.
"""

from __future__ import annotations

from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "chimera" / "fusion" / "engine.py"


def test_the_engine_does_not_say_the_lift_comes_from_synthesis() -> None:
    text = " ".join(ENGINE.read_text(encoding="utf-8").split())
    assert "lift comes from" not in text


def test_the_engine_does_not_call_the_panel_answers_independent() -> None:
    text = " ".join(ENGINE.read_text(encoding="utf-8").split())
    assert "so its answers are independent" not in text
    assert "1.46" in text  # the measured effective votes are stated instead
