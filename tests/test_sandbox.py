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


def test_local_sandbox_closes_stdin_so_reads_do_not_hang() -> None:
    # A command that reads stdin gets EOF immediately (stdin=DEVNULL), instead of blocking on input
    # the agent will never provide and burning the whole timeout. The anti-hang correctness fix.
    result = LocalSandbox().run('python -c "import sys; sys.stdin.read()"', timeout=5)
    assert result.timed_out is False
    assert result.exit_code == 0


def test_local_sandbox_runs_non_interactively() -> None:
    # The non-interactive env is applied, so git never opens an editor/pager or prompts.
    result = LocalSandbox().run('python -c "import os;print(os.environ[\'GIT_EDITOR\'])"')
    assert "true" in result.stdout
    result = LocalSandbox().run('python -c "import os;print(os.environ[\'GIT_TERMINAL_PROMPT\'])"')
    assert "0" in result.stdout


def test_docker_run_is_non_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    # The docker sandbox passes the non-interactive env and closes stdin too.
    argv = _capture_docker_argv(DockerSandbox(image="chimera-sandbox"), monkeypatch)
    assert "-e" in argv and "GIT_TERMINAL_PROMPT=0" in argv and "GIT_EDITOR=true" in argv


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
    assert "--runtime" not in argv  # daemon default (runc) unless configured
    assert result.exit_code == 0 and result.stdout == "out"


def _capture_docker_argv(sandbox: DockerSandbox, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    import chimera.sandbox.docker as docker_mod

    monkeypatch.setattr(sandbox, "_available", True)  # skip the docker-version probe
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(docker_mod.subprocess, "run", fake_run)
    sandbox.run("echo hi")
    return captured["argv"]


def test_docker_injects_gvisor_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    # gVisor (runsc) is a drop-in OCI runtime; it must land as a `--runtime runsc` flag.
    argv = _capture_docker_argv(DockerSandbox(runtime="runsc"), monkeypatch)
    assert "--runtime" in argv
    assert argv[argv.index("--runtime") + 1] == "runsc"


def test_docker_blank_runtime_is_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    # A whitespace/empty runtime must not emit a dangling `--runtime` flag.
    argv = _capture_docker_argv(DockerSandbox(runtime="  "), monkeypatch)
    assert "--runtime" not in argv


def test_get_sandbox_selects_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_SANDBOX", "docker")
    get_settings.cache_clear()
    assert isinstance(get_sandbox(), DockerSandbox)

    monkeypatch.setenv("CHIMERA_SANDBOX", "local")
    get_settings.cache_clear()
    assert isinstance(get_sandbox(), LocalSandbox)


def test_get_sandbox_reads_runtime_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_SANDBOX", "docker")
    monkeypatch.setenv("CHIMERA_SANDBOX_RUNTIME", "runsc")
    get_settings.cache_clear()
    sandbox = get_sandbox()
    assert isinstance(sandbox, DockerSandbox)
    assert sandbox.runtime == "runsc"


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
    assert fake.seen == ("echo hi", 60)


def test_run_shell_reports_timeout() -> None:
    from chimera.tools.shell import RunShellTool

    class TimeoutSandbox:
        def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
            return SandboxResult(exit_code=124, stderr="x", timed_out=True)

    out = RunShellTool(sandbox=TimeoutSandbox()).run(command="sleep 9", timeout=1)
    assert "timed out" in out
