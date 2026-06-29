"""Lightweight, structured logging/tracing for Chimera.

A thin wrapper over the stdlib ``logging`` with a Rich handler for readable output.
This is the seam where richer tracing (OpenTelemetry spans, trajectory logging)
will plug in later — call sites only ever touch :func:`get_logger`.
"""

from __future__ import annotations

import logging

from rich.logging import RichHandler

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """Install the Rich handler once. Idempotent."""
    global _CONFIGURED
    if _CONFIGURED:
        logging.getLogger("chimera").setLevel(level.upper())
        return

    handler = RichHandler(rich_tracebacks=True, show_path=False, markup=True)
    handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))

    root = logging.getLogger("chimera")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced child logger (``chimera.<name>``).

    Logging is configured lazily on first use using the level from settings.
    """
    if not _CONFIGURED:
        # Local import avoids a config<->telemetry import cycle at module load.
        from chimera.config import get_settings

        configure_logging(get_settings().log_level)
    return logging.getLogger(f"chimera.{name}")
