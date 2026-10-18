"""``chimera catalog`` — the public catalogues this Chimera reads, and the commits it reads them at.

``chimera catalog pins`` prints the pins shipped in the package; ``--check`` asks GitHub whether each
repository has moved past its pin. It never changes a pin: a catalogue only moves when a release
ships a new ``chimera/catalogs/pins.json`` (``scripts/bump_catalog_pins.py``, reviewed in a pull
request). See ``docs/catalogs.md``.
"""

from __future__ import annotations

import json

import typer
from rich.markup import escape
from rich.table import Table

from chimera.cli.commands._shared import app, console

catalog_app = typer.Typer(
    help="The public plugin, skill and connector catalogues, read from pinned commits.",
    no_args_is_help=True,
)
# Hidden like `chimera plugin`, its sibling: the screens that read the catalogues are not out yet,
# and the visible top-level count is ratcheted (`tests/test_s30_70_cli_structure.py`).
app.add_typer(catalog_app, name="catalog", hidden=True)

_CHECK_OPT = typer.Option(
    False, "--check", help="Ask GitHub whether each catalogue has newer commits. Changes nothing."
)


def _head(owner: str, repo: str) -> str:
    from chimera.skills import bundles

    data = json.loads(bundles._get(f"https://api.github.com/repos/{owner}/{repo}/commits/main"))
    return str(data.get("sha", "")) if isinstance(data, dict) else ""


@catalog_app.command("pins")
def catalog_pins(check: bool = _CHECK_OPT) -> None:
    """Show the commit each catalogue is pinned at; --check reports which have moved since."""
    from chimera.catalogs.pins import load_pins

    pins = load_pins()
    table = Table(title="Pinned catalogues")
    table.add_column("catalogue")
    table.add_column("repository")
    table.add_column("pinned at")
    table.add_column("files", justify="right")
    if check:
        table.add_column("main")
    behind = 0
    failed = 0
    for kind, pin in pins.catalogs.items():
        row = [kind, f"{pin.owner}/{pin.repo}", pin.sha[:12], str(len(pin.files))]
        if check:
            try:
                head = _head(pin.owner, pin.repo)
            except Exception as exc:  # noqa: BLE001 -- one unreachable repo is a row, not a crash
                failed += 1
                row.append(f"[red]unreachable: {escape(str(exc))}[/red]")
            else:
                if head == pin.sha:
                    row.append("[green]up to date[/green]")
                else:
                    behind += 1
                    row.append(f"[yellow]newer: {head[:12]}[/yellow]")
        table.add_row(*row)
    console.print(table)
    if check:
        if behind:
            console.print(
                f"{behind} catalogue(s) have newer commits. Nothing was changed: a release re-pins "
                "with `python scripts/bump_catalog_pins.py` in a reviewed pull request."
            )
        elif not failed:
            console.print("Every catalogue is pinned at its repository's current main.")
        if failed:
            raise typer.Exit(code=1)


__all__ = ["catalog_app", "catalog_pins"]
