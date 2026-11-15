"""`chimera agent` is built by `build_right_hand`, like `chat`, `assist` and `tui` (audit P0-15).

It built `default_registry` by hand and wrapped a kernel only under `--guard`, so the command a shell
script or a cron line can drive ran with no taint ledger, no reach floor and no kernel in the
deployment's mode — and `tests/test_governed_surfaces.py` carried an exemption that said it was fine
because "a person is at the terminal". Nothing about the command makes that true.

The first test asserts the construction itself, by replacing the builder and reading what it was
asked for; on the old code the builder is never called and the list stays empty. The last drives the
real builder and checks the registry the agent is handed is wrapped, which one built by hand is not.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.cli.main import app
from chimera.config import get_settings

runner = CliRunner()


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()
    yield tmp_path / "home"
    get_settings.cache_clear()


class _Agent:
    """Stands in for the loop: records what it was handed and answers at once."""

    handed: list[Any] = []

    def __init__(self, _backend: Any, registry: Any, _cfg: Any) -> None:
        _Agent.handed.append(registry)

    def run(self, _task: str, **_k: Any) -> Any:
        return SimpleNamespace(answer="done", stopped_reason="final", steps=1, tool_calls_made=0)


def test_agent_asks_the_governed_builder_for_its_registry(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[dict[str, Any]] = []
    turns: list[str] = []

    def fake_builder(workspace: Path, **kwargs: Any) -> Any:
        asked.append({"workspace": workspace, **kwargs})
        return SimpleNamespace(registry=object(), begin_turn=turns.append)

    monkeypatch.setattr("chimera.providers.LLMGateway", lambda *a, **k: object())
    monkeypatch.setattr("chimera.core.Agent", _Agent)
    monkeypatch.setattr("chimera.cli.right_hand.build_right_hand", fake_builder)

    result = runner.invoke(
        app, ["agent", "--guard", "--allow-tools", "read_file", "--deny-tools", "run_shell", "say hi"]
    )

    assert result.exception is None, repr(result.exception)
    assert len(asked) == 1, "chimera agent did not build its registry with build_right_hand"
    assert asked[0]["surface"] == "agent"
    # The per-session tool lists and --guard (the kernel, enforcing) keep their meaning.
    assert asked[0]["allow"] == "read_file" and asked[0]["deny"] == "run_shell"
    assert asked[0]["mode"] == "enforce"
    # The taint ledger is told whose words the turn is, as every other terminal surface does.
    assert turns == ["say hi"]


def test_agent_without_guard_leaves_the_kernel_mode_to_the_deployment(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[dict[str, Any]] = []

    def fake_builder(workspace: Path, **kwargs: Any) -> Any:
        asked.append(kwargs)
        return SimpleNamespace(registry=object(), begin_turn=lambda _m: None)

    monkeypatch.setattr("chimera.providers.LLMGateway", lambda *a, **k: object())
    monkeypatch.setattr("chimera.core.Agent", _Agent)
    monkeypatch.setattr("chimera.cli.right_hand.build_right_hand", fake_builder)

    result = runner.invoke(app, ["agent", "say hi"])

    assert result.exception is None, repr(result.exception)
    # An empty mode is "whatever CHIMERA_GOVERNANCE_MODE says" — no change to the default.
    assert asked[0]["mode"] == "" and asked[0]["allow"] is None and asked[0]["deny"] is None


def test_the_registry_the_agent_is_handed_is_wrapped_by_the_taint_ledger(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Agent.handed.clear()
    monkeypatch.setattr("chimera.providers.LLMGateway", lambda *a, **k: object())
    monkeypatch.setattr("chimera.core.Agent", _Agent)

    result = runner.invoke(app, ["agent", "say hi"])

    assert result.exception is None, repr(result.exception)
    assert _Agent.handed, "the stub agent was not reached, so this proves nothing"
    tool = _Agent.handed[0].get("run_shell")
    # A tool built by hand has no ledger; one built by build_right_hand is wrapped by it.
    assert getattr(tool, "ledger", None) is not None
