"""The app's port opens before the MCP servers finish connecting.

Measured on a restart of the 0.79.0 app: the service took about a hundred seconds to answer, because
boot connected every configured MCP server in turn before it bound the port, and the desktop app
showed "Failed to fetch" for all of it. A `uvx` server that never answered cost the full connect
timeout, and the `npx` one cost it again.

The fix starts the connects on a background thread and returns the pool at once. The pool fills as
servers arrive. A turn that comes first waits a short bounded time and then runs with what has
connected. None of that may fail the boot, and a server that never connects must leave the pool
honest about it.
"""

from __future__ import annotations

import json
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from chimera.api import mcp_api
from chimera.config import Settings
from chimera.integrations import mcp_pool
from tests.service_harness import ServiceSandbox


class _Fake:
    """A stdio session stand-in. ``delay`` is how long its handshake takes; ``fail`` makes it raise."""

    def __init__(self, delay: float = 0.0, fail: bool = False) -> None:
        self.delay = delay
        self.fail = fail

    def start(self) -> _Fake:
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("the server did not answer")
        return self

    def list_tools(self) -> list[Any]:
        return []

    def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _fresh_pool() -> Any:
    mcp_pool.reset_for_tests()
    yield
    # A connect still running on its thread must finish before the next test starts, or its
    # flag clears under the next test's feet.
    mcp_pool._done.wait(15)
    mcp_pool.reset_for_tests()


def _home(tmp_path: Path, servers: list[dict[str, Any]]) -> Path:
    home = tmp_path / ".chimera"
    home.mkdir(parents=True, exist_ok=True)
    (home / "mcp.json").write_text(json.dumps(servers), encoding="utf-8")
    return home


def _settings(home: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_MCP_AUTOLOAD", "1")
    settings = Settings()
    assert settings.mcp_autoload and settings.home == home, "the fixture did not build what it claims"
    return settings


def _plan(monkeypatch: pytest.MonkeyPatch, sessions: dict[str, _Fake]) -> None:
    """Each named server gets the fake session chosen for it, instead of a real subprocess."""
    from chimera.integrations import mcp_config

    def fake_session(cfg: Any, _timeout: float) -> _Fake:
        return sessions[cfg.name]

    monkeypatch.setattr(mcp_config, "_session_for", fake_session)


def _server(name: str) -> dict[str, Any]:
    return {"name": name, "command": "x", "args": [], "env": {}}


def test_start_returns_before_a_slow_server_has_connected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(_home(tmp_path, [_server("rapido"), _server("lento")]), monkeypatch)
    _plan(monkeypatch, {"rapido": _Fake(), "lento": _Fake(delay=2.0)})

    began = time.monotonic()
    pool = mcp_pool.start(settings)
    took = time.monotonic() - began

    assert pool is not None
    assert took < 1.0, f"start() waited for a 2 s handshake ({took:.2f} s) — the port would wait too"
    assert mcp_pool.pool_state(settings).connecting is True


def test_the_pool_fills_as_the_servers_arrive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(_home(tmp_path, [_server("rapido"), _server("lento")]), monkeypatch)
    _plan(monkeypatch, {"rapido": _Fake(), "lento": _Fake(delay=1.0)})

    pool = mcp_pool.start(settings)
    assert mcp_pool._done.wait(10), "the background connect never finished"

    assert sorted(pool.names()) == ["lento", "rapido"]
    state = mcp_pool.pool_state(settings)
    assert state.connecting is False
    assert sorted(state.connected or ()) == ["lento", "rapido"]


def test_a_turn_during_the_connect_waits_a_bounded_time_and_runs_with_what_arrived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(_home(tmp_path, [_server("rapido"), _server("lento")]), monkeypatch)
    _plan(monkeypatch, {"rapido": _Fake(), "lento": _Fake(delay=4.0)})
    monkeypatch.setattr(mcp_pool, "_FIRST_RUN_WAIT", 0.5)

    pool = mcp_pool.start(settings)
    deadline = time.monotonic() + 5
    while "rapido" not in pool.names() and time.monotonic() < deadline:
        time.sleep(0.02)

    began = time.monotonic()
    got = mcp_pool.connectors(settings)
    took = time.monotonic() - began

    assert got is not None and got.names() == ["rapido"], "a turn must use what has connected"
    assert took < 2.0, f"the turn waited {took:.2f} s, past its bound of 0.5 s"


def test_a_server_that_never_connects_does_not_leave_the_pool_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed connect is a skipped server, not a boot that stays "connecting" forever."""
    settings = _settings(_home(tmp_path, [_server("quebrado"), _server("rapido")]), monkeypatch)
    _plan(monkeypatch, {"quebrado": _Fake(fail=True), "rapido": _Fake()})

    mcp_pool.start(settings)
    assert mcp_pool._done.wait(10)

    state = mcp_pool.pool_state(settings)
    assert state.connecting is False
    assert state.connected == ("rapido",)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_failure_in_the_fill_still_clears_the_connecting_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(_home(tmp_path, [_server("rapido")]), monkeypatch)

    def broken_fill(*_args: Any) -> None:
        raise MemoryError("simulated failure outside the per-server try")

    monkeypatch.setattr(mcp_pool, "_fill", broken_fill)

    mcp_pool.start(settings)
    assert mcp_pool._done.wait(10), "a crash in the fill left the boot waiting on the event"

    assert mcp_pool.pool_state(settings).connecting is False


def test_the_reach_read_says_connecting_until_the_server_arrives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(_home(tmp_path, [_server("rapido"), _server("lento")]), monkeypatch)
    _plan(monkeypatch, {"rapido": _Fake(), "lento": _Fake(delay=2.0)})
    monkeypatch.setattr("chimera.config.get_settings", lambda: settings)

    pool = mcp_pool.start(settings)
    deadline = time.monotonic() + 5
    while "rapido" not in pool.names() and time.monotonic() < deadline:
        time.sleep(0.02)

    pending = mcp_api._reach("lento", settings.home)
    assert pending == {"reaches_agent": False, "reaches_agent_reason": "connecting"}, (
        "a server still connecting must not be reported as added after the connect"
    )
    assert mcp_api._reach("rapido", settings.home)["reaches_agent"] is True

    assert mcp_pool._done.wait(10)
    assert mcp_api._reach("lento", settings.home)["reaches_agent"] is True


def test_autoload_off_starts_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path, [_server("rapido")])
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_MCP_AUTOLOAD", "0")
    settings = Settings()
    _plan(monkeypatch, {"rapido": _Fake()})

    assert mcp_pool.start(settings) is None
    assert mcp_pool.pool_state(settings).connecting is False


# --- the real service, on a real port ---------------------------------------------------------

_SLOW_SERVER = textwrap.dedent(
    """
    import sys, time
    # Never answers the handshake. The server gives up at its own connect timeout and is skipped.
    time.sleep(120)
    sys.exit(0)
    """
)


class _AutoloadSandbox(ServiceSandbox):
    """The harness with MCP autoload on, so the boot has a server to connect."""

    def env(self) -> dict[str, str]:
        env = super().env()
        env["CHIMERA_MCP_AUTOLOAD"] = "1"
        return env


def test_the_service_answers_before_a_slow_mcp_server_has_connected(tmp_path: Path) -> None:
    """The boot log says the port opened before the server gave up.

    With the connect on the boot path, the server's "skipping" warning came out BEFORE the
    "Chimera Desktop on" line, because the port waited for it. Now the line is printed first, and the
    server gives up only at its own 40 s connect timeout. The order is what this measures: it is
    deterministic, where a wall-clock bound on a cold interpreter is not. (A bounded wait of a few
    seconds at boot would pass this test; the defect it guards is waiting out the connect itself.)
    """
    sandbox = _AutoloadSandbox(tmp_path)
    try:
        slow = tmp_path / "slow_server.py"
        slow.write_text(_SLOW_SERVER, encoding="utf-8")
        (sandbox.home / "mcp.json").write_text(
            json.dumps(
                [
                    {
                        "name": "lento",
                        "command": sys.executable,
                        "args": [str(slow)],
                        "env": {},
                        # Far enough off that the order below is decided by the boot, not by timing.
                        "connect_timeout": 40,
                    }
                ]
            ),
            encoding="utf-8",
        )

        info = sandbox.start()
        assert info, "the service did not start"

        deadline = time.monotonic() + 120
        log = ""
        while time.monotonic() < deadline:
            log = sandbox.log()
            if "skipping server 'lento'" in log:
                break
            time.sleep(1.0)
        assert "skipping server 'lento'" in log, f"the slow server was never given up on:\n{log[-2000:]}"

        port_line = log.find("Chimera Desktop on")
        skip_line = log.find("skipping server 'lento'")
        assert port_line != -1, "the boot never printed its address"
        assert port_line < skip_line, (
            "the port was announced only after the MCP connect gave up — boot still waits on the servers"
        )
    finally:
        sandbox.close()
