"""Kanban: a task board with worker lanes that dispatch to crews / the autonomous loop.

Cards flow backlog -> doing -> review -> done. The board persists to JSON; the
dispatcher routes each card to its lane's runner. This is Chimera's operational
orchestration surface — the same loop the agent already runs, made visible and queued.
"""

from chimera.kanban.board import KanbanBoard
from chimera.kanban.dispatch import (
    DispatchOutcome,
    LaneResult,
    LaneRunner,
    dispatch,
)
from chimera.kanban.models import COLUMNS, Column, KanbanCard

__all__ = [
    "KanbanBoard",
    "KanbanCard",
    "COLUMNS",
    "Column",
    "dispatch",
    "DispatchOutcome",
    "LaneResult",
    "LaneRunner",
]
