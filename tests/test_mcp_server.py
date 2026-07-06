"""Tests for Chimera-as-an-MCP-server (M12a).

Covers the pure contract — tool specs + dispatch — with injected fakes, so it runs without
the optional ``mcp`` SDK installed. The SDK-bound ``build``/``serve_stdio`` are thin wrappers
over this dispatch and are exercised only when the extra is present.
"""

from __future__ import annotations

import pytest

from chimera.server import CHIMERA_MCP_TOOLS, ChimeraMCP


def _bridge() -> ChimeraMCP:
    return ChimeraMCP(
        solve=lambda task: f"solved: {task}",
        fuse=lambda prompt: f"fused: {prompt}",
        memory_search=lambda query, k: [f"hit {i} for {query}" for i in range(k)],
    )


def test_tool_specs_are_valid_mcp_shape() -> None:
    names = {spec["name"] for spec in CHIMERA_MCP_TOOLS}
    assert names == {"chimera_solve", "chimera_fuse", "chimera_memory_search"}
    for spec in CHIMERA_MCP_TOOLS:
        assert spec["description"]
        schema = spec["inputSchema"]
        assert schema["type"] == "object"
        for required in schema["required"]:
            assert required in schema["properties"]


def test_dispatch_solve_and_fuse() -> None:
    bridge = _bridge()
    assert bridge.dispatch("chimera_solve", {"task": "ship it"}) == "solved: ship it"
    assert bridge.dispatch("chimera_fuse", {"prompt": "why"}) == "fused: why"


def test_dispatch_memory_search_formats_hits() -> None:
    bridge = _bridge()
    out = bridge.dispatch("chimera_memory_search", {"query": "cats", "k": 2})
    assert out == "- hit 0 for cats\n- hit 1 for cats"


def test_dispatch_memory_search_default_and_bad_k() -> None:
    bridge = _bridge()
    # Missing k -> default 5; non-numeric k -> default 5 (never a crash).
    assert bridge.dispatch("chimera_memory_search", {"query": "x"}).count("\n") == 4
    assert bridge.dispatch("chimera_memory_search", {"query": "x", "k": "oops"}).count("\n") == 4


def test_dispatch_memory_search_empty_is_friendly() -> None:
    bridge = ChimeraMCP(solve=lambda t: t, fuse=lambda p: p, memory_search=lambda q, k: [])
    assert bridge.dispatch("chimera_memory_search", {"query": "nothing"}) == "(no matching memories)"


def test_dispatch_unknown_tool_raises() -> None:
    with pytest.raises(KeyError):
        _bridge().dispatch("chimera_nope", {})
