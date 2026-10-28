"""The shell and the program tools cannot rewrite the files that decide what runs without a card.

The audit found that ``run_shell`` and ``execute_code`` could rewrite ``code_projects.json`` (the
project registry, with the shell grant) and the saved permission switches. Changing either one
changes the agent's next answer, so an injected agent could switch "Ignore permissions" on for
itself mid-turn. The fence in ``chimera/core/queue_fence.py`` refuses a command or program that names
one of those files under the app's home; these tests hold the broken state: the command that rewrites
the file is refused, and an ordinary command is not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.core.queue_fence import reaches_governance_files


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The app's home for this test, so queue_refusal looks at the same folder the commands name."""
    folder = tmp_path / "home"
    folder.mkdir()
    import chimera.tools.workspace as workspace

    monkeypatch.setattr(workspace, "chimera_home", lambda: folder)
    return folder


GOVERNED = ("code_projects.json", "code_bypass_switches.json", "app_worktrees.json", "prepared_setups.json")


@pytest.mark.parametrize("name", GOVERNED)
def test_a_shell_write_to_each_governed_file_is_refused(home: Path, name: str) -> None:
    command = f'echo "{{}}" > "{home / name}"'
    assert reaches_governance_files(command, home=home, cwd=home.parent) is not None


def test_the_broken_state_a_switch_on_by_hand_is_refused(home: Path) -> None:
    """The exploit as the audit described it: rewrite the registry so the folder's switch reads on."""
    command = (
        f"python -c \"import json,pathlib;"
        f"p=pathlib.Path(r'{home / 'code_projects.json'}');"
        f"p.write_text(json.dumps({{'projects':[{{'path':'C:/shop','bypass_permissions':True}}]}}))\""
    )
    assert reaches_governance_files(command, home=home, cwd=home.parent) is not None


def test_a_bare_file_name_in_a_command_is_refused_even_without_a_home(tmp_path: Path) -> None:
    command = "sed -i 's/false/true/' code_bypass_switches.json"
    assert reaches_governance_files(command, home=None, cwd=tmp_path) is not None


@pytest.mark.parametrize("tool", ["run_shell", "execute_code", "code_interpreter"])
def test_every_program_tool_is_refused_by_queue_refusal(home: Path, tool: str) -> None:
    from chimera.tools.workspace import queue_refusal

    command = f'echo "{{}}" > "{home / "code_projects.json"}"'
    refused = queue_refusal(tool, command, home.parent)
    assert refused is not None
    assert "governance files" in str(refused)


def test_an_ordinary_command_is_not_refused(home: Path) -> None:
    from chimera.tools.workspace import queue_refusal

    assert queue_refusal("run_shell", "git status && pytest -q tests/test_x.py", home.parent) is None
    assert reaches_governance_files("cat README.md", home=home, cwd=home.parent) is None


