"""Structured run events — the vocabulary for live progress from the solve/fusion loops.

Chimera ran silent: you got the final answer and nothing until then. Streaming changes that,
but "streaming" is two different things: raw model **tokens** (a primitive on the gateway) and
structured **run events** (attempt started, attempt failed, run finished). This module defines
the latter — a small, typed event the autonomous loop emits through an optional callback, so a
terminal, the messaging gateway, or the A2A ``message/stream`` endpoint can render progress
without any of them reaching into the loop's internals.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

EventKind = Literal["status", "attempt", "result", "final", "token", "edit"]

# A sink for events. Kept as a plain callable so any consumer (print, a queue, an SSE writer)
# plugs in without a shared base class.
EventSink = Callable[["AgentEvent"], None]


@dataclass
class AgentEvent:
    """One structured progress event.

    ``kind`` picks the shape; ``text`` is a human-readable line; ``data`` carries typed extras
    (e.g. the attempt index, success flag) for machine consumers.
    """

    kind: EventKind
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def status(text: str, **data: Any) -> AgentEvent:
    return AgentEvent("status", text, data)


def attempt(index: int, max_attempts: int) -> AgentEvent:
    return AgentEvent(
        "attempt", f"attempt {index}/{max_attempts}", {"index": index, "max_attempts": max_attempts}
    )


def result(index: int, success: bool, detail: str = "") -> AgentEvent:
    label = "passed" if success else "failed"
    return AgentEvent("result", f"attempt {index} {label}", {"index": index, "success": success, "detail": detail})


def final(success: bool, answer: str) -> AgentEvent:
    return AgentEvent("final", "done" if success else "gave up", {"success": success, "answer": answer})


def edit(path: str, patch: str) -> AgentEvent:
    """A live per-edit event: the REAL unified diff of one file the agent just changed (mid-run).

    Emitted once per write-tool call that actually changed the file, from the real on-disk content
    read before and after the tool ran — never fabricated. ``patch`` is a bounded ``difflib``-style
    unified diff (may be truncated); ``path`` is the workspace-relative file the tool targeted.
    """
    return AgentEvent("edit", f"edited {path}", {"path": path, "patch": patch})
