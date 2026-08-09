"""Dump the CLI's full command surface as JSON to stdout.

The twin of :mod:`chimera.api.schema_dump`, and for the same reason. That one exists so the
desktop app's TypeScript types cannot drift from the backend's response models; this one exists so
a *documentation site* cannot drift from the commands that exist.

    python -m chimera.cli.schema_dump > chimera/_cli_snapshot.json

It sits beside ``_benchmark_snapshot.json`` and ``_maturity_snapshot.json`` and follows the same
rule they do: generated, committed, and stamped with the version it describes, so a reader is
always told which release the reference belongs to.

``docs/usage.md`` documents the happy path for about seventeen commands, written by a human and
worth keeping that way. There are far more than seventeen. Writing the rest by hand would mean
maintaining a second, prose copy of every flag — and a hand-copied flag list is correct exactly
once: the day it was typed.

Output is sorted and indented so regeneration produces a deterministic diff, which is what lets CI
regenerate it and fail if the committed copy is stale.

**Everything below is duck-typed on purpose.** Typer vendors its own copy of Click
(``typer._click``), so ``TyperGroup`` is not a ``click.Group`` and ``isinstance`` checks against
the installed Click silently classify every group as a leaf command. Asking whether an object has
subcommands survives that; asking what class it is does not.
"""

from __future__ import annotations

import json
from typing import Any

import typer.main

from chimera import __version__
from chimera.cli.main import app as cli_app


def _has_subcommands(command: Any) -> bool:
    return isinstance(getattr(command, "commands", None), dict)


def _param_json(param: Any) -> dict[str, Any]:
    """One flag or argument, as data.

    ``default`` is rendered with ``repr`` rather than left as a Python object: defaults include
    things like ``Path`` and enum members, and a dump that crashes on an unusual default would be
    a gate that fails for a reason nobody can act on.
    """
    param_type = getattr(param, "type", None)
    entry: dict[str, Any] = {
        "name": getattr(param, "name", "") or "",
        # "option" or "argument" — Click sets this on the class, and Typer's fork keeps it.
        "kind": getattr(param, "param_type_name", "option"),
        "required": bool(getattr(param, "required", False)),
        "type": getattr(param_type, "name", str(param_type)),
    }
    opts = list(getattr(param, "opts", []) or [])
    if opts:
        entry["opts"] = opts
    secondary = list(getattr(param, "secondary_opts", []) or [])
    if secondary:
        entry["secondary_opts"] = secondary
    help_text = getattr(param, "help", None)
    if help_text:
        entry["help"] = str(help_text).strip()
    default = getattr(param, "default", None)
    if default is not None and default is not False:
        entry["default"] = repr(default)
    choices = getattr(param_type, "choices", None)
    if choices:
        entry["choices"] = [str(choice) for choice in choices]
    return entry


def _command_json(name: str, command: Any, path: list[str]) -> dict[str, Any]:
    """One command, and its subcommands if it has any."""
    full = [*path, name] if name else list(path)
    entry: dict[str, Any] = {
        "path": " ".join(full),
        "name": name,
        # `help` is the docstring Typer lifted off the function — the text a user sees on
        # `--help`, which is exactly the text a reference page should show.
        "help": (getattr(command, "help", None) or getattr(command, "short_help", None) or "").strip(),
        "hidden": bool(getattr(command, "hidden", False)),
        "deprecated": bool(getattr(command, "deprecated", False)),
        "params": [_param_json(p) for p in getattr(command, "params", [])],
    }
    if _has_subcommands(command):
        entry["commands"] = [
            _command_json(child_name, child, full)
            for child_name, child in sorted(command.commands.items())
        ]
    return entry


def build() -> dict[str, Any]:
    root = typer.main.get_command(cli_app)
    if not _has_subcommands(root):  # pragma: no cover - the CLI is always a group
        raise TypeError("the Chimera CLI is expected to expose subcommands")
    return {
        "generated_for": __version__,
        "name": getattr(root, "name", None) or "chimera",
        "help": (getattr(root, "help", None) or "").strip(),
        "commands": [
            _command_json(name, command, []) for name, command in sorted(root.commands.items())
        ],
    }


def main() -> None:
    print(json.dumps(build(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
