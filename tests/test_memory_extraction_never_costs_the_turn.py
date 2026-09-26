"""Memory extraction runs after the answer, and nothing it does can delay or fail the turn.

Study 25 S13. The extraction is one more model call per turn, and a model call can be slow, refuse,
or return nothing parseable. The answer is the product; memory is extra. So the turn records and
returns its answer whatever the extractor does, the background thread returns at once, and the
whole thing is off unless ``CHIMERA_MEMORY_EXTRACT`` turns it on — on every surface, the Code turn
included.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.core.agent import AgentResult
from chimera.interface import ChatSession
from chimera.memory.extract import MemoryExtractor
from chimera.memory.manager import MemoryManager
from chimera.memory.store import MemoryStore


class _Agent:
    def __init__(self, *_a: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config") or (_a[2] if len(_a) > 2 else None)

    def run(self, task: str, **_kw: Any) -> AgentResult:
        return AgentResult(answer="Crackers are fine.", steps=1, stopped_reason="final",
                           transcript=[{"role": "user", "content": task},
                                       {"role": "assistant", "content": "Crackers are fine."}])


class _Failing:
    def complete(self, messages: Any, **kwargs: Any) -> Any:
        raise RuntimeError("provider is down")


class _Recording:
    """An extractor stand-in that remembers what it was handed."""

    def __init__(self) -> None:
        self.turns: list[tuple[str, str, bool]] = []

    def after_turn(self, user_message: str, answer: str, *, tainted: bool = False) -> None:
        self.turns.append((user_message, answer, tainted))


def _memory(tmp_path: Path) -> MemoryManager:
    return MemoryManager(MemoryStore(tmp_path / "memory.json"))


def test_a_failing_extraction_leaves_the_answer_and_the_transcript_alone(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    extractor = MemoryExtractor(memory, _Failing(), background=False)
    session = ChatSession(_Agent(), memory=memory, extractor=extractor)

    assert session.send("I'm allergic to peanuts, snack ideas?") == "Crackers are fine."
    report = session.send_verbose("and for dinner?")

    assert report.answer == "Crackers are fine."
    assert [t.user for t in session.turns] == ["I'm allergic to peanuts, snack ideas?",
                                                "and for dinner?"]
    assert extractor.last is not None and "provider is down" in extractor.last.error
    assert memory.store.all() == []


def test_an_extractor_that_raises_on_hand_over_does_not_reach_the_caller(tmp_path: Path) -> None:
    class _Broken:
        def after_turn(self, *_a: Any, **_k: Any) -> None:
            raise RuntimeError("could not start a thread")

    session = ChatSession(_Agent(), extractor=_Broken())

    assert session.send("hello") == "Crackers are fine."


def test_the_background_extraction_returns_before_the_model_answers(tmp_path: Path) -> None:
    release = threading.Event()

    class _Slow:
        def complete(self, messages: Any, **kwargs: Any) -> Any:
            release.wait(5)
            raise RuntimeError("late")

    extractor = MemoryExtractor(_memory(tmp_path), _Slow())
    started = time.monotonic()
    extractor.after_turn("I'm allergic to peanuts", "Noted.")
    elapsed = time.monotonic() - started
    release.set()

    assert elapsed < 1.0


def test_the_session_hands_over_each_turn_with_its_provenance() -> None:
    recording = _Recording()
    session = ChatSession(_Agent(), extractor=recording)

    session.send("I'm vegetarian")
    session.send_verbose("what can I cook?")

    assert recording.turns == [
        ("I'm vegetarian", "Crackers are fine.", False),
        ("what can I cook?", "Crackers are fine.", False),
    ]


def test_an_empty_answer_or_message_is_not_extracted(tmp_path: Path) -> None:
    class _Counting:
        calls = 0

        def complete(self, messages: Any, **kwargs: Any) -> Any:
            _Counting.calls += 1
            raise AssertionError("must not be called")

    extractor = MemoryExtractor(_memory(tmp_path), _Counting(), background=False)
    extractor.after_turn("   ", "an answer")
    extractor.after_turn("a message", "")

    assert _Counting.calls == 0


def test_without_an_extractor_the_session_is_what_it_was() -> None:
    assert ChatSession(_Agent()).extractor is None
    assert Settings().memory_extract is False


# ------------------------------------------------------------------------------ the Code turn


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, extract: bool,
            memory: MemoryManager) -> tuple[TestClient, list[Any]]:
    import chimera.core
    from chimera.api import build_api_app
    from chimera.config import get_settings

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    built: list[Any] = []

    def agent(*args: Any, **kwargs: Any) -> _Agent:
        made = _Agent(*args, **kwargs)
        built.append(made)
        return made

    monkeypatch.setattr(chimera.core, "Agent", agent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_EXTRACT=extract,
                        CHIMERA_MEMORY_BACKEND="json")
    app = build_api_app(lambda: ChatSession(_Agent()), workspace=ws, settings=settings,
                        memory=memory)
    return TestClient(app), built


def _frames(response: Any) -> dict[str, Any]:
    event, out = "", {}
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            out[event] = json.loads(line[len("data: "):])
    return out


@pytest.mark.parametrize("extract", [False, True])
def test_the_code_turn_extracts_only_when_the_setting_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extract: bool
) -> None:
    import chimera.memory.extract as module

    handed: list[tuple[str, str, bool]] = []

    class _Fake:
        def __init__(self, memory: Any, *_a: Any, **_k: Any) -> None:
            pass

        def after_turn(self, user_message: str, answer: str, *, tainted: bool = False) -> None:
            handed.append((user_message, answer, tainted))

    monkeypatch.setattr(module, "MemoryExtractor", _Fake)
    client, _built = _client(tmp_path, monkeypatch, extract=extract, memory=_memory(tmp_path))

    frames = _frames(client.post("/api/code/turn", json={"message": "I'm allergic to peanuts"}))

    assert frames["done"]["answer"] == "Crackers are fine."
    expected = [("I'm allergic to peanuts", "Crackers are fine.", False)] if extract else []
    assert handed == expected


def test_a_failing_extraction_does_not_fail_the_code_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.memory.extract as module

    class _Exploding:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            raise RuntimeError("no key")

    monkeypatch.setattr(module, "MemoryExtractor", _Exploding)
    client, _built = _client(tmp_path, monkeypatch, extract=True, memory=_memory(tmp_path))

    frames = _frames(client.post("/api/code/turn", json={"message": "I'm allergic to peanuts"}))

    assert frames["done"]["answer"] == "Crackers are fine."
    assert "error" not in frames
