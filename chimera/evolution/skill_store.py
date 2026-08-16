"""Persistence for self-authored (learned) skills.

This store got the atomic write and not the re-read, and the two halves are not interchangeable.
Atomicity means a crash cannot truncate `skills.json`; it says nothing about a second holder of the
same file writing a snapshot taken before your change. That second holder is not hypothetical here —
`evolution/context.py` builds **two** instances over this exact path, one for the evolver (:128) and
one for the card retriever (:158) — so a single ordinary run does this:

    autonomous.py:1018   evolver copy    -> add(new skill)        skills.json = [old, NEW]
    autonomous.py:1095   retriever copy  -> record_use(old)       skills.json = [old]

The skill the run just learned is erased by the telemetry write that credits the cards it used, in
the same process, milliseconds later, atomically, with no error anywhere. Reproduced.

That configuration — evolution on **and** cards on — is exactly what `bench/learning_lift` has
measured since run 3, which added `--skill-cards` to the learning arm. It is a mechanism that
produces the null those runs found; whether it *explains* the null is a separate question, and the
suites' 84-92% ceiling is a documented rival explanation.

So mutations re-read under an advisory lock and apply on top, the same shape `chimera/memory/store.py`
uses and for the same reason.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path

from chimera.core.filelock import atomic_write_text, locked, read_text
from chimera.evolution.learned_skill import LearnedSkill
from chimera.providers.gateway import SupportsComplete


def _as_int(value: object) -> int:
    """Coerce a JSON-loaded counter to int (missing/odd values count as 0)."""
    return value if isinstance(value, int) else 0


def _set_status(dicts: dict[str, dict[str, object]], name: str, status: str) -> None:
    entry = dicts.get(name)
    if entry is not None:
        entry["status"] = status


class SkillStore:
    """A JSON-file store of learned skills (deduped by name)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._dicts: dict[str, dict[str, object]] = {}
        self._lock = threading.RLock()
        self.load()

    def load(self) -> None:
        self._dicts = {}
        if not self.path.exists():
            return
        try:
            raw = json.loads(read_text(self.path) or "[]")
        except json.JSONDecodeError:  # a truncated/corrupt file -> empty store, not a crash on init
            return
        if not isinstance(raw, list):
            return
        for item in raw:
            # Skip a single malformed entry instead of aborting the whole load — one bad record must
            # not brick the entire learned-skill library (the convention every sibling store follows).
            if isinstance(item, dict) and "name" in item:
                self._dicts[str(item["name"])] = item

    def _write(self) -> None:
        """Serialise to disk atomically. Assumes the caller holds the locks."""
        # Atomic write: this runs on every record_use (the flywheel's highest-frequency write), so a
        # crash mid-write must not truncate skills.json and make the whole library unloadable.
        atomic_write_text(self.path, json.dumps(list(self._dicts.values()), indent=2))

    def save(self) -> None:
        """Write the in-memory skills out, replacing whatever is on disk.

        The blunt form, kept for callers that hold the whole picture. It does **not** merge a
        concurrent writer — use the mutating methods, which do.
        """
        with self._lock, locked(self.path):
            self._write()

    def _mutate(self, apply: Callable[[dict[str, dict[str, object]]], None]) -> None:
        """Re-read, apply one change on top, write back — all under the lock.

        The re-read is the part that was missing. Without it every write publishes the dict this
        object loaded at construction, so the sibling instance's just-added skill disappears.
        """
        with self._lock, locked(self.path):
            self.load()
            apply(self._dicts)
            self._write()

    def _mutate_if_present(
        self, name: str, apply: Callable[[dict[str, dict[str, object]]], None]
    ) -> bool:
        """Re-read, change one named skill, write back. False when the name is unknown.

        The presence check has to happen AFTER the re-read: asking the stale snapshot whether a
        skill exists is how `skills-approve` reports "unknown" for something another process minted
        a second ago.
        """
        found = False
        with self._lock, locked(self.path):
            self.load()
            if name in self._dicts:
                found = True
                apply(self._dicts)
                self._write()
        return found

    def add(self, skill: LearnedSkill) -> None:
        def put(dicts: dict[str, dict[str, object]]) -> None:
            entry = skill.to_dict()
            # Usage counters are store-level state (not part of the skill definition):
            # re-adding/refining a skill must not wipe its measured track record.
            previous = dicts.get(skill.name)
            if previous is not None:
                entry["uses"] = previous.get("uses", 0)
                entry["successes"] = previous.get("successes", 0)
            dicts[skill.name] = entry

        self._mutate(put)

    def record_use(self, name: str, *, success: bool) -> None:
        """Count one retrieval-into-a-run for a skill and whether that run succeeded."""

        def credit(dicts: dict[str, dict[str, object]]) -> None:
            entry = dicts.get(name)
            if entry is None:
                return
            entry["uses"] = _as_int(entry.get("uses")) + 1
            if success:
                entry["successes"] = _as_int(entry.get("successes")) + 1

        self._mutate(credit)

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
        def apply(dicts: dict[str, dict[str, object]]) -> None:
            entry = dicts.get(name)
            if entry is None:
                return
            entry["status"] = "active"
            # Reset the probation counters so the demote rule measures POST-promotion behavior, not
            # the lifetime ratio. Otherwise a skill promoted at a high rate would need many more
            # failures to cross the demote threshold — the heavier its winning history, the slower
            # it can be retired on a real regression, contradicting the "auto-demote on measured
            # regression" contract.
            entry["uses"] = 0
            entry["successes"] = 0

        return self._mutate_if_present(name, apply)

    def approve(self, name: str) -> bool:
        """Activate a pending or retired skill after human review. Returns False if unknown.

        The single "back to active" transition — used both to accept a skill held for review
        (tainted-run provenance) and to un-retire one that was proposed for retirement.
        """
        return self._mutate_if_present(name, lambda dicts: _set_status(dicts, name, "active"))

    def retired(self) -> list[LearnedSkill]:
        """Skills proposed for retirement (kept for review, never deleted)."""
        return self.skills(status="retired")

    def retire(self, name: str) -> bool:
        """Mark a skill retired — excluded from retrieval, but kept for review/reactivation.

        Retirement is proposed-with-review, not deletion: a retired skill stops being injected
        as a card (retrieval takes only ``active``) yet stays inspectable and can be reactivated
        with :meth:`approve`. Returns False if the skill is unknown.
        """
        return self._mutate_if_present(name, lambda dicts: _set_status(dicts, name, "retired"))
