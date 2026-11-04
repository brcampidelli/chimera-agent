"""A Code-turn lesson is read only by the Code screen, and only behind the flag.

``AutonomousAgent.run`` reads ``experience.json`` unconditionally (``_recall_lessons``): lanes,
workflows, the lifecycle crew and ``chimera solve`` all do. A lesson written there by a Code turn
would reach their prompts with the flag off, so Code-turn lessons live in their own store.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core.autonomous import AutonomousAgent
from chimera.evolution.code_turn_learning import lesson_block, record_turn_lesson
from chimera.evolution.experience import ExperienceBuffer

TASK = "rename the loader module"
LESSON = "LESSON-MARKER: the loader is generated"


def _autonomous_reader(home: Path) -> Any:
    """The read an autonomous run makes before its first attempt, on a bare instance."""
    agent = object.__new__(AutonomousAgent)
    agent.experience = ExperienceBuffer(home / "experience.json")
    agent.arm_on_recalled_lessons = False
    return agent


def test_with_the_flag_off_an_autonomous_run_never_sees_a_code_turn_lesson(tmp_path: Path) -> None:
    record_turn_lesson(tmp_path, task=TASK, answer=LESSON, verdict="passed", tainted=False, steps=1)

    assert AutonomousAgent._recall_lessons(_autonomous_reader(tmp_path), TASK) == ""


def test_the_code_turn_store_is_its_own_file(tmp_path: Path) -> None:
    record_turn_lesson(tmp_path, task=TASK, answer=LESSON, verdict="passed", tainted=False, steps=1)

    assert (tmp_path / "code_lessons.json").exists()
    assert not (tmp_path / "experience.json").exists()


def test_a_tainted_code_turn_lesson_is_not_injected_into_a_code_prompt(tmp_path: Path) -> None:
    """A tainted lesson would reach the prompt with no ledger armed, unlike a tainted fact or
    lesson in the autonomous path, which arms the ledger. So the Code recall leaves it out."""
    record_turn_lesson(tmp_path, task=TASK, answer=LESSON, verdict="passed", tainted=True, steps=1)

    assert lesson_block(tmp_path, TASK) == ""


def test_a_clean_code_turn_lesson_is_injected_into_a_code_prompt(tmp_path: Path) -> None:
    record_turn_lesson(tmp_path, task=TASK, answer=LESSON, verdict="passed", tainted=False, steps=1)

    block = lesson_block(tmp_path, TASK)
    assert LESSON in block and "Lessons from past attempts" in block
