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

**How every question ended is remembered, though — separately from the question.** Until
2026-09-11 the request and the answer files were the only evidence a question had existed, and both
were deleted the moment it resolved, so a deployment could not be asked the two numbers that decide
whether this mechanism is worth anything: how often somebody answers, and how long they take. With
nobody reachable, "approver present" behaves exactly like "no approver" and the block rate still
reads perfect — the gate's usefulness is a property of the humans, not of the configuration
(pointed out under the LLMDevs post of 2026-09-11). :func:`history` and :func:`answer_stats` read
the append-only ``history.jsonl`` this module now writes on every resolution, and a timeout is
recorded as a timeout rather than folded into "refused".
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
    on_asked: Any = None,
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
    # One clock reading for the file, the announcement and the record. There used to be one per
    # site, and a time-to-answer measured between two of them carried their difference.
    asked_at = time.time()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        sweep(home)
        (directory / f"{request_id}.ask.json").write_text(
            json.dumps(
                {"id": request_id, "action": action, "reason": reason, "asked_at": asked_at},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        # A question that could not be written is a question nobody will answer, and pretending
        # otherwise would park a worker for fifteen minutes to reach the same refusal.
        _log.warning("could not record an approval request: %s", exc)
        return False

    if on_asked is not None:
        # The structured form, for a surface with a screen. Delivered BEFORE the text channel and
        # independently of it: a screen that can render a button must not depend on a webhook
        # being configured, and a failure here is as harmless as a failed delivery below.
        try:
            on_asked(PendingApproval(id=request_id, action=action, reason=reason, asked_at=asked_at))
        except Exception as exc:  # noqa: BLE001 — the question is on disk; the notice is a courtesy
            _log.warning("approval request not announced: %s", exc)
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
            answered_at: float | None = None
            try:
                dados = json.loads(resposta.read_text(encoding="utf-8"))
                decidido = bool(dados.get("approved"))
                answered_at = float(dados.get("answered_at") or 0.0) or None
                outcome = "approved" if decidido else "refused"
            except (OSError, ValueError):
                decidido = False
                outcome = "unreadable"
            _record(directory, request_id, action, reason, asked_at, outcome, answered_at)
            _cleanup(directory, request_id)
            return decidido
        sleep(poll_seconds)

    _log.warning(
        "approval request %s went unanswered for %.0fs; refusing. Action: %s",
        request_id, wait_seconds, action[:200],
    )
    _record(directory, request_id, action, reason, asked_at, "timeout", None)
    _cleanup(directory, request_id)
    return False


#: Where every resolved question leaves one line. Append-only, never swept: the questions are
#: ephemeral by design (see :func:`sweep`), the record of how they ended is the point.
HISTORY = "history.jsonl"

#: The four ways a question ends. ``timeout`` is its own value because it is the one that says
#: nobody was reachable, and a report that folded it into ``refused`` would read a night with no
#: one on call as a night of careful refusals.
OUTCOMES = ("approved", "refused", "timeout", "unreadable")


def _record(
    directory: Path,
    request_id: str,
    action: str,
    reason: str,
    asked_at: float,
    outcome: str,
    answered_at: float | None,
) -> None:
    resolved_at = time.time()
    line = {
        "id": request_id,
        "action": action[:200],
        "reason": reason[:300],
        "asked_at": asked_at,
        "resolved_at": resolved_at,
        # The person's clock, not the poller's: the answer file carries when it was written, and
        # the poll interval would otherwise be added to every measurement.
        "seconds_to_answer": (
            max(0.0, answered_at - asked_at) if answered_at is not None else None
        ),
        "waited_seconds": max(0.0, resolved_at - asked_at),
        "outcome": outcome,
    }
    try:
        with (directory / HISTORY).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError as exc:  # the decision stands either way; only the record is lost
        _log.warning("could not record how approval %s ended: %s", request_id, exc)


def history(home: Path) -> list[dict[str, Any]]:
    """Every resolved question this home has seen, oldest first. Empty when none were asked."""
    path = _dir(home) / HISTORY
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


def answer_stats(home: Path) -> dict[str, Any]:
    """The two operating metrics of this mechanism: how often somebody answers, and how fast.

    ``answer_rate`` counts a question as answered when a person wrote a verdict, yes or no; a timeout
    is not an answer. ``p50``/``p90`` are seconds-to-answer over the answered ones only, and are
    ``None`` until there is something to measure — a rate of zero over zero questions is not a rate.
    """
    rows = history(home)
    answered = [r for r in rows if r.get("outcome") in ("approved", "refused")]
    times = sorted(
        float(r["seconds_to_answer"]) for r in answered if r.get("seconds_to_answer") is not None
    )

    def pct(q: float) -> float | None:
        if not times:
            return None
        return times[min(len(times) - 1, int(round(q * (len(times) - 1))))]

    return {
        "asked": len(rows),
        "answered": len(answered),
        "approved": sum(r.get("outcome") == "approved" for r in rows),
        "refused": sum(r.get("outcome") == "refused" for r in rows),
        "timeouts": sum(r.get("outcome") == "timeout" for r in rows),
        "answer_rate": (len(answered) / len(rows)) if rows else None,
        "p50_seconds": pct(0.5),
        "p90_seconds": pct(0.9),
        "max_seconds": times[-1] if times else None,
    }


def _cleanup(directory: Path, request_id: str) -> None:
    for sufixo in (".ask.json", ".answer.json"):
        with contextlib.suppress(OSError):  # a file we cannot delete is not worth failing a run
            (directory / f"{request_id}{sufixo}").unlink(missing_ok=True)
