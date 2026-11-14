"""Settings-derived constructors for the learning seams that carry persistent state (M19-A4).

Lifted out of the CLI so every autonomous path — the kanban lanes, workflow executors, the SDLC
lifecycle crew, the project orchestrator — builds the SAME long-term memory backend and ACE
playbook the ``solve`` command does, instead of each re-implementing it or (worse) skipping it and
never learning. The CLI helpers now delegate here, so there is one source of truth for *where* the
memory/playbook live and *how* they are constructed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from chimera.core.filelock import atomic_write_text, locked, read_text
from chimera.telemetry import get_logger

_log = get_logger("evolution.wiring")

if TYPE_CHECKING:
    from pathlib import Path

    from chimera.config import Settings
    from chimera.evolution.playbook import Playbook
    from chimera.memory import EmbedFn, MemoryManager


def semantic_embed(settings: Settings, *, force: bool = False) -> EmbedFn | None:
    """The gateway embedder when semantic memory is on, else None (keyword recall).

    ``force`` is for a caller who asked for embeddings in so many words — `chimera find --semantic`
    — rather than inheriting the memory setting. The two are different decisions: turning on
    semantic *memory* is a standing choice about every run, and typing `--semantic` on one search is
    a choice about that search. Reading the first as consent to the second would put a bill on a
    flag the user did not set.
    """
    if not settings.semantic_memory and not force:
        return None
    from chimera.providers import LLMGateway

    return LLMGateway(settings).embed


def build_memory_manager(settings: Settings) -> MemoryManager:
    """The long-term memory manager for this home (sqlite or json backend, semantic if configured).

    The store comes from `chimera.memory.backend.open_memory_store` — the one place that resolves
    the backend, imports an existing `memory.json` into a new `memory.db` once, and refuses to
    shadow a file it could not read. Every surface that builds a manager goes through here or
    through that function, so no two screens can read two stores again.

    The manager chains its writes into the home's audit log (study 31, G31-06): memory is the
    writer with the longest reach, and its writes were the ones with no hashed record.
    """
    from chimera.governance.audit import AuditLog
    from chimera.memory import MemoryManager
    from chimera.memory.backend import open_memory_store
    from chimera.memory.consolidate import ConsolidationLedger

    home = Path(settings.home)
    return MemoryManager(
        open_memory_store(settings),
        embed=semantic_embed(settings),
        audit=AuditLog(home / "audit.jsonl"),
        # Beside the store, so what was settled by one process is known to the next: the manager is
        # rebuilt per request and per session end, and an in-memory ledger would never be seen twice.
        ledger=ConsolidationLedger(home / "memory_consolidation.json"),
        # Vectors keyed by text hash and embedding model: a search after the first embeds only the
        # query, and a changed `embed_model` cannot be answered with the old model's vectors.
        vector_cache=home / "memory_vectors.db",
        vector_namespace=str(settings.embed_model),
    )


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
