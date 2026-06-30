"""Worker-lane dispatch: pull backlog cards through their lane runners.

Each card names a *lane* (``solve``, ``crew``, ...); the dispatcher routes it to the
matching runner and walks it across the board: ``backlog -> doing -> done`` on success,
or ``-> review`` when it fails (so a human sees what needs attention). The runners are
injected, so the dispatch logic is fully testable without a model or a network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from chimera.kanban.board import KanbanBoard
from chimera.kanban.models import Column, KanbanCard
from chimera.telemetry import get_logger

_log = get_logger("kanban.dispatch")


@dataclass
class LaneResult:
    success: bool
    answer: str = ""


class LaneRunner(Protocol):
    """Anything that can work a card and report success (a solve/crew lane)."""

    def run(self, card: KanbanCard) -> LaneResult: ...


@dataclass
class DispatchOutcome:
    card_id: str
    lane: str
    success: bool
    moved_to: str


def dispatch(
    board: KanbanBoard,
    runners: dict[str, LaneRunner],
    *,
    limit: int | None = None,
) -> list[DispatchOutcome]:
    """Run queued backlog cards through their lanes; returns one outcome per card run."""
    outcomes: list[DispatchOutcome] = []
    queued = board.cards("backlog")
    if limit is not None:
        queued = queued[:limit]
    for card in queued:
        runner = runners.get(card.lane)
        if runner is None:
            _log.warning("no runner for lane %r; leaving card %s in backlog", card.lane, card.id)
            continue
        board.move(card.id, "doing")
        try:
            result = runner.run(card)
        except Exception as exc:  # noqa: BLE001 — a lane failure parks the card for review
            result = LaneResult(success=False, answer=f"error: {exc}")
        board.record_result(card.id, success=result.success, result=result.answer)
        target: Column = "done" if result.success else "review"
        board.move(card.id, target)
        outcomes.append(DispatchOutcome(card.id, card.lane, result.success, target))
    return outcomes
