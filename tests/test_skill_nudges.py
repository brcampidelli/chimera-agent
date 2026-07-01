"""Tests for skill nudges — suggest saving a recurring procedure as a skill."""

from __future__ import annotations

from chimera.evolution import detect_skill_nudges


def test_recurring_task_is_nudged() -> None:
    tasks = [
        "convert this CSV file to JSON",
        "please convert the CSV file into JSON format",  # same procedure
        "what is the capital of France?",  # one-off, unrelated
    ]
    nudges = detect_skill_nudges(tasks, known_skills=[], threshold=0.3)
    assert len(nudges) == 1
    assert nudges[0].count == 2
    assert "csv" in nudges[0].task.lower()


def test_one_off_tasks_are_not_nudged() -> None:
    tasks = ["summarize this article", "book a flight to Lisbon", "explain quantum tunnelling"]
    assert detect_skill_nudges(tasks, known_skills=[], threshold=0.3) == []


def test_existing_skill_suppresses_the_nudge() -> None:
    tasks = ["convert this CSV to JSON", "convert the CSV file to JSON please"]
    covered = detect_skill_nudges(
        tasks, known_skills=["csv_to_json — convert CSV files to JSON"], threshold=0.3
    )
    assert covered == []  # a skill already covers it


def test_nudges_are_capped() -> None:
    tasks = [
        "convert csv to json",
        "convert csv to json again",
        "resize the png image",
        "resize the png image once more",
        "translate text to spanish",
        "translate text to spanish again",
    ]
    nudges = detect_skill_nudges(tasks, known_skills=[], threshold=0.3, max_suggestions=2)
    assert len(nudges) == 2
