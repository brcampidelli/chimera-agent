"""Memory extraction is on by default, and the messaging bots still never extract.

The owner switched `CHIMERA_MEMORY_EXTRACT` on after `bench/memory_extraction/RESULTS.md`: 31 of 31
saves correct, 0 of 16 planted facts saved, 33 of 36 stated facts kept. What is pinned here is that
the default says so, that `CHIMERA_MEMORY_EXTRACT=0` still turns it off, that the surfaces which
answer the owner pick the default up without being told, and that a bot, where anyone who can reach
it would be writing the owner's memory, does not.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.config import Settings
from chimera.memory.extract import MemoryExtractor
from chimera.memory.manager import MemoryManager
from chimera.memory.store import MemoryStore


@pytest.fixture(autouse=True)
def _no_extract_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Own the name, so a developer's shell cannot decide what "the default" reads as."""
    monkeypatch.setenv("CHIMERA_MEMORY_EXTRACT", "")
    monkeypatch.delenv("CHIMERA_MEMORY_EXTRACT")


def _memory(tmp_path: Path) -> MemoryManager:
    return MemoryManager(MemoryStore(tmp_path / "memory.json"))


def test_the_default_is_on(tmp_path: Path) -> None:
    assert Settings(CHIMERA_HOME=str(tmp_path)).memory_extract is True


@pytest.mark.parametrize("value", ["0", "false", "off"])
def test_it_can_still_be_turned_off(tmp_path: Path, value: str) -> None:
    assert Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_MEMORY_EXTRACT=value).memory_extract is False


def test_a_terminal_conversation_gets_an_extractor_by_default(tmp_path: Path) -> None:
    from chimera.cli.main import _memory_extractor

    on = _memory_extractor(Settings(CHIMERA_HOME=str(tmp_path)), _memory(tmp_path), "t-1")
    off = _memory_extractor(
        Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_MEMORY_EXTRACT="0"), _memory(tmp_path), "t-1"
    )

    assert isinstance(on, MemoryExtractor) and on.usage_id == "t-1"
    assert off is None


def test_the_code_turn_extracts_with_nothing_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.core
    import chimera.memory.extract as module
    from chimera.api import build_api_app
    from chimera.config import get_settings
    from chimera.interface import ChatSession
    from tests.test_memory_extraction_never_costs_the_turn import _Agent, _frames

    handed: list[str] = []

    class _Fake:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def after_turn(self, user_message: str, answer: str, *, tainted: bool = False) -> None:
            handed.append(user_message)

    monkeypatch.setattr(module, "MemoryExtractor", _Fake)
    monkeypatch.setattr(chimera.core, "Agent", _Agent, raising=True)
    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    ws = tmp_path / "ws"
    ws.mkdir()
    # No CHIMERA_MEMORY_EXTRACT anywhere: the shipped default decides.
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    from fastapi.testclient import TestClient

    app = build_api_app(lambda: ChatSession(_Agent()), workspace=ws, settings=settings,
                        memory=_memory(tmp_path))
    frames = _frames(TestClient(app).post("/api/code/turn", json={"message": "I'm vegetarian"}))

    assert "done" in frames
    assert handed == ["I'm vegetarian"]


def test_the_in_app_bot_does_not_extract_even_with_the_default_on(tmp_path: Path) -> None:
    """The bot is given a memory to recall from, and still no extractor."""
    from chimera.server import MessagingManager
    from tests.test_messaging_manager import _FakeAdapter, _FakeBackend

    settings = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_DISCORD_BOT_TOKEN="t")
    assert settings.memory_extract is True
    adapter = _FakeAdapter()
    manager = MessagingManager(
        settings=settings, backend=_FakeBackend(), model=None, max_steps=4, workspace=tmp_path,
        memory=_memory(tmp_path), adapter_factory=lambda _p: adapter,
    )

    session = manager._gateway_on_message(adapter).__self__.session_for("chat")  # type: ignore[attr-defined]

    assert session.memory is not None
    assert session.extractor is None
