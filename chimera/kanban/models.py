"""Kanban data model: a card that flows backlog -> doing -> review -> done."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# "blocked" (M19 Track B): a card whose declared dependencies are not yet satisfied. It sits out
# of the ready queue until they are, then returns to "backlog". Additive — the generic dispatcher
# only ever pulls "backlog", so blocked cards are simply never picked up until unblocked.
Column = Literal["backlog", "doing", "review", "done", "blocked"]
COLUMNS: tuple[Column, ...] = ("backlog", "doing", "review", "done", "blocked")


class KanbanCard(BaseModel):
    """One unit of work on the board, routed to a worker *lane*."""

    id: str
    title: str
    action: str
    lane: str = "solve"
    column: Column = "backlog"
    verify: str | None = None
    result: str = ""
    success: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
