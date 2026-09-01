"""Telling "this file is corrupt" apart from "this file is the previous version".

Five readers in this package skip a record they cannot validate and carry on, and the reason is
sound: one hand-edited line must not cost every other memory, job or card. Each of them says so in a
comment.

What none of them could say is which of two very different things happened. A truncated last object
is one bad record among fifty. A field that became required in this release is fifty bad records out
of fifty — and the reader then returns an empty collection, which the store's own `save()` writes
back over the file a moment later. The daemon does that every minute, with nobody watching, and the
log carries fifty identical warnings that read like noise.

So the rule is about the RATIO, not the record: losing a few entries is tolerance working, and
losing all of them is a format that moved. In that case the load did not happen, and a store that
did not load must not be allowed to persist what it thinks it holds.

Deliberately not a schema version. That is a separate, larger change (a version stamp on the home,
and a migration path); this is the guard that keeps the day it lands from being the day somebody's
crontab is emptied.
"""

from __future__ import annotations

from pathlib import Path

from chimera.telemetry import get_logger

_log = get_logger("core.state_format")


class StaleFormatError(RuntimeError):
    """Every record in a state file was rejected — the format moved, the file is not corrupt."""


def looks_like_a_format_change(*, kept: int, skipped: int) -> bool:
    """True when a file's records were rejected wholesale rather than individually.

    One bad line among many is a bad line. Every line bad, with lines present, is this release
    reading the previous release's file — and the difference decides whether the right answer is
    "carry on with what loaded" or "do not touch this file".
    """
    return skipped > 0 and kept == 0


def report(*, kept: int, skipped: int, what: str, path: Path) -> bool:
    """Log the skips at the severity they deserve, and say whether the load is untrustworthy.

    Returns True when the caller should treat the load as not having happened. The severity split is
    the point: a handful of skipped records is a warning somebody may read later, and a wholesale
    rejection is an error somebody has to read now, because the alternative is a file quietly
    replaced with an empty one.
    """
    if not skipped:
        return False
    if looks_like_a_format_change(kept=kept, skipped=skipped):
        _log.error(
            "every %s in %s was rejected (%d of %d) — this looks like a file written by another "
            "version, not a corrupt one; refusing to treat it as empty",
            what, path, skipped, skipped,
        )
        return True
    _log.warning("skipped %d malformed %s in %s (kept %d)", skipped, what, path, kept)
    return False
