"""Worker-lane dispatch: pull backlog cards through their lane runners.

Each card names a *lane* (``solve``, ``crew``, ...); the dispatcher routes it to the
matching runner and walks it across the board: ``backlog -> doing -> done`` on success,
or ``-> review`` when it fails (so a human sees what needs attention). The runners are
injected, so the dispatch logic is fully testable without a model or a network.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
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
    """Anything that can work a card and report success (a solve/crew lane).

    A lane that writes files may also offer ``for_workspace(path) -> LaneRunner``, returning itself
    bound to another directory. Parallel dispatch calls it to give each card its own git worktree;
    a lane without it runs as-is, which is right for one that only calls a model. It is optional
    rather than required so that somebody's own lane keeps working without knowing this exists.
    """

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
    on_outcome: Callable[[DispatchOutcome], None] | None = None,
    workers: int = 1,
    workspace: Path | None = None,
    on_conflict: Callable[[list[str]], None] | None = None,
) -> list[DispatchOutcome]:
    """Run queued backlog cards through their lanes; returns one outcome per card run.

    ``on_outcome`` fires as each card finishes, for a caller that is streaming rather than waiting.
    Without it the behaviour is byte-identical — a board dispatched from a terminal has nobody to
    tell, and a callback nobody passed should cost nothing.

    ``workers`` > 1 works several cards at once. It is not a thread pool around this loop: two of
    the three lanes run the autonomous plan / execute / verify-or-revert loop **against the
    workspace**, so running them together as-is would have each one reverting files the other had
    just written. Parallel therefore means *isolated* — one git worktree per card, edits merged
    back afterwards, and a file two successful cards both changed reported through ``on_conflict``
    rather than silently taking one of them.

    ``workspace`` is where those worktrees are cut from; without it, or outside a git repo, cards
    share the directory and the caller owns that decision. ``workers=1`` is the default and is the
    old path exactly — the sequential loop below, unchanged.
    """
    queued = board.cards("backlog")
    if limit is not None:
        queued = queued[:limit]
    runnable = [(card, runners[card.lane]) for card in queued if card.lane in runners]
    for card in queued:
        if card.lane not in runners:
            _log.warning("no runner for lane %r; leaving card %s in backlog", card.lane, card.id)

    if workers > 1 and len(runnable) > 1:
        return _dispatch_parallel(board, runnable, on_outcome, workers, workspace, on_conflict)

    outcomes: list[DispatchOutcome] = []
    for card, runner in runnable:
        outcomes.append(_work(board, card, runner, on_outcome))
    return outcomes


def _work(
    board: KanbanBoard,
    card: KanbanCard,
    runner: LaneRunner,
    on_outcome: Callable[[DispatchOutcome], None] | None,
) -> DispatchOutcome:
    """One card, start to finish. The board's own lock makes this safe from several threads."""
    board.move(card.id, "doing")
    try:
        result = runner.run(card)
    except Exception as exc:  # noqa: BLE001 — a lane failure parks the card for review
        result = LaneResult(success=False, answer=f"error: {exc}")
    board.record_result(card.id, success=result.success, result=result.answer)
    target: Column = "done" if result.success else "review"
    board.move(card.id, target)
    outcome = DispatchOutcome(card.id, card.lane, result.success, target)
    if on_outcome is not None:
        on_outcome(outcome)
    return outcome


def _dispatch_parallel(
    board: KanbanBoard,
    runnable: list[tuple[KanbanCard, LaneRunner]],
    on_outcome: Callable[[DispatchOutcome], None] | None,
    workers: int,
    workspace: Path | None,
    on_conflict: Callable[[list[str]], None] | None,
) -> list[DispatchOutcome]:
    from chimera.orchestration.isolation import run_isolated

    def unit(card: KanbanCard, runner: LaneRunner) -> Callable[[Path], DispatchOutcome]:
        def go(isolated: Path) -> DispatchOutcome:
            # A lane that writes files says so by offering `for_workspace`; one that only calls a
            # model (the crew lane) has nothing to rebind and runs as it is. Asking the object
            # rather than listing the classes here keeps a user's own lane working too.
            bound = runner
            rebind = getattr(runner, "for_workspace", None)
            if callable(rebind):
                bound = rebind(isolated)
            return _work(board, card, bound, on_outcome)

        return go

    batch = run_isolated(
        workspace or Path.cwd(),
        [(card.id, unit(card, runner)) for card, runner in runnable],
        # Every unit reports its own success on the card; a unit that raised is already recorded as
        # a failed card by `_work`, so nothing here should re-judge it.
        succeeded=lambda _outcome: True,
        max_workers=workers,
    )
    if batch.conflicts and on_conflict is not None:
        on_conflict(batch.conflicts)
    if batch.conflicts:
        _log.warning("kanban dispatch: %d file(s) changed by more than one card", len(batch.conflicts))

    outcomes: list[DispatchOutcome] = []
    for card, _runner in runnable:
        result = next((r for r in batch.results if r.name == card.id), None)
        if result is not None and result.value is not None:
            outcomes.append(result.value)
        else:
            # The unit itself died (a timeout, or a crash outside the lane's own try). The card is
            # still in `doing`, which is honest — nothing finished it — and saying so beats
            # reporting a card that never ran as done.
            outcomes.append(DispatchOutcome(card.id, card.lane, False, "doing"))
    return outcomes
