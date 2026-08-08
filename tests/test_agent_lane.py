"""A card names an agent in its `lane`, and that agent works it.

`KanbanCard.lane` was already a free string and `dispatch()` already took its runners as an injected
mapping — so this needed one lane class and one line where the runners are built. The dispatcher is
untouched: it never knew what lanes exist and it still does not, which is what made the whole thing
cheap.
"""

from __future__ import annotations

from pathlib import Path

from chimera.core.registry import AgentDef
from chimera.kanban import KanbanBoard, dispatch
from chimera.kanban.dispatch import LaneResult
from chimera.kanban.lanes import AgentLane, runners_for
from chimera.kanban.models import KanbanCard


class Recorder:
    """A lane that records rather than runs — the dispatcher's contract is what is under test."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.worked: list[str] = []

    def run(self, card: KanbanCard) -> LaneResult:
        self.worked.append(card.action)
        return LaneResult(success=True, answer=f"{self.name} did it")


def _board(tmp_path: Path) -> KanbanBoard:
    return KanbanBoard(tmp_path / "kanban.json")


def test_a_card_goes_to_the_agent_it_names(tmp_path: Path) -> None:
    board = _board(tmp_path)
    board.add("Review", "review the parser", lane="reviewer")
    board.add("Docs", "write the docs", lane="writer")

    reviewer, writer = Recorder("reviewer"), Recorder("writer")
    outcomes = dispatch(board, {"reviewer": reviewer, "writer": writer})

    assert reviewer.worked == ["review the parser"]
    assert writer.worked == ["write the docs"]
    assert {o.lane for o in outcomes} == {"reviewer", "writer"}


def test_a_card_for_an_agent_that_is_gone_waits_rather_than_failing(tmp_path: Path) -> None:
    """This is why removing an agent leaves its cards alone.

    An unknown lane leaves the card in the backlog — so registering the agent again picks the work
    straight back up. Failing the card instead would turn "I deleted the wrong agent" into work that
    has to be re-entered by hand.
    """
    board = _board(tmp_path)
    card = board.add("Review", "review the parser", lane="reviewer")

    assert dispatch(board, {}) == []
    assert board.get(card.id) is not None
    assert board.get(card.id).column == "backlog"  # type: ignore[union-attr]

    assert dispatch(board, {"reviewer": Recorder("reviewer")})[0].moved_to == "done"


def test_runners_are_keyed_by_agent_id(tmp_path: Path) -> None:
    runners = runners_for(
        [AgentDef(id="reviewer"), AgentDef(id="writer")], workspace=tmp_path
    )
    assert sorted(runners) == ["reviewer", "writer"]
    assert isinstance(runners["reviewer"], AgentLane)


def test_an_agents_pinned_model_beats_the_dispatch_flag(tmp_path: Path) -> None:
    """Someone who pinned a model on an agent said something more specific than the flag that
    dispatched the whole board."""
    pinned = AgentLane(AgentDef(id="a", model="openrouter/pinned"), workspace=tmp_path, model="x")
    assert pinned.model == "openrouter/pinned"

    unpinned = AgentLane(AgentDef(id="b"), workspace=tmp_path, model="openrouter/flag")
    assert unpinned.model == "openrouter/flag"  # empty means inherit, so the flag is what is left


def test_the_dispatcher_never_learned_what_lanes_exist() -> None:
    """The reason this cost one class and one line.

    `dispatch` takes its runners as an argument and looks them up by the card's own string. Nothing
    in it enumerates lanes, so adding a kind of worker is adding a key to a dictionary — asserted
    here because the next person to add a lane should know they do not have to touch it.
    """
    import inspect

    source = inspect.getsource(dispatch)
    for builtin in ("solve", "crew", "AgentLane", "registry"):
        assert builtin not in source
