"""Migration framework: bring config, skills and memory from another agent.

``scan`` produces a non-destructive preview (dry-run); ``apply`` writes the imported
artifacts under the Chimera home. Long-term *memory merge* is intentionally deferred
to M4 (it reuses the Memory Manager): for now memory files are detected and reported,
never silently dropped.
"""

from __future__ import annotations

import json
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from chimera.telemetry import get_logger

_log = get_logger("migration.base")
_MEMORY_DEFERRED = "long-term memory merge is deferred to M4 (Memory Manager)"


class MigrationResult(BaseModel):
    """A preview or record of what a migration imported."""

    source: str
    home: str
    dry_run: bool = True
    default_model: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list)
    memory_files: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class Importer(ABC):
    """Reads another agent's home directory and maps it to Chimera."""

    source: str

    def __init__(self, home: Path) -> None:
        self.home = Path(home)

    @abstractmethod
    def scan(self) -> MigrationResult:
        """Return a dry-run preview of what would be imported."""
        raise NotImplementedError

    @abstractmethod
    def skill_sources(self) -> dict[str, Path]:
        """Map skill name -> path to copy on apply."""
        raise NotImplementedError

    def apply(self, target_home: Path) -> MigrationResult:
        """Write imported config + skills under ``target_home/imported/<source>``."""
        result = self.scan()
        result.dry_run = False
        dest = Path(target_home) / "imported" / self.source
        (dest / "skills").mkdir(parents=True, exist_ok=True)

        (dest / "config.json").write_text(
            json.dumps(result.config, indent=2), encoding="utf-8"
        )
        for name, src in self.skill_sources().items():
            target = dest / "skills" / name
            if src.is_dir():
                shutil.copytree(src, target, dirs_exist_ok=True)
            elif src.is_file():
                shutil.copy2(src, target if target.suffix else target.with_suffix(src.suffix))
        result.notes.append(f"imported into {dest}")
        _log.debug("applied migration from %s", self.home)
        return result


class DirectoryImporter(Importer):
    """Shared logic for agents that keep a config file, a skills dir and memory files."""

    config_files: tuple[str, ...] = ()
    skills_dirs: tuple[str, ...] = ()
    memory_candidates: tuple[str, ...] = ()
    model_keys: tuple[tuple[str, ...], ...] = ()

    def _find_first(self, names: tuple[str, ...]) -> Path | None:
        for name in names:
            candidate = self.home / name
            if candidate.exists():
                return candidate
        return None

    def _read_config(self) -> dict[str, Any]:
        path = self._find_first(self.config_files)
        if path is None:
            return {}
        text = path.read_text(encoding="utf-8")
        try:
            data = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
        except (json.JSONDecodeError, yaml.YAMLError):
            return {}
        return data if isinstance(data, dict) else {}

    def _extract_model(self, config: dict[str, Any]) -> str | None:
        for path in self.model_keys:
            node: Any = config
            for key in path:
                if isinstance(node, dict) and key in node:
                    node = node[key]
                else:
                    node = None
                    break
            if isinstance(node, str) and node:
                return node
        return None

    def _skills_dir(self) -> Path | None:
        return self._find_first(self.skills_dirs)

    def skill_sources(self) -> dict[str, Path]:
        skills_dir = self._skills_dir()
        if skills_dir is None or not skills_dir.is_dir():
            return {}
        sources: dict[str, Path] = {}
        for entry in sorted(skills_dir.iterdir()):
            if entry.name.startswith("."):
                continue
            sources[entry.stem if entry.is_file() else entry.name] = entry
        return sources

    def _memory_files(self) -> list[str]:
        found: list[str] = []
        for name in self.memory_candidates:
            candidate = self.home / name
            if candidate.exists():
                found.append(name)
        return found

    def scan(self) -> MigrationResult:
        config = self._read_config()
        default_model = self._extract_model(config)
        skills = sorted(self.skill_sources())
        memory_files = self._memory_files()
        notes: list[str] = []
        if memory_files:
            notes.append(_MEMORY_DEFERRED)
        if not config:
            notes.append("no config file found")
        return MigrationResult(
            source=self.source,
            home=str(self.home),
            dry_run=True,
            default_model=default_model,
            config=config,
            skills=skills,
            memory_files=memory_files,
            notes=notes,
        )
