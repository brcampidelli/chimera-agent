"""The open System One interface — typed questions over a state, in the ecosystem's request shape.

Study 22, phase 4 (`bench/PLAN-study22-system-one.md`, Architecture B). One function turns a request
into a response; the CLI (`chimera decide`), the sidecar route (`POST /api/decide`) and the agent tool
call it, so the three cannot drift apart.

**Request** — the vendor SDK's own shape (``typesafe-sdk-python``, MIT, ``_schemas/models.py``), which
is also what this package sends as a client (`chimera/decisions/openrouter.py`)::

    {"state": "..." | {...} | [...],
     "questions": {"<key>": {"type": "noul" | "choice" | "score",
                             "instructions": "..." | {...} | [...],   # optional on a noul with criteria
                             "criteria": {...} | [...]}},
     "decision": "<optional name for map lookup>"}

* ``noul`` — ``criteria`` may carry ``true`` / ``false``.
* ``choice`` — ``criteria`` is option → meaning; the options are its keys, **in order** (2–255). A
  meaning of ``null`` leaves the option to be read by its name alone.
* ``score`` — ``criteria`` is the SDK's **list** of level meanings, lowest first, whose levels are
  ``"0"``, ``"1"``, … (2–10); or level → meaning, the levels being the keys in order.

A state, an instruction or a meaning that is an object or a list reaches the model as compact JSON,
``json.dumps(value, ensure_ascii=False)`` — the convention every JevBench adapter uses, and the one our
public-JevBench number was measured with (study 24, item 4: this interface refused the shape until then).

**Response**::

    {"model": "<the build that answered, when known>",
     "answers": {"<key>": {"type": "noul", "noul": p}
                        | {"type": "choice", "choice": o, "probabilities": {...}, "confidence": c}
                        | {"type": "score", "score": e, "probabilities": {...}, "legend": {...}, "confidence": c}
                        | {"type": ..., "error": "..."}},              # a halt, never a guess
     "receipts": {"<key>": {...}}}

``noul`` is the probability of *yes* — through a calibration map only when one exists for exactly this
decision, backend, model and wording, which for an ad-hoc question is never; ``receipts[key].calibrated``
says which. ``confidence`` is ``(K*p_max - 1)/(K - 1)``, a shape statistic and not a probability of being
right (study 22, I5). ``score`` is the expected level index under the probabilities; ``legend`` maps each
level, keyed like ``probabilities``, to its meaning — the SDK's documented reading of the field.

Every question is read **in isolation** (``Decider.decide_many``), and a question the linter rejects
(`chimera/decisions/lint.py`, errors only) is refused before any call, with the reason.
"""

from __future__ import annotations

import json
from typing import Any

from chimera.decisions.contract import Answer, Choice, Decider, Noul, Question, Score
from chimera.decisions.lint import errors

ADHOC = "adhoc"
KINDS = ("noul", "choice", "score")


class RequestError(ValueError):
    """A request that cannot be asked. The message names the question and what is wrong with it."""


def render(value: Any) -> str:
    """How a structured value reaches the model: text as it is, anything else as compact JSON."""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _structured(value: Any) -> bool:
    return isinstance(value, str | dict | list)


def _meanings(key: str, raw: Any, *, allow_null: bool) -> dict[str, str]:
    """name -> rendered meaning from a criteria object. ``null`` is dropped (the name is the meaning)
    where the SDK allows it; any other value that is not text, an object or a list is refused."""
    if not isinstance(raw, dict) or not all(isinstance(k, str) for k in raw):
        raise RequestError(f"{key}: criteria map names to meanings")
    out: dict[str, str] = {}
    for name, value in raw.items():
        if value is None and allow_null:
            continue
        if not _structured(value):
            raise RequestError(f"{key}: the meaning of {name!r} is text, an object or a list")
        out[name] = render(value)
    return out


def parse_question(key: str, body: Any) -> Question:
    if not isinstance(body, dict):
        raise RequestError(f"{key}: a question is an object")
    kind = str(body.get("type") or "").strip().lower()
    if kind not in KINDS:
        raise RequestError(f"{key}: type must be one of {', '.join(KINDS)}")
    raw_instructions = body.get("instructions")
    if raw_instructions is not None and not _structured(raw_instructions):
        raise RequestError(f"{key}: instructions are text, an object or a list")
    instructions = render(raw_instructions).strip() if raw_instructions is not None else ""
    raw_criteria = body.get("criteria")
    try:
        if kind == "noul":
            criteria = _meanings(key, raw_criteria or {}, allow_null=True)
            unknown = set(criteria) - {"true", "false"}
            if unknown:
                raise RequestError(f"{key}: a noul's criteria are 'true' and 'false', not {sorted(unknown)}")
            if not instructions and not criteria:
                raise RequestError(f"{key}: a noul needs instructions or criteria")
            return Noul(key, instructions, criteria=criteria)
        if not instructions:
            raise RequestError(f"{key}: instructions are required")
        if kind == "choice":
            if not isinstance(raw_criteria, dict):
                raise RequestError(f"{key}: a choice's criteria map its options to their meanings")
            return Choice(key, instructions, tuple(raw_criteria), criteria=_meanings(key, raw_criteria, allow_null=True))
        if isinstance(raw_criteria, list):
            # The SDK's Score: ordered meanings, whose position IS the level, starting at zero.
            if any(not _structured(v) for v in raw_criteria):
                raise RequestError(f"{key}: each level's meaning is text, an object or a list")
            levels = tuple(str(i) for i in range(len(raw_criteria)))
            meanings = {level: render(v) for level, v in zip(levels, raw_criteria, strict=True)}
            return Score(key, instructions, levels, criteria=meanings)
        if not isinstance(raw_criteria, dict):
            raise RequestError(f"{key}: a score's criteria are a list of level meanings, lowest first")
        return Score(key, instructions, tuple(raw_criteria), criteria=_meanings(key, raw_criteria, allow_null=False))
    except ValueError as exc:
        if isinstance(exc, RequestError):
            raise
        raise RequestError(f"{key}: {exc}") from exc


def parse_request(body: Any) -> tuple[str, dict[str, Question], str]:
    """``(state, questions, decision)`` from a request body, or :class:`RequestError`."""
    if not isinstance(body, dict):
        raise RequestError("the request is an object with 'state' and 'questions'")
    raw_state = body.get("state")
    if not _structured(raw_state) or not raw_state or (isinstance(raw_state, str) and not raw_state.strip()):
        raise RequestError("'state' is non-empty text, an object or a list")
    state = render(raw_state)
    raw = body.get("questions")
    if not isinstance(raw, dict) or not raw:
        raise RequestError("'questions' is a non-empty object of key -> question")
    questions = {str(k): parse_question(str(k), v) for k, v in raw.items()}
    refused = {k: [f.message for f in errors(q)] for k, q in questions.items() if errors(q)}
    if refused:
        raise RequestError("; ".join(f"{k}: {' / '.join(m)}" for k, m in refused.items()))
    decision = str(body.get("decision") or ADHOC).strip() or ADHOC
    return state, questions, decision


def kind_of(question: Question) -> str:
    return "noul" if isinstance(question, Noul) else "score" if isinstance(question, Score) else "choice"


def answer_payload(question: Question, answer: Answer) -> dict[str, Any]:
    kind = kind_of(question)
    if answer.halt:
        return {"type": kind, "error": answer.halt}
    if isinstance(question, Noul):
        if answer.p is None:
            return {"type": kind, "error": "no probability on the answer"}
        return {"type": kind, "noul": round(answer.p, 6)}
    shares = {k: round(v, 6) for k, v in (answer.shares or {}).items()}
    confidence = answer.confidence
    if isinstance(question, Score):
        expectation = answer.expectation(question)
        if expectation is None:
            return {"type": kind, "error": "no probabilities on the answer"}
        return {
            "type": kind, "score": round(expectation, 6), "probabilities": shares,
            "legend": {level: question.criteria.get(level, level) for level in question.levels},
            "confidence": None if confidence is None else round(confidence, 6),
        }
    if answer.choice is None:
        return {"type": kind, "error": "no option on the answer"}
    return {
        "type": kind, "choice": answer.choice, "probabilities": shares,
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
