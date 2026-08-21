"""The Memory screen and the chat have to be looking at the same store.

`chimera app` builds ONE memory manager at boot and hands the same object to every conversational
surface. The Memory screen builds a fresh one per request, from live settings. So switching
`CHIMERA_MEMORY_BACKEND` from json to sqlite left that screen showing the new store — usually empty
— while every coding turn kept recalling from the old one. Two screens, two stores, one app, and
nothing on either saying so.
"""

from __future__ import annotations

from pathlib import Path

from chimera.api.code_api import _live_memory, memory_key
from chimera.config import Settings


def _settings(tmp_path: Path, backend: str = "json") -> Settings:
    return Settings(  # type: ignore[call-arg]
        CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_MEMORY_BACKEND=backend
    )


class _Boot:
    """Stands in for the manager the app built at launch."""


def test_nothing_changed_means_the_exact_object_the_app_booted_with(tmp_path: Path) -> None:
    """An install that changes no setting must not pay for a rebuild, or get a different store."""
    settings = _settings(tmp_path)
    boot, graph = _Boot(), object()

    got, got_graph = _live_memory(settings, boot, graph, memory_key(settings))

    assert got is boot and got_graph is graph


def test_switching_the_backend_switches_what_the_turn_reads(tmp_path: Path) -> None:
    booted_with = _settings(tmp_path, "json")
    now = _settings(tmp_path, "sqlite")

    got, _graph = _live_memory(now, _Boot(), object(), memory_key(booted_with))

    assert not isinstance(got, _Boot), "the turn must read the store the settings describe"
    assert type(got).__name__ == "MemoryManager"


def test_the_switched_store_is_built_once_not_per_turn(tmp_path: Path) -> None:
    """The entity graph walks every stored memory. Rebuilding it per question would put a cost on
    every question that grows with how much the agent has learned."""
    booted_with = memory_key(_settings(tmp_path, "json"))
    now = _settings(tmp_path, "sqlite")

    first, _ = _live_memory(now, _Boot(), object(), booted_with)
    second, _ = _live_memory(now, _Boot(), object(), booted_with)

    assert first is second


def test_no_memory_stays_no_memory(tmp_path: Path) -> None:
    """`--no-memory` is a launch decision. There is no store to switch to."""
    assert _live_memory(_settings(tmp_path, "sqlite"), None, None, ("x", "json", False)) == (
        None,
        None,
    )
