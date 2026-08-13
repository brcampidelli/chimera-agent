"""Was the suggestion any good? Counted, not asserted.

The plan for this phase carried one condition in bold: **no quality claim without a measured
acceptance rate**. That is easy to agree with and easy to quietly skip, because measuring it means
building the ledger before there is anything to put in it. So the ledger comes first, and the
README says nothing about how good the completions are until this file has numbers in it.

What is recorded is deliberately thin: that a suggestion was shown, how long it took, how long it
was, and whether it was taken. No prefix, no suffix, no file path, no code — an acceptance rate
does not need to know what you were writing, and a local file that did would be a copy of your
source under a name nobody expects.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()

#: Cap on the ledger. It is append-only and one line per suggestion, so an editor left open for a
#: week would otherwise grow without bound for a statistic that needs a few thousand samples.
MAX_LINES = 20_000


def _path(home: Path) -> Path:
    return Path(home) / "completion_outcomes.jsonl"


def _append(home: Path, row: dict[str, Any]) -> None:
    target = _path(home)
    with _lock:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        # Cheap because it only reads when the file is already large enough to matter.
        if target.stat().st_size > MAX_LINES * 120:
            lines = target.read_text(encoding="utf-8").splitlines()[-MAX_LINES:]
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def record_shown(home: Path, *, ms: int, chars: int, model: str) -> str:
    """Log that a suggestion reached the screen. Returns its id, which the outcome refers back to."""
    ident = f"{time.time():.6f}"
    _append(home, {"id": ident, "event": "shown", "ms": ms, "chars": chars, "model": model})
    return ident


def record_outcome(home: Path, *, ident: str, accepted: bool) -> None:
    """Log what became of it. A suggestion with no outcome is one the editor never resolved —
    counted in `shown` and in neither column, which is why the rate below states its denominator."""
    _append(home, {"id": ident, "event": "accepted" if accepted else "dismissed"})


def acceptance(home: Path) -> dict[str, Any]:
    """Shown, accepted, dismissed, and the rate — or `None` for the rate when nothing was resolved.

    Zero would be a claim ("nobody wants these") where the truth is that nobody has answered yet,
    and this is the one file in the feature whose entire job is not to overstate.
    """
    target = _path(home)
    shown = accepted = dismissed = 0
    total_ms = 0
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            event = row.get("event")
            if event == "shown":
                shown += 1
                total_ms += int(row.get("ms") or 0)
            elif event == "accepted":
                accepted += 1
            elif event == "dismissed":
                dismissed += 1
    resolved = accepted + dismissed
    return {
        "shown": shown,
        "accepted": accepted,
        "dismissed": dismissed,
        "rate": (accepted / resolved) if resolved else None,
        "mean_ms": (total_ms // shown) if shown else None,
        "note": "" if resolved else "no suggestion has been accepted or dismissed yet",
    }
