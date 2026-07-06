"""Progress ledger — a structured per-attempt self-check (Magentic-One's inner loop).

A weak or cheap model, when an attempt fails, tends to *wander*: it re-tries a slightly
different phrasing of the same dead end. The fix that lifts weak models is not a stronger
model but a **structured checkpoint**: after each failed attempt, force a fixed questionnaire —
is the task complete? is progress being made? what should the next attempt concretely focus
on? — and validate the answer against a schema. The concrete ``next_focus`` replaces the
generic "verification failed" nudge with an instruction the model can actually act on.

Two deliberate limits:
- The verifier stays authoritative. ``complete`` here is advisory only — a ledger that claims
  "done" never overrides a failing verify-or-revert; we use ``next_focus`` and ``progressing``.
- It never breaks the loop: any model/parse error degrades to a neutral assessment (keep going,
  no extra focus), so the ledger can only help, never stall.

``progressing == False`` is the signal the dual-ledger re-plan (a later step) will escalate on.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

from chimera.providers.gateway import Message, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("core.ledger")
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

_LEDGER_SYSTEM = (
    "You are a progress monitor for an autonomous agent, not the agent. You do not solve the "
    "task; you assess the last attempt and output a single JSON object with exactly these keys: "
    '"complete" (bool: did the last attempt fully satisfy the task?), "progressing" (bool: is the '
    'work moving toward the goal, or stuck repeating the same dead end?), and "next_focus" (a '
    "single concrete instruction for the next attempt — what to do differently or check next; "
    "empty string if complete). Output ONLY the JSON object, nothing else."
)


class ProgressAssessment(BaseModel):
    """A validated self-check of the last attempt."""

    complete: bool = False
    progressing: bool = True
    next_focus: str = ""


_NEUTRAL = ProgressAssessment(complete=False, progressing=True, next_focus="")


def _strip_fence(text: str) -> str:
    return _FENCE.sub("", text.strip())


class ProgressLedger:
    """Runs the structured per-attempt questionnaire against a backend model."""

    def __init__(self, backend: SupportsComplete, model: str | None = None) -> None:
        self.backend = backend
        self.model = model

    def _prompt(self, task: str, answer: str, feedback: str, attempt: int, max_attempts: int) -> str:
        return (
            f"Task:\n{task}\n\n"
            f"Attempt {attempt} of {max_attempts} produced this answer:\n{answer or '(empty)'}\n\n"
            f"Why it did not pass:\n{feedback or '(no detail)'}\n\n"
            "Assess the attempt and return the JSON object."
        )

    def assess(
        self, task: str, answer: str, feedback: str, *, attempt: int, max_attempts: int
    ) -> ProgressAssessment:
        """Return a validated assessment of the last attempt; neutral on any failure."""
        try:
            result = self.backend.complete(
                [
                    Message(role="system", content=_LEDGER_SYSTEM),
                    Message(role="user", content=self._prompt(task, answer, feedback, attempt, max_attempts)),
                ],
                model=self.model,
                temperature=0.0,
            )
            raw = json.loads(_strip_fence(result.content))
            if not isinstance(raw, dict):
                return _NEUTRAL
            return ProgressAssessment.model_validate(raw)
        except (json.JSONDecodeError, ValueError, ValidationError) as exc:
            _log.debug("progress ledger: unusable response (%s) — neutral assessment", exc)
            return _NEUTRAL
        except Exception as exc:  # noqa: BLE001 — a monitor must never break the solve loop
            _log.warning("progress ledger failed, continuing without it: %s", exc)
            return _NEUTRAL
