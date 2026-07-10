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
    """Load the persisted ACE playbook for this home (empty when none exists yet)."""
    from chimera.evolution.playbook import Playbook

    path = playbook_path(settings)
    if not path.exists():
        return Playbook()
    return Playbook.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_playbook(settings: Settings, playbook: Playbook) -> None:
    """Persist the ACE playbook for this home."""
    path = playbook_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(playbook.to_dict(), indent=2), encoding="utf-8")
