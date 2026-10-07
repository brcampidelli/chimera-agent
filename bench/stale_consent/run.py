"""The stale-consent probes: four scenarios against the real `ask_durably`, no model, US$ 0.

Study 31, G31-03 (`PREREGISTRATION.md` is the contract; this file only implements it). Every probe
drives `chimera.governance.pending.ask_durably` — the mechanism production runs — with an injected
clock so the waits are deterministic, and measures the gap between the moment a person's yes lands
and the moment the ask returns (the return is the only thing the gated effect follows).

The one deliberate non-measurement is `plan_gate_drift_spec`: the join that will measure how far a
turn drifts from its approved plan, committed here so the number has a definition, and run only
when a plan-gated turn exists to join (this machine's home has none — checked 2026-10-05).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.governance import pending

#: The wait and poll the desktop's own durable path uses (`settings.approval_wait` default).
WAIT_SECONDS = 300.0
POLL_SECONDS = 2.0

#: S4's real-clock shape: a short wait, a fast poll, an answer one second in.
REAL_WAIT = 5.0
REAL_POLL = 0.2
REAL_ANSWER_DELAY = 1.0
REAL_RUNS = 10

#: The epsilon the code-expiry probe sits off the deadline with.
EPSILON = 0.5


class _Clock:
    """The fake clock: `sleep` advances it, and is where a scheduled answer gets written.

    The answer is written INSIDE the waiter's own sleep call, deterministically — never from a
    racing thread — so "the person answered at t" means "the file exists at the first check at or
    after t", which is exactly the property the poll loop defines.
    """

    def __init__(self, home: Path, moment: float | None, approved: bool = True) -> None:
        self.now = 0.0
        self.home = home
        self.moment = moment
        self.approved = approved
        self.written_at: float | None = None
        self.question: pending.PendingApproval | None = None

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        if self.moment is not None and self.written_at is None and self.now >= self.moment:
            assert self.question is not None, "the answer moment arrived before the question did"
            assert pending.answer(self.home, self.question.id, self.approved)
            # The person's clock: answered_at = asked_at + the moment they answered at.
            path = pending._dir(self.home) / f"{self.question.id}.answer.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["answered_at"] = self.question.asked_at + self.moment
            path.write_text(json.dumps(data), encoding="utf-8")
            self.written_at = self.moment


@dataclass
class Probe:
    """One ask, its outcome, and what the caller did with the return."""

    delta: float
    outcome: str
    seconds_to_answer: float | None
    gap: float | None
    """Fake ticks from the answer's moment to the ask's return — the consent-to-effect gap."""
    effect_ran: bool


def _run_one(
    home: Path, moment: float | None, *, wait: float, poll: float, approved: bool = True,
) -> Probe:
    """One `ask_durably` with the scheduled answer, and the effect the return gates."""
    clock = _Clock(home, moment, approved)
    effects: list[int] = []
    outcome = pending.ask_durably(
        home, "run_shell: make deploy", "stale-consent probe",
        wait_seconds=wait, poll_seconds=poll, clock=clock, sleep=clock.sleep,
        on_asked=lambda q: setattr(clock, "question", q),
    )
    if outcome:
        effects.append(1)  # the gated effect, run only on the return being True
    assert clock.question is not None, "the ask never announced its question"
    rows = [r for r in pending.history(home) if r["id"] == clock.question.id]
    row = rows[-1]
    gap = None if clock.written_at is None else clock.now - clock.written_at
    return Probe(
        delta=moment if moment is not None else -1.0,
        outcome=str(row["outcome"]),
        seconds_to_answer=row.get("seconds_to_answer"),
        gap=gap,
        effect_ran=bool(effects),
    )


def probe_sync_grid(home: Path) -> list[Probe]:
    """S1: answers at a fixed grid of moments; the gap must stay under one poll interval."""
    deltas = [0.5, 2.0, 30.0, 120.0, 299.0]
    return [_run_one(home, d, wait=WAIT_SECONDS, poll=POLL_SECONDS) for d in deltas]


def probe_sync_grid_fast_poll(home: Path) -> list[Probe]:
    """S1's second arm: the same grid against a 0.5 s poll — the bound is the poll, not the wait."""
    deltas = [0.5, 30.0, 299.0]
    return [_run_one(home, d, wait=WAIT_SECONDS, poll=0.5) for d in deltas]


def probe_boundaries(home: Path) -> list[Probe]:
    """S2: answers placed against the deadline — honoured to the last look, never past it."""
    limite = WAIT_SECONDS
    offsets = [-4.0, -2.0, -1.0, -0.5, +0.5]
    return [_run_one(home, limite + o, wait=WAIT_SECONDS, poll=POLL_SECONDS) for o in offsets]


def probe_code_expiry(home: Path) -> dict[str, Any]:
    """S2b: the chat code stops one poll BEFORE the deadline — by design, now measured.

    The question is asked with a deliverer that offers a code (the chat's shape); the code is read
    off the delivered text the way its recipient would, then answered at `expires_at ± ε` through
    the real `answer_with_code`, whose `now` parameter makes both sides deterministic.
    """

    class _Chat:
        offers_chat_code = True

        def __init__(self) -> None:
            self.messages: list[str] = []

        def __call__(self, text: str) -> None:
            self.messages.append(text)

    chat = _Chat()
    clock = _Clock(home, None)  # nobody answers through the file: the code path is the one under test
    outcome = pending.ask_durably(
        home, "run_shell: make deploy", "code-expiry probe", deliver=chat,
        wait_seconds=WAIT_SECONDS, poll_seconds=POLL_SECONDS, clock=clock, sleep=clock.sleep,
        on_asked=lambda q: setattr(clock, "question", q),
    )
    assert outcome is False  # the wait runs out; the code probe happens against the deadline
    # The wait consumed fake time to the deadline; the question file is already cleaned up, so the
    # expiry probe rebuilds the same question by hand — the file format `ask_durably` writes. Since
    # S30-30 that is `chat_code: true` plus `expires_at` on disk, and the code itself only in the
    # asking process's memory, which `answer_with_code` checks; this process is the asker here, so
    # it remembers the code the way `ask_durably` does. (The probe used to write the retired
    # `code_hash` field, which nothing reads any more: every answer then read `no_code`.)
    request_id = uuid.uuid4().hex[:12]
    code = pending.new_code()
    expires_at = clock.now + WAIT_SECONDS - POLL_SECONDS  # the same bound the ask wrote
    pending._dir(home).mkdir(parents=True, exist_ok=True)
    (pending._dir(home) / f"{request_id}.ask.json").write_text(
        json.dumps({
            "id": request_id, "action": "run_shell: make deploy", "reason": "code expiry",
            "asked_at": time.time(), "decision": "review",
            "chat_code": True, "expires_at": expires_at,
        }),
        encoding="utf-8",
    )
    pending._remember_code(request_id, code)
    try:
        before = pending.answer_with_code(
            home, request_id, code, True, via="probe", now=expires_at - EPSILON
        )
    finally:
        pending._forget_code(request_id)
    # A second question for the late side: the code is consumed once, by design.
    request_id2 = uuid.uuid4().hex[:12]
    code2 = pending.new_code()
    (pending._dir(home) / f"{request_id2}.ask.json").write_text(
        json.dumps({
            "id": request_id2, "action": "run_shell: make deploy", "reason": "code expiry",
            "asked_at": time.time(), "decision": "review",
            "chat_code": True, "expires_at": expires_at,
        }),
        encoding="utf-8",
    )
    pending._remember_code(request_id2, code2)
    try:
        after = pending.answer_with_code(
            home, request_id2, code2, True, via="probe", now=expires_at + EPSILON
        )
    finally:
        pending._forget_code(request_id2)
    return {
        "expires_at_minus_epsilon": before,   # expected "applied"
        "expires_at_plus_epsilon": after,     # expected "expired"
        "bound_is_one_poll_early": expires_at == clock.now + WAIT_SECONDS - POLL_SECONDS,
    }


def probe_orphan(home: Path) -> dict[str, Any]:
    """S3, as amended: the record is written BEFORE the return, and the effect follows the return.

    The first version of this probe "abandoned" a waiter thread and counted the effect as never
    run — but the thread was never stopped, so it consumed its own return and ran the effect. That
    was the probe lying, not the mechanism: the effect is the caller's own next line, and killing
    the caller kills the effect with it. What is measurable in-process is the ORDERING: the record
    exists while the waiter is still inside the ask (so a surface that dies here leaves a truthful
    record), and the effect runs exactly once, after the thread completes.
    """
    clock = _Clock(home, 5.0)  # the person answers at t+5
    effects: list[int] = []

    def waiter() -> None:
        outcome = pending.ask_durably(
            home, "run_shell: make deploy", "orphan probe",
            wait_seconds=WAIT_SECONDS, poll_seconds=POLL_SECONDS, clock=clock, sleep=clock.sleep,
            on_asked=lambda q: setattr(clock, "question", q),
        )
        if outcome:
            effects.append(1)  # the gated effect, in the same thread, after the return

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    request_id = ""
    while time.monotonic() < deadline:
        rows = pending.history(home)
        if rows and rows[-1]["outcome"] == "approved":
            request_id = rows[-1]["id"]
            break
        time.sleep(0.02)
    # The record exists while the waiter is still inside the ask — the orphan window's truth.
    record_while_waiting = bool(request_id)
    thread.join(timeout=30)
    return {
        "record_outcome": "approved" if request_id else "missing",
        "record_written_before_return": record_while_waiting,
        "effect_count": len(effects),
        "record_truthful": bool(request_id) and len(effects) == 1,
    }


def probe_real_clock(home: Path) -> list[dict[str, Any]]:
    """S4: the real wall clock — the probe that could actually surprise.

    A thread answers one second after the question is announced (real sleep); the gap measured is
    the REAL time from the answer's `answered_at` to the ask's return. A fake clock cannot catch a
    scheduling surprise; this can.
    """
    rows: list[dict[str, Any]] = []
    for index in range(REAL_RUNS):
        effects: list[int] = []
        question_box: dict[str, Any] = {}

        def on_asked(q: pending.PendingApproval, _box: dict[str, Any] = question_box) -> None:
            _box["q"] = q

            def answerer(_q: pending.PendingApproval = q) -> None:
                time.sleep(REAL_ANSWER_DELAY)
                # No answered_at override here: production's `answer()` stamps the write moment,
                # and this probe measures what production measures. (The first version overrode it
                # to the scheduled moment, and a thread scheduled a second late inflated the gap
                # with its own lateness — the instrument lying, caught on run 8.)
                assert pending.answer(home, _q.id, True)

            threading.Thread(target=answerer, daemon=True).start()

        started = time.time()
        outcome = pending.ask_durably(
            home, "run_shell: make deploy", f"real-clock probe {index}",
            wait_seconds=REAL_WAIT, poll_seconds=REAL_POLL, on_asked=on_asked,
        )
        returned = time.time()
        if outcome:
            effects.append(1)
        q: pending.PendingApproval = question_box["q"]
        recorded = [r for r in pending.history(home) if r["id"] == q.id][-1]["seconds_to_answer"]
        # The moment the answer was WRITTEN, as production stamped it — not the moment it was
        # scheduled for. The first fix of this probe stopped overriding `answered_at` in the file
        # but kept computing the gap from `asked_at + REAL_ANSWER_DELAY`, so an answerer thread
        # that woke late under load still counted its own lateness as the mechanism's latency
        # (the suite tripped on it twice in four full runs on 2026-10-06, both on a busy machine).
        answered_at = q.asked_at + float(recorded)
        rows.append({
            "run": index,
            "outcome": "approved" if outcome else "timeout",
            "gap_seconds": round(returned - answered_at, 4),
            "seconds_to_answer_recorded": recorded,
            "effect_ran": bool(effects),
            "wall_seconds": round(returned - started, 4),
        })
    return rows


def plan_gate_drift_spec() -> dict[str, Any]:
    """The committed join for the number this bench does NOT produce (see the registration).

    Measured when the first plan-gated turn exists to join; until then this is the definition, so
    the number cannot be invented later to fit a claim.
    """
    return {
        "question": "how far the turn's actions drifted from the plan a person approved",
        "left": "approvals/history.jsonl rows with facts.tool == 'plan' (the plan gate's question)",
        "right": "runs.jsonl attempts for the same run_id (the gate passes facts.run_id = turn_id)",
        "measures": [
            "actions whose tool is not named in the approved plan's steps",
            "the per-action questions the turn raised after the plan was approved",
            "time from the plan's approval to the turn's last action",
        ],
        "trigger": "the first recorded plan-gated turn on any home this project reads",
        "status": "unmeasured — this machine's home has no plan-gated turn (checked 2026-10-05)",
    }


def main() -> None:
    """Run every probe on a fresh home and write `results/run.json` beside this file."""
    home = Path.home() / ".chimera-stale-consent-probe"
    results: dict[str, Any] = {"wait_seconds": WAIT_SECONDS, "poll_seconds": POLL_SECONDS}

    import shutil

    shutil.rmtree(home, ignore_errors=True)
    results["sync_grid"] = [p.__dict__ for p in probe_sync_grid(home)]
    results["sync_grid_fast_poll"] = [p.__dict__ for p in probe_sync_grid_fast_poll(home)]
    results["boundaries"] = [p.__dict__ for p in probe_boundaries(home)]
    results["code_expiry"] = probe_code_expiry(home)
    results["orphan"] = probe_orphan(home)
    results["real_clock"] = probe_real_clock(home)
    results["plan_gate_drift_spec"] = plan_gate_drift_spec()

    # The registered summary numbers, computed here and printed — the reading lives in RESULTS.md.
    gaps = [p["gap"] for p in results["sync_grid"] + results["sync_grid_fast_poll"] if p["gap"] is not None]
    real_gaps = [r["gap_seconds"] for r in results["real_clock"]]
    violations = sum(
        1 for p in results["boundaries"]
        if p["outcome"] == "timeout" and p["delta"] < WAIT_SECONDS
    )
    results["summary"] = {
        "sync_gap_max_fake": max(gaps) if gaps else None,
        "sync_gap_max_real": max(real_gaps) if real_gaps else None,
        "boundary_violations": violations,
        "code_bound_holds": (
            results["code_expiry"]["expires_at_minus_epsilon"] == "applied"
            and results["code_expiry"]["expires_at_plus_epsilon"] == "expired"
        ),
        "orphan_record_truthful": results["orphan"]["record_truthful"],
        "seconds_to_answer_exact": all(
            p["seconds_to_answer"] == p["delta"]
            for p in results["sync_grid"] + results["sync_grid_fast_poll"]
        ),
    }

    out = Path(__file__).parent / "results" / "run.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(results["summary"], indent=2, ensure_ascii=False))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
