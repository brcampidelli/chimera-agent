"""The open System One interface — typed questions over a state, in the ecosystem's request shape.

Study 22, phase 4 (`bench/PLAN-study22-system-one.md`, Architecture B). One function turns a request
into a response; the CLI (`chimera decide`), the sidecar route (`POST /api/decide`) and the agent tool
call it, so the three cannot drift apart.

**Request** — the shape of the Decisions API this package already speaks as a client
(`chimera/decisions/openrouter.py`)::

    {"state": "...",
     "questions": {"<key>": {"type": "noul" | "choice" | "score",
                             "instructions": "...",
                             "criteria": {...}}},
     "decision": "<optional name for map lookup>"}

* ``noul`` — ``criteria`` may carry ``true`` / ``false``.
* ``choice`` — ``criteria`` is option → meaning; the options are its keys, **in order** (2–255).
* ``score`` — ``criteria`` is level → meaning; the levels are its keys in order, lowest first (2–10).

**Response**::

    {"model": "<the build that answered, when known>",
     "answers": {"<key>": {"noul": p}                                   # noul
                        | {"choice": o, "probabilities": {...}, "confidence": c}      # choice
                        | {"score": e, "probabilities": {...}, "legend": [...], "confidence": c}  # score
                        | {"error": "..."}},                            # a halt, never a guess
     "receipts": {"<key>": {...}}}

``noul`` is the probability of *yes* — through a calibration map only when one exists for exactly this
decision, backend, model and wording, which for an ad-hoc question is never; ``receipts[key].calibrated``
says which. ``confidence`` is ``(K*p_max - 1)/(K - 1)``, a shape statistic and not a probability of being
right (study 22, I5). ``score`` is the expected level index under the probabilities; ``legend`` names the
levels in index order — this package's reading of the field, since its meaning was never documented.

Every question is read **in isolation** (``Decider.decide_many``), and a question the linter rejects
(`chimera/decisions/lint.py`, errors only) is refused before any call, with the reason.
"""

from __future__ import annotations

from typing import Any

from chimera.decisions.contract import Answer, Choice, Decider, Noul, Question, Score
from chimera.decisions.lint import errors

ADHOC = "adhoc"
KINDS = ("noul", "choice", "score")


class RequestError(ValueError):
    """A request that cannot be asked. The message names the question and what is wrong with it."""


def parse_question(key: str, body: Any) -> Question:
    if not isinstance(body, dict):
        raise RequestError(f"{key}: a question is an object")
    kind = str(body.get("type") or "").strip().lower()
    instructions = str(body.get("instructions") or "").strip()
    criteria = body.get("criteria") or {}
    if kind not in KINDS:
        raise RequestError(f"{key}: type must be one of {', '.join(KINDS)}")
    if not instructions:
        raise RequestError(f"{key}: instructions are required")
    if not isinstance(criteria, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in criteria.items()):
        raise RequestError(f"{key}: criteria map strings to strings")
    try:
        if kind == "noul":
            unknown = set(criteria) - {"true", "false"}
            if unknown:
                raise RequestError(f"{key}: a noul's criteria are 'true' and 'false', not {sorted(unknown)}")
            return Noul(key, instructions, criteria=dict(criteria))
        if kind == "choice":
            return Choice(key, instructions, tuple(criteria), criteria=dict(criteria))
        return Score(key, instructions, tuple(criteria), criteria=dict(criteria))
    except ValueError as exc:
        if isinstance(exc, RequestError):
            raise
        raise RequestError(f"{key}: {exc}") from exc


def parse_request(body: Any) -> tuple[str, dict[str, Question], str]:
    """``(state, questions, decision)`` from a request body, or :class:`RequestError`."""
    if not isinstance(body, dict):
        raise RequestError("the request is an object with 'state' and 'questions'")
    state = body.get("state")
    if not isinstance(state, str) or not state.strip():
        raise RequestError("'state' is a non-empty string")
    raw = body.get("questions")
    if not isinstance(raw, dict) or not raw:
        raise RequestError("'questions' is a non-empty object of key -> question")
    questions = {str(k): parse_question(str(k), v) for k, v in raw.items()}
    refused = {k: [f.message for f in errors(q)] for k, q in questions.items() if errors(q)}
    if refused:
        raise RequestError("; ".join(f"{k}: {' / '.join(m)}" for k, m in refused.items()))
    decision = str(body.get("decision") or ADHOC).strip() or ADHOC
    return state, questions, decision


def answer_payload(question: Question, answer: Answer) -> dict[str, Any]:
    if answer.halt:
        return {"error": answer.halt}
    if isinstance(question, Noul):
        if answer.p is None:
            return {"error": "no probability on the answer"}
        return {"noul": round(answer.p, 6)}
    shares = {k: round(v, 6) for k, v in (answer.shares or {}).items()}
    confidence = answer.confidence
    if isinstance(question, Score):
        expectation = answer.expectation(question)
        if expectation is None:
            return {"error": "no probabilities on the answer"}
        return {
            "score": round(expectation, 6), "probabilities": shares, "legend": list(question.levels),
            "confidence": None if confidence is None else round(confidence, 6),
        }
    if answer.choice is None:
        return {"error": "no option on the answer"}
    return {
        "choice": answer.choice, "probabilities": shares,
        "confidence": None if confidence is None else round(confidence, 6),
    }


def decide(decider: Decider, body: Any) -> dict[str, Any]:
    """Answer one request. Raises :class:`RequestError` for a request that cannot be asked; a backend
    that fails on a question is that question's ``error``, and the others are still answered."""
    state, questions, decision = parse_request(body)
    answers = decider.decide_many(decision, state, questions.values())
    by_key = {key: answers[key] for key in questions}
    resolved = next((a.resolved_model for a in by_key.values() if a.resolved_model), "")
    return {
        "model": resolved or decider.backend.model,
        "answers": {key: answer_payload(questions[key], a) for key, a in by_key.items()},
        "receipts": {key: a.receipt() for key, a in by_key.items()},
    }
