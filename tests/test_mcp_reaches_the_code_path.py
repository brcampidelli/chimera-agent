"""A server the user connected has to exist on the surfaces they connect it for.

The chat path has had MCP tools since autoload existed. Every other surface — the Code screen, a
run, both orchestration routes — builds through `assemble_registry`, which started from
`default_registry` and never poured them in. So somebody could add the GitHub server, press Test,
watch it prove itself live, and find the coding agent had no idea it existed.

**The half that matters is not that the tools appear.** It is WHERE they appear in the assembly. The
chat path records the lesson at its own injection point — *"a denylist that covers only the tools we
wrote is not a denylist"* — and the same applies here twice over, because this surface has two more
layers the chat path does not: the trust kernel, and a taint ledger that exists precisely because
tool output from outside is untrusted. Pouring MCP tools in AFTER those would hand the model a set
of tools that no denial, no kernel verdict and no taint rule can touch.

So most of this file asserts placement, not presence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.api.code_api import CodeSeams, assemble_registry
from chimera.config import Settings
from chimera.integrations import mcp_pool


class _FakeTool:
    """The shape `ConnectorRegistry` yields: a named, callable tool."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = "from a connected server"
        self.parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def run(self, **_kw: Any) -> str:
        return "ok"


class _FakePool:
    """Stands in for the connected servers, so no subprocess is spawned by a test."""

    def __init__(self, *names: str) -> None:
        self._tools = [_FakeTool(n) for n in names]

    def names(self) -> list[str]:
        return [t.name for t in self._tools]

    def into_tool_registry(self, registry: Any) -> int:
        for tool in self._tools:
            if tool.name not in registry:
                registry.register(tool)
        return len(self._tools)


@pytest.fixture(autouse=True)
def _pool_limpo():
    """Process-wide state that a test can set and never clear makes the next test depend on it."""
    mcp_pool.reset_for_tests()
    yield
    mcp_pool.reset_for_tests()


def _monta(tmp_path: Path, monkey: pytest.MonkeyPatch, pool: Any, **seam: Any):
    # Patched on the MODULE, not on an attribute of `code_api`: the import there is inside the
    # function, so there is nothing named `mcp_pool` at its module level to reach for.
    monkey.setattr(mcp_pool, "connectors", lambda _s: pool)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    registry, ledger = assemble_registry(
        CodeSeams(**seam), tmp_path, settings, object(), steps=3, surface="turn"
    )
    return registry, ledger


def test_a_connected_server_reaches_the_coding_surface(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry, _ = _monta(tmp_path, monkeypatch, _FakePool("github_search_code"))

    assert "github_search_code" in registry, (
        "the Code screen still assembles without the servers the user connected"
    )


def test_nothing_is_added_when_autoload_is_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The control, and the default. A stock install must behave exactly as it did."""
    registry, _ = _monta(tmp_path, monkeypatch, None)

    assert not [n for n in registry.names() if n.startswith("github_")]


def test_the_denylist_reaches_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reason placement matters more than presence.

    A tool poured in after `restrict_registry` is one no denial can touch — an owner who fenced
    their agent would have a fence with a hole shaped exactly like the servers they added.
    """
    registry, _ = _monta(
        tmp_path,
        monkeypatch,
        _FakePool("github_search_code", "github_delete_repository"),
        deny_tools=["github_delete_repository"],
    )

    assert "github_search_code" in registry
    assert "github_delete_repository" not in registry, (
        "an MCP tool survived an explicit denial — it was registered after the filter"
    )


def test_an_allowlist_reaches_them_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The other direction of the same guard: an allowlist that lists only builtins must not
    silently admit every tool an MCP server chose to publish."""
    registry, _ = _monta(
        tmp_path, monkeypatch, _FakePool("github_search_code"), allow_tools=["read_file"]
    )

    assert "read_file" in registry
    assert "github_search_code" not in registry


def test_they_are_wrapped_like_every_other_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The taint ledger exists because content from outside is untrusted, and an MCP server IS
    outside — it is the single most obvious source of injected instructions on this surface.

    Asserted by identity rather than by type name: what came back must NOT be the bare tool the
    connector handed over, because everything between here and there is a wrapper.
    """
    pool = _FakePool("github_search_code")
    cru = pool._tools[0]
    registry, ledger = _monta(tmp_path, monkeypatch, pool)

    assert ledger is not None
    assert registry.get("github_search_code") is not cru, (
        "the MCP tool reached the model unwrapped — neither the kernel nor the ledger sees its calls"
    )


def test_a_server_cannot_shadow_a_builtin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A remote server picks its own tool names. `read_file` here respects the write region and the
    workspace root; a connector's `read_file` respects whatever its author decided."""
    registry, _ = _monta(tmp_path, monkeypatch, _FakePool("read_file"))

    # The assertion has to be that the CONNECTOR's tool lost, not merely that something is there.
    # The first version ended in `or registry.get(...) is not None`, which passes for any registry
    # that has a `read_file` at all — including one where the server's version won.
    conector = _FakePool("read_file")._tools[0]
    registry2, _ = _monta(tmp_path, monkeypatch, _FakePool("read_file"))
    del registry2

    achado = registry.get("read_file")
    assert achado is not None
    assert type(achado).__name__ != type(conector).__name__, (
        "a connected server published `read_file` and replaced the builtin that respects the "
        "write region"
    )
