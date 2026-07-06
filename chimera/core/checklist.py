"""Requirement checklist — catch the constraints a weak model silently drops.

Small models suffer *omission-constraint decay*: "must do X" tends to survive, but "don't do Y"
and "also include Z" quietly fall out as context grows (a documented failure, and the exact
complaint a user raised — small models "always miss some info from the task"). The fix is cheap
and model-agnostic: before solving, have the model extract the task into an explicit list of
atomic requirements; after solving, grade the answer against each one and force a *targeted*
retry on the misses.

It composes with completion contracts but is different: a contract checks declared, deterministic
*artifacts* (file exists, regex matches); this checks *coverage* of the task's own requirements,
which the model extracts. Both are opt-in AND-gates on success. Like the progress ledger, any
model/parse error degrades to "no misses" so the checklist can only help, never falsely block.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ValidationError

from chimera.providers.gateway import Message, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("core.checklist")
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

RequirementKind = Literal["do", "avoid", "include"]

_EXTRACT_SYSTEM = (
    "You extract the atomic requirements of a task. Read the task and output a single JSON object "
    '{"items": [{"text": "...", "kind": "do|avoid|include"}]}. Each item is ONE checkable '
    'requirement: "do" = an action that must happen, "avoid" = something that must NOT happen, '
    '"include" = something the output must contain. Split compound requirements; keep each atomic '
    "and literal. Do NOT solve the task. Output ONLY the JSON object."
)
_GRADE_SYSTEM = (
    "You grade whether an answer meets each requirement. Given the requirements and the answer, "
    'output {"items": [{"text": "<requirement>", "met": true|false}]} with one entry per '
    "requirement, in order. Judge only what the answer actually shows; if a requirement is not "
    "clearly satisfied, mark it false. Output ONLY the JSON object."
)


class Requirement(BaseModel):
    text: str
    kind: RequirementKind = "do"


class _Checklist(BaseModel):
    items: list[Requirement] = []


class _GradeItem(BaseModel):
    text: str
    met: bool = False


class _Grade(BaseModel):
    items: list[_GradeItem] = []


def _strip_fence(text: str) -> str:
    return _FENCE.sub("", text.strip())


class RequirementChecklist:
    """Extracts a task's atomic requirements and grades an answer's coverage of them."""

    def __init__(self, backend: SupportsComplete, model: str | None = None) -> None:
        self.backend = backend
        self.model = model

    def extract(self, task: str) -> list[Requirement]:
        """Extract atomic requirements from the task (empty list on any failure)."""
        try:
            result = self.backend.complete(
                [Message(role="system", content=_EXTRACT_SYSTEM), Message(role="user", content=task)],
                model=self.model,
                temperature=0.0,
            )
            raw = json.loads(_strip_fence(result.content))
            return _Checklist.model_validate(raw).items if isinstance(raw, dict) else []
        except (json.JSONDecodeError, ValueError, ValidationError) as exc:
            _log.debug("checklist extract: unusable response (%s)", exc)
            return []
        except Exception as exc:  # noqa: BLE001 — never break the solve loop
            _log.warning("checklist extract failed, continuing without it: %s", exc)
            return []

    def grade(self, task: str, answer: str, requirements: list[Requirement]) -> list[str]:
        """Return the texts of the requirements the answer does NOT meet (empty on failure)."""
        if not requirements:
            return []
        listing = "\n".join(f"- [{r.kind}] {r.text}" for r in requirements)
        prompt = f"Requirements:\n{listing}\n\n<<answer>>\n{answer}\n<<end-answer>>"
        try:
            result = self.backend.complete(
                [Message(role="system", content=_GRADE_SYSTEM), Message(role="user", content=prompt)],
                model=self.model,
                temperature=0.0,
            )
            raw = json.loads(_strip_fence(result.content))
            if not isinstance(raw, dict):
                return []
            graded = _Grade.model_validate(raw)
            return [item.text for item in graded.items if not item.met]
        except (json.JSONDecodeError, ValueError, ValidationError) as exc:
            _log.debug("checklist grade: unusable response (%s)", exc)
            return []
        except Exception as exc:  # noqa: BLE001 — a grader must never break the run
            _log.warning("checklist grade failed, continuing without it: %s", exc)
            return []
