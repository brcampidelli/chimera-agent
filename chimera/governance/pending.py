"""Asking a person who is not at the keyboard.

`Approver` is a synchronous `bool`, and the only implementation that asks anybody calls `input()`.
On the VPS, in a container, under cron — anywhere without a terminal — `approver_for("ask")`
degrades to `deny`, which is the right default and also means the three-state gate collapses into
two: every REVIEW becomes a refusal, and the mandate that says "confirm before billing, before a
destructive migration, before touching RLS" has nothing to confirm WITH.

The three parts of an answer already exist here and were never composed: a durable pause with a
resume key (`autonomous.py`'s taint pause), a delivery channel that reaches a person
(`scheduler/delivery.py`), and the approver seam itself.

This is the missing middle. A question is written to a file, delivered wherever the deployment
delivers, and the approver waits for an answer file to appear. `chimera approve` writes it.

Three properties decide whether this is safe:

**Silence is refusal.** The wait times out and returns False. A gate that treats an unanswered
question as consent produces a record of an approval nobody gave, which is worse than no gate.

**An answer is for one question.** The file is named by a random id and carries the question's own
text; an answer whose question no longer matches is discarded rather than applied to whatever is
pending now.

**Nothing is remembered across runs.** A stale request is cleaned up on the way in. Reusing
yesterday's yes for today's question is the same defect as treating silence as consent, one day
later.
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("governance.pending")

#: How long a question waits before it is refused.
#:
#: Fifteen minutes: long enough for somebody to see a chat notification and answer, short enough
#: that a worker thread is not held for an afternoon by a question nobody will read. A run that
#: needed the answer and did not get one is refused and says so, which is a recoverable outcome —
#: a thread parked forever is not.
WAIT_SECONDS = 900.0

#: How often the file is checked. Cheap, and the latency a person perceives is dominated by how
#: long it takes them to read the message.
POLL_SECONDS = 2.0

#: A request older than this was left by a run that is gone. Cleared on the way in, because a
#: directory of dead questions makes `chimera approve` unreadable and hides the live one.
STALE_SECONDS = 24 * 3600.0


def _dir(home: Path) -> Path:
    return Path(home) / "approvals"


@dataclass(frozen=True)
class PendingApproval:
    """One question waiting for a person."""

    id: str
    action: str
    reason: str
    asked_at: float

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.asked_at)


def sweep(home: Path, *, now: float | None = None) -> int:
    """Delete requests and answers older than :data:`STALE_SECONDS`. Returns how many went."""
    agora = time.time() if now is None else now
    removidos = 0
    for path in sorted(_dir(home).glob("*.json")) if _dir(home).exists() else []:
        try:
            if agora - path.stat().st_mtime > STALE_SECONDS:
                path.unlink()
                removidos += 1
        except OSError:  # pragma: no cover - a file that vanished under us is already gone
            continue
    return removidos


def pending(home: Path) -> list[PendingApproval]:
    """Every question currently waiting, oldest first."""
    out: list[PendingApproval] = []
    directory = _dir(home)
    if not directory.exists():
        return out
    for path in sorted(directory.glob("*.ask.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append(
            PendingApproval(
                id=str(data.get("id") or path.name.split(".")[0]),
                action=str(data.get("action") or ""),
                reason=str(data.get("reason") or ""),
                asked_at=float(data.get("asked_at") or 0.0),
            )
        )
    return sorted(out, key=lambda p: p.asked_at)


def answer(home: Path, request_id: str, approved: bool) -> bool:
    """Record a person's decision. False when there is no such question waiting."""
    directory = _dir(home)
    pergunta = directory / f"{request_id}.ask.json"
    if not pergunta.exists():
        return False
    (directory / f"{request_id}.answer.json").write_text(
        json.dumps({"approved": bool(approved), "answered_at": time.time()}), encoding="utf-8"
    )
    return True


def ask_durably(
    home: Path,
    action: str,
    reason: str,
    *,
    deliver: Any = None,
    wait_seconds: float = WAIT_SECONDS,
    poll_seconds: float = POLL_SECONDS,
    clock: Any = time.monotonic,
    sleep: Any = time.sleep,
) -> bool:
    """Put one question to a person who is elsewhere, and wait for the answer.

    Returns False on timeout, on an unreadable answer, and on any failure to write the question —
    every path that is not an explicit yes. That is the same rule the terminal prompt follows, and
    it is the only rule under which an unattended deployment can be given a three-state gate at all.
    """
    directory = _dir(home)
    request_id = uuid.uuid4().hex[:12]
    try:
        directory.mkdir(parents=True, exist_ok=True)
        sweep(home)
        (directory / f"{request_id}.ask.json").write_text(
            json.dumps(
                {"id": request_id, "action": action, "reason": reason, "asked_at": time.time()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        # A question that could not be written is a question nobody will answer, and pretending
        # otherwise would park a worker for fifteen minutes to reach the same refusal.
        _log.warning("could not record an approval request: %s", exc)
        return False

    if deliver is not None:
        try:
            deliver(
                f"Chimera needs a decision.\n\n{reason or 'review required'}\n"
                f"Action: {action[:300]}\n\n"
                f"Answer with:  chimera approve {request_id} --yes   (or --no)"
            )
        except Exception as exc:  # noqa: BLE001 — a failed delivery must not fail the run
            _log.warning("approval request not delivered: %s", exc)

    resposta = directory / f"{request_id}.answer.json"
    limite = clock() + wait_seconds
    while clock() < limite:
        if resposta.exists():
            try:
                decidido = bool(json.loads(resposta.read_text(encoding="utf-8")).get("approved"))
            except (OSError, ValueError):
                decidido = False
            _cleanup(directory, request_id)
            return decidido
        sleep(poll_seconds)

    _log.warning(
        "approval request %s went unanswered for %.0fs; refusing. Action: %s",
        request_id, wait_seconds, action[:200],
    )
    _cleanup(directory, request_id)
    return False


def _cleanup(directory: Path, request_id: str) -> None:
    for sufixo in (".ask.json", ".answer.json"):
        with contextlib.suppress(OSError):  # a file we cannot delete is not worth failing a run
            (directory / f"{request_id}{sufixo}").unlink(missing_ok=True)
