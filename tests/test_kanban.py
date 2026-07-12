"""Tests for the Kanban board and worker-lane dispatch (no network)."""

from __future__ import annotations

from pathlib import Path

from chimera.kanban import KanbanBoard, LaneResult, dispatch
from chimera.kanban.models import KanbanCard


class FakeRunner:
    def __init__(self, success: bool) -> None:
        self.success = success
        self.seen: list[str] = []

    def run(self, card: KanbanCard) -> LaneResult:
        self.seen.append(card.id)
        return LaneResult(success=self.success, answer="ok" if self.success else "nope")


class BoomRunner:
    def run(self, card: KanbanCard) -> LaneResult:
        raise RuntimeError("lane crashed")


def test_board_skips_a_malformed_card_and_saves_atomically(tmp_path: Path) -> None:
    import json

    path = tmp_path / "k.json"
    good = KanbanBoard(path).add("keep", "act")  # writes one valid card
    # Hand-corrupt: append a malformed entry.
    data = json.loads(path.read_text(encoding="utf-8"))
    data.append({"id": "bad", "column": "not-a-column"})
    path.write_text(json.dumps(data), encoding="utf-8")

    board = KanbanBoard(path)  # must not crash on the bad entry
    assert [c.id for c in board.cards()] == [good.id]
    board.add("another", "act")
    assert not (tmp_path / "k.json.tmp").exists()  # atomic save leaves no stray temp file


def test_board_add_starts_in_backlog(tmp_path: Path) -> None:
    board = KanbanBoard(tmp_path / "k.json")
    card = board.add("title", "do the thing", lane="solve")
    assert card.column == "backlog"
    assert board.cards("backlog") == [card]
    assert board.get(card.id) is not None
    assert len(board) == 1


def test_board_move_persists(tmp_path: Path) -> None:
    path = tmp_path / "k.json"
    card = KanbanBoard(path).add("t", "a")
    KanbanBoard(path).move(card.id, "doing")
    reopened = KanbanBoard(path).get(card.id)
    assert reopened is not None and reopened.column == "doing"


def test_board_remove(tmp_path: Path) -> None:
    board = KanbanBoard(tmp_path / "k.json")
    card = board.add("t", "a")
    assert board.remove(card.id) is True
    assert board.remove("nope") is False
    assert len(board) == 0


def test_dispatch_success_to_done_failure_to_review(tmp_path: Path) -> None:
    board = KanbanBoard(tmp_path / "k.json")
    ok = board.add("ok", "do", lane="solve")
    bad = board.add("bad", "do", lane="flaky")
    outcomes = dispatch(board, {"solve": FakeRunner(True), "flaky": FakeRunner(False)})

    by_id = {o.card_id: o for o in outcomes}
    assert by_id[ok.id].moved_to == "done"
    assert by_id[bad.id].moved_to == "review"
    ok_card, bad_card = board.get(ok.id), board.get(bad.id)
    assert ok_card is not None and ok_card.column == "done" and ok_card.success is True
    assert bad_card is not None and bad_card.column == "review" and bad_card.success is False


def test_dispatch_unknown_lane_left_in_backlog(tmp_path: Path) -> None:
    board = KanbanBoard(tmp_path / "k.json")
    card = board.add("x", "do", lane="ghost")
    assert dispatch(board, {"solve": FakeRunner(True)}) == []
    left = board.get(card.id)
    assert left is not None and left.column == "backlog"


def test_dispatch_runner_exception_parks_for_review(tmp_path: Path) -> None:
    board = KanbanBoard(tmp_path / "k.json")
    card = board.add("x", "do", lane="solve")
    outcomes = dispatch(board, {"solve": BoomRunner()})
    assert outcomes[0].moved_to == "review"
    parked = board.get(card.id)
    assert parked is not None and "lane crashed" in parked.result


def test_dispatch_respects_limit(tmp_path: Path) -> None:
    board = KanbanBoard(tmp_path / "k.json")
    for i in range(3):
        board.add(f"c{i}", "do", lane="solve")
    outcomes = dispatch(board, {"solve": FakeRunner(True)}, limit=2)
    assert len(outcomes) == 2
    assert len(board.cards("backlog")) == 1
