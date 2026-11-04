"""A Code-screen turn learns, and the owner decides whether the next turn is told.

The Code screen builds a plain ``Agent``, which never wrote to the experience buffer. Now every turn
that ran a loop records one lesson, in ``code_lessons.json`` — a store the autonomous paths never
read. What comes back into a Code prompt is the flag's question, ``CHIMERA_SKILL_CARDS``, and it is
off by default because the seven pre-registered runs in ``bench/learning_lift`` have not shown that
reading learned material helps.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings, get_settings
from chimera.evolution.code_turn_learning import CodeTurnRecall
from chimera.evolution.experience import ExperienceBuffer
from chimera.interface import ChatSession
from chimera.providers.gateway import CompletionResult
from chimera.tools import ToolRegistry

LESSON = "LESSON-MARKER: the loader is generated, so edit its template"
TASK = "rename the loader module"
#: The heading only the lesson block carries. The marker alone also sits in the conversation history
#: (turn one's answer), so a lesson is only "in the prompt" when both appear in one message.
HEADER = "Lessons from past attempts on similar tasks"


class _Gateway:
    """The first call answers with the lesson; every later call answers plainly.

    ``calls`` keeps the text of every message of every call, so the test can see what a turn's
    prompt carried, wherever the turn composes it (the Code turn uses the turn context).
    """

    first = True
    calls: list[list[str]] = []

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def complete(self, messages: list[Any], **_kwargs: Any) -> CompletionResult:
        texts = []
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else message.content
            texts.append(str(content))
        _Gateway.calls.append(texts)
        content = LESSON if _Gateway.first else "ok"
        _Gateway.first = False
        return CompletionResult(content=content, model="test/turn", prompt_tokens=10, completion_tokens=5)


def _prompt_carried_a_lesson(calls: list[list[str]]) -> bool:
    return any(HEADER in text and LESSON in text for call in calls for text in call)


def _done(text: str) -> dict[str, Any]:
    event, out = "", {}
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: ") and event == "done":
            out = json.loads(line[len("data: "):])
    return out


def _agent() -> Any:
    from chimera.core import Agent

    return Agent(_Gateway(), ToolRegistry())


def _turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, skill_cards: bool) -> dict[str, Any]:
    from chimera.api import build_api_app

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_PREFIX_NONCE", "")
    monkeypatch.setenv("CHIMERA_SKILL_CARDS", "1" if skill_cards else "0")
    get_settings.cache_clear()
    monkeypatch.setattr("chimera.providers.LLMGateway", _Gateway)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_SKILL_CARDS=skill_cards)  # type: ignore[call-arg]
    client = TestClient(build_api_app(lambda: ChatSession(_agent()), workspace=ws, settings=settings))
    return _done(client.post("/api/code/turn", json={"message": TASK, "stream": False}).text)


def _code_lessons(tmp_path: Path) -> list[Any]:
    return ExperienceBuffer(tmp_path / "home" / "code_lessons.json").all()


def test_a_turn_records_its_lesson_and_the_default_does_not_read_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Gateway.first = True
    _Gateway.calls = []
    _turn(tmp_path, monkeypatch, skill_cards=False)

    # No check ran, so the lesson is unverified: recorded, and not counted as a success.
    assert [(e.outcome, LESSON in e.detail) for e in _code_lessons(tmp_path)] == [("unverified", True)]

    _Gateway.calls = []
    _turn(tmp_path, monkeypatch, skill_cards=False)

    assert not _prompt_carried_a_lesson(_Gateway.calls), (
        "with the flag off the lesson is recorded and never reaches a prompt"
    )


def test_with_the_flag_on_the_next_turn_prompt_carries_the_lesson(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Gateway.first = True
    _Gateway.calls = []
    _turn(tmp_path, monkeypatch, skill_cards=True)
    assert any(LESSON in e.detail for e in _code_lessons(tmp_path))

    _Gateway.calls = []
    _turn(tmp_path, monkeypatch, skill_cards=True)

    assert _prompt_carried_a_lesson(_Gateway.calls), (
        "the same task, recorded a turn earlier, comes back in the next turn's prompt"
    )


# --- the recording rules, unit-level -----------------------------------------------------------------


def _record(*args: Any, **kwargs: Any) -> Any:
    from chimera.evolution.code_turn_learning import record_turn_lesson

    return record_turn_lesson(*args, **kwargs)


def test_a_passed_check_records_a_success(tmp_path: Path) -> None:
    exp = _record(tmp_path, task=TASK, answer="done", verdict="passed", tainted=False, steps=2)

    assert exp is not None and exp.outcome == "success"


def test_a_failed_check_records_a_failure(tmp_path: Path) -> None:
    exp = _record(tmp_path, task=TASK, answer="done", verdict="failed", tainted=False, steps=2)

    assert exp is not None and exp.outcome == "failure"


def test_a_turn_with_no_check_is_recorded_as_unverified_and_labelled(tmp_path: Path) -> None:
    exp = _record(tmp_path, task=TASK, answer="done", verdict="none", tainted=False, steps=2)

    assert exp is not None and exp.outcome == "unverified"
    assert exp.detail.startswith("[check: none]")


def test_an_unverified_turn_does_not_count_toward_recurrence(tmp_path: Path) -> None:
    """Self-reported success must not mint a card: only a passed or failed check is evidence."""
    from chimera.core.autonomous import AutonomousAgent

    _record(tmp_path, task=TASK, answer="done", verdict="none", tainted=False, steps=2)
    agent = object.__new__(AutonomousAgent)
    agent.experience = ExperienceBuffer(tmp_path / "code_lessons.json")

    assert AutonomousAgent._count_prior_successes(agent, TASK) == 0


def test_a_turn_that_never_ran_a_loop_records_nothing(tmp_path: Path) -> None:
    """A gate that stopped the turn before the model ran has nothing to learn from."""
    assert _record(tmp_path, task=TASK, answer="", verdict="", tainted=False, steps=0) is None
    assert not (tmp_path / "code_lessons.json").exists()


def test_taint_is_kept_on_the_lesson(tmp_path: Path) -> None:
    exp = _record(tmp_path, task=TASK, answer="done", verdict="passed", tainted=True, steps=1)

    assert exp is not None and exp.provenance == "tainted"


def test_the_stored_task_and_answer_are_capped(tmp_path: Path) -> None:
    exp = _record(tmp_path, task="x" * 1000, answer="y" * 2000, verdict="passed", tainted=False, steps=1)

    assert exp is not None
    assert len(exp.task) <= 300
    assert len(exp.detail) <= 500


def test_the_code_recall_reads_the_flag_only_through_its_own_object(tmp_path: Path) -> None:
    """With no cards and no lessons there is nothing to add, and the recall says so with an empty string."""
    assert CodeTurnRecall(None, tmp_path).card_context(TASK) == ""
