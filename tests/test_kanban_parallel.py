"""Working several cards at once, without them running over each other.

The board dispatched one card at a time. Making that parallel is not a thread pool around the loop:
two of the three lanes run the autonomous plan / execute / verify-or-revert loop **against the
workspace**, so N of them in one directory means each reverting files another had just written —
silent loss of work, reported as N successes.

So parallel means isolated, and these tests are about the two things that makes true: each card
sees its own directory, and a file two cards both changed is reported instead of quietly taking
one. The third is the board itself, which rewrites its whole JSON on every mutation and had no
lock — unreachable while dispatch was sequential, and reachable the moment it is not.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from chimera.kanban.board import KanbanBoard
from chimera.kanban.dispatch import DispatchOutcome, LaneResult, dispatch
from chimera.kanban.models import KanbanCard


def _git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=path, check=True)


class WritingLane:
    """A lane that writes a file, like the two real ones that touch the workspace do."""

    def __init__(self, workspace: Path, *, name_for: Any = None, body: str = "x") -> None:
        self.workspace = workspace
        self.name_for = name_for or (lambda card: f"{card.id}.txt")
        self.body = body
        self.seen: list[Path] = []

    def for_workspace(self, workspace: Path) -> WritingLane:
        clone = WritingLane(workspace, name_for=self.name_for, body=self.body)
        clone.seen = self.seen  # shared, so the test can see every path handed out
        return clone

    def run(self, card: KanbanCard) -> LaneResult:
        self.seen.append(self.workspace)
        (self.workspace / self.name_for(card)).write_text(self.body, encoding="utf-8")
        return LaneResult(success=True, answer="done")


class ModelOnlyLane:
    """A lane with nothing to rebind — the crew lane's shape."""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, card: KanbanCard) -> LaneResult:
        self.calls += 1
        return LaneResult(success=True, answer=card.action)


def _board(tmp_path: Path, n: int, lane: str = "w") -> KanbanBoard:
    board = KanbanBoard(tmp_path / "kanban.json")
    for i in range(n):
        board.add(f"card {i}", f"do {i}", lane=lane)
    return board


def test_one_worker_is_the_old_path(tmp_path: Path) -> None:
    """The default must not change for anyone. Same lane, same board, sequential."""
    ws = tmp_path / "ws"
    ws.mkdir()
    lane = WritingLane(ws)
    board = _board(tmp_path, 3)
    outcomes = dispatch(board, {"w": lane}, workspace=ws)

    assert [o.moved_to for o in outcomes] == ["done"] * 3
    # Every card ran against the REAL workspace: no worktree was cut, nothing was rebound.
    # (`seen` records the directory of every run, not only the rebound ones — the first version of
    # this assertion read it as "rebinds" and failed on its own bookkeeping.)
    assert lane.seen == [ws, ws, ws]
    assert sorted(p.name for p in ws.glob("*.txt")) == sorted(
        f"{c.id}.txt" for c in board.cards("done")
    )


def test_each_card_gets_its_own_directory(tmp_path: Path) -> None:
    """The point of the whole feature. Two cards, two worktrees, neither is the real workspace."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _git_repo(ws)
    lane = WritingLane(ws)
    board = _board(tmp_path, 3)

    outcomes = dispatch(board, {"w": lane}, workers=3, workspace=ws)

    assert [o.success for o in outcomes] == [True] * 3
    assert len(lane.seen) == 3
    assert len({str(p) for p in lane.seen}) == 3, "two cards shared a directory"
    assert ws.resolve() not in {p.resolve() for p in lane.seen}, "a card ran in the real workspace"


def test_the_edits_come_back(tmp_path: Path) -> None:
    """Isolation that does not merge back is isolation that threw the work away."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _git_repo(ws)
    board = _board(tmp_path, 3)

    dispatch(board, {"w": WritingLane(ws)}, workers=3, workspace=ws)

    # Minus the repo's own seed file, which `_git_repo` commits so the worktrees have a base.
    escritos = sorted(p.name for p in ws.glob("*.txt") if p.name != "seed.txt")
    assert escritos == sorted(f"{c.id}.txt" for c in board.cards("done"))


def test_a_file_two_cards_changed_is_reported_not_swallowed(tmp_path: Path) -> None:
    """The failure isolation cannot prevent, only surface.

    Two cards told to edit the same file both succeed in their own worktree, and only one version
    can come back. Picking one silently is the version of this that loses work and says it did not.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    _git_repo(ws)
    board = _board(tmp_path, 2)
    lane = WritingLane(ws, name_for=lambda _card: "shared.txt")

    conflitos: list[list[str]] = []
    dispatch(board, {"w": lane}, workers=2, workspace=ws, on_conflict=conflitos.append)

    assert conflitos, "two cards wrote the same file and nobody was told"
    assert "shared.txt" in conflitos[0][0]


def test_a_lane_with_nothing_to_rebind_still_runs(tmp_path: Path) -> None:
    """The crew lane calls a model and touches no files; it must not need to know any of this."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _git_repo(ws)
    lane = ModelOnlyLane()
    board = _board(tmp_path, 3, lane="crew")

    outcomes = dispatch(board, {"crew": lane}, workers=3, workspace=ws)

    assert lane.calls == 3
    assert [o.moved_to for o in outcomes] == ["done"] * 3


def test_outside_a_git_repo_it_still_works(tmp_path: Path) -> None:
    """No repo, no worktrees — the cards share the directory and the run still completes.

    Refusing here would make the feature unavailable to anyone whose workspace is not a repo, which
    is most first uses. The docstring says whose decision that is.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    board = _board(tmp_path, 2)

    outcomes = dispatch(board, {"w": WritingLane(ws)}, workers=2, workspace=ws)
    assert [o.success for o in outcomes] == [True, True]


def test_a_failing_card_goes_to_review_not_done(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _git_repo(ws)

    class Explode:
        def run(self, card: KanbanCard) -> LaneResult:
            raise RuntimeError("estourou")

    board = _board(tmp_path, 2)
    outcomes = dispatch(board, {"w": Explode()}, workers=2, workspace=ws)

    assert [o.moved_to for o in outcomes] == ["review", "review"]
    assert all("estourou" in (c.result or "") for c in board.cards("review"))


def test_the_board_survives_being_written_from_several_threads(tmp_path: Path) -> None:
    """The lock, tested against what it is for.

    Every mutation rewrites the whole JSON file. Two threads finishing at the same moment can
    interleave one's read of the card dict with the other's write and persist a board missing a
    result — a task that silently never happened. Reverting the lock makes this fail.
    """
    board = KanbanBoard(tmp_path / "kanban.json")
    ids = [board.add(f"c{i}", "x").id for i in range(40)]
    erros: list[BaseException] = []

    def finish(card_id: str) -> None:
        try:
            for _ in range(10):
                board.move(card_id, "doing")
                board.record_result(card_id, success=True, result="ok")
                board.move(card_id, "done")
                board.cards("done")
        except BaseException as exc:  # noqa: BLE001 - the point is that nothing escapes
            erros.append(exc)

    threads = [threading.Thread(target=finish, args=(i,)) for i in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not erros, f"a concurrent board raised: {erros[:3]}"
    # And the file on disk still holds every card, which is what a lost write would break.
    reread = KanbanBoard(tmp_path / "kanban.json")
    assert len(reread.cards()) == 40
    assert all(c.column == "done" for c in reread.cards())


@pytest.mark.parametrize("workers", [1, 4])
def test_a_lane_nobody_registered_leaves_the_card_alone(tmp_path: Path, workers: int) -> None:
    board = _board(tmp_path, 2, lane="inexistente")
    outcomes = dispatch(board, {"w": WritingLane(tmp_path)}, workers=workers)
    assert outcomes == []
    assert len(board.cards("backlog")) == 2


def test_outcomes_are_reported_as_each_card_finishes(tmp_path: Path) -> None:
    """The app streams these; collecting them at the end would make a parallel board look frozen."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _git_repo(ws)
    board = _board(tmp_path, 4)
    vistos: list[DispatchOutcome] = []

    dispatch(board, {"w": WritingLane(ws)}, workers=4, workspace=ws, on_outcome=vistos.append)

    assert len(vistos) == 4
    assert {o.card_id for o in vistos} == {c.id for c in board.cards("done")}
