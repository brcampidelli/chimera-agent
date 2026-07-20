"""Host-execution confirmation gate — the shell/code tools must ask before running on the host."""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.sandbox.base import SandboxResult
from chimera.sandbox.confirm import resolve_host_exec_confirm
from chimera.tools.code import CodeInterpreterTool, ExecuteCodeTool
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
    # The HIGH the review found, end-to-end through the REAL resolver: sandbox=docker but the daemon
    # is down → not isolated → the gate must fire. Building the callback with resolve_host_exec_confirm
    # (not a hand-written lambda) is what makes this non-vacuous: with the docker short-circuit back
    # in place the resolver returns None, no gate is passed, and this test fails as it should.
    gate = resolve_host_exec_confirm(_Settings("docker", "deny"), interactive=False)
    sb = _RecordingSandbox(isolated=False)  # a docker sandbox that fell back to local
    tool = RunShellTool(tmp_path, sb, confirm=gate)
    out = tool.run(command="echo PWNED")
    assert sb.ran is False
    assert "declined" in out.lower()


def test_code_interpreter_honours_host_exec_deny() -> None:
    # REGRESSION (adversarial review 2026-07-18, HIGH): code_interpreter exec()s in-process, so it is
    # host execution by construction and used to be registered with no gate at all. `deny` was
    # therefore bypassable — the model just picks this tool instead of run_shell. Proven live:
    # run_shell/execute_code refused while code_interpreter printed PWNED_VIA_INTERPRETER.
    gate = resolve_host_exec_confirm(_Settings("docker", "deny"), interactive=False)
    tool = CodeInterpreterTool(confirm=gate)
    out = tool.run(code="print('PWNED_VIA_INTERPRETER')")
    assert "declined" in out.lower()
    assert "PWNED" not in out


def test_default_registry_gates_every_host_exec_door(monkeypatch: pytest.MonkeyPatch) -> None:
    # The wiring, not just the class: it is default_registry() that the agent actually gets. The
    # class-level test above passes even with `CodeInterpreterTool()` registered ungated, so only
    # this one proves the door is shut in production. deny+docker must block ALL THREE doors —
    # a gap in any one of them makes CHIMERA_HOST_EXEC=deny meaningless, since the model just
    # picks whichever tool is not gated.
    from chimera.config import get_settings
    from chimera.tools.builtin import default_registry

    monkeypatch.setenv("CHIMERA_HOST_EXEC", "deny")
    monkeypatch.setenv("CHIMERA_SANDBOX", "docker")  # a config that is NOT proof of isolation
    # ...and pin that "not proof" rather than inheriting it from the machine. `sandbox_is_isolated`
    # asks the Docker daemon at runtime, so without this the test asserts different things in
    # different places: on a box with no daemon the sandbox falls back to the host and the gate
    # fires (what we want to prove), while on one WITH a daemon the command runs contained and the
    # gate is correctly skipped — so this failed only on CI runners, which have Docker. Forcing the
    # not-isolated answer tests the gap that matters: docker configured, isolation absent.
    monkeypatch.setattr(RunShellTool, "_sandbox_is_isolated", staticmethod(lambda _s: False))
    monkeypatch.setattr("chimera.tools.code.sandbox_is_isolated", lambda _s: False)
    get_settings.cache_clear()
    try:
        registry = default_registry()
        calls = {
            "run_shell": {"command": "id"},
            "execute_code": {"code": "import os; os.system('id')"},
            "code_interpreter": {"code": "print('PWNED_VIA_INTERPRETER')"},
        }
        for name, kwargs in calls.items():
            out = registry.get(name).run(**kwargs)
            assert "declined" in out.lower(), f"{name} ran on the host under deny"
            assert "PWNED" not in out
    finally:
        get_settings.cache_clear()


def test_real_isolation_lets_the_sandboxed_doors_run_but_not_the_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the contract, which no test covered: isolation ACTUALLY present.

    `deny` means "never on the host" — not "never". When the daemon is genuinely reachable, the
    shell/code doors run inside the container and the gate is rightly skipped; only
    ``code_interpreter`` stays shut, because it ``exec()``s in this process and a container being up
    never excuses it. Pinning this stops a future change from silently turning the isolation escape
    into a bypass of `deny` for the interpreter too — the case the CI failure made visible.
    """
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOST_EXEC", "deny")
    monkeypatch.setenv("CHIMERA_SANDBOX", "docker")
    monkeypatch.setattr(RunShellTool, "_sandbox_is_isolated", staticmethod(lambda _s: True))
    monkeypatch.setattr("chimera.tools.code.sandbox_is_isolated", lambda _s: True)
    get_settings.cache_clear()
    try:
        gate = resolve_host_exec_confirm(_Settings("docker", "deny"), interactive=False)
        # The interpreter has no isolation escape: still refused, container or not.
        out = CodeInterpreterTool(confirm=gate).run(code="print('PWNED_VIA_INTERPRETER')")
        assert "declined" in out.lower()
        assert "PWNED" not in out
    finally:
        get_settings.cache_clear()


def test_code_interpreter_gate_ignores_sandbox_isolation() -> None:
    # There is deliberately no is_isolated() escape here: the interpreter never uses the sandbox, so
    # a container being up must NOT excuse it. Under `ask`+headless it runs (non-breaking); the point
    # is that the gate is consulted at all.
    seen: list[str] = []
    tool = CodeInterpreterTool(confirm=lambda summary: seen.append(summary) or True)
    out = tool.run(code="x = 41\nprint(x + 1)")
    assert out == "42"
    assert seen and seen[0].startswith("code_interpreter: x = 41")


def test_code_interpreter_without_gate_is_unchanged() -> None:
    # confirm=None (library callers, allow posture) keeps the exact prior behaviour, including state.
    tool = CodeInterpreterTool()
    tool.run(code="counter = 1")
    assert tool.run(code="print(counter + 1)") == "2"  # session state persists


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


def test_execute_code_non_callable_is_isolated_does_not_crash(tmp_path: Path) -> None:
    # REGRESSION (review MED): the shell half of the non-callable fix had a test, the code.py half had
    # none — the suite was byte-identical with it reverted. Both now share sandbox_is_isolated().
    class _BadSandbox:
        is_isolated = True  # attribute, not a method

        def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
            return SandboxResult(exit_code=0, stdout="ran")

    tool = ExecuteCodeTool(tmp_path, _BadSandbox(), confirm=lambda _s: False)
    assert "declined" in tool.run(code="print(1)").lower()  # treated as host → gated, no TypeError


def test_execute_code_isolated_sandbox_skips_confirm(tmp_path: Path) -> None:
    # The other direction of the shared helper: a genuinely isolated container is not gated.
    called = False

    def confirm(_s: str) -> bool:
        nonlocal called
        called = True
        return False

    sb = _RecordingSandbox(isolated=True)
    ExecuteCodeTool(tmp_path, sb, confirm=confirm).run(code="print(1)")
    assert called is False
    assert sb.ran is True


def test_headless_warning_is_per_resolve_not_per_process() -> None:
    # REGRESSION (review MED): the warn-once flag was module-global, so a long-lived process
    # (chimera serve / the API / a cron daemon) logged ONE warning ever and then host-executed
    # silently forever — defeating the "the risk is visible in logs" rationale. Each resolve (i.e.
    # each run) must get a fresh flag: two runs → two warnings.
    logged: list[str] = []

    import chimera.sandbox.confirm as confirm_mod

    class _Rec:
        def warning(self, msg: str, *args: object) -> None:
            logged.append(msg)

    original = confirm_mod._log
    confirm_mod._log = _Rec()  # type: ignore[assignment]
    try:
        for _run in range(2):
            gate = resolve_host_exec_confirm(_Settings("local", "ask"), interactive=False)
            assert gate is not None
            for _cmd in range(3):  # repeated commands inside one run warn once
                assert gate("echo hi") is True
    finally:
        confirm_mod._log = original
    assert len(logged) == 2  # one per run, not one per process and not one per command


def test_resolver_ask_interactive_returns_a_prompt() -> None:
    # We can't drive a real TTY here; assert it resolves to a (non-None) prompt callback.
    gate = resolve_host_exec_confirm(_Settings("local", "ask"), interactive=True)
    assert gate is not None
