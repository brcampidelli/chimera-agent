"""Migration: import config, memory and skills from other agents (Hermes/OpenClaw).

Long-term memory is *merged* with existing history (never overwritten) — that merge
lands in M4. v1 imports config + skills and reports detected memory files.
"""

from chimera.migration.base import DirectoryImporter, Importer, MigrationResult
from chimera.migration.importers import (
    HermesImporter,
    OpenClawImporter,
    available_sources,
    get_importer,
)

__all__ = [
    "Importer",
    "DirectoryImporter",
    "MigrationResult",
    "HermesImporter",
    "OpenClawImporter",
    "available_sources",
    "get_importer",
]
