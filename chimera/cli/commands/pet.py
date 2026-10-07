"""The optional companion, ``chimera pet`` — the one CLI module allowed to import chimera.pet.

Moved verbatim out of ``chimera/cli/main.py`` (S30-70); ``chimera.cli.main`` re-exports every
name defined here.
"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING

import typer
from rich.table import Table

from chimera.cli.commands._shared import app, console
from chimera.config import get_settings

if TYPE_CHECKING:
    from chimera.pet import Pet, PetStore



pet_app = typer.Typer(help="Your virtual companion — an optional toy, unrelated to the agent.", no_args_is_help=True)
app.add_typer(pet_app, name="pet")


def _pet_store() -> PetStore:
    from chimera.pet import PetStore

    return PetStore(get_settings().home / "pet.json")


def _render_pet(pet: Pet) -> None:
    from chimera.pet import mood

    table = Table(title=f"🐾 {pet.name} the {pet.species}", show_header=False, title_style="bold")
    table.add_row("mood", mood(pet))
    table.add_row("fullness", f"{pet.fullness:.0f}/100")
    table.add_row("happiness", f"{pet.happiness:.0f}/100")
    table.add_row("energy", f"{pet.energy:.0f}/100")
    console.print(table)


def _pet_interact(action: str | None) -> None:
    from datetime import datetime

    from chimera.pet import apply_decay, feed, play, rest

    moves = {"feed": feed, "play": play, "rest": rest}
    store = _pet_store()
    pet = apply_decay(store.load(), datetime.now(UTC))
    if action:
        moves[action](pet)
    store.save(pet)
    _render_pet(pet)


@pet_app.command("status")
def pet_status() -> None:
    """Check on your companion (stats drift while you're away)."""
    _pet_interact(None)


@pet_app.command("feed")
def pet_feed() -> None:
    """Feed it (raises fullness)."""
    _pet_interact("feed")


@pet_app.command("play")
def pet_play() -> None:
    """Play with it (raises happiness; costs energy + a little fullness)."""
    _pet_interact("play")


@pet_app.command("rest")
def pet_rest() -> None:
    """Let it rest (restores energy)."""
    _pet_interact("rest")


@pet_app.command("new")
def pet_new(
    name: str = typer.Option("Chimi", "--name", help="Companion name."),
    species: str = typer.Option("chimera", "--species", help="Companion species."),
) -> None:
    """Adopt a fresh companion (resets stats)."""
    from chimera.pet import Pet

    store = _pet_store()
    store.save(Pet(name=name, species=species))
    _render_pet(store.load())
