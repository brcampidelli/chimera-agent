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

_lock = threading.Lock()
_pool: Any = None
_tried = False
#: True while the background connect is running. Separate from ``_tried`` because a test (and the
#: status read) can set ``_tried`` without any connect happening, and "connecting" must not be inferred
#: from that.
_building = False
_done = threading.Event()


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
    if not settings.mcp_autoload:
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

    The first caller builds the pool on a worker thread (:func:`_build`), and every caller waits at
    most :data:`_FIRST_RUN_WAIT` for it. A caller gets what has connected by then; a server still
    starting is not waited on beyond that bound. Built under a lock so several turns arriving at once
    cannot each start their own set of subprocesses — the failure this pool exists to prevent.

    Boot does not come through here: it uses :func:`start`, which fills the pool live. Whichever of
    the two runs first latches ``_tried``, so a server is never connected twice.
    """
    global _tried, _building, _done
    if not settings.mcp_autoload:
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


def _build_background(settings: Settings, done: threading.Event) -> None:
    """The worker :func:`connectors` starts. Stores the built pool, then clears the flag."""
    global _pool, _building
    try:
        built = _build(settings)
        with _lock:
            _pool = built
    finally:
        with _lock:
            _building = False
        done.set()


def _configured(settings: Settings) -> list[Any]:
    """The enabled servers in ``mcp.json``. Read synchronously: it is a file, not a connect."""
    from chimera.integrations.mcp_config import load_servers

    return [cfg for cfg in load_servers(settings.home / "mcp.json") if not cfg.disabled]


def _build(settings: Settings) -> Any:
    """Connect every configured server before returning. The pool, or ``None`` if none connected.

    The synchronous form, kept for a caller that needs the finished pool in hand. Boot does not use
    it: :func:`start` runs the same :func:`_fill` on a thread, so the port is not held behind it.
    """
    servers = _configured(settings)
    if not servers:
        return None
    from chimera.integrations import ConnectorRegistry

    pool = ConnectorRegistry()
    _fill(pool, servers, settings.home / "mcp.json")
    return pool if pool.names() else None


def _run_background(
    pool: Any, servers: list[Any], mcp_path: Path, done: threading.Event
) -> None:
    """The thread :func:`start` runs. Clears the connecting flag and sets ``done`` in a ``finally``,
    so a bug in the fill cannot leave ``pool_state`` reporting "connecting" for the life of the
    process."""
    global _building
    try:
        _fill(pool, servers, mcp_path)
    finally:
        with _lock:
            _building = False
        done.set()


def _fill(pool: Any, servers: list[Any], mcp_path: Path) -> None:
    """Connect each server in turn and register it into ``pool``.

    One at a time: the pin file is read and rewritten per server, and two connects writing it at
    once would lose one of the pins.
    """
    from chimera.integrations import MCPConnector
    from chimera.integrations.mcp_config import _session_for

    for cfg in servers:
        try:
            session = _session_for(cfg, _CONNECT_TIMEOUT).start()
            pinned = pinned_session(mcp_path, cfg.name, session)
            if pinned is None:
                continue
            # Namespaced, so a server cannot publish a tool called `read_file` and shadow the one
            # that respects the write region. `into_tool_registry` skips collisions anyway, but a
            # prefix means there is nothing to collide over.
            pool.register(MCPConnector(cfg.name, pinned, name_prefix=f"{cfg.name}_"))
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
            reason = type(exc).__name__ if cfg.url else str(exc)[:300]
            _log.warning("MCP: skipping server %r — %s", cfg.name, reason)


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
    global _pool, _tried, _building, _done
    with _lock:
        _pool = None
        _tried = False
        _building = False
        _done = threading.Event()
