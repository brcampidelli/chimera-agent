"""A command that is not in the reference is a command nobody can find.

This check existed, on the documentation site, and it worked — it caught the same omission three
times. What it could not do was catch it in time: the list of themes lived in the site's repository,
so the only place the check could run was the site's *deploy*, which happens after the release. All
three times (`sessions`, the installable skills catalogue, then `approve` and `secrets`) the release
shipped, the deploy went red, and the download page stayed on the previous version until somebody
read the failure to find out why.

Moving the list into this package moves the check to the pull request that adds the command. That is
the whole change: same assertions, three days earlier, and the site now reads the grouping out of
the product instead of keeping a second copy of it.

The commands are read from the Typer app, not from `_cli_snapshot.json`. The snapshot is a committed
file and can be stale; the app object is the CLI. Reading the snapshot would mean this gate passes
whenever the person who added the command also forgot to regenerate — the two failures that most
often arrive together.
"""

from __future__ import annotations

import json
from pathlib import Path

from chimera.cli import themes

ARQUIVO = Path(__file__).resolve().parents[1] / "chimera" / "_cli_themes.json"


def test_every_visible_command_is_in_a_theme() -> None:
    orfaos = themes.unthemed()

    assert orfaos == [], (
        f"not listed anywhere in the command reference: {', '.join(orfaos)}. "
        "Add each one to a theme in chimera/cli/themes.py, then regenerate: "
        "python -m chimera.cli.themes_dump > chimera/_cli_themes.json"
    )


def test_no_theme_names_a_command_that_does_not_exist() -> None:
    """The other direction. A removed command leaves a reference entry that 404s, and a reference
    with a dead link is worse than one that is merely incomplete: it looks maintained."""
    fantasmas = themes.phantoms()

    assert fantasmas == [], f"themes point at commands the CLI does not have: {', '.join(fantasmas)}"


def test_no_command_is_in_two_themes() -> None:
    """The reference is rendered theme by theme, so a command in two of them is printed twice, and
    a reader has no way to tell which grouping was meant."""
    todos = [nome for tema in themes.THEMES for nome in tema.commands]

    duplicados = sorted({nome for nome in todos if todos.count(nome) > 1})

    assert duplicados == [], f"listed under more than one theme: {', '.join(duplicados)}"


def test_the_committed_json_is_what_the_module_says() -> None:
    """The site reads the JSON, not the module. Without this the two drift and the gate above
    guards a file nobody renders."""
    gravado = json.loads(ARQUIVO.read_text(encoding="utf-8"))

    assert gravado == themes.build(), (
        "chimera/_cli_themes.json is stale. Regenerate it: "
        "python -m chimera.cli.themes_dump > chimera/_cli_themes.json"
    )


def test_the_gate_can_actually_fail(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A guard that has never been seen to fail may be inert.

    Every assertion above is green today, which is exactly the state in which a broken check is
    indistinguishable from a working one. This drives the failure on purpose: hide a theme, and the
    commands it held must be reported missing.
    """
    monkeypatch.setattr(themes, "THEMES", themes.THEMES[1:])

    orfaos = themes.unthemed()

    assert "doctor" in orfaos, orfaos
    assert "secrets" in orfaos, orfaos
