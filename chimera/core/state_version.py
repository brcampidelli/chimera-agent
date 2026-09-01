"""Which version wrote the state directory, so a version can tell it is reading an older one.

There are ~27 distinct artefacts under the home — `runs.jsonl`, `traces.jsonl`, `jobs.json`,
`memory.json`, `memory.db`, `runs.db`, the session and skill files — and none of them carried a
version. `grep schema_version|first_run|last_version|PRAGMA user_version` over the package returned
nothing, so the process could not *detect* that it was running against an older layout, let alone do
anything about it.

The immediate value is not migrating anything. It is being able to SAY "this home was created by
0.31 and you are on 0.48" — because without that, every question about an upgrade is answered by
guessing, and the answers are indistinguishable from a bug.

Deliberately small. One file, two fields, read on demand and never in a hot path. A migration
registry belongs here later; putting one in now would be scaffolding around a house with no door.
The behaviour that already exists — the tolerant readers, the optional field with an honest default,
the SQLite column check in `memory/sqlite_store.py` — is the right pattern and stays where it is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from chimera.telemetry import get_logger

_log = get_logger("core.state_version")

FILENAME = "state_version.json"

#: The layout number, bumped when an artefact under the home changes shape in a way a previous
#: version cannot read. Separate from the package version on purpose: most releases change no file
#: format at all, and tying the two would make every release look like a migration.
STATE_VERSION = 1


@dataclass(frozen=True)
class StateStamp:
    """What a home says about itself. Every field may be empty on a home that predates the stamp."""

    chimera_version: str = ""
    state_version: int = 0

    @property
    def known(self) -> bool:
        """False for a home written before this file existed — which is not the same as version 0.

        The distinction is the whole point of an unknown: "created before we recorded it" and
        "created by the first version that did" are different facts, and reporting the first as the
        second invents evidence about a machine nobody looked at.
        """
        return bool(self.chimera_version) or self.state_version > 0


def read(home: Path) -> StateStamp:
    """What this home was stamped with, or an empty stamp. Never raises."""
    path = Path(home) / FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return StateStamp()
    if not isinstance(data, dict):
        return StateStamp()
    return StateStamp(
        chimera_version=str(data.get("chimera_version") or ""),
        state_version=int(data.get("state_version") or 0),
    )


def stamp(home: Path) -> StateStamp:
    """Record this version against the home, and return what was there BEFORE.

    Returns the previous stamp so a caller can say what changed. Writing unconditionally rather than
    only-if-absent, because the useful question is "which version last touched this", and a home
    carried forward through five releases that only records the first answers a question nobody
    asked.

    Never raises: a home that cannot be stamped is a home that keeps working without the stamp,
    which is strictly better than a process that will not start because of a bookkeeping file.
    """
    from chimera import __version__

    anterior = read(home)
    path = Path(home) / FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"chimera_version": __version__, "state_version": STATE_VERSION}, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:  # pragma: no cover - disk-shaped failure
        _log.debug("could not stamp the state directory: %s", exc)
    return anterior
