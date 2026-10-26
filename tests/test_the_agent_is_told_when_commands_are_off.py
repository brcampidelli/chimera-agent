"""A folder with commands off says so to the agent up front, and a refused command says why.

Study 34, item 15: the agent ran ``npm run build`` in a folder where the owner had not allowed
commands, was answered with a missing ``tsc``, and reported an environment failure. The permission
was the cause and nothing had told the agent about it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.api.code_api import CodeSeams, assemble_registry, commands_are_off
from chimera.api.posture import Posture
from chimera.config import Settings, get_settings
from chimera.providers.gateway import LLMGateway
from chimera.sandbox.os_sandbox import os_sandbox_available
from chimera.tools.shell import COMMANDS_OFF_NOTE, COMMANDS_OFF_REFUSAL, RunShellTool


@pytest.fixture(autouse=True)
def _host_sandbox(monkeypatch: pytest.MonkeyPatch):
    """The declined-command test covers the host-execution gate explicitly."""
    monkeypatch.setenv("CHIMERA_SANDBOX", "local")
    monkeypatch.delenv("CHIMERA_HOST_EXEC", raising=False)
    monkeypatch.delenv("CHIMERA_REACH", raising=False)

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


def test_a_declined_command_in_an_ungranted_folder_says_commands_are_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    # No posture sent: the shell stays mounted but gated, and the headless gate refuses.
    settings = _settings(tmp_path)
    registry, _ = assemble_registry(
        CodeSeams(), ws, settings, LLMGateway(), steps=2, surface="api:turn"
    )
    assert "run_shell" in registry
    out = registry.get("run_shell").run(command="npm run build")
    assert COMMANDS_OFF_REFUSAL in out or "commands are off" in out
    assert "OSError" not in out and "not found" not in out


@pytest.mark.skipif(not os_sandbox_available(), reason="OS sandbox (bubblewrap or Seatbelt) is unavailable")
def test_os_sandbox_skips_host_confirmation_but_confines_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shlex
    import sys
    import uuid

    from chimera.sandbox import OsSandbox

    outside = tmp_path.parent / f"chimera-sandbox-{uuid.uuid4().hex}"
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()
    monkeypatch.setenv("TMPDIR", str(temp_dir))
    script = "\n".join((
        "from pathlib import Path",
        "import socket",
        f"p = Path({str(outside)!r})",
        "try:",
        "    p.write_text('escaped')",
        "except OSError:",
        "    print('write-blocked')",
        "else:",
        "    print('write-allowed')",
        "try:",
        "    socket.create_connection(('1.1.1.1', 80), timeout=1).close()",
        "except OSError:",
        "    print('network-blocked')",
        "else:",
        "    print('network-allowed')",
    ))
    confirm_calls: list[str] = []
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
    tool = RunShellTool(
        tmp_path, OsSandbox(), confirm=lambda value: confirm_calls.append(value) or True
    )

    output = tool.run(command=command)

    assert confirm_calls == [], "an isolated command must not ask to run on the host"
    assert "write-blocked" in output, output
    assert "network-blocked" in output, output
    assert not outside.exists(), "the command wrote outside the sandbox's writable roots"


@pytest.mark.skipif(not os_sandbox_available(), reason="OS sandbox (bubblewrap or Seatbelt) is unavailable")
def test_ungranted_folder_shows_commands_off_message_with_os_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIMERA_SANDBOX", "os")
    get_settings.cache_clear()
    ws = tmp_path / "ws"
    ws.mkdir()
    seams = CodeSeams(posture=Posture(reach="workspace_shell"))
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_SANDBOX="os")
    assert commands_are_off(seams, settings, ws)
    registry, _ledger = assemble_registry(
        seams, ws, settings, LLMGateway(), steps=2, surface="api:turn"
    )
    assert "run_shell" not in registry
    assert "Commands are off for this folder" in COMMANDS_OFF_NOTE
    get_settings.cache_clear()


def test_the_refusal_names_the_permission_not_the_operating_system() -> None:
    assert "commands are off for this folder" in COMMANDS_OFF_REFUSAL
    assert "ask the owner to allow them" in COMMANDS_OFF_REFUSAL


def test_a_tool_without_a_reason_keeps_the_generic_sentence(tmp_path: Path) -> None:
    tool = RunShellTool(tmp_path, confirm=lambda _command: False)
    assert tool.run(command="echo hi").startswith("error: host execution declined")
    tool.declined_message = COMMANDS_OFF_REFUSAL
    assert tool.run(command="echo hi") == COMMANDS_OFF_REFUSAL
