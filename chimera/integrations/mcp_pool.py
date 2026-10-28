"""The configured MCP servers, connected once per process and shared by every surface.

The chat path has had MCP tools since autoload existed: ``chimera app`` connects the servers at
boot and hands the connectors to the session factory. Every other surface — the Code screen, a run,
both orchestration routes — builds its registry through
:func:`~chimera.api.code_api.assemble_registry`, which starts from ``default_registry`` and never
saw them. So a user could connect GitHub, watch the Test button prove it live, and find the coding
agent had no idea it existed.

**Why this is a pool and not a connect-per-call.** ``assemble_registry`` runs once per TURN, and per
worker inside a fan-out. Connecting there without reuse would spawn a Docker container per message
and tear it down again — the GitHub server alone would re-run its OAuth handshake every time. The
sessions are long-lived by design (``autoload_into_registry`` documents the same thing), so they
belong to the process, not to the request.

**Connected once, never reconnected.** Editing ``mcp.json`` does not take effect until restart, which
is exactly what the MCP screen already tells the user about the autoload toggle. Reconnecting on a
config change sounds friendlier and would leak the previous sessions — a subprocess per edit, held
open by a registry nobody points at any more.

**Off unless asked.** With ``mcp_autoload`` off — the default — nothing here spawns anything, and a
stock install behaves exactly as it did.
"""

from __future__ import annotations

import atexit
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.config import Settings
from chimera.telemetry import get_logger

_log = get_logger("integrations.mcp_pool")

#: Same bound the CLI uses. A server that cannot answer in ten seconds is skipped rather than
#: waited on. The wait lands on the background connect, and on a turn that arrives during it (bounded
#: by :data:`_FIRST_RUN_WAIT`); a turn that hangs on somebody's broken config is worse than a turn
#: without their tools.
_CONNECT_TIMEOUT = 10.0

#: How long a turn waits for a pool that is still connecting. The servers are connected in the
#: background so the app's port opens at once (see :func:`start`), so a turn that arrives in that
#: window waits this long and then runs with whatever has connected. Bounded on purpose: a turn is
#: never held for a server that is still starting, and a server that never answers costs the wait
#: once, not on every turn.
_FIRST_RUN_WAIT = 5.0

#: How long a conversation begun during boot waits for the servers still connecting. Each server
#: may take :data:`_CONNECT_TIMEOUT` to answer, and the connects run one after another, so this is a
#: bound on a typical boot rather than a promise; anything later than it is still missing.
CONVERSATION_WAIT = 30.0

_lock = threading.Lock()
_pool: Any = None
_tried = False
#: True while the background connect is running. Separate from ``_tried`` because a test (and the
#: status read) can set ``_tried`` without any connect happening, and "connecting" must not be inferred
#: from that.
_building = False
_done = threading.Event()
#: Set by :func:`shutdown`. Checked between servers, so the connect thread stops starting new ones.
_stopping = False
#: Every session this pool has started or is starting, so :func:`shutdown` can close one that is
#: still mid-handshake. Without it a stop during the connect leaves a server process that nothing
#: closes; the SDK's Windows job object only ends it when this process exits.
_sessions: list[Any] = []


def start(settings: Settings) -> Any:
    """Begin connecting the configured servers in the background, and return the pool at once.

    Used at app boot. The connects take up to ``_CONNECT_TIMEOUT`` each, one after another. Measured
    on a restart of the 0.79.0 app, a boot that waited for them took about a hundred seconds to
    answer, and the desktop app showed "Failed to fetch" for all of it. Here the port opens first
    and the servers connect behind it.

    The pool object returned is the one the pool keeps filling, so a surface that holds it from boot
    sees the servers as they arrive. ``None`` means nothing is configured (or autoload is off) and no
    thread is started.
    """
    global _pool, _tried, _building, _done
    if not settings.mcp_autoload or _stopping:
        return None
    with _lock:
        if _tried:
            return _pool
        _tried = True
        servers = _configured(settings)
        if not servers:
            return None
        from chimera.integrations import ConnectorRegistry

        _pool = ConnectorRegistry()
        _done = threading.Event()
        _building = True
        pool = _pool
        done = _done
    threading.Thread(
        target=_run_background,
        args=(pool, servers, settings.home / "mcp.json", done),
        name="mcp-autoload",
        daemon=True,
    ).start()
    return pool


def connectors(settings: Settings) -> Any:
    """The connected MCP servers, or ``None`` when autoload is off or nothing is configured.

    The first caller builds the pool on a worker thread (:func:`_build_background`), and every caller
    waits at most :data:`_FIRST_RUN_WAIT` for it. A caller gets what has connected by then; a server
    still starting is not waited on beyond that bound. Built under a lock so several turns arriving
    at once cannot each start their own set of subprocesses — the failure this pool exists to prevent.

    Boot does not come through here: it uses :func:`start`, which fills the pool live. Whichever of
    the two runs first latches ``_tried``, so a server is never connected twice.
    """
    global _tried, _building, _done
    if not settings.mcp_autoload or _stopping:
        return None
    with _lock:
        starter = not _tried
        if starter:
            _tried = True
            _building = True
            _done = threading.Event()
        done = _done
    if starter:
        threading.Thread(
            target=_build_background, args=(settings, done), name="mcp-pool", daemon=True
        ).start()
    if _building:
        done.wait(timeout=_FIRST_RUN_WAIT)
    pool = _pool
    if pool is None or not pool.names():
        return None
    return pool


def wait_built(timeout: float) -> bool:
    """Block at most ``timeout`` while a background connect runs. ``True`` when nothing is pending.

    For a conversation created during boot: it waits for the servers that are about to arrive, so its
    registry is assembled once with them in it, instead of once without them for good.
    """
    if not _building:
        return True
    return _done.wait(timeout=timeout)


def shutdown() -> None:
    """Stop the pool: refuse further connects, and close every session, including one mid-handshake.

    Called from the app's teardown and at interpreter exit. Never raises: a stop that fails half way
    must still let the rest of the teardown run.
    """
    global _stopping
    with _lock:
        _stopping = True
        sessions = list(_sessions)
        _sessions.clear()
    for session in sessions:
        try:
            session.close()
        except Exception:  # noqa: BLE001 — a close that fails must not stop the others closing
            _log.warning("MCP: a session did not close cleanly during shutdown")


def _shutdown_at_exit() -> None:
    """The interpreter's exit hook: the same stop as the app's teardown, for a path that skips it."""
    shutdown()


atexit.register(_shutdown_at_exit)


def _build_background(settings: Settings, done: threading.Event) -> None:
    """The worker :func:`connectors` starts. The pool is published before its servers connect, so a
    caller that looks while the connect runs gets whatever has arrived, not nothing."""
    global _pool, _building
    try:
        built = _build(settings, _publish)
        if built is not None:
            with _lock:
                _pool = built
    except Exception:  # noqa: BLE001 — logged; the flag still clears below
        _log.exception("MCP: background build failed")
    finally:
        with _lock:
            _building = False
        done.set()


def _publish(pool: Any) -> None:
    """Make ``pool`` the shared pool before its servers are connected."""
    global _pool
    with _lock:
        _pool = pool


def _configured(settings: Settings) -> list[Any]:
    """The enabled servers in ``mcp.json``. Read synchronously: it is a file, not a connect."""
    from chimera.integrations.mcp_config import load_servers

    return [cfg for cfg in load_servers(settings.home / "mcp.json") if not cfg.disabled]


def _build(settings: Settings, publish: Any = None) -> Any:
    """Connect every configured server before returning. The pool, or ``None`` if none connected.

    The synchronous form. ``publish``, when given, receives the registry BEFORE the first connect, so
    a caller can hold the pool while it fills. Boot does not use this: :func:`start` runs the same
    :func:`_fill` on a thread.
    """
    servers = _configured(settings)
    if not servers:
        return None
    from chimera.integrations import ConnectorRegistry

    pool = ConnectorRegistry()
    if publish is not None:
        publish(pool)
    _fill(pool, servers, settings.home / "mcp.json")
    return pool if pool.names() else None


def _run_background(
    pool: Any, servers: list[Any], mcp_path: Path, done: threading.Event
) -> None:
    """The thread :func:`start` runs. Clears the connecting flag and sets ``done`` in a ``finally``,
    so a bug in the fill cannot leave ``pool_state`` reporting "connecting" for the life of the
    process. A crash is logged with its traceback, not swallowed."""
    global _building
    try:
        _fill(pool, servers, mcp_path)
    except Exception:  # noqa: BLE001 — logged; the flag still clears
        _log.exception("MCP: background connect failed")
    finally:
        with _lock:
            _building = False
        done.set()


def _fill(pool: Any, servers: list[Any], mcp_path: Path) -> None:
    """Connect each server in turn and register it into ``pool``.

    One at a time: the pin file is read and rewritten per server, and two connects writing it at
    once would lose one of the pins. Checks :data:`_stopping` between servers, and a session is
    tracked BEFORE its handshake so :func:`shutdown` can close it while it is still connecting.
    """
    from chimera.integrations import MCPConnector
    from chimera.integrations.mcp_config import _session_for
    from chimera.proc.stdio import CmdArgumentRefused

    for cfg in servers:
        if _stopping:
            _log.info("MCP: connect stopped before server %r, the app is shutting down", cfg.name)
            return
        session = None
        try:
            session = _session_for(cfg, _CONNECT_TIMEOUT)
            with _lock:
                _sessions.append(session)
            session.start()
            if _stopping:
                # A stop that landed while this handshake ran: shutdown() closed the session, and
                # registering it now would hand a dead session to the pool.
                return
            pinned = pinned_session(mcp_path, cfg.name, session)
            if pinned is None:
                _untrack(session)
                continue
            # Namespaced, so a server cannot publish a tool called `read_file` and shadow the one
            # that respects the write region. `into_tool_registry` skips collisions anyway, but a
            # prefix means there is nothing to collide over.
            pool.register(MCPConnector(cfg.name, pinned, name_prefix=f"{cfg.name}_"))
        except CmdArgumentRefused as exc:
            # Named as refused, not as a skipped server: the remedy is in the arguments, not in the
            # server's availability, and "skipping" sends somebody to check the wrong thing. The
            # message names the argument and character, never its value.
            _untrack(session)
            _log.warning("MCP: refused server %r, %s", cfg.name, exc)
        except Exception as exc:  # noqa: BLE001 — a broken server must never break a turn
            # The MESSAGE, not just the class. `ModuleNotFoundError` on its own is the least useful
            # half of "the MCP SDK is not installed — install it with: pip install
            # 'chimera-agent[mcp]'": the sentence was written precisely so this case would stop
            # looking like a broken config, and logging only the type threw it away again.
            #
            # Bounded, and the bound is not decoration: a connect failure carries the command and
            # its arguments, and `mcp.json` is also where a user's tokens live. `env` is never in
            # an exception message today, and a log line is the wrong place to bet on that staying
            # true forever.
            _untrack(session)
            reason = type(exc).__name__ if cfg.url else str(exc)[:300]
            _log.warning("MCP: skipping server %r, %s", cfg.name, reason)


def _untrack(session: Any) -> None:
    """Forget a session that failed or was not kept. Idempotent, and ``None`` is a no-op."""
    if session is None:
        return
    with _lock:
        if session in _sessions:
            _sessions.remove(session)


def pinned_session(mcp_path: Path, name: str, session: Any) -> Any:
    """``session`` behind its checked tool listing, or ``None`` — and the session closed — if held.

    The one gate every mount goes through (study 30, S30-24): a server whose tools no longer read as
    they did when the owner accepted them is not handed to any surface until the owner approves the
    change, which `chimera mcp approve` and the MCP screen show as a diff. See
    :mod:`chimera.integrations.mcp_pins` for why first sight pins rather than asks.
    """
    from chimera.integrations.mcp_pins import PinnedSession, check_manifest

    close = getattr(session, "close", None)
    try:
        specs = list(session.list_tools())
    except Exception:
        # Listing used to happen later, inside the registry; now it happens here, and a server that
        # connects and then fails to list must not be left running by the caller's skip.
        if callable(close):
            close()
        raise
    verdict = check_manifest(mcp_path, name, specs)
    if verdict.held:
        if callable(close):
            close()
        # The tool NAMES only. The descriptions are the server's text, and the new ones are
        # exactly the text being held back; a log line is not where they get read.
        _log.warning(
            "MCP: holding server %r — its tools changed since they were approved (%s); "
            "review with `chimera mcp approve %s` or on the MCP screen",
            name,
            ", ".join(c["tool"] for c in verdict.changes)[:300],
            name,
        )
        return None
    return PinnedSession(session, specs)


@dataclass(frozen=True)
class PoolState:
    """Whether a run started right now would receive the configured MCP tools.

    Exists because :func:`connectors` cannot answer that question: calling it BUILDS the pool, which
    spawns a subprocess per configured server. A screen asking "will the agent see this?" must not
    be the thing that connects them.

    ``connected`` is ``None`` when the pool has not been built yet, which is not the same as "no
    servers": the pool is built lazily on the first turn, so a server configured while autoload is
    on will be picked up then. Collapsing that into an empty tuple would report "the agent cannot
    see it" about a server the agent is about to see.
    """

    autoload: bool
    built: bool
    connected: tuple[str, ...] | None
    #: The background connect is still running. ``connected`` is then what has arrived so far, and a
    #: server missing from it may yet arrive; reporting it as "not connected" would be a promise.
    connecting: bool = False


def pool_state(settings: Settings) -> PoolState:
    """Report the pool without building it. Safe to call from a read-only endpoint."""
    with _lock:
        nomes: tuple[str, ...] | None = None
        if _pool is not None:
            try:
                nomes = tuple(_pool.names())
            except Exception:  # noqa: BLE001 -- a broken pool must not break a status read
                nomes = ()
        elif _tried:
            # Built and came back empty (no servers, or every one failed to connect). That is a real
            # answer, and reporting it as "not built yet" would promise a later pickup that is not
            # coming — `_tried` latches, so nothing will try again in this process.
            nomes = ()
        return PoolState(
            autoload=bool(settings.mcp_autoload),
            built=_tried,
            connected=nomes,
            connecting=_building,
        )


def built_pool() -> Any:
    """The pool if a turn already built it, else ``None`` — and never builds it.

    For a reader that wants to MEASURE the connected servers (`GET /api/tools/defer-saving`) and
    must not be the thing that spawns them, for the reason :class:`PoolState` gives. ``None`` here
    means "nothing to measure yet"; :func:`pool_state` says which of the reasons it is.
    """
    with _lock:
        return _pool


def reset_for_tests() -> None:
    """Forget the pool so a test can build a different one.

    Named for what it is. Process-wide state that tests can only set and never clear is state that
    makes the second test in a file depend on the first.
    """
    global _pool, _tried, _building, _done, _stopping
    with _lock:
        _pool = None
        _tried = False
        _building = False
        _done = threading.Event()
        _stopping = False
        _sessions.clear()
