"""Custom commands stay project data while expanding into ordinary user message text."""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.core.commands import PromptCommand, discover_commands, expand_command, find_command


def _command(tmp_path: Path, template: str) -> PromptCommand:
    commands_dir = tmp_path / "project" / ".chimera" / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    path = commands_dir / "command.md"
    path.write_text(template, encoding="utf-8")
    return discover_commands(tmp_path / "project", tmp_path / "home")[0]


def test_project_command_wins_over_global_and_subfolders_become_namespaces(tmp_path: Path) -> None:
    workspace, home = tmp_path / "project", tmp_path / "home"
    project_commands = workspace / ".chimera" / "commands"
    global_commands = home / "commands"
    (project_commands / "time").mkdir(parents=True)
    (global_commands / "time").mkdir(parents=True)
    (project_commands / "time" / "revisar.md").write_text("project", encoding="utf-8")
    (global_commands / "time" / "revisar.md").write_text("global", encoding="utf-8")
    global_commands.mkdir(parents=True, exist_ok=True)
    (global_commands / "draft.md").write_text("global only", encoding="utf-8")

    commands = discover_commands(workspace, home)

    assert [(item.name, item.origin, item.template) for item in commands] == [
        ("draft", "global", "global only"),
        ("time/revisar", "project", "project"),
    ]


def test_arguments_placeholder_keeps_the_original_full_argument_text(tmp_path: Path) -> None:
    command = _command(tmp_path, "Ask: $ARGUMENTS")

    assert expand_command(command, 'two words "quoted words"') == 'Ask: two words "quoted words"'


def test_positional_arguments_group_quotes_and_largest_position_absorbs_rest(tmp_path: Path) -> None:
    command = _command(tmp_path, "first=$1 second=$2 rest=$3 again=$3")

    assert expand_command(command, 'one "two words" three four') == (
        "first=one second=two words rest=three four again=three four"
    )


def test_absent_positional_arguments_expand_to_empty_strings(tmp_path: Path) -> None:
    command = _command(tmp_path, "[$1][$2][$4]")

    assert expand_command(command, "one") == "[one][][]"


def test_no_placeholders_appends_nonempty_arguments_after_a_blank_line(tmp_path: Path) -> None:
    command = _command(tmp_path, "Template")

    assert expand_command(command, "extra context") == "Template\n\nextra context"
    assert expand_command(command, "") == "Template"


def test_invalid_frontmatter_remains_listed_with_an_error(tmp_path: Path) -> None:
    commands_dir = tmp_path / "project" / ".chimera" / "commands"
    commands_dir.mkdir(parents=True)
    (commands_dir / "broken.md").write_text("---\nunknown: field\n---\nbody", encoding="utf-8")

    commands = discover_commands(tmp_path / "project", tmp_path / "home")

    assert len(commands) == 1
    assert commands[0].name == "broken"
    assert "unsupported metadata field" in (commands[0].error or "")
    with pytest.raises(ValueError, match="invalid frontmatter"):
        expand_command(commands[0], "")


def test_shell_looking_template_is_returned_literally_and_never_executed(tmp_path: Path) -> None:
    template = "Before `!touch NEVER_CREATE_THIS_FILE` after"
    command = _command(tmp_path, template)

    assert expand_command(command, "") == template
    assert not (tmp_path / "NEVER_CREATE_THIS_FILE").exists()


def test_embedded_command_has_precedence_over_a_custom_file(tmp_path: Path) -> None:
    commands_dir = tmp_path / "project" / ".chimera" / "commands"
    commands_dir.mkdir(parents=True)
    (commands_dir / "help.md").write_text("custom help", encoding="utf-8")
    (commands_dir / "review.md").write_text("review it", encoding="utf-8")
    found = discover_commands(tmp_path / "project", tmp_path / "home")

    assert find_command(found, "/help", builtins={"help"}) is None
    review = find_command(found, "/review", builtins={"help"})
    assert review is not None and review.template == "review it"


def test_frontmatter_model_must_be_a_catalog_slug(tmp_path: Path) -> None:
    command = _command(tmp_path, "---\nmodel: not-a-catalog-model\n---\nBody")

    assert command.error is not None
    assert "unknown model slug" in command.error
