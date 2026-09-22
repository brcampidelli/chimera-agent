"""Every spoken request, with the label the deterministic classifier gave it — the corpus a typed
decision for the voice router will be measured on.

Study 20 §3 named the voice split (`classify_task` deciding *talk* against *work* for a spoken
request, `chimera/api/code_api.py::_is_work`) as a decision that should carry a number. It cannot
be measured yet for one reason: there is no labelled corpus of spoken requests, and a classifier
compared on sentences its author wrote is compared on its author. So the first step is not a
model — it is a record. Each spoken request lands here with the label the regex gave it and the
context the regex used (`has_works`), and the person who spoke it says later which label was right.
A hundred of those is the bench; until then nothing routes differently.

The file is `<home>/voice/requests.jsonl`, best-effort like every other log under the home: a
failure to record what was said must not fail the turn that answers it. One line per request:

    {"ts": "...", "session_id": "...", "message": "...", "label": "work"|"talk",
     "has_works": bool, "reviewed": null}

`reviewed` is the column the person fills — `"work"`, `"talk"`, or left `null` — by editing the
file or through whatever screen reads it later. It is written as `null` on purpose: a row that
does not say whether it was checked cannot be told from one that was, and a bench that counts
unchecked rows as agreement measures nothing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from chimera.telemetry import get_logger

_log = get_logger("api.spoken_log")

SpokenLabel = Literal["work", "talk"]

FILE = Path("voice") / "requests.jsonl"
"""Relative to the home."""


def record_spoken_request(
    home: Path,
    message: str,
    label: SpokenLabel,
    *,
    session_id: str | None,
    has_works: bool,
) -> None:
    """Append one spoken request and the label the classifier gave it. Never raises."""
    row = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "session_id": session_id or "",
        "message": message,
        "label": label,
        "has_works": bool(has_works),
        "reviewed": None,
    }
    try:
        path = Path(home) / FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:  # pragma: no cover - a full disk is the case, and it must not fail the turn
        _log.debug("spoken request not recorded: %s", exc)


def read_spoken_requests(home: Path) -> list[dict[str, object]]:
    """Every recorded request, oldest first; a line that is not JSON is skipped, not fatal."""
    path = Path(home) / FILE
    if not path.is_file():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows
