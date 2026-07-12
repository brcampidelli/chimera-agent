"""Migration framework: bring config, skills and memory from another agent.

``scan`` produces a non-destructive preview (dry-run); ``apply`` writes the imported
artifacts under the Chimera home and — when given a :class:`MemoryManager` — *merges*
the source agent's long-term memory into Chimera's, deduping rather than overwriting.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from chimera.memory.manager import MemoryManager
from chimera.memory.models import MemoryItem
from chimera.telemetry import get_logger

_log = get_logger("migration.base")


def _taint_imported_skills(skills_dir: Path) -> None:
    """Stamp every imported SKILL.md with ``provenance=tainted`` — the import boundary owns the
    security label, not the untrusted source file. ``skills-import`` then holds a foreign skill
    ``pending`` until a human approves it, instead of admitting an attacker's ``provenance: clean``.
    """
    if not skills_dir.exists():
        return
    from chimera.skills.skill_md import parse_skill_md, render_skill_md

    candidates = list(skills_dir.rglob("SKILL.md")) + [
        p for p in skills_dir.glob("*.md") if p.name != "SKILL.md"
    ]
    for md in candidates:
        try:
            skill = parse_skill_md(md.read_text(encoding="utf-8", errors="replace"))
            skill.manifest.provenance = "tainted"
            md.write_text(render_skill_md(skill), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — a non-skill .md must not break the import
            _log.debug("could not taint-stamp %s: %s", md, exc)
_MEMORY_NOTE = "memory files found; run --apply to merge into Chimera memory (dedup, non-destructive)"
_BULLET = re.compile(r"^[-*]\s+")


class MigrationResult(BaseModel):
    """A preview or record of what a migration imported."""

    source: str
    home: str
    dry_run: bool = True
    default_model: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list)
    memory_files: list[str] = Field(default_factory=list)
    memory_merged: dict[str, int] | None = None
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

    @abstractmethod
    def memory_items(self) -> list[MemoryItem]:
        """Parse the source agent's long-term memory files into items."""
        raise NotImplementedError

    def apply(
        self, target_home: Path, *, memory_manager: MemoryManager | None = None
    ) -> MigrationResult:
        """Write imported config + skills; optionally merge memory (deduped)."""
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
                # symlinks=True: preserve links as links instead of dereferencing them — a crafted
                # source skill dir could otherwise symlink to ~/.ssh/id_rsa and copy its CONTENT in.
                shutil.copytree(src, target, dirs_exist_ok=True, symlinks=True)
            elif src.is_file() and not src.is_symlink():  # skip a symlinked skill file (no deref)
                shutil.copy2(src, target if target.suffix else target.with_suffix(src.suffix))
        _taint_imported_skills(dest / "skills")  # foreign skills stay pending until a human approves
        result.notes.append(f"imported into {dest}")

        if memory_manager is not None:
            items = self.memory_items()
            result.memory_merged = memory_manager.merge(items)
            result.notes.append(f"merged {len(items)} memory item(s): {result.memory_merged}")

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
        text = path.read_text(encoding="utf-8", errors="replace")  # non-UTF-8 must not crash the scan
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
        # Dedup by resolved path: on case-insensitive filesystems (Windows/macOS) two
        # candidates like "MEMORY.md" and "memory.md" hit the SAME file — without this
        # the preview lists it twice and apply parses its items twice.
        found: list[str] = []
        seen: set[str] = set()
        for name in self.memory_candidates:
            candidate = self.home / name
            if candidate.exists():
                real = str(candidate.resolve())
                if real not in seen:
                    seen.add(real)
                    found.append(name)
        return found

    def memory_items(self) -> list[MemoryItem]:
        items: list[MemoryItem] = []
        for name in self._memory_files():
            # errors="replace": a non-UTF-8 memory file must not crash the import mid-write.
            text = (self.home / name).read_text(encoding="utf-8", errors="replace")
            for raw in text.splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                content = _BULLET.sub("", line)
                items.append(
                    MemoryItem(
                        id=uuid.uuid4().hex,  # full uuid — no 8-char collision-overwrite
                        kind="semantic",
                        content=content,
                        source=self.source,
                        # SECURITY: imported memory is FOREIGN, unvetted content — it crosses a trust
                        # boundary, so it must be tainted. Recall then surfaces it as [unverified],
                        # and it can never launder itself into a "clean" fact.
                        provenance="tainted",
                        metadata={"file": name},
                    )
                )
        return items

    def scan(self) -> MigrationResult:
        config = self._read_config()
        default_model = self._extract_model(config)
        skills = sorted(self.skill_sources())
        memory_files = self._memory_files()
        notes: list[str] = []
        if memory_files:
            notes.append(_MEMORY_NOTE)
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
