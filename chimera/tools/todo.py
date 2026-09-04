"""The task list the agent keeps for itself — declared by the agent, never inferred by the harness.

``RunState.tasks`` has existed since compaction was written, carrying a docstring that says exactly
what it is for: *"Task list with status, so finished work is not redone. Status is the point: a bare
copy of the plan's steps asserts that none are done, which is a claim, not a blank."*

Nothing ever wrote to it. ``AutonomousAgent._seed_run_state`` says why, in as many words: that loop
counts attempts, not steps, so it has no completion to report, and filling the field from the plan
would be *"a lie in the field whose entire purpose is to be believed"*.

Both of those are right, and this module does not overturn either. The field was empty because no
truthful source of completion existed — and there is exactly one source that can be truthful about
what an agent has finished, which is the agent. This tool is that source. What it records is a
**claim**, and every surface that renders it has to say so; a list of ticks that reads as verified
progress would be a new instance of the defect this repository has just spent a release fixing six
of, where a screen states something true beside something it has no way to know.

Two rules are enforced rather than suggested, because both are cheap and both are what makes the
list worth reading:

* **At most one item in progress.** A list with six items "doing" answers no question. This is the
  only field that tells a reader — or the agent after a compaction — where the work actually is.
* **Dropped items are named.** An agent that quietly deletes a requirement from its own list looks
  identical, in the rendered output, to one that never had it. The observation names what left, so
  the omission has to be deliberate rather than silent.

Cost, stated because this repository's convention is that a tool schema earns its place: this adds
one schema to every prompt of every step for the whole run, the same charge that keeps ``edit_batch``
off by default (``bench/edit_tools``) and that ``chimera/tools/defer.py`` measured at 86% of the
schema budget for the 18 tools nobody calls. :func:`schema_cost_chars` reports the number so a
future measurement can put it against whatever the list is worth, instead of arguing about it.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from chimera.tools.base import Tool

#: The three states. Deliberately three: "blocked" invites an agent to park work it should be
#: reporting as a failure, and a fourth word buys nothing a sentence in the task text cannot say.
STATUSES = ("pending", "doing", "done")


@dataclass(frozen=True)
class TodoItem:
    """One line of the list: what it is, and where it stands."""

    task: str
    status: str

    def render(self) -> str:
        """The form that goes into the prompt on restore — status first, so it scans."""
        return f"[{self.status}] {self.task}"


def render(items: list[TodoItem]) -> list[str]:
    """The list as ``RunState.tasks`` holds it: plain strings, one per item.

    ``RunState.tasks`` is ``list[str]`` and ``RunState.as_message`` already renders it. Keeping the
    structure here and the strings there means the restore path needs no change at all — the field
    that was designed for this receives exactly what its docstring describes.
    """
    return [item.render() for item in items]


class TodoWriteTool(Tool):
    """Record the run's task list. Replaces the whole list; the agent owns it.

    Whole-list replacement rather than per-item patching is the smaller contract: a patch API needs
    stable ids, an agent that loses track of an id writes a duplicate, and the failure is invisible.
    Sending the list back every time costs a few tokens and cannot desynchronise.
    """

    name = "todo_write"
    description = (
        "Record your task list for this run, replacing whatever you recorded before. "
        "Send every item each time, including the finished ones. At most one item may be 'doing'. "
        "Use this for work with several steps so progress survives a context compaction and the "
        "person watching can see where you are."
    )
    parameters = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "description": "The complete list, in order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "What is to be done."},
                        "status": {"type": "string", "enum": list(STATUSES)},
                    },
                    "required": ["task", "status"],
                },
            }
        },
        "required": ["items"],
    }

    def __init__(
        self,
        state: Any = None,
        *,
        on_change: Callable[[list[TodoItem]], None] | None = None,
    ) -> None:
        """One instance lives in the registry; what it writes to is chosen per thread.

        This tool is registered like every other, which is what makes the session allowlist and the
        governance kernel govern it — a registry the operator scoped down to two tools must not
        quietly gain a third, however harmless the third one is. But a registry is routinely SHARED:
        a crew runs several workers off one, concurrently, in separate threads, and a single
        instance holding a single ``state`` would merge their lists into one and report entries no
        worker in that run created.

        Thread-local binding is what reconciles those. `Agent.run` binds its own run state at the
        top of the call, so each concurrently running agent writes to its own list, and a sequential
        second run rebinds rather than inheriting. Constructed with an explicit ``state`` — as tests
        and library callers do — it binds that one immediately for the calling thread.
        """
        self._local = threading.local()
        #: The structured list per thread lives beside the state it mirrors, for the same reason.
        if state is not None:
            self.bind(state, on_change)

    def bind(self, state: Any, on_change: Callable[[list[TodoItem]], None] | None = None) -> None:
        """Point this thread's writes at ``state``, announcing through ``on_change``."""
        self._local.state = state
        self._local.on_change = on_change
        self._local.items = []

    @property
    def state(self) -> Any:
        """This thread's carrier, defaulting to a private one so an unbound call still records."""
        found = getattr(self._local, "state", None)
        if found is None:
            from chimera.core.context_budget import RunState

            found = RunState()
            self.bind(found)
        return found

    @property
    def on_change(self) -> Callable[[list[TodoItem]], None] | None:
        return getattr(self._local, "on_change", None)

    @property
    def items(self) -> list[TodoItem]:
        self.state  # noqa: B018 - ensures this thread is bound before reading its list
        return list(getattr(self._local, "items", []))

    def run(self, **kwargs: Any) -> str:
        raw = kwargs.get("items")
        # Some backends hand an array-valued argument back as its JSON text. Parsing it here costs
        # one branch and turns a whole class of provider-shaped failure into a working call.
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return "error: 'items' must be a list of {task, status} objects"
        if not isinstance(raw, list):
            return "error: 'items' must be a list of {task, status} objects"

        parsed: list[TodoItem] = []
        for entry in raw:
            if not isinstance(entry, dict):
                return "error: every item must be an object with 'task' and 'status'"
            task = str(entry.get("task") or "").strip()
            status = str(entry.get("status") or "").strip().lower()
            if not task:
                return "error: an item has an empty 'task'"
            if status not in STATUSES:
                return f"error: status must be one of {', '.join(STATUSES)} (got {status!r})"
            parsed.append(TodoItem(task=task, status=status))

        doing = [item.task for item in parsed if item.status == "doing"]
        if len(doing) > 1:
            # Refused rather than normalised. Silently demoting the extras would leave the agent
            # believing a list it does not have, which is the failure this whole module exists to
            # avoid — and the message is enough for it to send a corrected list on the next step.
            return (
                "error: at most one item may be 'doing' at a time, and "
                f"{len(doing)} were sent ({'; '.join(doing)}). "
                "Mark the one you are working on now and leave the rest 'pending'."
            )

        dropped = [item.task for item in self.items if item.task not in {p.task for p in parsed}]
        state = self.state
        self._local.items = parsed
        # Assigned, not mutated in place: a caller holding the old list keeps a coherent snapshot
        # rather than watching it change under them mid-render.
        state.tasks = render(parsed)
        if self.on_change is not None:
            # Suppressed, and the breadth is the point: the list is already committed by the
            # time this runs, so a renderer that falls over must not undo a record the agent
            # has been told it made. A narrower catch would let one broken consumer decide
            # what the run believes about its own progress.
            with contextlib.suppress(Exception):
                self.on_change(list(parsed))

        return self._observation(parsed, dropped)

    @staticmethod
    def _observation(items: list[TodoItem], dropped: list[str]) -> str:
        """What the model reads back — its own list, plus anything that left it.

        Echoing the list is not decoration: it is the only way the agent can tell a call that landed
        from one the provider mangled, and it is what a later step reads when deciding what is next.
        """
        counts = {status: sum(1 for i in items if i.status == status) for status in STATUSES}
        head = (
            f"Task list recorded — {counts['done']} done, "
            f"{counts['doing']} in progress, {counts['pending']} pending."
        )
        body = "\n".join(item.render() for item in items) or "(the list is empty)"
        note = (
            "\n\nNo longer in the list, and therefore no longer reported as work you are doing:\n"
            + "\n".join(f"- {task}" for task in dropped)
            if dropped
            else ""
        )
        return f"{head}\n{body}{note}"


def schema_cost_chars() -> int:
    """Characters this tool's schema adds to every prompt of every step, for the whole run.

    Reported rather than assumed, because the repository's standing rule for a new tool is that it
    earns its schema. ``bench/edit_tools`` is the precedent that kept a measured-but-marginal tool
    off by default; this number is what a comparable measurement for the task list would weigh.
    """
    return len(
        json.dumps(
            {
                "name": TodoWriteTool.name,
                "description": TodoWriteTool.description,
                "parameters": TodoWriteTool.parameters,
            },
            separators=(",", ":"),
        )
    )
