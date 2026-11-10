"""The product's default: built-in tools are on demand, and nothing a surface needs is moved.

`CHIMERA_DEFER_TOOLS` is on by default since the owner's decision of 2026-11-10. The conftest pins the
old value for the rest of the suite; this file removes that pin on purpose and checks the new default,
and what deferral must NOT touch: the turn-bound built-ins a surface hands a sink to, and every tool
that is not a built-in (connectors, plugins, a caller's own).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.config import Settings
from chimera.tools.builtin import default_registry
from chimera.tools.defer import CORE, TURN_BOUND, defer_builtins

pytestmark = pytest.mark.product_default  # every test here is about the default and what deferral keeps


def test_the_setting_defaults_to_on() -> None:
    assert Settings(_env_file=None).defer_tools is True  # type: ignore[call-arg]


def test_a_real_assembly_declares_the_core_and_hides_the_rest_behind_tool_list(tmp_path: Path) -> None:
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.providers import LLMGateway

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))  # type: ignore[arg-type]
    registry, _ = assemble_registry(CodeSeams(), tmp_path, settings, LLMGateway(), steps=4)
    names = set(registry.names())

    assert names >= CORE
    assert "tool_list" in names
    assert "scrape" not in names, "a built-in outside the core must be behind the proxy by default"


def test_turn_bound_builtins_are_never_moved(tmp_path: Path) -> None:
    full = default_registry(tmp_path)
    lean, _moved = defer_builtins(full)

    for name in TURN_BOUND & set(full.names()):
        assert name in lean.names(), f"{name} needs a sink its surface hands it; it must stay declared"


def test_a_tool_that_is_not_a_builtin_is_never_moved(tmp_path: Path) -> None:
    class _Connector:
        """Stands in for a connector tool: defined outside `chimera.tools`, so not a built-in."""

        name = "github_search_code"
        description = "search"
        parameters: dict[str, Any] = {"type": "object", "properties": {}}

        def run(self, **_: Any) -> str:
            return "ok"

        def to_openai_schema(self, **_: Any) -> dict[str, Any]:
            return {"type": "function", "function": {"name": self.name, "parameters": self.parameters}}

    registry = default_registry(tmp_path)
    registry.register(_Connector())  # type: ignore[arg-type]
    lean, _moved = defer_builtins(registry)

    assert "github_search_code" in lean.names(), "a connector stays declared; CHIMERA_MCP_DEFER governs it"
    assert "github_search_code" not in lean.deferred


def test_the_shipped_list_is_the_list_the_source_defines() -> None:
    """`defer.SHIPPED_BUILTINS` is what deferral moves. Re-derive the names from the source, so a built-in
    added to `chimera/tools` without a line in that list fails here instead of staying declared silently."""
    import re

    from chimera.tools import defer

    root = Path(__file__).resolve().parents[1] / "chimera" / "tools"
    found: set[str] = set()
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        found |= set(re.findall(r'^\s+name\s*=\s*"([a-z_][a-z0-9_]*)"', text, re.M))
        found |= set(re.findall(r'ConditionalTool\(\s*"([a-z_][a-z0-9_]*)"', text))
        found |= set(re.findall(r'ConditionalTool\(\s*\n\s*"([a-z_][a-z0-9_]*)"', text))
    found -= {"echo", "tool_list", "tool_describe", "tool_call"}  # test-only, and the proxies

    assert found - defer.TURN_BOUND - defer.SHIPPED_BUILTINS == set(), "a built-in is missing from SHIPPED_BUILTINS"
    assert defer.SHIPPED_BUILTINS - found == set(), "SHIPPED_BUILTINS names a tool the source does not define"


def test_the_repl_approver_reaches_a_deferred_open_pull_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With deferral on, `open_pull_request` is not declared; the terminal's approver must still be installed."""
    import chimera.governance.approval as approval
    from chimera.cli.right_hand import build_right_hand
    from chimera.config import get_settings

    built: list[bool] = []

    def spy(*_a: Any, **_k: Any) -> Any:
        built.append(True)
        return lambda *_x, **_y: True

    monkeypatch.setattr(approval, "always_ask", spy)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    try:
        right_hand = build_right_hand(tmp_path, settings=get_settings(), surface="chat")
    finally:
        get_settings.cache_clear()

    assert "open_pull_request" not in right_hand.registry.names(), "this test is about the deferred arm"
    assert built, "no approver was installed on a deferred open_pull_request"
