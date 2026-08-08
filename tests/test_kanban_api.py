"""The board stops being a display case.

`GET /api/kanban` was the only route it had. The dispatcher worked, the lanes existed, and the screen
could render all of it and change none of it — creating, moving, removing and dispatching a card were
terminal-only operations on a board the app was already showing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A client whose board is in `tmp_path`, set through the ENVIRONMENT rather than the injection.

    `build_api_app(settings=...)` does not reach these routes: `register_features` reads
    `get_settings()` directly, so the injected object configures the app and not the feature
    endpoints. Without the env var these tests write to the developer's real `.chimera/kanban.json`
    and then read each other's cards — which is how the first version of this file "passed" while
    dispatching a board it had not built.

    Worth naming rather than papering over: the injection gap is real, pre-dates this work, and is
    the kind of thing that makes a test suite quietly depend on a machine.
    """
    from chimera.api.app import build_api_app
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    get_settings.cache_clear()
    return TestClient(
        build_api_app(lambda: None, settings=Settings(CHIMERA_HOME=str(tmp_path)))  # type: ignore[arg-type]
    )


def _add(client: TestClient, **body: Any) -> dict[str, Any]:
    return client.post("/api/kanban/cards", json={"title": "T", **body}).json()


def test_a_card_can_be_filed_from_the_app(client: TestClient) -> None:
    card = _add(client, action="do the thing", lane="reviewer", verify="pytest -q")
    assert card["column"] == "backlog"
    assert card["lane"] == "reviewer"
    assert card["verify"] == "pytest -q"

    board = client.get("/api/kanban").json()
    assert [c["id"] for c in board["backlog"]] == [card["id"]]


def test_the_action_falls_back_to_the_title(client: TestClient) -> None:
    """Matching the CLI: a one-line card should not have to say the same sentence twice."""
    assert _add(client, title="ship the release")["action"] == "ship the release"


def test_the_board_says_who_works_each_card(client: TestClient) -> None:
    """A board where every card looks identical cannot show what it is about. Which agent picks
    this up is the question a lane answers, so it travels."""
    _add(client, lane="reviewer")
    _add(client, lane="solve")
    lanes = {c["lane"] for c in client.get("/api/kanban").json()["backlog"]}
    assert lanes == {"reviewer", "solve"}


def test_moving_a_card(client: TestClient) -> None:
    card = _add(client)
    moved = client.patch(f"/api/kanban/cards/{card['id']}", json={"column": "doing"})
    assert moved.json()["column"] == "doing"
    assert client.get("/api/kanban").json()["doing"][0]["id"] == card["id"]


def test_a_column_that_does_not_exist_is_refused_with_a_reason(client: TestClient) -> None:
    card = _add(client)
    bad = client.patch(f"/api/kanban/cards/{card['id']}", json={"column": "somewhere"})
    assert bad.status_code == 400 and "somewhere" in bad.json()["detail"]


def test_moving_a_card_that_is_gone_is_a_404_not_a_crash(client: TestClient) -> None:
    assert client.patch("/api/kanban/cards/nope", json={"column": "doing"}).status_code == 404


def test_removing_a_card(client: TestClient) -> None:
    card = _add(client)
    assert client.delete(f"/api/kanban/cards/{card['id']}").json() == {"deleted": True}
    # Idempotent rather than 404: a second delete of something already gone got what it asked for.
    assert client.delete(f"/api/kanban/cards/{card['id']}").json() == {"deleted": False}


def _frames(response: Any) -> list[tuple[str, dict[str, Any]]]:
    """Parse an SSE body into (event, data) pairs."""
    out: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in response.text.splitlines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            out.append((event, json.loads(line.split(":", 1)[1].strip())))
    return out


def test_dispatching_an_empty_board_still_reports(client: TestClient) -> None:
    """No cards is a result, not silence — a stream that closed without a verdict is
    indistinguishable from a stream that broke."""
    frames = _frames(client.post("/api/kanban/run", json={}))
    assert frames == [("done", {"worked": 0})]


def test_a_card_nobody_can_work_waits_rather_than_failing(client: TestClient) -> None:
    """The behaviour that makes deleting an agent recoverable, asserted through the API.

    The card names a lane no runner answers to, so the dispatch reports zero worked and the card is
    still in the backlog — register that agent and the work resumes.
    """
    card = _add(client, lane="an-agent-that-does-not-exist")
    frames = _frames(client.post("/api/kanban/run", json={}))

    assert frames[-1] == ("done", {"worked": 0})
    assert [c["id"] for c in client.get("/api/kanban").json()["backlog"]] == [card["id"]]


def test_each_card_is_reported_as_it_lands(tmp_path: Path) -> None:
    """The whole reason this endpoint streams: a board dispatch calls models for as long as it has
    cards, and a client that only learns the outcome at the end learns it far too late."""
    from chimera.kanban import KanbanBoard
    from chimera.kanban.dispatch import DispatchOutcome, LaneResult, dispatch

    class Lane:
        def run(self, card: Any) -> LaneResult:
            return LaneResult(success=True, answer="ok")

    board = KanbanBoard(tmp_path / "kanban.json")
    board.add("A", "a", lane="x")
    board.add("B", "b", lane="x")

    seen: list[DispatchOutcome] = []
    outcomes = dispatch(board, {"x": Lane()}, on_outcome=seen.append)

    assert [o.card_id for o in seen] == [o.card_id for o in outcomes]
    assert len(seen) == 2


def test_dispatch_without_a_listener_is_unchanged(tmp_path: Path) -> None:
    """A board dispatched from a terminal has nobody to tell, and a callback nobody passed should
    cost nothing — this is the guard that the new parameter stayed optional."""
    from chimera.kanban import KanbanBoard
    from chimera.kanban.dispatch import LaneResult, dispatch

    class Lane:
        def run(self, card: Any) -> LaneResult:
            return LaneResult(success=True, answer="ok")

    board = KanbanBoard(tmp_path / "kanban.json")
    board.add("A", "a", lane="x")
    assert [o.moved_to for o in dispatch(board, {"x": Lane()})] == ["done"]
