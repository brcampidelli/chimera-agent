"""S30-70: the CLI's surface was regrouped and its 10,486-line file split — and nothing a person or a
script typed before stopped working.

`chimera --help` listed 89 top-level entries, fifteen of them `skills-*` and fifteen of them rulers.
Those thirty now live under `chimera skills ...` and `chimera bench ...`; each old name is still
registered, hidden from `--help`, with the same function behind it. The fixture
`tests/fixtures/cli_commands_before_s30_70.json` was generated from the app BEFORE the change and is
the contract: every command path it lists must still resolve, with the same parameters.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest
import typer.main
from typer.testing import CliRunner

from chimera import __version__
from chimera.cli.main import app
from tests.cli_sources import CLI_DIR, cli_command_files

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cli_commands_before_s30_70.json"
BEFORE: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))

#: Visible top-level commands after S30-70, measured. A ratchet: it may fall, never rise. A new
#: top-level command needs a reason to be top-level rather than a subcommand of a group.
VISIBLE_TOP_LEVEL_NOW = 59

#: The old top-level name and its place in a group — the mapping the PR describes.
SKILLS = {
    "skills-library": "library", "skills-catalog": "catalog", "skills-install": "install",
    "skills-bundles": "bundles", "skills-bundle-enable": "bundle-enable",
    "skills-bundle-disable": "bundle-disable", "skills-uninstall": "uninstall",
    "skills-pending": "pending", "skills-stats": "stats", "skills-approve": "approve",
    "skills-export": "export", "skills-import": "import", "skills-retire": "retire",
    "skills-lifecycle": "lifecycle", "skills-evolve": "evolve",
}
BENCH = {
    "fusion-bench": "fusion", "cascade-bench": "cascade", "hierarchy-bench": "hierarchy",
    "skillcard-bench": "skillcard", "schema-bench": "schema", "sandbox-bench": "sandbox",
    "memory-bench": "memory", "bench-compare": "compare", "swe-bench-compare": "swe-compare",
    "memory-poison": "memory-poison", "probe-select": "probe-select",
    "transfer-gate": "transfer-gate", "evoclaw": "evoclaw", "rubric-grade": "rubric-grade",
    "context-curve": "context-curve",
}

runner = CliRunner()


def _root() -> Any:
    return typer.main.get_command(app)


def _resolve(path: str) -> Any:
    command = _root()
    for word in path.split():
        children = getattr(command, "commands", None)
        assert isinstance(children, dict), f"`chimera {path}`: {word!r} is under a leaf command"
        assert word in children, f"`chimera {path}` no longer resolves (no {word!r})"
        command = children[word]
    return command


def _signature(command: Any) -> list[dict[str, Any]]:
    """The same shape the fixture was written in — keep the two in step."""
    return [
        {
            "kind": getattr(p, "param_type_name", "option"),
            "name": p.name,
            "opts": sorted([*getattr(p, "opts", []), *getattr(p, "secondary_opts", [])]),
            "required": bool(p.required),
        }
        for p in getattr(command, "params", [])
    ]


def test_the_fixture_is_the_pre_change_surface() -> None:
    """If the fixture were regenerated from the current app, the test below would pass vacuously."""
    assert BEFORE["visible_top_level"] == 89
    assert len(BEFORE["commands"]) == 171
    assert "skills-library" in BEFORE["commands"] and "skills library" not in BEFORE["commands"]


@pytest.mark.parametrize("path", sorted(BEFORE["commands"]))
def test_every_command_that_resolved_before_still_resolves_with_the_same_parameters(
    path: str,
) -> None:
    assert _signature(_resolve(path)) == BEFORE["commands"][path], (
        f"`chimera {path}` changed its options — the regrouping promised it would not"
    )


def test_help_lists_fewer_top_level_commands_than_before() -> None:
    visible = [n for n, c in _root().commands.items() if not getattr(c, "hidden", False)]
    assert len(visible) < BEFORE["visible_top_level"]
    assert len(visible) <= VISIBLE_TOP_LEVEL_NOW, (
        f"{len(visible)} visible top-level commands; the ratchet is {VISIBLE_TOP_LEVEL_NOW}. "
        "Put the new command in a group, or lower another number to pay for it."
    )
    # And the --help text a person reads, not only the object: no old name is printed there.
    shown = runner.invoke(app, ["--help"]).output
    assert "skills-library" not in shown and "fusion-bench" not in shown


@pytest.mark.parametrize(
    ("old", "group", "sub"),
    [(old, "skills", sub) for old, sub in SKILLS.items()]
    + [(old, "bench", sub) for old, sub in BENCH.items()],
)
def test_each_old_name_is_a_hidden_alias_of_its_grouped_command(
    old: str, group: str, sub: str
) -> None:
    alias = _root().commands[old]
    grouped = _resolve(f"{group} {sub}")
    assert alias.hidden, f"`chimera {old}` should be hidden from --help now"
    assert not grouped.hidden
    # Typer wraps each registration's callback; the function underneath must be the same one.
    same = inspect.unwrap(alias.callback) is inspect.unwrap(grouped.callback)
    assert same, f"`chimera {old}` and `chimera {group} {sub}` run different code"


def test_bare_skills_still_lists_the_built_in_skills() -> None:
    result = runner.invoke(app, ["skills"])
    assert result.exit_code == 0, result.output
    assert "Built-in skills" in result.output


def test_bare_bench_is_still_the_benchmark_and_keeps_its_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`chimera bench` with no key refused with exit 1 before; a group must not turn that into a
    help screen and exit 0, which a CI step would read as a passed benchmark."""
    from chimera.config import Settings, get_settings

    monkeypatch.setattr(Settings, "can_answer", lambda self: False)
    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["bench", "--limit", "1"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 1
    assert "No provider key configured" in result.output


def test_an_old_name_and_its_new_name_print_the_same() -> None:
    old = runner.invoke(app, ["skills-library"])
    new = runner.invoke(app, ["skills", "library"])
    assert old.exit_code == new.exit_code == 0
    assert old.output == new.output


def test_version_prints_the_release_and_the_commit_when_known(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chimera.build_info as build_info

    monkeypatch.setattr(build_info, "chimera_git_sha", lambda: "0123456789abcdef0123")
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output == f"chimera {__version__} (0123456789ab)\n"

    monkeypatch.setattr(build_info, "chimera_git_sha", lambda: "")
    result = runner.invoke(app, ["--version"])
    assert result.output == f"chimera {__version__}\n"


def test_shell_completion_is_offered() -> None:
    options = {opt for p in _root().params for opt in getattr(p, "opts", [])}
    assert {"--install-completion", "--show-completion", "--version"} <= options


def test_a_patch_on_a_re_export_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting `chimera.cli.main.X` reaches no command since the split; it must not pass silently."""
    import chimera.cli.main as cli

    with pytest.raises(AttributeError, match="re-export"):
        setattr(cli, "_memory_manager", lambda: None)  # noqa: B010 — the call under test
    # The module the command reads it from is where a patch works.
    monkeypatch.setattr("chimera.cli.commands.memory._memory_manager", lambda: None)


def test_no_cli_module_is_a_ten_thousand_line_file_again() -> None:
    """The reason for the split. A first step, not the end state: `solve.py` sits at the bound."""
    sizes = {p.name: len(p.read_text(encoding="utf-8").splitlines()) for p in cli_command_files()}
    assert sizes["main.py"] < 600, sizes["main.py"]
    over = {name: n for name, n in sizes.items() if n > 1550}
    assert not over, f"CLI modules over 1,550 lines: {over}"
    assert (CLI_DIR / "commands" / "_shared.py").exists()
