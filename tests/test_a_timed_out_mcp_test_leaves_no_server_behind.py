"""A Test that timed out left the server it started running, and "Add and test" made that common.

``probe_tools`` called ``start()`` OUTSIDE its ``try/finally``, so when the handshake did not finish
inside the timeout the ``close()`` in the finally never ran. And ``close()`` could not have stopped it
anyway: it set an event that only a session which had finished its handshake waits on, waited ten
seconds, and stopped the event loop under a task that was still suspended inside ``initialize`` —
so the stdio client never exited and the server's process lived until the app closed.

The case that makes this matter is a server that signs in through the browser (the mcp-remote
bridge for Notion and Supabase, and for Stripe when its key is refused): the first Test downloads
the package and waits on a person, overruns twelve seconds, and leaves a process holding a sign-in
nobody will use, while the screen records the test as failed. Every further click left another.

The handshake is stood in for by a coroutine that never returns, so these run without the ``mcp``
package and without spawning anything; what they check is that the task holding the server's
streams is unwound, which is what ends the subprocess.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import types
from typing import Any

import pytest

from chimera.integrations.mcp_client import StdioMCPSession
from chimera.integrations.mcp_config import McpServerConfig, probe_tools


def _a_handshake_that_never_answers(torn_down: threading.Event) -> Any:
    async def _serve(*_args: Any) -> None:
        try:
            await asyncio.sleep(3600)  # a bridge waiting for a browser sign-in that does not come
        finally:
            torn_down.set()  # where stdio_client would terminate the server's process

    return _serve


@pytest.fixture
def no_sdk_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    # start() checks the SDK is importable before spawning anything; the stand-in never uses it.
    monkeypatch.setitem(sys.modules, "mcp", types.ModuleType("mcp"))


def test_closing_a_session_that_never_became_ready_unwinds_its_handshake(
    no_sdk_needed: None,
) -> None:
    torn_down = threading.Event()
    session = StdioMCPSession("npx", ["-y", "mcp-remote"], connect_timeout=0.3)
    session._serve = _a_handshake_that_never_answers(torn_down)  # type: ignore[method-assign]  # the stand-in handshake

    with pytest.raises(TimeoutError):
        session.start()
    inicio = time.monotonic()
    session.close()

    assert torn_down.is_set(), (
        "close() returned with the handshake still suspended - the server's process outlives it"
    )
    assert time.monotonic() - inicio < 5, "close() sat out its whole wait instead of cancelling"


def test_a_test_that_times_out_still_closes_the_server_it_started(
    no_sdk_needed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    torn_down = threading.Event()
    monkeypatch.setattr(StdioMCPSession, "_serve", _a_handshake_that_never_answers(torn_down))
    cfg = McpServerConfig(name="notion", command="npx", args=["-y", "mcp-remote"])

    with pytest.raises(TimeoutError):
        probe_tools(cfg, connect_timeout=0.3)

    assert torn_down.wait(5), "a probe that timed out never closed the session it had started"


def test_a_server_that_signs_in_through_the_browser_gets_time_for_a_person() -> None:
    from chimera.api.mcp_api import _TEST_CONNECT_TIMEOUT, _TEST_SIGNIN_TIMEOUT, _test_timeout
    from chimera.integrations.mcp_catalog import CATALOG

    # Now that a timed-out Test is torn down, a sign-in that does not fit inside the test can never
    # finish. Twelve seconds is for a server that answers or does not; a person needs more.
    assert _TEST_SIGNIN_TIMEOUT > _TEST_CONNECT_TIMEOUT
    por_id = {e.id: e for e in CATALOG}

    def _cfg(entry_id: str) -> McpServerConfig:
        e = por_id[entry_id]
        return McpServerConfig(name=e.id, command=e.command, args=list(e.args))

    for entry_id in ("notion", "supabase", "stripe", "hostinger", "github"):
        assert _test_timeout(_cfg(entry_id)) == _TEST_SIGNIN_TIMEOUT, entry_id
    # A hand-written bridge is the same bridge.
    hand = McpServerConfig(name="x", command="npx", args=["-y", "mcp-remote@0.1.0", "https://x"])
    assert _test_timeout(hand) == _TEST_SIGNIN_TIMEOUT

    # A server that answers on its own keeps the short bound: a hung one still fails fast.
    assert _test_timeout(_cfg("sentry")) == _TEST_CONNECT_TIMEOUT
    plain = McpServerConfig(name="fs", command="uvx", args=["mcp-server-fetch"])
    assert _test_timeout(plain) == _TEST_CONNECT_TIMEOUT


# --- the other three callers -----------------------------------------------------------------------
#
# The fix above lived in `probe_tools`, which learned to wrap `start()` in try/finally. The pool, the
# CLI's autoload and `connect_stdio` all write `StdioMCPSession(...).start()` and never hold the
# session when start raises, so they had nothing to close. With autoload on and a browser sign-in
# entry saved (Notion, Hostinger), the first turn's 10 s connect timed out on the sign-in and left the
# bridge alive for the rest of the app. The close now happens inside `start()`, which covers all of
# them; these call each path the way it is called in production.


def test_a_start_that_times_out_closes_what_it_started_without_being_asked(
    no_sdk_needed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    torn_down = threading.Event()
    monkeypatch.setattr(StdioMCPSession, "_serve", _a_handshake_that_never_answers(torn_down))

    with pytest.raises(TimeoutError):
        StdioMCPSession("npx", ["-y", "mcp-remote"], connect_timeout=0.3).start()

    assert torn_down.wait(5), "a timed-out start left its server running for whoever never closes it"


def test_the_pool_does_not_leave_a_timed_out_server_running(
    no_sdk_needed: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from chimera.config import Settings
    from chimera.integrations import mcp_pool

    torn_down = threading.Event()
    monkeypatch.setattr(StdioMCPSession, "_serve", _a_handshake_that_never_answers(torn_down))
    monkeypatch.setattr(mcp_pool, "_CONNECT_TIMEOUT", 0.3)
    home = tmp_path / ".chimera"
    home.mkdir()
    (home / "mcp.json").write_text(
        '[{"name": "notion", "command": "npx", "args": ["-y", "mcp-remote"], "env": {}}]',
        encoding="utf-8",
    )
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_MCP_AUTOLOAD", "1")

    assert mcp_pool._build(Settings()) is None  # skipped, as before - a broken server never breaks a turn

    assert torn_down.wait(5), "the pool skipped the server and left its process waiting on a sign-in"


def test_autoload_does_not_leave_a_timed_out_server_running(
    no_sdk_needed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.integrations.mcp_config import autoload_into_registry
    from chimera.tools.registry import ToolRegistry

    torn_down = threading.Event()
    monkeypatch.setattr(StdioMCPSession, "_serve", _a_handshake_that_never_answers(torn_down))
    cfg = McpServerConfig(name="hostinger", command="npx", args=["-y", "@hostinger/mcp"])

    assert autoload_into_registry(ToolRegistry(), [cfg], connect_timeout=0.3) == 0

    assert torn_down.wait(5), "autoload skipped the server and left its process waiting on a sign-in"


def test_a_start_that_fails_to_connect_stops_the_thread_it_started(no_sdk_needed: None) -> None:
    async def _refused(self: StdioMCPSession) -> None:
        # What the real `_serve` does with a handshake that fails: record it and wake start().
        self._connect_error = ConnectionError("the server exited during the handshake")
        self._ready.set()

    session = StdioMCPSession("npx", ["-y", "nope"], connect_timeout=5)
    session._serve = types.MethodType(_refused, session)  # type: ignore[method-assign]  # the stand-in handshake

    with pytest.raises(ConnectionError):
        session.start()

    session._thread.join(5)
    assert not session._thread.is_alive(), "a failed start left its event loop thread running"
