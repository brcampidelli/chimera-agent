"""Settings-derived constructors for the learning seams that carry persistent state (M19-A4).

Lifted out of the CLI so every autonomous path — the kanban lanes, workflow executors, the SDLC
lifecycle crew, the project orchestrator — builds the SAME long-term memory backend and ACE
playbook the ``solve`` command does, instead of each re-implementing it or (worse) skipping it and
never learning. The CLI helpers now delegate here, so there is one source of truth for *where* the
memory/playbook live and *how* they are constructed.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from chimera.core.filelock import atomic_write_text, locked, read_text
from chimera.telemetry import get_logger

_log = get_logger("evolution.wiring")

if TYPE_CHECKING:
    from pathlib import Path

    from chimera.config import Settings
    from chimera.evolution.playbook import Playbook
    from chimera.memory import EmbedFn, MemoryManager


def semantic_embed(settings: Settings) -> EmbedFn | None:
    """The gateway embedder when semantic memory is on, else None (keyword recall)."""
    if not settings.semantic_memory:
        return None
    from chimera.providers import LLMGateway

    return LLMGateway(settings).embed


def build_memory_manager(settings: Settings) -> MemoryManager:
    """The long-term memory manager for this home (sqlite or json backend, semantic if configured)."""
    from chimera.memory import MemoryManager, MemoryStore, SqliteMemoryStore

    embed = semantic_embed(settings)
    if settings.memory_backend == "sqlite":
        return MemoryManager(SqliteMemoryStore(settings.home / "memory.db"), embed=embed)
    return MemoryManager(MemoryStore(settings.home / "memory.json"), embed=embed)


def playbook_path(settings: Settings) -> Path:
    return settings.home / "playbook.json"


def load_playbook(settings: Settings) -> Playbook:
    """Load the persisted ACE playbook for this home (empty when none exists yet).

    A corrupt file degrades to an empty playbook rather than raising. `build_evolution_context`
    loads this before any work starts, so an unreadable file did not cost the playbook — it stopped
    the next run from starting at all, which is a strictly worse trade for advisory content.
    """
    from chimera.evolution.playbook import Playbook

    path = playbook_path(settings)
    raw = read_text(path)
    if not raw.strip():
        return Playbook()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _log.warning("playbook at %s is not readable JSON: %s", path, exc)
        return Playbook()
    if not isinstance(data, dict):
        _log.warning("playbook at %s is not an object; ignoring", path)
        return Playbook()
    try:
        return Playbook.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        _log.warning("playbook at %s has an unreadable shape: %s", path, exc)
        return Playbook()


def save_playbook(settings: Settings, playbook: Playbook) -> None:
    """Persist the ACE playbook for this home, atomically and under a lock.

    This runs after EVERY `solve`, and the whole playbook is one JSON array — so a bare
    `write_text` that died mid-write did not cost the last bullet, it cost all of them. Two
    processes doing it at once cost one of the two sets, silently. Same shape and same reasoning
    as the memory, experience, trajectory and skill stores.
    """
    path = playbook_path(settings)
    with locked(path):
        atomic_write_text(path, json.dumps(playbook.to_dict(), indent=2))
