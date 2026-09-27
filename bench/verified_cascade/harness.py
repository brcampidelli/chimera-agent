"""The fixed instruments of the verified-cascade bench, the call log, the spend ledger and the policies.

Everything that decides what a call *is* lives here, pinned by hash, so ``run.py`` (which makes the
calls) and ``report.py`` (which replays the policies over them) read the same thing. Nothing in this
module touches the network: the backends are in ``backends.py``.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.verified_cascade.common import number_check_fires, sha

# --- budget (Amendment 0) --------------------------------------------------------------------------
CAP_USD = 20.00
ADMISSION_STOP_USD = 18.00
THRESHOLD = 0.8

# --- models and their pinned routes (PREREGISTRATION.md §4.1, §5.2) ---------------------------------
LUNA = "openrouter/openai/gpt-6-luna"
SOL = "openrouter/openai/gpt-6-sol"
JEV = "typesafe/jev-1.13"
LOCAL = "qwen3:4b"
G1 = "openrouter/deepseek/deepseek-v4-flash-0731"
G2 = "openrouter/mistralai/mistral-small-3.2-24b-instruct"
G2_SWAP = "openrouter/google/gemini-3.8-flash"
PINS = {LUNA: "OpenAI", SOL: "OpenAI", G1: "DeepInfra", G2: "DeepInfra"}
MAX_TOKENS = {LUNA: 4000, SOL: 4000, G1: 2000, G2: 1000, G2_SWAP: 1000}
#: Per-call estimates used for admission until the pilot has measured a mean (§10).
ESTIMATE_USD = {"draft_luna": 0.0005, "draft_sol": 0.013, "jev": 0.00006, "local": 0.0, "grade": 0.0003}

# --- the drafting prompt, byte for byte (§4.1) ------------------------------------------------------
DRAFT_SYSTEM = (
    "You answer questions using only the excerpts in the user's message. Use no other knowledge. "
    "If the excerpts answer the question, answer it concisely; every fact, number, name, flag or "
    "setting you state must appear in the excerpts. If the excerpts do not contain the answer, say "
    "that the provided excerpts do not cover it, and add no facts or guesses. Answer in the language "
    "of the question."
)
DRAFT_SYSTEM_SHA = sha(DRAFT_SYSTEM)


def draft_user(excerpts: Sequence[str], question: str) -> str:
    body = "\n\n".join(f"[{i}] {text}" for i, text in enumerate(excerpts, 1))
    return f"Excerpts:\n{body}\n\nQuestion: {question}"


def draft_messages(excerpts: Sequence[str], question: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": DRAFT_SYSTEM}, {"role": "user", "content": draft_user(excerpts, question)}]


# --- the decision (§4.1) ----------------------------------------------------------------------------
DECISION_KEY = "grounded_answer"
OPTIONS = ("supported", "unsupported", "declined")
DECISION_INSTRUCTIONS = "Is this answer supported by the excerpts?"
DECISION_CRITERIA = {
    "supported": "The answer addresses the question, and every fact, number and policy it states appears in the excerpts.",
    "unsupported": "The answer states facts that are not in the excerpts, contradicts the excerpts, or answers a different question.",
    "declined": "The answer says the excerpts do not cover the question and adds no facts.",
}


def decision_question() -> Any:
    from chimera.decisions.contract import Choice

    return Choice(
        key=DECISION_KEY, instructions=DECISION_INSTRUCTIONS, options=OPTIONS,
        criteria=dict(DECISION_CRITERIA), event=("supported",), event_name="p_supported",
    )


def decision_state(excerpts: Sequence[str], question: str, answer: str) -> str:
    return json.dumps({"excerpts": list(excerpts), "question": question, "answer": answer}, ensure_ascii=False)


# --- the grader (§5) --------------------------------------------------------------------------------
LABELS = ("wrong", "correct", "incomplete", "declined")
GRADER_SYSTEM = (
    "You grade one answer to a question about software documentation. You see the excerpts the "
    "answerer was given, the question, what is known about the question, and the answer.\n"
    "Labels:\n"
    "- wrong: the answer states any specific claim (a fact, number, name, flag, setting or behaviour) "
    "that the excerpts do not support or that contradicts them, or it answers a different question. "
    "When the excerpts do not answer the question, any answer that asserts facts instead of declining "
    "is wrong. A claim that may be true of the software but is absent from the excerpts is wrong.\n"
    "- correct: when the excerpts answer the question, the answer is consistent with the reference "
    "answer, contains every key fact and makes no wrong claim; when the excerpts do not answer the "
    "question, the answer declines (says the excerpts do not cover it) and adds no facts.\n"
    "- incomplete: the excerpts answer the question and the answer makes no wrong claim, but a key "
    "fact is missing.\n"
    "- declined: the excerpts answer the question, but the answer says they do not.\n"
    'Reply with JSON only: {"label": "wrong" | "correct" | "incomplete" | "declined"}'
)
GRADER_SYSTEM_SHA = sha(GRADER_SYSTEM)


def grader_user(item: dict[str, Any], excerpts: Sequence[str], answer: str) -> str:
    body = "\n\n".join(f"[{i}] {text}" for i, text in enumerate(excerpts, 1))
    if item["family"] == "ANS":
        known = (
            "The excerpts answer this question.\nReference answer: " + item["reference"]
            + "\nKey facts: " + "; ".join(item["key_facts"])
        )
    else:
        known = "The excerpts do not answer this question; the correct response declines and adds no facts."
    return f"Excerpts:\n{body}\n\nQuestion: {item['question']}\n\nWhat is known: {known}\n\nAnswer to grade:\n{answer}"


def parse_label(text: str) -> str | None:
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            label = str(json.loads(text[start : end + 1]).get("label", "")).strip().lower()
        except (ValueError, AttributeError):
            label = ""
        if label in LABELS:
            return label
    return None


def wall_violations(request: dict[str, Any]) -> list[str]:
    """§12: no tools, no plugins, no ``:online`` in a drafting or grading request."""
    blob = json.dumps(request, ensure_ascii=False)
    out = []
    if request.get("tools") or '"tools"' in blob:
        out.append("tools")
    if '"plugins"' in blob:
        out.append("plugins")
    if ":online" in str(request.get("model", "")):
        out.append(":online")
    return out


# --- the call log and the ledger --------------------------------------------------------------------
RATE_LIMIT_MARKERS = ("429", "rate limit", "ratelimit", "too many requests")


class BudgetExhausted(RuntimeError):
    """Admission stopped: the next paid call would cross the admission stop."""


class CallLog:
    """Append-only JSONL of every call; resumable by key; the spend ledger is its sum."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: dict[str, dict[str, Any]] = {}
        self.spent = 0.0
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._absorb(row)

    def _absorb(self, row: dict[str, Any]) -> None:
        self.spent += float(row.get("usd") or 0.0)
        prev = self.rows.get(row["key"])
        if prev is None or row.get("status") == "ok" or prev.get("status") != "ok":
            self.rows[row["key"]] = row

    def get(self, key: str) -> dict[str, Any] | None:
        return self.rows.get(key)

    def done(self, key: str) -> bool:
        """A call is done when it succeeded, or halted after its re-run (it is not asked again)."""
        row = self.rows.get(key)
        return row is not None and row.get("status") in ("ok", "halt")

    def append(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._absorb(row)

    def of_kind(self, kind: str) -> Iterator[dict[str, Any]]:
        return (r for r in self.rows.values() if r.get("kind") == kind)

    def mean_cost(self, kind: str) -> float | None:
        vals = [float(r.get("usd") or 0.0) for r in self.of_kind(kind) if r.get("status") == "ok"]
        return sum(vals) / len(vals) if vals else None


@dataclass
class Ledger:
    log: CallLog
    admission_stop: float = ADMISSION_STOP_USD
    cap: float = CAP_USD

    def estimate(self, kind: str) -> float:
        measured = self.log.mean_cost(kind)
        return measured if measured is not None else ESTIMATE_USD.get(kind, 0.0)

    def admit(self, kind: str) -> None:
        est = self.estimate(kind)
        if est <= 0.0:
            return
        if self.log.spent + est > self.admission_stop or self.log.spent + est > self.cap:
            raise BudgetExhausted(f"spent {self.log.spent:.4f} + {est:.4f} would cross the admission stop {self.admission_stop:.2f}")


def is_rate_limit(message: str) -> bool:
    low = message.lower()
    return any(m in low for m in RATE_LIMIT_MARKERS)


def run_call(log: CallLog, ledger: Ledger, key: str, kind: str, meta: dict[str, Any],
             fn: Callable[[], dict[str, Any]], *, requeue: list[Callable[[], None]] | None = None) -> dict[str, Any]:
    """One logged call: skipped when done, admitted against the budget, re-run once fresh on error
    (PROTOCOL §2). A second failure is a halt; a rate-limit failure is re-queued once, apart."""
    existing = log.get(key)
    if log.done(key) and existing is not None:
        return existing
    prior_rl = existing is not None and existing.get("status") == "rate_limited"
    last = ""
    for attempt in (1, 2):
        ledger.admit(kind)
        t0 = time.perf_counter()
        try:
            out = fn()
        except BudgetExhausted:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed call is data, recorded as a halt
            last = f"{type(exc).__name__}: {str(exc)[:200]}"
            if is_rate_limit(last) and not prior_rl and requeue is not None:
                row = {"key": key, "kind": kind, **meta, "status": "rate_limited", "error": last, "attempt": attempt, "usd": 0.0}
                log.append(row)
                requeue.append(lambda: run_call(log, ledger, key, kind, meta, fn))
                return row
            continue
        # A halt the call itself declares (an empty answer after its re-ask) is final: not re-run.
        status = "halt" if out.get("halt") else "ok"
        row = {"key": key, "kind": kind, **meta, **out, "status": status, "attempt": attempt,
               "seconds": out.get("seconds", round(time.perf_counter() - t0, 3))}
        log.append(row)
        return row
    row = {"key": key, "kind": kind, **meta, "status": "halt", "error": last, "attempt": 2, "usd": 0.0,
           "rate_limit": prior_rl}
    log.append(row)
    return row


# --- labels ------------------------------------------------------------------------------------------
def final_label(votes: dict[str, str | None], adjudicated: str | None, number_fires: bool) -> tuple[str | None, str]:
    """The label of a graded answer: the number check first and final, then agreement, then a human."""
    if number_fires:
        return "wrong", "number_check"
    if adjudicated in LABELS:
        return adjudicated, "adjudicated"
    got = [v for v in votes.values() if v]
    if len(got) == 2 and got[0] == got[1]:
        return got[0], "agreement"
    return None, "pending"


# --- the policies (§4.2): routing over the logged calls -----------------------------------------------
@dataclass(frozen=True)
class Reading:
    choice: str | None
    p: float | None

    def accepts(self, threshold: float) -> bool:
        return self.choice == "supported" and self.p is not None and self.p >= threshold


@dataclass(frozen=True)
class Outcome:
    shipped: str | None
    """``d1`` / ``d2`` / ``f1``, ``handoff``, ``decline`` (a shipped decline), or None when a needed call is missing."""
    escalated: bool
    calls: tuple[str, ...]
    """The calls the policy pays for, by name (``d1``, ``jev:d1``, ``f1``, ...)."""


def verified(first: str, read1: Reading | None, read2: Reading | None, verifier: str, *, has_f1: bool,
             threshold: float = THRESHOLD, declined_ships: bool = False) -> Outcome:
    """B and D: read the draft; supported at p >= threshold ships it; declined hands off (or ships the
    decline, the secondary variant); otherwise escalate to f1, read it, ship or hand off."""
    calls: list[str] = [first, f"{verifier}:{first}"]
    if read1 is None or read1.choice is None:
        return Outcome(None, False, tuple(calls))
    if read1.accepts(threshold):
        return Outcome(first, False, tuple(calls))
    if read1.choice == "declined":
        return Outcome("decline" if declined_ships else "handoff", False, tuple(calls))
    calls += ["f1", f"{verifier}:f1"]
    if not has_f1 or read2 is None or read2.choice is None:
        return Outcome(None, True, tuple(calls))
    return Outcome("f1" if read2.accepts(threshold) else "handoff", True, tuple(calls))


def lexical(d1: str, d2: str, f1: str | None) -> tuple[Outcome, dict[str, Any]]:
    """L: the shipped gate code, in the same two rungs — majority over (d1, d2) at 0.85, then
    ``default_gate``; else f1 through ``default_gate``; else hand off."""
    from chimera.fusion.cascade import default_gate
    from chimera.fusion.consistency import majority
    from chimera.providers.gateway import CompletionResult

    def gate(text: str) -> bool:
        return default_gate(CompletionResult(content=text, model="bench"))

    winner = majority([d1, d2], threshold=0.85)
    info: dict[str, Any] = {"majority": winner is not None}
    if winner is not None:
        info["gate_winner"] = gate(winner)
        if info["gate_winner"]:
            return Outcome("d1" if winner == d1 else "d2", False, ("d1", "d2")), info
    if f1 is None:
        return Outcome(None, True, ("d1", "d2", "f1")), info
    info["gate_f1"] = gate(f1)
    return Outcome("f1" if info["gate_f1"] else "handoff", True, ("d1", "d2", "f1")), info


def escalates(read: Reading | None, threshold: float = THRESHOLD) -> bool:
    return read is not None and read.choice is not None and not read.accepts(threshold) and read.choice != "declined"


def permuted(seq: Sequence[str], key: str) -> list[str]:
    """The paraphrase floor's permutation of excerpt order: seeded, never the identity for n > 1."""
    out = list(seq)
    rng = random.Random(sha("perm:" + key))
    for _ in range(10):
        rng.shuffle(out)
        if out != list(seq) or len(out) < 2:
            break
    return out


__all__ = ["number_check_fires"]
