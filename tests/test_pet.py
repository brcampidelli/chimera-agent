"""Tests for the virtual companion (deterministic — no real clock)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from chimera.pet import Pet, PetStore, apply_decay, feed, mood, play, rest

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _pet(**kwargs: Any) -> Pet:
    return Pet(last_tick=T0, **kwargs)


def test_decay_reduces_stats_over_time() -> None:
    pet = _pet(fullness=100, happiness=100, energy=100)
    apply_decay(pet, T0 + timedelta(hours=5))
    assert pet.fullness == 60.0  # 100 - 8*5
    assert pet.happiness == 75.0  # 100 - 5*5
    assert pet.energy == 70.0  # 100 - 6*5
    assert pet.last_tick == T0 + timedelta(hours=5)


def test_decay_clamps_at_zero() -> None:
    pet = _pet(fullness=10, happiness=10, energy=10)
    apply_decay(pet, T0 + timedelta(hours=100))
    assert (pet.fullness, pet.happiness, pet.energy) == (0.0, 0.0, 0.0)


def test_feed_caps_fullness() -> None:
    pet = _pet(fullness=90)
    feed(pet)
    assert pet.fullness == 100.0


def test_play_then_rest() -> None:
    pet = _pet(happiness=50, energy=50, fullness=50)
    play(pet)
    assert (pet.happiness, pet.energy, pet.fullness) == (75.0, 35.0, 40.0)
    rest(pet)
    assert (pet.energy, pet.happiness) == (75.0, 80.0)


def test_mood_reflects_the_worst_need() -> None:
    assert mood(_pet(fullness=10, happiness=90, energy=90)) == "hungry"
    assert mood(_pet(fullness=90, energy=10, happiness=90)) == "tired"
    assert mood(_pet(fullness=90, energy=90, happiness=10)) == "sad"
    assert mood(_pet(fullness=90, happiness=90, energy=90)) == "happy"
    assert mood(_pet(fullness=50, happiness=50, energy=50)) == "content"


def test_store_roundtrip(tmp_path: Path) -> None:
    store = PetStore(tmp_path / "pet.json")
    assert store.load().name == "Chimi"  # default when the file is missing
    store.save(_pet(name="Rex", happiness=42))
    reloaded = store.load()
    assert reloaded.name == "Rex"
    assert reloaded.happiness == 42.0
