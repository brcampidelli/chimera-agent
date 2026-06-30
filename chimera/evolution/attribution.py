"""Step-level failure attribution (SkillAdaptor, 2606.01311).

Trajectory-level skill updates diffuse a single early error across many unrelated steps.
This module attributes a failure to the **first actionable fault step** instead:

- :func:`localize_fault` (the *Localizer*) finds the earliest failed tool observation in
  an agent transcript and the tool that produced it.
- :func:`attribute` (the *Linker*) assigns responsibility to the candidate skill whose
  text overlaps the fault most.
- :func:`qualify` (the *Qualification* gate) accepts a revision only if it does not
  regress (Δ ≥ 0) — so a misdirected revision is rejected, not kept.

Operates on the agent transcript shape (role/content/tool_calls dicts), so it is fully
deterministic and testable without a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if len(word) >= 2}


@dataclass
class Fault:
    tool: str
    error: str
    step_index: int


def localize_fault(transcript: list[dict[str, Any]]) -> Fault | None:
    """Return the first failed tool step in the transcript, or None if none failed."""
    last_tool = "?"
    for index, message in enumerate(transcript):
        role = message.get("role")
        if role == "assistant":
            calls = message.get("tool_calls") or []
            if calls:
                last_tool = calls[0].get("function", {}).get("name", "?")
        elif role == "tool":
            content = str(message.get("content", "")).strip()
            if content.lower().startswith("error:"):
                return Fault(tool=last_tool, error=content, step_index=index)
    return None


def attribute(fault: Fault, candidates: dict[str, str]) -> str | None:
    """Link a fault to the candidate (name -> text) whose tokens overlap it most."""
    target = _tokens(f"{fault.tool} {fault.error}")
    best: str | None = None
    best_score = 0
    for name, text in candidates.items():
        score = len(_tokens(f"{name} {text}") & target)
        if score > best_score:
            best_score, best = score, name
    return best


def qualify(before_score: float, after_score: float) -> bool:
    """Accept a revision only if it does not regress (Δ ≥ 0)."""
    return after_score >= before_score
