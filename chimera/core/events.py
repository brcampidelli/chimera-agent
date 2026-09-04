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

EventKind = Literal["status", "attempt", "result", "final", "token", "edit", "tool", "todo"]

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


#: How much of a tool's arguments and of its observation travel in an event.
#:
#: These are progress frames, not the transcript. A `read_file` observation is an entire file and a
#: `run_shell` observation can be a build log; forwarding either in full turns a live progress
#: channel into a firehose that a UI has to defend itself against. The transcript keeps the real
#: thing — this is the caption. Truncation is marked, never silent, because a reader who cannot tell
#: a truncated observation from a complete one will eventually draw a conclusion from half of one.
TOOL_ARG_CHARS = 200
TOOL_OBSERVATION_CHARS = 400


def _clip(text: str, limit: int) -> str:
    """Keep the head. Right for an *argument*, whose first characters are what identify it."""
    return text if len(text) <= limit else text[:limit] + f"… (+{len(text) - limit} chars)"


def _clip_observation(text: str, limit: int) -> str:
    """Keep both ends of an *observation*, because the useful half of a tool result is the last one.

    Head-clipping is how a failing test run becomes invisible in the UI. The first 400 characters of
    pytest are the platform banner, the rootdir and the plugin list; the assertion, the traceback and
    the ``FAILED …`` summary are all at the other end, and the caption therefore showed the part that
    is identical whether the suite passed or failed. The same holds for a compiler, a linter, an npm
    build, and for a Python traceback, whose exception line is last by construction.

    The head is kept too, and deliberately: it carries the command, the path, or the first error,
    which is what tells the reader *which* of several running things they are looking at.
    """
    if len(text) <= limit:
        return text
    head = limit * 2 // 5
    tail = limit - head
    return f"{text[:head]}\n… (+{len(text) - limit} chars)\n{text[-tail:]}"


def tool(name: str, arguments: dict[str, Any], ok: bool, observation: str = "") -> AgentEvent:
    """One tool call, as a live progress event: what was called, with what, and whether it worked.

    This is the difference between watching an agent work and watching a spinner. The loop has
    always known it — ``Agent.run``'s ``on_tool`` carries the full ``ToolActivity`` — but the
    autonomous loop had no event for it, so a run started from the desktop showed attempt boundaries
    and nothing in between.

    Argument *values* are stringified and clipped individually so one large argument (a whole file
    passed to ``write_file``) cannot crowd out the rest; the keys always survive, since knowing
    which path was written matters more than seeing its contents scroll past.
    """
    clipped = {str(k): _clip(str(v), TOOL_ARG_CHARS) for k, v in (arguments or {}).items()}
    return AgentEvent(
        "tool",
        f"{name} {'ok' if ok else 'failed'}",
        {
            "name": name,
            "arguments": clipped,
            "ok": ok,
            "observation": _clip_observation(observation or "", TOOL_OBSERVATION_CHARS),
        },
    )


def todo(items: list[dict[str, str]]) -> AgentEvent:
    """The run's task list, as the agent just declared it.

    ``claimed`` is in the payload and is not decoration. Every other structured event in this
    vocabulary reports something the harness observed: ``edit`` carries a diff read off disk before
    and after, ``result`` carries a verifier's exit code. This one carries what the model said about
    its own progress, which nothing checked — and a row of ticks renders identically whether it was
    earned or asserted. A consumer that drops the flag is free to; a consumer that never had it
    would have no way to know it needed one.

    Sent whole rather than as a delta, mirroring the tool: a UI that missed one frame would
    otherwise render a list that never existed, and there is no sequence number here to notice with.
    """
    counts = {status: sum(1 for i in items if i.get("status") == status) for status in
              ("pending", "doing", "done")}
    return AgentEvent(
        "todo",
        f"task list: {counts['done']}/{len(items)} done",
        {"items": list(items), "claimed": True, **counts},
    )


def edit(path: str, patch: str) -> AgentEvent:
    """A live per-edit event: the REAL unified diff of one file the agent just changed (mid-run).

    Emitted once per write-tool call that actually changed the file, from the real on-disk content
    read before and after the tool ran — never fabricated. ``patch`` is a bounded ``difflib``-style
    unified diff (may be truncated); ``path`` is the workspace-relative file the tool targeted.
    """
    return AgentEvent("edit", f"edited {path}", {"path": path, "patch": patch})
