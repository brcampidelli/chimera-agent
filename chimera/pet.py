"""A tiny virtual companion — a persistent pet with stats that drift over time.

Pure, deterministic logic (every time-dependent function takes an explicit ``now``)
so it unit-tests without clocks; the CLI persists it as JSON under the Chimera home.
Three stats (0–100, higher is better) decay between visits; feeding, playing and
resting nudge them back.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


class Pet(BaseModel):
    """A companion's state."""

    name: str = "Chimi"
    species: str = "chimera"
    fullness: float = 80.0
    happiness: float = 80.0
    energy: float = 80.0
    born_at: datetime = Field(default_factory=_now)
    last_tick: datetime = Field(default_factory=_now)


_DECAY_PER_HOUR = {"fullness": 8.0, "happiness": 5.0, "energy": 6.0}


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def apply_decay(pet: Pet, now: datetime) -> Pet:
    """Drift the stats down for the time elapsed since the last visit."""
    hours = max(0.0, (now - pet.last_tick).total_seconds() / 3600.0)
    pet.fullness = _clamp(pet.fullness - _DECAY_PER_HOUR["fullness"] * hours)
    pet.happiness = _clamp(pet.happiness - _DECAY_PER_HOUR["happiness"] * hours)
    pet.energy = _clamp(pet.energy - _DECAY_PER_HOUR["energy"] * hours)
    pet.last_tick = now
    return pet


def feed(pet: Pet) -> Pet:
    pet.fullness = _clamp(pet.fullness + 30.0)
    return pet


def play(pet: Pet) -> Pet:
    pet.happiness = _clamp(pet.happiness + 25.0)
    pet.energy = _clamp(pet.energy - 15.0)
    pet.fullness = _clamp(pet.fullness - 10.0)
    return pet


def rest(pet: Pet) -> Pet:
    pet.energy = _clamp(pet.energy + 40.0)
    pet.happiness = _clamp(pet.happiness + 5.0)
    return pet


def mood(pet: Pet) -> str:
    """A one-word mood from the stats (worst need wins)."""
    if pet.fullness < 25:
        return "hungry"
    if pet.energy < 25:
        return "tired"
    if pet.happiness < 25:
        return "sad"
    return "happy" if (pet.fullness + pet.happiness + pet.energy) / 3 >= 70 else "content"


class PetStore:
    """JSON-backed persistence for a single pet."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> Pet:
        if self.path.exists():
            return Pet.model_validate_json(self.path.read_text(encoding="utf-8"))
        return Pet()

    def save(self, pet: Pet) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(pet.model_dump_json(indent=2), encoding="utf-8")
