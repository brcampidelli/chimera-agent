"""A server the user connected in the app now exists for ``chat`` and ``assist`` too.

``mcp_pool.connectors(settings)`` is called at ``chimera/api/code_api.py:498`` and
``chimera/cli/main.py:2206`` — the Code screen and ``chimera app`` — and was called **nowhere** in
the ``chat``/``assist``/``tui`` bodies. So a person could connect GitHub, watch the Test button
prove it live, and find that their terminal right-hand had no idea it existed, ``CHIMERA_MCP_AUTOLOAD``
or not.

Where in the assembly matters as much as whether. The tools go in **before** the deployment fence,
where ``assemble_registry`` and ``chimera run`` put them and for the reason both write down: a
denylist that covers only the tools we wrote is not a denylist. Everything after that line — the
reach floor, the trust kernel, the taint ledger — therefore covers them, which is the whole reason
the two governed REPLs are a safe place to mount them and the TUI is not.

Everything here is free: the pool is replaced by a stub, so nothing is spawned and no server runs.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from chimera.cli.right_hand import build_right_hand
from chimera.config import get_settings
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry


class _ServerTool(Tool):
    name = "github_create_issue"
    description = "Open an issue."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    #: What `MCPTool` carries (`mcp_client.py:49`): a server's answer is external content.
    untrusted_output = True

    def run(self, **kwargs: Any) -> str:
        return "opened"


class _Pool:
    """What ``ConnectorRegistry`` offers the two call sites that already mount MCP."""

    def __init__(self) -> None:
        self.mounted = 0

    def into_tool_registry(self, registry: ToolRegistry) -> None:
        self.mounted += 1
        registry.register(_ServerTool())

    def all_tools(self) -> list[Tool]:
        return [_ServerTool()]

    def names(self) -> list[str]:
        return ["github"]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    for var in ("CHIMERA_MCP_AUTOLOAD", "CHIMERA_MCP_DEFER", "CHIMERA_TOOL_DENYLIST"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _pool(monkeypatch: pytest.MonkeyPatch) -> _Pool:
    pool = _Pool()
    monkeypatch.setattr("chimera.integrations.mcp_pool.connectors", lambda _s: pool)
    return pool


def _hand(workspace: Path) -> Any:
    return build_right_hand(workspace, settings=get_settings(), surface="chat")


# -- mounted, and only when asked -----------------------------------------------------------------


def test_the_servers_tools_reach_the_terminals_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CHIMERA_MCP_AUTOLOAD", "1")
    get_settings.cache_clear()
    pool = _pool(monkeypatch)
    hand = _hand(tmp_path)
    assert pool.mounted == 1
    assert "github_create_issue" in hand.registry.names()


def test_with_autoload_off_nothing_is_connected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The default. A stock install must behave exactly as it did and spawn nothing."""
    asked: list[Any] = []

    def _connectors(settings: Any) -> Any:
        asked.append(settings)
        return _Pool()

    monkeypatch.setattr("chimera.integrations.mcp_pool.connectors", _connectors)
    hand = _hand(tmp_path)
    assert asked == [], "the pool was consulted with autoload off"
    assert "github_create_issue" not in hand.registry.names()


def test_a_denylist_can_still_remove_a_server_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ordering claim, made falsifiable.

    Mounted AFTER the fence, this name would be unreachable by any list an owner can write — the
    one tool in the registry that no denylist covers.
    """
    monkeypatch.setenv("CHIMERA_MCP_AUTOLOAD", "1")
    monkeypatch.setenv("CHIMERA_TOOL_DENYLIST", "github_create_issue")
    get_settings.cache_clear()
    _pool(monkeypatch)
    hand = _hand(tmp_path)
    assert "github_create_issue" not in hand.registry.names()


def test_the_deferred_shape_is_honoured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``CHIMERA_MCP_DEFER=1`` replaces N schemas with three access tools, here as elsewhere."""
    monkeypatch.setenv("CHIMERA_MCP_AUTOLOAD", "1")
    monkeypatch.setenv("CHIMERA_MCP_DEFER", "1")
    get_settings.cache_clear()
    _pool(monkeypatch)
    names = _hand(tmp_path).registry.names()
    assert {"mcp_list", "mcp_describe", "mcp_call"} <= set(names)
    assert "github_create_issue" not in names


def test_the_bench_seam_does_not_mount_servers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``base=`` exists so the injection bench drives stub tools at US$ 0 and executes nothing.

    Pouring the machine's real servers into that registry would make a bench whose whole point is
    that it costs nothing reach the network.
    """
    monkeypatch.setenv("CHIMERA_MCP_AUTOLOAD", "1")
    get_settings.cache_clear()
    pool = _pool(monkeypatch)
    build_right_hand(tmp_path, settings=get_settings(), surface="bench", base=ToolRegistry())
    assert pool.mounted == 0


# -- the catalogue proxies are external text too ---------------------------------------------------


def test_every_deferred_proxy_declares_untrusted_output() -> None:
    """``docs/audits/sleeper-channels.md`` channel 10, open question 6.

    ``mcp_call`` carried the flag; ``mcp_list`` and ``mcp_describe`` did not, and what they return
    is a **server-authored description** — external text arriving as an observation. The taint
    layer keys fencing and run-tainting off this flag (`ledger_tool.py:177-185`), so without it a
    hostile server's description reached the model unfenced and did not taint the run. It stayed
    open while only fenced surfaces mounted MCP; mounting it where a person sits is what made it
    reachable.
    """
    from chimera.integrations.mcp_defer import McpCallTool, McpDescribeTool, McpListTool

    for proxy in (McpListTool, McpDescribeTool, McpCallTool):
        assert getattr(proxy, "untrusted_output", False) is True, proxy.__name__


def test_the_ledger_treats_a_catalogue_answer_as_external(tmp_path: Path) -> None:
    """The flag is not decoration: it is what the fence is built from."""
    from chimera.governance.ledger import TaintLedger
    from chimera.governance.ledger_tool import FENCE_OPEN, LedgeredTool
    from chimera.integrations.mcp_defer import register_deferred_mcp

    registry = ToolRegistry()
    register_deferred_mcp(_Pool(), registry)
    ledger = TaintLedger()
    fenced = LedgeredTool(registry.get("mcp_list"), ledger)
    assert FENCE_OPEN in fenced.run(query="")
    assert ledger.run_tainted() is True
