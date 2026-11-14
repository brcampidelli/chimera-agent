"""Warnings and errors written to a file on disk, rotated, with secrets masked (plan M5).

Its own module rather than part of `chimera.telemetry`, which nearly every module imports: the
repository map ranks files by how often they are referenced, and three more names on that line
pushed a core file out of the map's budget (`tests/test_repomap_ranking.py`).
"""

from __future__ import annotations

import logging
from pathlib import Path

from chimera.telemetry import get_logger

#: The file :func:`attach_file_log` writes, once attached; None before that and in a CLI run.
_FILE_LOG: Path | None = None

#: One megabyte per file and three old ones kept: warnings and errors only, so this is weeks of
#: failures, and a log that can fill a disk is a second incident.
_FILE_LOG_BYTES = 1_000_000
_FILE_LOG_BACKUPS = 3


class _Redacting(logging.Filter):
    """Masks secrets in a record before it reaches the file (`chimera/core/redact.py`).

    A failure's message often quotes a provider's answer, and a traceback can quote a request. The
    console shows a record and forgets it; this file stays on disk for weeks.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        from chimera.core.redact import redact

        try:
            record.msg = redact(record.getMessage())
            record.args = None
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact(record.exc_text)
        except Exception:  # a record that cannot be masked is not written at all
            return False
        return True


def attach_file_log(path: Path) -> Path | None:
    """Also write warnings and errors to ``path``, rotated and with secrets masked. Idempotent.

    The desktop app's backend logged only to its console, which nobody sees: the app starts it as a
    child process. A turn that failed for a reason no sentence names left nothing anywhere (plan M5,
    observed 2026-10-09/10: "the coding turn failed" with no cause, three times in one day). Returns
    the path, or None when the folder cannot be written, in which case nothing changes.
    """
    global _FILE_LOG
    from logging.handlers import RotatingFileHandler

    get_logger("telemetry")  # configured at the settings' level first, as every other logger is
    root = logging.getLogger("chimera")
    for handler in root.handlers:
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == path.resolve():
            return _FILE_LOG
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=_FILE_LOG_BYTES, backupCount=_FILE_LOG_BACKUPS, encoding="utf-8"
        )
    except OSError:
        return None
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [turn=%(turn_id)s]: %(message)s"))
    handler.addFilter(_Redacting())
    root.addHandler(handler)
    _FILE_LOG = path.resolve()
    return _FILE_LOG


def file_log_path() -> Path | None:
    """Where warnings and errors are written to disk in this process, if anywhere."""
    return _FILE_LOG
