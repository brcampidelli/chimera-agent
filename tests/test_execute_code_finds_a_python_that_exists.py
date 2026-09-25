"""`execute_code` runs a Python the machine has, not the literal name `python` (study 24, 2026-09-24).

Measured in `bench/tool_loop_silent_failure`: on a WSL Ubuntu with `python3` and no `python`, 743 of 748
failing `execute_code` calls answered `python: not found`. The tool had never run a line of code there,
and the same holds on any machine without the alias (Debian and Ubuntu without `python-is-python3`,
current macOS).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from chimera.sandbox import LocalSandbox
from chimera.sandbox.base import SandboxResult
from chimera.sandbox.docker import DockerSandbox
from chimera.tools import code as code_mod
from chimera.tools.code import ExecuteCodeTool, python_command


def test_it_runs_where_no_python_is_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # PATH with no interpreter on it: the literal `python` fails; this process's own interpreter does not.
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    out = ExecuteCodeTool(workspace=tmp_path, sandbox=LocalSandbox()).run(code="print(6 * 7)")
    assert out.startswith("[exit 0]"), out
    assert "42" in out


def test_the_local_command_is_this_interpreter_by_absolute_path() -> None:
    cmd = python_command(LocalSandbox(), ".chimera_exec_1.py")
    assert cmd is not None
    assert cmd.lstrip("'\"").startswith(sys.executable)
    assert ".chimera_exec_1.py" in cmd


def test_a_frozen_build_looks_on_path_and_says_so_when_nothing_is_there(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(code_mod.shutil, "which", lambda name: "/opt/py/bin/python3" if name == "python3" else None)
    cmd = python_command(LocalSandbox(), "x.py")
    if code_mod.os.name == "nt":  # Windows looks for python, then py
        assert cmd is None
    else:
        assert cmd is not None and cmd.startswith("/opt/py/bin/python3")

    monkeypatch.setattr(code_mod.shutil, "which", lambda name: None)
    assert python_command(LocalSandbox(), "x.py") is None


def test_no_interpreter_is_an_error_that_names_what_was_looked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(code_mod.shutil, "which", lambda name: None)
    out = ExecuteCodeTool(workspace=tmp_path, sandbox=LocalSandbox()).run(code="print(1)")
    assert out.startswith("error: no Python interpreter found on PATH")
    assert not list(tmp_path.glob(".chimera_exec_*.py"))  # nothing written for a run that cannot happen


def test_a_container_resolves_its_own_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = DockerSandbox()
    monkeypatch.setattr(sandbox, "available", lambda: True)
    cmd = python_command(sandbox, "x.py")
    assert cmd is not None and "command -v python3 || command -v python" in cmd
    assert sys.executable not in cmd  # a path from this machine means nothing inside the container


def test_a_docker_sandbox_without_a_daemon_runs_here(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = DockerSandbox()
    monkeypatch.setattr(sandbox, "available", lambda: False)
    cmd = python_command(sandbox, "x.py")
    assert cmd is not None and sys.executable in cmd  # it falls back to the local sandbox


def test_another_kind_of_sandbox_keeps_the_command_it_had() -> None:
    class Elsewhere:
        def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
            return SandboxResult(exit_code=0, stdout="")

    assert python_command(Elsewhere(), "x.py") == 'python "x.py"'
