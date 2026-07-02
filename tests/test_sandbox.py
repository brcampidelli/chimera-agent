"""Tests for the execution sandboxes (local + docker backend, no real Docker)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.sandbox import DockerSandbox, LocalSandbox, get_sandbox
from chimera.sandbox.base import SandboxResult

# --- LocalSandbox (real subprocess, hermetic) ----------------------------------------


def test_local_sandbox_runs_command() -> None:
    result = LocalSandbox().run('python -c "print(123)"')
    assert result.exit_code == 0
    assert "123" in result.stdout


def test_local_sandbox_reports_nonzero_exit() -> None:
    result = LocalSandbox().run('python -c "import sys; sys.exit(3)"')
    assert result.exit_code == 3


def test_local_sandbox_times_out() -> None:
    result = LocalSandbox().run('python -c "import time; time.sleep(5)"', timeout=1)
    assert result.timed_out is True


# --- DockerSandbox (no real Docker) --------------------------------------------------


def test_docker_falls_back_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class RecordingFallback:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
            self.calls.append(command)
            return SandboxResult(exit_code=0, stdout="local")

    fallback = RecordingFallback()
    sandbox = DockerSandbox(fallback=fallback)
    monkeypatch.setattr(sandbox, "_available", False)  # force "docker not available"
    result = sandbox.run("whoami")
    assert fallback.calls == ["whoami"]
    assert result.stdout == "local"


def test_docker_builds_isolated_run_command(monkeypatch: pytest.MonkeyPatch) -> None:
    import chimera.sandbox.docker as docker_mod

    sandbox = DockerSandbox(image="chimera-sandbox")
    monkeypatch.setattr(sandbox, "_available", True)  # skip the docker-version probe

    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout="out", stderr="")

    monkeypatch.setattr(docker_mod.subprocess, "run", fake_run)
    result = sandbox.run("echo hi", cwd=Path("scratch"))

    argv = captured["argv"]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--network" in argv and "none" in argv  # network isolated by default
    assert "chimera-sandbox" in argv and "sh" in argv and "echo hi" in argv
    assert result.exit_code == 0 and result.stdout == "out"


def test_get_sandbox_selects_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_SANDBOX", "docker")
    get_settings.cache_clear()
    assert isinstance(get_sandbox(), DockerSandbox)

    monkeypatch.setenv("CHIMERA_SANDBOX", "local")
    get_settings.cache_clear()
    assert isinstance(get_sandbox(), LocalSandbox)


# --- RunShellTool uses the sandbox seam ----------------------------------------------


def test_run_shell_uses_injected_sandbox() -> None:
    from chimera.tools.shell import RunShellTool

    class FakeSandbox:
        def __init__(self) -> None:
            self.seen: tuple[str, int] | None = None

        def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
            self.seen = (command, timeout)
            return SandboxResult(exit_code=0, stdout="hi")

    fake = FakeSandbox()
    out = RunShellTool(sandbox=fake).run(command="echo hi")
    assert "[exit 0]" in out and "hi" in out
    assert fake.seen == ("echo hi", 180)  # default timeout raised for slow scripts


def test_run_shell_reports_timeout() -> None:
    from chimera.tools.shell import RunShellTool

    class TimeoutSandbox:
        def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
            return SandboxResult(exit_code=124, stderr="x", timed_out=True)

    out = RunShellTool(sandbox=TimeoutSandbox()).run(command="sleep 9", timeout=1)
    assert "timed out" in out
