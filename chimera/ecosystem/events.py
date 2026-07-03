"""Per-step tool-event extraction from an agent transcript (SkillCoach, 2607.01874).

Outcome-only trajectory filtering rewards lucky-but-sloppy runs. SkillCoach's insight is
to grade the PROCESS. This module captures the cheapest, highest-value process signal —
step-following: did each tool step produce visible, successful evidence? — from the agent
transcript the loop already builds. The heavier four-dimension weighted rubric (selection
F1, composition, reflection) needs gold-skill labels and precedence graphs Chimera lacks,
so it is deferred; this is the ablation-proven dimension that drives most of the gain.
"""

from __future__ import annotations

from typing import Any


def events_from_transcript(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract per-tool-step events ``{tool, ok}`` from an agent transcript.

    ``ok`` is False when the tool observation begins with 'error:' (the loop's error
    convention). Pairs each ``role='tool'`` result back to the tool name from the
    preceding assistant ``tool_calls`` by call id.
    """
    names: dict[str, str] = {}
    events: list[dict[str, Any]] = []
    for message in transcript:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                cid = call.get("id")
                if cid:
                    names[cid] = call.get("function", {}).get("name", "?")
        elif role == "tool":
            content = str(message.get("content", "")).strip().lower()
            events.append(
                {
                    "tool": names.get(str(message.get("tool_call_id") or ""), "?"),
                    "ok": not content.startswith("error:"),
                }
            )
    return events


def step_following_score(events: list[dict[str, Any]]) -> float:
    """Fraction of tool steps with visible successful evidence (SkillCoach 'following').

    Returns 1.0 when there were no tool steps — a pure-reasoning answer is not penalized
    for having no tool evidence to show.
    """
    if not events:
        return 1.0
    return sum(1 for event in events if event.get("ok")) / len(events)
