"""Lightweight, structured logging/tracing for Chimera.

A thin wrapper over the stdlib ``logging`` with a Rich handler for readable output.
This is the seam where richer tracing (OpenTelemetry spans, trajectory logging)
will plug in later — call sites only ever touch :func:`get_logger`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from rich.logging import RichHandler

_CONFIGURED = False

#: The turn the current thread/task is working for, so a line written deep inside a tool or a
#: provider call can be tied back to the turn that caused it. A turn runs on its own worker thread
#: and logs from a dozen modules; without this, reading one turn's story meant grepping timestamps.
_TURN_ID: ContextVar[str | None] = ContextVar("chimera_turn_id", default=None)


def current_turn_id() -> str | None:
    """The turn id bound to this context, or None outside a turn."""
    return _TURN_ID.get()


@contextmanager
def bind_turn(turn_id: str) -> Iterator[None]:
    """Make every log record written inside the block carry ``turn_id``."""
    token = _TURN_ID.set(turn_id)
    try:
        yield
    finally:
        _TURN_ID.reset(token)


def bound_to_turn(turn_id: str, work: Callable[[], None]) -> Callable[[], None]:
    """Wrap a thread's target so it runs with ``turn_id`` bound (a new thread starts with no context)."""

    def run() -> None:
        with bind_turn(turn_id):
            work()

    return run


_FACTORY_INSTALLED = False


def _install_record_factory() -> None:
    """Stamp ``turn_id`` and ``turn_prefix`` on every record, once, at creation.

    A record factory rather than a handler filter: a filter on the ``chimera`` logger does not see
    records from its children, and each handler (console, file) would need its own copy. The stamp is
    taken where the record is made, which is the thread that logged, the only place the context is right.
    """
    global _FACTORY_INSTALLED
    if _FACTORY_INSTALLED:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        turn = _TURN_ID.get()
        record.turn_id = turn or "-"
        # Round brackets: the console handler reads square ones as Rich markup.
        record.turn_prefix = f"(turn {turn[:12]}) " if turn else ""
        return record

    logging.setLogRecordFactory(factory)
    _FACTORY_INSTALLED = True


# At import, not in configure_logging: a handler built elsewhere (the file log, a test's) may format
# `%(turn_id)s` before logging was ever configured, and a record without the field raises.
_install_record_factory()


def configure_logging(level: str = "INFO") -> None:
    """Install the Rich handler once. Idempotent."""
    global _CONFIGURED
    if _CONFIGURED:
        logging.getLogger("chimera").setLevel(level.upper())
        return

    _install_record_factory()
    handler = RichHandler(rich_tracebacks=True, show_path=False, markup=True)
    handler.setFormatter(logging.Formatter("%(turn_prefix)s%(message)s", datefmt="[%X]"))

    root = logging.getLogger("chimera")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    root.propagate = False
    _CONFIGURED = True

    # Initialise optional OpenTelemetry once logging is up (no-op unless enabled + the [otel] extra is
    # installed). Local import breaks the telemetry<->obs cycle; guarded so a failure never blocks logs.
    try:
        from chimera.obs import configure_otel

        configure_otel()
    except Exception:  # observability must never break logging/startup
        pass


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced child logger (``chimera.<name>``).

    Logging is configured lazily on first use using the level from settings.
    """
    if not _CONFIGURED:
        # Local import avoids a config<->telemetry import cycle at module load.
        from chimera.config import get_settings

        configure_logging(get_settings().log_level)
    return logging.getLogger(f"chimera.{name}")
