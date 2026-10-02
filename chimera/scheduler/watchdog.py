"""Reading the daemon's heartbeat: is the clock alive, and how sure are we?

The heartbeat (:mod:`chimera.scheduler.daemon`) is the daemon's one outward sign of life. This
module is the reader's half of issue #26 — the verdict, not the beat.

**Why the verdict is three-valued, and the default is the honest one.** A two-valued
alive/dead answer has to pick a staleness threshold, and any threshold is a guess about a
deployment nobody asked: a daemon ticking every 30 s and one ticking every 30 min are both
correct, and a fixed "stale after 5 min" calls the second dead. So the default is
**unknown** — "there is a heartbeat, and I cannot say whether it is fresh, because I do not
know how often this daemon ticks" — and a caller that knows its deployment passes
``max_gap_seconds`` and gets a real verdict. The reader never invents a number the writer
did not leave.

**Why the verdict is a question, not a watcher.** Nothing here notices while nothing runs —
the same limit `cron doctor` already states, and the reason the host cron in
``docs/deploy.md`` is the recommended caller: it is supervised by something other than
Chimera, which is the whole point. What this adds to that line is the half it could not see
before: the host cron ran `cron doctor --check`, which reads *jobs*; a dead daemon with a
daily job looks healthy for ~23 hours, because the job is not yet late. The heartbeat closes
exactly that window: the beat stops the moment the daemon dies, so the *next* host-cron run
sees it, whatever the job's schedule.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from chimera.scheduler.daemon import HEARTBEAT_RELPATH, heartbeat_age

#: The verdict names, in the order they narrow. ``unknown`` is the honest default: a beat
#: exists but no ceiling was given, so freshness cannot be judged without inventing a number.
Verdict = str  # "alive" | "stale" | "unknown" | "none"


@dataclass(frozen=True)
class DaemonWatch:
    """What the heartbeat says about the daemon, as of ``now``."""

    verdict: Verdict
    """``alive`` (a beat, fresh against the ceiling given), ``stale`` (a beat, too old),
    ``unknown`` (a beat, no ceiling to judge it against), ``none`` (no beat at all)."""

    age_seconds: float | None
    """Seconds since the last beat. ``None`` only with ``none`` — a beat that cannot be read
    is not a beat."""

    max_gap_seconds: float | None
    """The ceiling this verdict was judged against, so the reader can see the threshold rather
    than trust it. ``None`` with ``unknown``."""

    pid: int | None
    """The process that left the beat, when the file carried one. Lets a caller notice a beat
    left by a process that is no longer running (a stale beat from a crashed daemon whose file
    outlived it) — best-effort, and not checked here: on Windows, probing another pid needs
    more than the standard library gives honestly."""


def default_heartbeat_path(home: Path) -> Path:
    """Where the daemon leaves its beat for the install rooted at ``home``."""
    return Path(home) / HEARTBEAT_RELPATH


def watch_daemon(
    heartbeat_path: Path,
    *,
    now: float,
    max_gap_seconds: float | None = None,
) -> DaemonWatch:
    """Read the heartbeat and answer: is the daemon alive?

    ``max_gap_seconds`` is the deployment's own ceiling — how long a daemon may go between
    ticks before it counts as dead. The honest default is ``None``: with no ceiling, a beat
    that exists is ``unknown`` rather than alive, because "fresh" needs a number and the
    reader does not have one. A caller that knows its tick interval passes it; the CLI does
    (from the beat's own ``tick_seconds``, with headroom), the API takes it as a parameter.

    ``none`` is "no signal", not "dead": a daemon that has never run and a corrupt file are
    both absence of evidence. The caller decides what absence means — the CLI says so in
    words, the API returns the verdict and lets the screen phrase it.
    """
    age = heartbeat_age(heartbeat_path, now=now)
    if age is None:
        return DaemonWatch(verdict="none", age_seconds=None, max_gap_seconds=None, pid=None)
    if max_gap_seconds is None:
        return DaemonWatch(
            verdict="unknown", age_seconds=age, max_gap_seconds=None, pid=None
        )
    verdict: Verdict = "alive" if age <= max_gap_seconds else "stale"
    return DaemonWatch(
        verdict=verdict, age_seconds=age, max_gap_seconds=max_gap_seconds, pid=None
    )


def infer_max_gap(tick_seconds: float, *, multiplier: float = 3.0) -> float:
    """The staleness ceiling implied by a daemon's own tick interval.

    Three ticks of headroom: one missed tick is a busy machine, two is a slow dispatch, three
    is a daemon that is not coming back. The multiplier is a stated default, not a secret —
    the CLI prints the ceiling it used, so a reader can disagree with the number rather than
    wonder where it came from.

    A tick of zero or less (a caller that never set it) yields a ceiling of zero, which makes
    every beat stale — the loud reading, and the right one for a daemon that claims to tick
    instantly and did not.
    """
    return max(0.0, tick_seconds) * multiplier


def watch_tick_seconds(heartbeat_path: Path) -> float:
    """The tick interval the beat itself recorded, or 0.0 when there is none.

    The ceiling is derived from the beat's own field rather than from a flag, so `cron doctor`
    needs no configuration and stays right when the deployment changes its tick: the daemon
    wrote how often it ticks, and the reader believes the writer about the writer's own
    cadence. 0.0 (no beat, or a beat without the field) makes `infer_max_gap` return 0 — every
    beat stale — which is the loud reading for a daemon that claims to tick instantly.
    """
    from chimera.scheduler.daemon import read_heartbeat

    beat = read_heartbeat(heartbeat_path)
    if beat is None:
        return 0.0
    return float(beat.get("tick_seconds") or 0.0)


def beat_pid_is_running(pid: int) -> bool | None:
    """Whether the process that left the beat still exists — best-effort, ``None`` when unknown.

    POSIX has ``os.kill(pid, 0)``; Windows does not, and probing a foreign pid there needs
    more than the standard library gives honestly. ``None`` means "cannot say", and every
    caller must treat it as no evidence rather than as either answer.
    """
    if pid <= 0:
        return None
    if os.name == "nt":
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # the process exists and is not ours
    except OSError:
        return None
    return True
