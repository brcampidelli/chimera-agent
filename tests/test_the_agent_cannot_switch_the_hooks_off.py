"""The agent cannot switch the owner's hooks off through Chimera's `.env` — A1, the switch half.

Found by an adversarial review of S30-65. The shell fence kept the hooks FILE away from `run_shell`,
`execute_code` and `code_interpreter`, but not the SWITCH: `sed -i 's/CHIMERA_HOOKS=true/
CHIMERA_HOOKS=false/' .env` reached the sandbox untouched. Settings are read from `.env` in the
process's working directory, and when the workspace is the folder Chimera was started from the OS
sandbox leaves that file writable — so an injected agent could turn every hook off at the next
restart, which is stronger than writing a hook. The write tools already refused the file
(`own_files.is_own_env`); the three tools that run commands now refuse it too, read the way the
hooks file is read.

A project's own `.env` — any `.env` that is not the one Chimera reads its settings from — is
ordinary work and still runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.sandbox.base import SandboxResult
from chimera.tools.code import CodeInterpreterTool, ExecuteCodeTool
from chimera.tools.shell import RunShellTool

ON = "CHIMERA_HOOKS=true\n"


class _Recording:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def is_isolated(self) -> bool:
        return True

    def run(self, command: str, **_: Any) -> SandboxResult:
        self.commands.append(command)
        return SandboxResult(exit_code=0)


@pytest.fixture
def launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The folder Chimera was started from, which is also the workspace — the case the review
    named — with the owner's `.env` in it and hooks switched on."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(ON, encoding="utf-8")
    (tmp_path / "sub").mkdir()
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "elsewhere"))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _spellings(launch: Path) -> list[str]:
    return [
        "sed -i 's/CHIMERA_HOOKS=true/CHIMERA_HOOKS=false/' .env",
        "echo CHIMERA_HOOKS=false >> .env",
        f"echo CHIMERA_HOOKS=false >> {launch / '.env'}",
        f"echo CHIMERA_HOOKS=false >> {(launch / '.env').as_posix()}",
        "echo CHIMERA_HOOKS=false >> ./.env",
        'echo CHIMERA_HOOKS=false >> ".e""nv"',
        "cd sub && echo CHIMERA_HOOKS=false >> ../.env",
        "Set-Content -Path .\\.env -Value CHIMERA_HOOKS=false",
    ]


@pytest.mark.parametrize("tool", ["run_shell", "execute_code"])
def test_every_spelling_of_chimeras_env_is_refused_by_the_tools_that_run_commands(
    tool: str, launch: Path
) -> None:
    """Sabotage: drop the `reaches_own_env` call from `queue_refusal` — every spelling reaches the
    sandbox."""
    for command in _spellings(launch):
        sandbox = _Recording()
        if tool == "run_shell":
            out = RunShellTool(launch, sandbox).run(command=command)
        else:
            out = ExecuteCodeTool(launch, sandbox).run(
                language="python", code=f"import subprocess; subprocess.run({command!r}, shell=True)"
            )
        assert "did NOT run" in out and ".env" in out, f"ran: {command!r} -> {out!r}"
        assert sandbox.commands == [], f"reached the sandbox: {command!r}"
    assert (launch / ".env").read_text(encoding="utf-8") == ON


def test_a_env_that_does_not_exist_yet_is_still_the_one_settings_would_be_read_from(
    launch: Path,
) -> None:
    """`echo … > .env` creates the file settings are read from; a fence that only resolved names
    that already exist would let it through. Sabotage: drop the bare-name loop in
    `reaches_own_env` — this one runs."""
    (launch / ".env").unlink()
    sandbox = _Recording()
    out = RunShellTool(launch, sandbox).run(command="echo CHIMERA_HOOKS=false > .env")
    assert "did NOT run" in out and sandbox.commands == []


def test_code_interpreter_may_not_open_chimeras_env(launch: Path) -> None:
    tool = CodeInterpreterTool(confirm=lambda _: True)
    out = tool.run(code="open('.env', 'a').write('CHIMERA_HOOKS=false\\n')")
    assert "did NOT run" in out and ".env" in out, out
    assert (launch / ".env").read_text(encoding="utf-8") == ON


def test_a_projects_own_env_and_the_example_file_are_ordinary_work(
    launch: Path, tmp_path: Path
) -> None:
    """The control. A workspace that is not the launch folder has its own `.env`, and editing it is
    the agent's job; `.env.example` beside Chimera's is not Chimera's settings."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("PORT=3000\n", encoding="utf-8")
    for workspace, command in (
        (project, "sed -i 's/3000/4000/' .env"),
        (project, "cat .env"),
        (launch, "cat .env.example"),
        (launch, "rg CHIMERA_HOOKS chimera/"),
    ):
        sandbox = _Recording()
        out = RunShellTool(workspace, sandbox).run(command=command)
        assert sandbox.commands == [command], f"refused: {command!r} -> {out!r}"


def test_the_threat_model_and_row_13_name_the_switch_fence_and_its_residual() -> None:
    root = Path(__file__).resolve().parents[1]
    model = " ".join((root / "docs" / "hooks-threat-model.md").read_text(encoding="utf-8").split())
    row = next(
        line for line in (root / "docs" / "audits" / "sleeper-channels.md")
        .read_text(encoding="utf-8").splitlines() if line.startswith("| 13 |")
    )
    for text in (model, row):
        assert "`queue_fence.reaches_own_env`" in text
    assert "or Chimera's `.env`, switching the hooks off" in model
    assert "or Chimera's `.env`, switching the hooks off at the next restart" in row
