"""Host-execution confirmation gate — the shell/code tools must ask before running on the host."""

from __future__ import annotations

from pathlib import Path

from chimera.sandbox.base import SandboxResult
from chimera.sandbox.confirm import resolve_host_exec_confirm
from chimera.tools.code import ExecuteCodeTool
from chimera.tools.shell import RunShellTool


class _RecordingSandbox:
    """A host sandbox that records whether it was actually asked to run."""

    def __init__(self, *, isolated: bool = False) -> None:
        self.ran = False
        self._isolated = isolated

    def is_isolated(self) -> bool:
        return self._isolated

    def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
        self.ran = True
        return SandboxResult(exit_code=0, stdout="ran")


def test_shell_declined_does_not_execute(tmp_path: Path) -> None:
    sb = _RecordingSandbox()
    tool = RunShellTool(tmp_path, sb, confirm=lambda _cmd: False)
    out = tool.run(command="rm -rf /")
    assert sb.ran is False  # the command never reached the host
    assert "declined" in out.lower()


def test_shell_approved_executes(tmp_path: Path) -> None:
    seen: list[str] = []
    sb = _RecordingSandbox()
    tool = RunShellTool(tmp_path, sb, confirm=lambda cmd: seen.append(cmd) or True)
    out = tool.run(command="echo hi")
    assert sb.ran is True
    assert seen == ["echo hi"]  # confirm saw the exact command
    assert "[exit 0]" in out


def test_shell_isolated_sandbox_skips_confirm(tmp_path: Path) -> None:
    # An isolated container needs no host-exec confirm: the callback must not even be consulted.
    called = False

    def confirm(_cmd: str) -> bool:
        nonlocal called
        called = True
        return False

    sb = _RecordingSandbox(isolated=True)
    tool = RunShellTool(tmp_path, sb, confirm=confirm)
    tool.run(command="echo hi")
    assert called is False
    assert sb.ran is True


def test_shell_no_confirm_is_unchanged(tmp_path: Path) -> None:
    # confirm=None (the default for headless/library callers) preserves the exact prior behaviour.
    sb = _RecordingSandbox()
    tool = RunShellTool(tmp_path, sb)  # no confirm
    tool.run(command="echo hi")
    assert sb.ran is True


def test_execute_code_declined_does_not_run(tmp_path: Path) -> None:
    sb = _RecordingSandbox()
    tool = ExecuteCodeTool(tmp_path, sb, confirm=lambda _summary: False)
    out = tool.run(code="import os; os.system('bad')")
    assert sb.ran is False
    assert "declined" in out.lower()


def test_execute_code_approved_runs(tmp_path: Path) -> None:
    sb = _RecordingSandbox()
    tool = ExecuteCodeTool(tmp_path, sb, confirm=lambda _summary: True)
    tool.run(code="print(1)")
    assert sb.ran is True


class _Settings:
    def __init__(self, sandbox: str, host_exec: str) -> None:
        self.sandbox = sandbox
        self.host_exec = host_exec


def test_resolver_does_not_special_case_docker_config() -> None:
    # REGRESSION (adversarial review 2026-07-18): a docker *config* is NOT proof of isolation —
    # DockerSandbox falls back to the host when the daemon is down. The resolver must return the
    # posture callback regardless of `sandbox`; whether to skip is decided by the actual sandbox's
    # is_isolated() at the tool. Returning None here (the old bug) made `deny` a no-op and let a
    # fallen-back docker run on the host ungated.
    assert resolve_host_exec_confirm(_Settings("docker", "deny"), interactive=True) is not None
    assert resolve_host_exec_confirm(_Settings("docker", "deny"), interactive=True)("x") is False
    # ask under a docker config still resolves to a real callback (the tool skips it only if isolated).
    assert resolve_host_exec_confirm(_Settings("docker", "ask"), interactive=True) is not None


def test_fallen_back_docker_is_gated_not_isolated(tmp_path: Path) -> None:
    # The HIGH the review found end-to-end: sandbox=docker but the daemon is down → not isolated →
    # the host-exec gate MUST fire (deny → refused), not silently run on the host.
    sb = _RecordingSandbox(isolated=False)  # a docker sandbox that fell back to local
    tool = RunShellTool(tmp_path, sb, confirm=lambda _cmd: False)  # a deny-style gate
    out = tool.run(command="echo PWNED")
    assert sb.ran is False
    assert "declined" in out.lower()


def test_non_callable_is_isolated_does_not_crash(tmp_path: Path) -> None:
    # REGRESSION (review LOW): a backend exposing is_isolated as a truthy ATTRIBUTE (not a method)
    # must be treated as host (the safe direction), not crash with `TypeError: 'bool' not callable`.
    class _BadSandbox:
        is_isolated = True  # attribute, not a method

        def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
            return SandboxResult(exit_code=0, stdout="ran")

    tool = RunShellTool(tmp_path, _BadSandbox(), confirm=lambda _cmd: False)
    out = tool.run(command="echo hi")  # must not raise
    assert "declined" in out.lower()  # treated as host → gated


def test_resolver_allow_is_ungated() -> None:
    assert resolve_host_exec_confirm(_Settings("local", "allow"), interactive=True) is None


def test_resolver_deny_refuses() -> None:
    gate = resolve_host_exec_confirm(_Settings("local", "deny"), interactive=True)
    assert gate is not None
    assert gate("anything") is False  # deny never runs on the host


def test_resolver_ask_headless_runs_with_warning() -> None:
    # Non-interactive `ask` must not break cron/CI/bench: it returns a callback that permits the run.
    gate = resolve_host_exec_confirm(_Settings("local", "ask"), interactive=False)
    assert gate is not None
    assert gate("echo hi") is True


def test_resolver_ask_interactive_returns_a_prompt() -> None:
    # We can't drive a real TTY here; assert it resolves to a (non-None) prompt callback.
    gate = resolve_host_exec_confirm(_Settings("local", "ask"), interactive=True)
    assert gate is not None
