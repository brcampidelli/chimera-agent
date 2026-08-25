"""One configured server, one connection — however many surfaces ask for it.

Found by watching processes on a running app rather than by reading code. With autoload on and a
single server in `mcp.json`:

    API up, no turn run ........ 1 connection   (the chat path, at boot)
    after one turn on Code ..... 2 connections  (the coding path, its own)

Both paths were correct in isolation and the pair was not: two subprocesses per configured server,
two Docker containers for the GitHub entry, and its sign-in run twice. The chat path built its
connectors as a local inside the `app` command, closed over by the session factory and reachable
from nowhere else — so when the coding surfaces needed MCP tools, a second set was the only thing
available to build.

The tests assert the COUNT OF CONNECTS, not the count of tools. A test that only checked the tools
arrive passes just as happily with two connections as with one, which is how this shipped.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.config import Settings
from chimera.integrations import mcp_pool


@pytest.fixture(autouse=True)
def _fresh_pool():
    mcp_pool.reset_for_tests()
    yield
    mcp_pool.reset_for_tests()


def _settings(tmp_path, monkeypatch) -> Settings:
    home = tmp_path / ".chimera"
    home.mkdir(parents=True, exist_ok=True)
    (home / "mcp.json").write_text(
        '[{"name": "srv", "command": "x", "args": [], "env": {}}]', encoding="utf-8"
    )
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_MCP_AUTOLOAD", "1")
    settings = Settings()
    assert settings.mcp_autoload and settings.home == home, (
        "the fixture did not produce the settings it claims to — nothing below measures anything"
    )
    return settings


class _Sessao:
    """A session that records that it was started, and lists one tool."""

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    def start(self) -> _Sessao:
        _conexoes.append(1)
        return self

    def list_tools(self) -> list[dict[str, str]]:
        return [{"name": "t", "description": "d"}]

    def call_tool(self, name: str, args: dict[str, Any]) -> str:  # pragma: no cover - unused here
        return ""


_conexoes: list[int] = []


@pytest.fixture(autouse=True)
def _zera_contagem(monkeypatch):
    _conexoes.clear()
    monkeypatch.setattr("chimera.integrations.StdioMCPSession", _Sessao)
    yield


def test_asking_twice_connects_once(tmp_path, monkeypatch) -> None:
    """The pool's whole reason to exist, asserted on the number that shows it."""
    settings = _settings(tmp_path, monkeypatch)

    primeiro = mcp_pool.connectors(settings)
    segundo = mcp_pool.connectors(settings)

    assert primeiro is segundo, "two callers got two different pools"
    assert len(_conexoes) == 1, (
        f"one configured server produced {len(_conexoes)} connections — each is a subprocess, and "
        "for the GitHub entry each is a container and a sign-in"
    )


def test_many_callers_at_once_still_connect_once(tmp_path, monkeypatch) -> None:
    """Turns arrive concurrently, and a fan-out builds a registry per worker.

    Without the lock each of them starts its own set of subprocesses — the failure the pool exists
    to prevent, reached by the code that prevents it.
    """
    import threading

    settings = _settings(tmp_path, monkeypatch)
    resultados: list[Any] = []

    fios = [
        threading.Thread(target=lambda: resultados.append(mcp_pool.connectors(settings)))
        for _ in range(8)
    ]
    for f in fios:
        f.start()
    for f in fios:
        f.join()

    assert len(_conexoes) == 1, f"8 concurrent callers made {len(_conexoes)} connections"
    assert all(r is resultados[0] for r in resultados), "concurrent callers got different pools"


def test_the_chat_path_goes_through_the_pool_rather_than_building_its_own() -> None:
    """Asserted on the source, because the alternative is booting a real app in a unit test.

    The line it checks is the line that was wrong: `chimera app` used to construct
    `StdioMCPSession` itself, which is what made the second connection.
    """
    from pathlib import Path

    fonte = (Path(__file__).resolve().parent.parent / "chimera" / "cli" / "main.py").read_text(
        encoding="utf-8"
    )
    inicio = fonte.index("mcp_connectors = None")
    trecho = fonte[inicio : inicio + 900]

    assert "mcp_pool.connectors(" in trecho, (
        "the app command no longer gets its MCP connections from the shared pool"
    )
    assert "StdioMCPSession(" not in trecho, (
        "the app command is opening its own MCP sessions again — one configured server becomes two "
        "subprocesses, and for GitHub two sign-ins"
    )
