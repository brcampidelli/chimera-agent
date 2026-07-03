"""Persistence for self-authored (learned) skills."""

from __future__ import annotations

import json
from pathlib import Path

from chimera.evolution.learned_skill import LearnedSkill
from chimera.providers.gateway import SupportsComplete


class SkillStore:
    """A JSON-file store of learned skills (deduped by name)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._dicts: dict[str, dict[str, object]] = {}
        self.load()

    def load(self) -> None:
        self._dicts = {}
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        for item in raw:
            self._dicts[str(item["name"])] = item

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(list(self._dicts.values()), indent=2), encoding="utf-8")

    def add(self, skill: LearnedSkill) -> None:
        self._dicts[skill.name] = skill.to_dict()
        self.save()

    def names(self) -> list[str]:
        return list(self._dicts)

    def labels(self) -> list[str]:
        """"name description" strings for each skill (e.g. for coverage checks)."""
        return [f"{d.get('name', '')} {d.get('description', '')}".strip() for d in self._dicts.values()]

    def __len__(self) -> int:
        return len(self._dicts)

    def __contains__(self, name: object) -> bool:
        return name in self._dicts

    def skills(
        self, backend: SupportsComplete | None = None, model: str | None = None
    ) -> list[LearnedSkill]:
        return [LearnedSkill.from_dict(d, backend=backend, model=model) for d in self._dicts.values()]
