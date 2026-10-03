"""Were the next-step suggestions under an answer any use? Counted, not asserted.

Study 29, P4.5. After a turn the Code screen offers up to three suggestions derived from facts of
that turn — a failed check ("try to fix it"), files it changed that git still reports as changed
("commit …"), an item left open on the agent's own list ("continue: …"). They only FILL the box; the
person sends. The plan's measure for them is the acceptance rate, kept the way
:mod:`chimera.complete.outcomes` keeps the inline completion's: three events, one ledger, a rate that
is ``None`` until there is a denominator.

- ``shown`` — a suggestion reached the screen under a turn this screen sent;
- ``picked`` — the person clicked it, which put its text in the box;
- ``sent`` — the box was sent while it still held a picked suggestion, ``edited`` when its text was
  changed first.

What is recorded is deliberately thin: the event, the KIND of suggestion and, for a send, whether it
was edited. Never the text — a "continue:" suggestion carries the agent's own task wording and a
"commit" one names files, and an acceptance rate needs neither.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Literal

SuggestionKind = Literal["fix", "commit", "continue"]
SuggestionEvent = Literal["shown", "picked", "sent"]

KINDS: tuple[str, ...] = ("fix", "commit", "continue")

#: Cap on the ledger: append-only, one line per event, and a rate needs a few thousand samples.
MAX_LINES = 20_000

_lock = threading.Lock()


def _path(home: Path) -> Path:
    return Path(home) / "suggestion_outcomes.jsonl"


def record(home: Path, *, event: str, kind: str, edited: bool = False) -> None:
    """Append one event. Never raises: a statistic must not fail the click it is counting."""
    row: dict[str, Any] = {"at": round(time.time(), 3), "event": event, "kind": kind}
    if event == "sent":
        row["edited"] = bool(edited)
    target = _path(home)
    try:
        with _lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            # Only reads back once the file is large enough to be worth trimming.
            if target.stat().st_size > MAX_LINES * 80:
                lines = target.read_text(encoding="utf-8").splitlines()[-MAX_LINES:]
                target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def _rate(top: int, bottom: int) -> float | None:
    return (top / bottom) if bottom else None


def _counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    shown = sum(1 for r in rows if r.get("event") == "shown")
    picked = sum(1 for r in rows if r.get("event") == "picked")
    sent = [r for r in rows if r.get("event") == "sent"]
    return {
        "shown": shown,
        "picked": picked,
        "sent": len(sent),
        "edited": sum(1 for r in sent if r.get("edited")),
        # A click out of the suggestions shown, and a send out of the clicks: two rates, because
        # "nobody clicks them" and "people click and then rewrite or abandon them" are different
        # findings with different fixes.
        "pick_rate": _rate(picked, shown),
        "send_rate": _rate(len(sent), picked),
    }


def stats(home: Path) -> dict[str, Any]:
    """The totals, the two rates and the same per kind — each rate ``None`` with no denominator.

    Zero would be a claim ("nobody wants these") where the truth is that nobody has been shown one.
    """
    rows: list[dict[str, Any]] = []
    target = _path(home)
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("kind") in KINDS:
                rows.append(row)
    total = _counts(rows)
    return {
        **total,
        "by_kind": {kind: _counts([r for r in rows if r.get("kind") == kind]) for kind in KINDS},
        "note": "" if total["shown"] else "no suggestion has been shown yet",
    }
