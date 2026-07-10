"""Tests for the measured skill-lifecycle loop (M18-4): promote/demote from usage stats."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.evolution import (
    AutoSkillEvolver,
    LearnedSkill,
    SkillEvolver,
    SkillLifecyclePolicy,
    SkillStore,
)


def _row(name: str, status: str, uses: int, rate: float | None) -> dict[str, object]:
    return {"name": name, "status": status, "uses": uses, "rate": rate}


# --- SkillLifecyclePolicy ---------------------------------------------------------------


def test_promotes_proven_provisional() -> None:
    d = SkillLifecyclePolicy().decide([_row("good", "provisional", 5, 0.8)])
    assert d.promote == ["good"] and d.demote == []


def test_demotes_failed_provisional_and_regressed_active() -> None:
    d = SkillLifecyclePolicy().decide(
        [_row("bad_prov", "provisional", 6, 0.2), _row("regressed", "active", 8, 0.1)]
    )
    assert set(d.demote) == {"bad_prov", "regressed"} and d.promote == []


def test_leaves_midrate_lowuse_and_healthy_active_alone() -> None:
    d = SkillLifecyclePolicy().decide(
        [
            _row("mid", "provisional", 6, 0.5),   # between thresholds -> keep observing
            _row("young", "provisional", 2, 1.0),  # too few uses -> stays
            _row("healthy", "active", 10, 0.9),    # active, fine -> stays
            _row("nouse", "active", 0, None),      # no measured rate -> stays
        ]
    )
    assert d.promote == [] and d.demote == []


# --- SkillStore: retrievable + promote --------------------------------------------------


def _skill(name: str, status: str) -> LearnedSkill:
    return LearnedSkill(name=name, description="d", prompt_template="", status=status)  # type: ignore[arg-type]


def test_retrievable_is_active_plus_provisional(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "s.json")
    for name, status in [("a", "active"), ("p", "provisional"), ("pend", "pending"), ("ret", "retired")]:
        store.add(_skill(name, status))
    assert {s.name for s in store.retrievable()} == {"a", "p"}


def test_promote_flips_provisional_to_active(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "s.json")
    store.add(_skill("p", "provisional"))
    assert store.promote("p") is True
    got = store.get("p")
    assert got is not None and got.status == "active"
    assert store.promote("ghost") is False


# --- AutoSkillEvolver: provisional birth (config-gated) ---------------------------------


class _Backend:
    def complete(self, *a: Any, **k: Any) -> Any:  # never called by _mark_and_store
        raise AssertionError("backend should not be used here")


def test_provisional_birth_when_enabled(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "s.json")
    ev = AutoSkillEvolver(SkillEvolver(_Backend()), store, provisional=True)
    ev._mark_and_store(_skill("x", "active"), tainted=False)
    got = store.get("x")
    assert got is not None and got.status == "provisional"


def test_tainted_still_overrides_to_pending(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "s.json")
    ev = AutoSkillEvolver(SkillEvolver(_Backend()), store, provisional=True)
    ev._mark_and_store(_skill("y", "active"), tainted=True)
    got = store.get("y")
    assert got is not None and got.status == "pending"


def test_default_off_keeps_new_skills_active(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "s.json")
    ev = AutoSkillEvolver(SkillEvolver(_Backend()), store)  # provisional defaults False
    ev._mark_and_store(_skill("z", "active"), tainted=False)
    got = store.get("z")
    assert got is not None and got.status == "active"
