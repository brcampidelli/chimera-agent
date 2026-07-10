"""Persistence for self-authored (learned) skills."""

from __future__ import annotations

import json
from pathlib import Path

from chimera.evolution.learned_skill import LearnedSkill
from chimera.providers.gateway import SupportsComplete


def _as_int(value: object) -> int:
    """Coerce a JSON-loaded counter to int (missing/odd values count as 0)."""
    return value if isinstance(value, int) else 0


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
        entry = skill.to_dict()
        # Usage counters are store-level state (not part of the skill definition):
        # re-adding/refining a skill must not wipe its measured track record.
        previous = self._dicts.get(skill.name)
        if previous is not None:
            entry["uses"] = previous.get("uses", 0)
            entry["successes"] = previous.get("successes", 0)
        self._dicts[skill.name] = entry
        self.save()

    def record_use(self, name: str, *, success: bool) -> None:
        """Count one retrieval-into-a-run for a skill and whether that run succeeded."""
        entry = self._dicts.get(name)
        if entry is None:
            return
        entry["uses"] = _as_int(entry.get("uses")) + 1
        if success:
            entry["successes"] = _as_int(entry.get("successes")) + 1
        self.save()

    def stats(self) -> list[dict[str, object]]:
        """Per-skill usage stats: name, status, provenance, uses, successes, rate."""
        rows: list[dict[str, object]] = []
        for entry in self._dicts.values():
            uses = _as_int(entry.get("uses"))
            successes = _as_int(entry.get("successes"))
            rows.append(
                {
                    "name": entry.get("name", ""),
                    "kind": entry.get("kind", "pattern"),
                    "status": entry.get("status", "active"),
                    "provenance": entry.get("provenance", "clean"),
                    "uses": uses,
                    "successes": successes,
                    "rate": round(successes / uses, 3) if uses else None,
                }
            )
        return rows

    def retirement_candidates(self, *, min_uses: int = 5, max_rate: float = 1 / 3) -> list[str]:
        """Skills with enough uses and a low win rate — SIGNALED for pruning, never deleted.

        Feeds the anti-stagnation loop: a skill that keeps being retrieved but doesn't
        move outcomes is the first candidate to retire or rewrite.
        """
        names: list[str] = []
        for row in self.stats():
            uses = _as_int(row["uses"])
            rate = row["rate"]
            if uses >= min_uses and isinstance(rate, float) and rate <= max_rate:
                names.append(str(row["name"]))
        return names

    def names(self) -> list[str]:
        return list(self._dicts)

    def get(self, name: str) -> LearnedSkill | None:
        """Return one learned skill by name (for export/inspection), or None if absent."""
        entry = self._dicts.get(name)
        return LearnedSkill.from_dict(entry) if entry is not None else None

    def labels(self) -> list[str]:
        """"name description" strings for each skill (e.g. for coverage checks)."""
        return [f"{d.get('name', '')} {d.get('description', '')}".strip() for d in self._dicts.values()]

    def __len__(self) -> int:
        return len(self._dicts)

    def __contains__(self, name: object) -> bool:
        return name in self._dicts

    def skills(
        self,
        backend: SupportsComplete | None = None,
        model: str | None = None,
        *,
        status: str | None = None,
    ) -> list[LearnedSkill]:
        """All skills, or only those with the given ``status`` ("active" / "pending")."""
        loaded = [
            LearnedSkill.from_dict(d, backend=backend, model=model) for d in self._dicts.values()
        ]
        if status is None:
            return loaded
        return [skill for skill in loaded if skill.status == status]

    def pending(self) -> list[LearnedSkill]:
        """Skills held for human review (e.g. distilled during a tainted run)."""
        return self.skills(status="pending")

    def retrievable(
        self, backend: SupportsComplete | None = None, model: str | None = None
    ) -> list[LearnedSkill]:
        """Skills eligible for retrieval: ``active`` plus ``provisional`` (on measured probation).

        Provisional skills (M18-4) run so they accrue a real track record; the lifecycle policy then
        promotes the ones that prove themselves and demotes the ones that don't. ``pending`` (held for
        review) and ``retired`` stay excluded.
        """
        return [
            s for s in self.skills(backend, model) if s.status in ("active", "provisional")
        ]

    def promote(self, name: str) -> bool:
        """Promote a provisional skill to ``active`` (it passed measured probation). False if unknown."""
        entry = self._dicts.get(name)
        if entry is None:
            return False
        entry["status"] = "active"
        self.save()
        return True

    def approve(self, name: str) -> bool:
        """Activate a pending or retired skill after human review. Returns False if unknown.

        The single "back to active" transition — used both to accept a skill held for review
        (tainted-run provenance) and to un-retire one that was proposed for retirement.
        """
        entry = self._dicts.get(name)
        if entry is None:
            return False
        entry["status"] = "active"
        self.save()
        return True

    def retired(self) -> list[LearnedSkill]:
        """Skills proposed for retirement (kept for review, never deleted)."""
        return self.skills(status="retired")

    def retire(self, name: str) -> bool:
        """Mark a skill retired — excluded from retrieval, but kept for review/reactivation.

        Retirement is proposed-with-review, not deletion: a retired skill stops being injected
        as a card (retrieval takes only ``active``) yet stays inspectable and can be reactivated
        with :meth:`approve`. Returns False if the skill is unknown.
        """
        entry = self._dicts.get(name)
        if entry is None:
            return False
        entry["status"] = "retired"
        self.save()
        return True
