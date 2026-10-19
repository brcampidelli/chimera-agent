"""A folder with commands off says so to the agent up front, and a refused command says why.

Study 34, item 15: the agent ran ``npm run build`` in a folder where the owner had not allowed
commands, was answered with a missing ``tsc``, and reported an environment failure. The permission
was the cause and nothing had told the agent about it.
"""

from __future__ import annotations

from pathlib import Path

from chimera.api.code_api import CodeSeams, assemble_registry, commands_are_off
from chimera.api.posture import Posture
from chimera.config import Settings
from chimera.providers.gateway import LLMGateway
from chimera.tools.shell import COMMANDS_OFF_NOTE, COMMANDS_OFF_REFUSAL, RunShellTool


def _settings(tmp_path: Path) -> Settings:
    return Settings(CHIMERA_HOME=str(tmp_path / "home"))  # type: ignore[call-arg]


def test_a_folder_without_a_grant_has_commands_off(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    asked = CodeSeams(posture=Posture(reach="workspace_shell"))
    # The request asks for commands; the server's record for the folder has none, so it runs
    # without them — and the agent is told.
    assert commands_are_off(asked, _settings(tmp_path), ws)


def test_a_read_only_reach_has_commands_off_too(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    assert commands_are_off(CodeSeams(posture=Posture(reach="read_only")), _settings(tmp_path), ws)


def test_a_granted_folder_is_not_told_commands_are_off(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_REACH="workspace_shell")  # type: ignore[call-arg]
    asked = CodeSeams(posture=Posture(reach="workspace_shell"))
    assert not commands_are_off(asked, settings, ws)


def test_the_note_tells_the_agent_not_to_try_and_not_to_blame_the_environment() -> None:
    assert "Commands are off for this folder" in COMMANDS_OFF_NOTE
    assert "Do not try" in COMMANDS_OFF_NOTE and "environment fault" in COMMANDS_OFF_NOTE
    assert "ask the owner to allow commands" in COMMANDS_OFF_NOTE


def test_a_declined_command_in_an_ungranted_folder_says_commands_are_off(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    # No posture sent: the shell stays mounted but gated, and the headless gate refuses.
    registry, _ = assemble_registry(
        CodeSeams(), ws, _settings(tmp_path), LLMGateway(), steps=2, surface="api:turn"
    )
    assert "run_shell" in registry
    out = registry.get("run_shell").run(command="npm run build")
    assert COMMANDS_OFF_REFUSAL in out or "commands are off" in out
    assert "OSError" not in out and "not found" not in out


def test_the_refusal_names_the_permission_not_the_operating_system() -> None:
    assert "commands are off for this folder" in COMMANDS_OFF_REFUSAL
    assert "ask the owner to allow them" in COMMANDS_OFF_REFUSAL


def test_a_tool_without_a_reason_keeps_the_generic_sentence(tmp_path: Path) -> None:
    tool = RunShellTool(tmp_path, confirm=lambda _command: False)
    assert tool.run(command="echo hi").startswith("error: host execution declined")
    tool.declined_message = COMMANDS_OFF_REFUSAL
    assert tool.run(command="echo hi") == COMMANDS_OFF_REFUSAL
