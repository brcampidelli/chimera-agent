"""A tool an agent DECLARES is still granted when `CHIMERA_DEFER_TOOLS` moves it out of the list.

Deferral (on by default since 2026-11-10) takes the built-ins outside files, search and shell out of
the declared list and puts them behind `tool_list` / `tool_call`. The names are still the same tools.
Before this, a Markdown agent or a subagent that named one of them in its `tools:` was granted nothing
and nobody was told: the grant was read against the declared list, so the tool vanished in silence.
These tests pin the grant, and that the deferred tool object is the one handed over.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.config import Settings
from chimera.tools.builtin import default_registry
from chimera.tools.defer import CORE, defer_builtins


def _deferred_name(registry: object) -> str:
    """A built-in that deferral really moves: not in the core, and present in the default registry."""
    names = [n for n in registry.names() if n not in CORE]  # type: ignore[attr-defined]
    assert names, "the default registry has nothing to defer, so this test would prove nothing"
    return names[0]


def test_deferral_moves_a_builtin_out_of_the_declared_list_but_keeps_it_declared(tmp_path: Path) -> None:
    full = default_registry(tmp_path)
    name = _deferred_name(full)

    lean, moved = defer_builtins(full)

    assert moved > 0
    assert name not in lean.names(), "deferral must take the tool out of the list the model sees"
    assert name in lean.declared_names(), "and it must stay grantable by name"
    assert lean.deferred[name] is full.get(name), "the same tool object, not a copy or a stub"


def test_available_tools_keeps_a_deferred_builtin_so_an_agent_can_declare_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api.commands_api import available_tools

    name = _deferred_name(default_registry(tmp_path))
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_DEFER_TOOLS", "1")
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))  # type: ignore[arg-type]

    assert settings.defer_tools is True
    assert name in available_tools(tmp_path, settings), (
        "a Markdown agent declaring this tool would have it silently dropped from its effective tools"
    )


def test_a_subagent_grant_reaches_the_deferred_tool(tmp_path: Path) -> None:
    from chimera.core.subagent import _declared

    full = default_registry(tmp_path)
    name = _deferred_name(full)
    lean, _moved = defer_builtins(full)

    assert name in _declared(lean)
    assert name not in lean.names()


def test_a_subagent_cannot_be_granted_a_denied_deferred_tool(tmp_path: Path) -> None:
    full = default_registry(tmp_path)
    name = _deferred_name(full)

    lean, _moved = defer_builtins(full, denied=frozenset({name}))

    assert name not in lean.deferred
    assert name not in lean.declared_names()
