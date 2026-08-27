"""Reading back what the scheduled jobs answered.

Every cron dispatch appends a line to ``<home>/scheduler/cron_results.jsonl``, and until now the
only code that touched that file was the code that wrote it. So a schedule could run every night for
a month, produce a good answer every time, and the person who set it up had no way to read one
without opening a JSONL by hand — while the screen that created the schedule promised it would
"save each result".

**Read from the END.** This file grows for the life of the install and is never rotated: a job on
the hour writes 8,760 lines a year, and an answer can be a page long. Loading it whole to show the
last ten is the kind of cost that arrives eighteen months after the decision, on the machine of
whoever left the app running longest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: How much of the file's tail to read. Generous next to the answers it holds (a few KB each) and
#: small next to a file nobody prunes.
TAIL_BYTES = 256 * 1024


@dataclass(frozen=True)
class CronResult:
    """One dispatch that produced an answer."""

    at: float
    job_id: str
    name: str
    action: str
    answer: str
    #: None when the job named no webhook — which is not the same as a delivery that failed, and
    #: the screen has to be able to tell those apart.
    delivered: bool | None = None
    delivery_detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _tail_lines(path: Path, limit_bytes: int) -> list[str]:
    """The last complete lines of a file, without reading the rest of it.

    The first line of the window is dropped when the window did not start at the beginning: a read
    that begins mid-file almost certainly begins mid-line, and half a JSON object parses as nothing
    at best and as something wrong at worst.
    """
    size = path.stat().st_size
    inicio = max(0, size - limit_bytes)
    with path.open("rb") as fh:
        fh.seek(inicio)
        bruto = fh.read()
    linhas = bruto.decode("utf-8", "replace").splitlines()
    return linhas[1:] if inicio > 0 and linhas else linhas


def load_results(path: Path, *, job_id: str | None = None, limit: int = 50) -> list[CronResult]:
    """The most recent answers first. Malformed lines are skipped rather than fatal.

    ``job_id`` narrows to one schedule. A malformed line is skipped in silence on purpose: this file
    is append-only from a background thread, so a torn final write is an ordinary event and not
    something to fail a screen over.
    """
    path = Path(path)
    if not path.exists():
        return []

    fora: list[CronResult] = []
    for linha in reversed(_tail_lines(path, TAIL_BYTES)):
        if not linha.strip():
            continue
        try:
            d = json.loads(linha)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        if job_id is not None and str(d.get("id", "")) != job_id:
            continue
        fora.append(
            CronResult(
                at=float(d.get("at") or 0.0),
                job_id=str(d.get("id") or ""),
                name=str(d.get("name") or ""),
                action=str(d.get("action") or ""),
                answer=str(d.get("answer") or ""),
                delivered=d.get("delivered") if isinstance(d.get("delivered"), bool) else None,
                delivery_detail=str(d.get("delivery_detail") or ""),
            )
        )
        if len(fora) >= limit:
            break
    return fora
