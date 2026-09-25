"""The terminal draws the task list the agent keeps, as the desktop does.

`todo_write` is registered on the terminal by default (`CHIMERA_TODO_LIST`, on), and its own
description says it exists so "the person watching can see where you are". The desktop's Code screen
draws it; `chat`, `assist` and the TUI drew nothing, so the model kept a checklist no one at a
terminal ever saw (bench/PLAN-right-hand.md, step 6: "todo_write either drawn or removed").
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from chimera.core.agent import AgentResult, ToolActivity
from chimera.interface import render
from chimera.interface.session import ChatSession, TurnReport, last_todo_list

ITEMS = [
    {"task": "read the failing test", "status": "done"},
    {"task": "fix the parser", "status": "doing"},
    {"task": "run the suite", "status": "pending"},
]


class _Agent:
    """A fake agent that emits the tool activity it is given."""

    def __init__(self, activities: list[ToolActivity]) -> None:
        self._activities = activities

    def run(
        self,
        task: str,
        *,
        on_token: Callable[[str], None] | None = None,
        on_tool: Callable[[ToolActivity], None] | None = None,
    ) -> AgentResult:
        for activity in self._activities:
            if on_tool is not None:
                on_tool(activity)
        return AgentResult(
            answer="ok", steps=1, stopped_reason="final",
            tool_names=[a.name for a in self._activities],
        )


def _todo(items: Any, ok: bool = True) -> ToolActivity:
    return ToolActivity("todo_write", {"items": items}, ok, "recorded" if ok else "error: bad list")


# --- what the turn recorded -------------------------------------------------------------------


def test_the_report_carries_the_last_list_the_agent_wrote() -> None:
    first = [{"task": "read the failing test", "status": "doing"}]
    report = ChatSession(_Agent([_todo(first), _todo(ITEMS)])).send_verbose("fix it")
    assert report.todos == [(i["task"], i["status"]) for i in ITEMS]


def test_a_refused_write_recorded_nothing_and_is_skipped() -> None:
    kept = [{"task": "a", "status": "done"}]
    assert last_todo_list([_todo(kept), _todo(ITEMS, ok=False)]) == [("a", "done")]


def test_items_sent_as_json_text_are_read_as_the_tool_reads_them() -> None:
    import json

    assert last_todo_list([_todo(json.dumps(ITEMS))])[1] == ("fix the parser", "doing")


def test_a_turn_without_the_tool_has_no_list() -> None:
    other = ToolActivity("read_file", {"path": "x"}, True, "ok")
    assert ChatSession(_Agent([other])).send_verbose("hi").todos == []


# --- what the terminal prints -----------------------------------------------------------------


def test_the_list_is_drawn_with_its_progress_and_a_mark_per_status() -> None:
    lines = render.todo_lines(TurnReport(answer="ok", todos=[(i["task"], i["status"]) for i in ITEMS]))
    assert "tasks 1/3 done" in lines[0]
    assert "✓" in lines[1] and "▸" in lines[2] and "○" in lines[3]
    assert "fix the parser" in lines[2]


def test_nothing_is_drawn_when_nothing_was_written() -> None:
    assert render.todo_lines(TurnReport(answer="ok")) == []


def test_a_task_cannot_inject_markup() -> None:
    line = render.todo_lines(TurnReport(answer="ok", todos=[("[red]boom[/red] [/]", "pending")]))[1]
    assert "\\[red]" in line  # escaped, so the console prints it as text instead of parsing it
