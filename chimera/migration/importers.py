"""Concrete importers for Hermes Agent and OpenClaw."""

from __future__ import annotations

from pathlib import Path

from chimera.migration.base import DirectoryImporter, Importer


class HermesImporter(DirectoryImporter):
    """Import from a Hermes Agent home (Python; config.yaml, skills/, MEMORY.md)."""

    source = "hermes"
    config_files = ("config.yaml", "config.yml")
    skills_dirs = ("skills",)
    memory_candidates = ("MEMORY.md", "USER.md", "memories/MEMORY.md", "memories/USER.md")
    model_keys = (("model", "default"),)


class OpenClawImporter(DirectoryImporter):
    """Import from an OpenClaw home (TS; config.json, skills/, MEMORY.md)."""

    source = "openclaw"
    config_files = ("config.json", "openclaw.json", "config.yaml")
    skills_dirs = ("skills",)
    memory_candidates = ("MEMORY.md", "memory.md")
    model_keys = (("model",), ("defaultModel",), ("model", "default"))


_IMPORTERS: dict[str, type[DirectoryImporter]] = {
    HermesImporter.source: HermesImporter,
    OpenClawImporter.source: OpenClawImporter,
}


def available_sources() -> list[str]:
    return sorted(_IMPORTERS)


def get_importer(source: str, home: Path) -> Importer:
    """Return an importer for ``source`` rooted at ``home``."""
    try:
        importer_cls = _IMPORTERS[source]
    except KeyError as exc:
        raise ValueError(
            f"unknown migration source {source!r}; available: {available_sources()}"
        ) from exc
    return importer_cls(home)
