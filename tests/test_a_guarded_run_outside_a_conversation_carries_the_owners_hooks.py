"""`chimera agent --guard` and a standalone `chimera solve --guard` carry the owner's hooks.

Found by an adversarial review of S30-65: both commands build their kernel with `govern_registry`
directly, so they never reached `govern_step`, which is where the hooks are installed. An owner who
switched hooks on and wrote a `pre_tool` deny saw the call run under `--guard`, with no `hook`
receipt and no warning — while the threat model listed "a guarded `chimera solve`" among the places
hooks apply. Both commands are driven end to end here with a stub agent that makes one tool call.

The hook denies `echo hooked`, a command the kernel itself lets through, so a refusal here can only
have come from the hook, and the `hook` receipt in the audit says which one.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.cli.main import app
from chimera.config import get_settings
from chimera.governance.hooks import HOOKS_FILE_NAME
from chimera.tools.base import Refusal, Tool
from chimera.tools.registry import ToolRegistry

runner = CliRunner()
COMMAND = "echo hooked"


class _Shell(Tool):
    def __init__(self) -> None:
        self.name = "run_shell"
        self.description = "records"
        self.parameters = {"type": "object", "properties": {}}
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> str:
        self.calls.append(dict(kwargs))
        return "ran"


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    home = tmp_path / "home"
    home.mkdir()
    (home / HOOKS_FILE_NAME).write_text(json.dumps({"hooks": [{
        "id": "no-echo", "event": "pre_tool", "tools": ["run_shell"], "pattern": r"echo\s+hooked",
        "decision": "deny", "reason": "not this one",
    }]}), encoding="utf-8")
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_HOOKS", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()
    yield home
    get_settings.cache_clear()


def _hook_receipts(home: Path) -> list[dict[str, Any]]:
    path = home / "audit.jsonl"
    if not path.exists():
        return []
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [entry for entry in lines if entry.get("type") == "hook"]


def _shell_registry(shell: _Shell) -> Any:
    def build(*_a: Any, **_k: Any) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(shell)
        return registry

    return build


def test_chimera_agent_guard_meets_the_owners_pre_tool_deny(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shell = _Shell()
    answers: list[str] = []

    class Agent:
        def __init__(self, _backend: Any, registry: ToolRegistry, _cfg: Any) -> None:
            self.registry = registry

        def run(self, _task: str, **_k: Any) -> Any:
            answers.append(self.registry.get("run_shell").run(command=COMMAND))
            return SimpleNamespace(answer="done", stopped_reason="final", steps=1,
                                   tool_calls_made=1)

    monkeypatch.setattr("chimera.providers.LLMGateway", lambda *a, **k: object())
    monkeypatch.setattr("chimera.tools.default_registry", _shell_registry(shell))
    monkeypatch.setattr("chimera.core.Agent", Agent)
    result = runner.invoke(app, ["agent", "--guard", "say hi"])

    assert result.exception is None, repr(result.exception)
    assert answers, "the stub agent was not reached, so this would prove nothing"
    assert isinstance(answers[0], Refusal) and "no-echo" in answers[0]
    assert shell.calls == []
    assert [r["hook"] for r in _hook_receipts(home)] == ["no-echo"]


def test_chimera_agent_guard_runs_the_call_when_hooks_are_off(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: the same command, the same kernel, hooks off — it runs. Without this the deny
    above could have been the kernel's."""
    monkeypatch.setenv("CHIMERA_HOOKS", "false")
    get_settings.cache_clear()
    shell = _Shell()

    class Agent:
        def __init__(self, _backend: Any, registry: ToolRegistry, _cfg: Any) -> None:
            self.registry = registry

        def run(self, _task: str, **_k: Any) -> Any:
            self.registry.get("run_shell").run(command=COMMAND)
            return SimpleNamespace(answer="done", stopped_reason="final", steps=1,
                                   tool_calls_made=1)

    monkeypatch.setattr("chimera.providers.LLMGateway", lambda *a, **k: object())
    monkeypatch.setattr("chimera.tools.default_registry", _shell_registry(shell))
    monkeypatch.setattr("chimera.core.Agent", Agent)
    result = runner.invoke(app, ["agent", "--guard", "say hi"])

    assert result.exception is None, repr(result.exception)
    assert shell.calls == [{"command": COMMAND}]
    assert _hook_receipts(home) == []


class _Stop(Exception):
    """Raised once the worker's registry is in hand: nothing after it is under test."""


def test_a_standalone_chimera_solve_guard_meets_the_owners_pre_tool_deny(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shell = _Shell()
    registries: list[ToolRegistry] = []

    class Agent:
        def __init__(self, _backend: Any, registry: ToolRegistry, *_a: Any, **_k: Any) -> None:
            registries.append(registry)

    class AutonomousAgent:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            raise _Stop

    monkeypatch.setattr("chimera.providers.LLMGateway", lambda *a, **k: object())
    monkeypatch.setattr("chimera.tools.default_registry", _shell_registry(shell))
    monkeypatch.setattr("chimera.core.Agent", Agent)
    monkeypatch.setattr("chimera.core.AutonomousAgent", AutonomousAgent)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = runner.invoke(app, ["solve", "--guard", "--workspace", str(workspace), "say hi"])

    assert registries, f"the worker was never built: {result.output!r} {result.exception!r}"
    answer = registries[0].get("run_shell").run(command=COMMAND)
    assert isinstance(answer, Refusal) and "no-echo" in answer
    assert shell.calls == []
    assert [r["hook"] for r in _hook_receipts(home)] == ["no-echo"]


def test_owner_hooks_leaves_the_registry_alone_while_hooks_are_off(tmp_path: Path) -> None:
    from chimera.governance.audit import AuditLog
    from chimera.governance.profile import owner_hooks

    registry = ToolRegistry()
    registry.register(_Shell())
    settings = SimpleNamespace(hooks=False, home=tmp_path)
    assert owner_hooks(
        registry, settings=settings, audit=AuditLog(tmp_path / "a.jsonl")
    ) is registry


# --- solve-batch and crew-isolated -------------------------------------------------------------
#
# Found by the second adversarial review: both build a protection layer of their own — a taint
# ledger per worker with an approver — without `govern_step`, so they never reached the hooks. An
# owner's `pre_tool` deny did not stop a batch or crew worker, and the threat model's residual did
# not list either command.


def test_every_solve_batch_worker_meets_the_owners_pre_tool_deny(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sabotage: hand `ledger_registry` the bare `default_registry(ws)` again — the echo runs."""
    import chimera.core as core
    import chimera.orchestration as orch
    from chimera.core.autonomous import AutonomousResult
    from chimera.orchestration.isolation import IsolatedBatch, IsolatedResult

    shells: list[_Shell] = []
    registries: list[ToolRegistry] = []

    def build(*_a: Any, **_k: Any) -> ToolRegistry:
        shells.append(_Shell())
        return _shell_registry(shells[-1])()

    class Agent:
        def __init__(self, _backend: Any, registry: ToolRegistry, *_a: Any, **_k: Any) -> None:
            registries.append(registry)

    def fake_run_isolated(_workspace: Path, units: list[Any], **_k: Any) -> Any:
        results = []
        for name, run in units:
            with contextlib.suppress(_Stop):
                run(tmp_path / name)
            done = AutonomousResult(answer="", success=True, ending="success")
            results.append(IsolatedResult(name=name, ok=True, value=done))
        return IsolatedBatch(results=results, conflicts=[], merged=0)

    def stop(*_a: Any, **_k: Any) -> Any:
        raise _Stop

    monkeypatch.setenv("CHIMERA_APPROVAL_MODE", "deny")
    get_settings.cache_clear()
    monkeypatch.setattr("chimera.providers.LLMGateway", lambda *a, **k: object())
    monkeypatch.setattr("chimera.tools.default_registry", build)
    monkeypatch.setattr(core, "Agent", Agent)
    monkeypatch.setattr(core, "AutonomousAgent", stop)
    monkeypatch.setattr(core, "Planner", lambda *a, **k: None)
    monkeypatch.setattr(core, "Manager", lambda *a, **k: None)
    monkeypatch.setattr(core, "WorkspaceGuard", lambda *a, **k: None)
    monkeypatch.setattr(orch, "run_isolated", fake_run_isolated)
    from chimera.cli import main as cli

    cli.solve_batch(
        tasks=["one", "two"], workspace=".", model=None, max_steps=1, context_budget=None,
        max_attempts=1, max_workers=1, fuse=False, taint=False,
    )
    assert len(registries) == 2, "the workers were never built, so this would prove nothing"
    for registry in registries:
        answer = registry.get("run_shell").run(command=COMMAND)
        assert isinstance(answer, Refusal) and "no-echo" in answer
    assert all(shell.calls == [] for shell in shells)
    assert [r["hook"] for r in _hook_receipts(home)] == ["no-echo", "no-echo"]


def test_every_crew_isolated_worker_meets_the_owners_pre_tool_deny(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sabotage: hand `ledger_registry` the bare `default_registry(ws)` again — the echo runs."""
    shell = _Shell()
    registries: list[ToolRegistry] = []

    class Crew:
        def __init__(self, _backend: Any, workers: list[Any], **_k: Any) -> None:
            registries.extend(w.tools(tmp_path / w.role.name) for w in workers)
            raise _Stop

    monkeypatch.setenv("CHIMERA_APPROVAL_MODE", "deny")
    get_settings.cache_clear()
    monkeypatch.setattr("chimera.providers.LLMGateway", lambda *a, **k: object())
    monkeypatch.setattr("chimera.tools.default_registry", _shell_registry(shell))
    monkeypatch.setattr("chimera.orchestration.IsolatedCrew", Crew)
    result = runner.invoke(app, ["crew-isolated", "say hi", "--worker", "a:do it"])

    assert registries, f"the worker was never built: {result.output!r} {result.exception!r}"
    answer = registries[0].get("run_shell").run(command=COMMAND)
    assert isinstance(answer, Refusal) and "no-echo" in answer
    assert shell.calls == []
    assert [r["hook"] for r in _hook_receipts(home)] == ["no-echo"]
