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


# --- stop, refusal, publication, bounds, and logging -------------------------------------------


def _hanging_server(tmp_path: Path) -> tuple[Path, Path]:
    """A stdio server that writes its own pid, then never answers the handshake."""
    pidfile = tmp_path / "pid.txt"
    script = tmp_path / "hang.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import os, time
            open({str(pidfile)!r}, "w").write(str(os.getpid()))
            time.sleep(120)
            """
        ),
        encoding="utf-8",
    )
    return script, pidfile


def test_a_shutdown_during_the_connect_leaves_no_server_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured by the process, not by the pool's state: a stop mid-handshake must end the child.

    The child is a real interpreter that never answers `initialize`, so the connect is in progress
    when shutdown() runs. Without the tracked session, nothing closes it until this process exits.
    """
    from chimera.core.service_record import pid_alive
    from tests.service_harness import wait_dead

    script, pidfile = _hanging_server(tmp_path)
    cfg = {
        "name": "hang",
        "command": sys.executable,
        "args": [str(script)],
        "env": {},
        "connect_timeout": 60,
    }
    settings = _settings(_home(tmp_path, [cfg]), monkeypatch)

    mcp_pool.start(settings)
    deadline = time.monotonic() + 20
    while not pidfile.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert pidfile.exists(), "the server never started, so there is nothing to stop"
    pid = int(pidfile.read_text(encoding="utf-8"))
    assert pid_alive(pid), "precondition: the server is running while the connect is in progress"

    mcp_pool.shutdown()

    assert mcp_pool._done.wait(15), "the connect thread did not return after shutdown()"
    assert wait_dead(pid, 15), "the server process outlived shutdown() — nothing closed it"


def test_a_stop_before_the_connect_starts_means_no_connect_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(_home(tmp_path, [_server("rapido")]), monkeypatch)
    started: list[str] = []
    _plan(monkeypatch, {"rapido": _Fake()})
    from chimera.integrations import mcp_config

    original = mcp_config._session_for

    def counting(cfg: Any, timeout: float) -> Any:
        started.append(cfg.name)
        return original(cfg, timeout)

    monkeypatch.setattr(mcp_config, "_session_for", counting)
    mcp_pool.shutdown()

    assert mcp_pool.start(settings) is None
    assert mcp_pool.connectors(settings) is None
    assert started == []


def test_a_refused_argument_is_logged_as_refused_with_its_position(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from chimera.integrations import mcp_config
    from chimera.proc.stdio import CmdArgumentRefused

    class _Refusing:
        def start(self) -> Any:
            raise CmdArgumentRefused(1, "%")

        def close(self) -> None:
            return None

    monkeypatch.setattr(mcp_config, "_session_for", lambda cfg, timeout: _Refusing())
    settings = _settings(_home(tmp_path, [_server("loja")]), monkeypatch)
    caplog.set_level("WARNING")

    pool = mcp_pool.start(settings)
    assert mcp_pool._done.wait(10)

    assert pool is not None and pool.names() == []
    text = caplog.text
    assert "refused server 'loja'" in text, "a refused server must not read as a skipped one"
    assert "argument 1" in text and "'%'" in text, "the log must name the argument and the character"
    assert "skipping server" not in text


def test_the_pool_is_published_before_its_servers_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller that looks while the connect runs must find the pool, not nothing."""
    settings = _settings(_home(tmp_path, [_server("lento")]), monkeypatch)
    seen: dict[str, Any] = {}

    def slow_fill(pool: Any, servers: Any, path: Any) -> None:
        seen["published"] = mcp_pool._pool is pool
        time.sleep(0.3)

    monkeypatch.setattr(mcp_pool, "_fill", slow_fill)
    mcp_pool._build(settings, mcp_pool._publish)

    assert seen["published"] is True


def test_a_conversation_waits_for_a_connect_that_is_about_to_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(_home(tmp_path, [_server("lento")]), monkeypatch)
    _plan(monkeypatch, {"lento": _Fake(delay=1.0)})

    mcp_pool.start(settings)
    assert mcp_pool.wait_built(0.1) is False, "still connecting: the wait must report it"
    assert mcp_pool.wait_built(10) is True, "the wait must return once the connect has finished"
    assert "lento" in mcp_pool._pool.names()


def test_a_crash_in_the_background_connect_is_logged_with_its_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(_home(tmp_path, [_server("rapido")]), monkeypatch)

    def broken_fill(*_args: Any) -> None:
        raise RuntimeError("simulated crash in the fill")

    monkeypatch.setattr(mcp_pool, "_fill", broken_fill)
    caplog.set_level("ERROR")

    mcp_pool.start(settings)
    assert mcp_pool._done.wait(10)

    assert "background connect failed" in caplog.text
    assert "simulated crash in the fill" in caplog.text, "the traceback must reach the log"
    assert mcp_pool.pool_state(settings).connecting is False


def test_the_service_answers_its_health_check_while_a_server_still_connects(tmp_path: Path) -> None:
    """A real HTTP request to the real service, made before the slow server gives up.

    The server's own `connect_timeout` is set to 40 s, and the gap between the moment `service start`
    returns and the moment the skip is logged is what proves that value is the one in force. A gap
    near 10 s would mean the pool's default was used instead.
    """
    import urllib.request

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
                        "connect_timeout": 40,
                    }
                ]
            ),
            encoding="utf-8",
        )

        info = sandbox.start()
        returned = time.monotonic()
        url = info["url"]

        # The guard wants the owner's token from the service record, as every client does.
        token = sandbox.record().token
        request = urllib.request.Request(url + "/api/health", headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=10) as resp:
            assert resp.status == 200, "the service did not answer its health check"
        assert "skipping server 'lento'" not in sandbox.log(), (
            "the server gave up before the health check — the probe does not measure the boot"
        )

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and "skipping server 'lento'" not in sandbox.log():
            time.sleep(0.5)
        skipped_after = time.monotonic() - returned
        assert "skipping server 'lento'" in sandbox.log()
        assert skipped_after >= 25, (
            f"the skip came {skipped_after:.1f} s after start returned; a 40 s connect_timeout "
            "should leave well over 25 s — a figure near 10 s means the default was used"
        )
    finally:
        sandbox.close()
