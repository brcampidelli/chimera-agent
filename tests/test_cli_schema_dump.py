"""The CLI reference is generated, so the generator is what has to be trusted.

`docs/usage.md` covers the happy path for about seventeen commands. The other ninety exist only as
docstrings on functions, and the documentation site renders them from this dump. If the dump
quietly loses a group — which is exactly what happened while writing it, because Typer vendors its
own Click and `isinstance(root, click.Group)` is False — the site would publish a reference missing
half the CLI and look entirely convincing doing it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from chimera import __version__
from chimera.cli.schema_dump import build

SNAPSHOT = Path(__file__).resolve().parents[1] / "chimera" / "_cli_snapshot.json"


def _leaves(commands: list[dict]) -> list[dict]:
    return [c for c in commands if "commands" not in c]


def _groups(commands: list[dict]) -> list[dict]:
    return [c for c in commands if "commands" in c]


def test_finds_the_groups_and_not_just_the_leaves() -> None:
    """The failure mode that motivated this test.

    Typer's `TyperGroup` does not subclass the installed `click.Group`, so a class check reports
    every group as a leaf command and the reference silently drops every subcommand.
    """
    data = build()
    groups = _groups(data["commands"])
    assert len(groups) >= 10, f"expected the typer sub-apps, found {len(groups)}"
    names = {g["name"] for g in groups}
    for expected in ("agents", "kanban", "memory", "models", "project", "cron", "mcp"):
        assert expected in names, f"{expected} is a command group and the dump missed it"


def test_every_command_carries_the_help_a_user_would_see() -> None:
    data = build()
    for command in data["commands"]:
        if command.get("hidden"):
            continue
        assert command["help"], f"{command['path']} has no help text to document"
        for child in command.get("commands", []):
            assert child["help"], f"{child['path']} has no help text to document"


def test_params_are_data_not_python_objects() -> None:
    """A `Path` or an enum member in a default must not crash the dump."""
    payload = json.dumps(build())  # would raise on a non-serialisable default
    assert '"kind": "argument"' in payload
    assert '"kind": "option"' in payload


def test_stamps_the_version_it_describes() -> None:
    # A reference that does not say which release it documents is a reference that lies quietly
    # as soon as the CLI moves.
    assert build()["generated_for"] == __version__


def test_the_committed_snapshot_is_current() -> None:
    """The drift gate, mirroring the one that guards the desktop API types.

    CI runs this too, but keeping it in the suite means the failure lands on the person who
    changed the CLI rather than three commits later.
    """
    assert SNAPSHOT.exists(), "run: python -m chimera.cli.schema_dump > chimera/_cli_snapshot.json"
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert committed == build(), (
        "chimera/_cli_snapshot.json is stale. Regenerate it:\n"
        "  python -m chimera.cli.schema_dump > chimera/_cli_snapshot.json"
    )


def test_the_module_runs_as_a_script() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "chimera.cli.schema_dump"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(result.stdout)["name"] == "chimera"
