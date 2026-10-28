"""The owner's "Ignore permissions" for a whole project, and both switches surviving a restart.

What the owner asked for (28/10): a project-wide switch that covers every conversation of the folder,
including the ones created later, and both switches (conversation and project) saved so that a
restart comes back with them on. The switch is still only the owner's screen's to turn on or off: the
bridge and a paired device are refused, and the release rules of the conversation switch (block,
denylist, secrets, irreversible actions, the Google search, questions) apply unchanged.

The saved switches are the accepted risk the module docstrings name: a file the agent's shell can
write. These tests prove the server reads and writes them the right way; they do not prove the file
is out of the agent's reach, because it is not.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.core import code_session
from chimera.core.bypass_switches import forget_all_switches
from chimera.core.code_projects import CodeProjectRegistry
from chimera.governance.audit import AuditLog
from chimera.governance.pending import answer, ask_durably, asking_for_call, pending


@pytest.fixture(autouse=True)
def _no_switch_leaks() -> Iterator[None]:
    """The switches are process state: start and end every test with none on and none cached."""
    forget_all_switches()
    code_session.load_session_bypass(None)
    yield
    code_session.load_session_bypass(None)
    forget_all_switches()


class _Store:
    """The one method a turn reads from the conversation store: the switch of a saved conversation."""

    def stored_bypass_permissions(self, session_id: str) -> bool:
        return code_session.session_bypass(session_id)


def _repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "--allow-empty", "-m", "initial"],
        check=True, capture_output=True,
    )
    return repo


def _question() -> Any:
    return SimpleNamespace(
        decision=SimpleNamespace(value="review"), source=SimpleNamespace(value="agent"),
        action="run_shell: git status",
    )


def _owner_for(home: Path, ws: Path, switch: Any, calls: list[Any]) -> Any:
    from chimera.api.code_api import bypass_owner_for

    def original(*args: Any) -> bool:
        calls.append(args)
        return False

    return bypass_owner_for(
        original, bypass_now=switch, home=home, ws=ws, audit=AuditLog(home / "audit.jsonl"),
        approval_sink=None, run_id="run-1",
    )


# ---- the switch a new turn reads ------------------------------------------------------------------

def test_a_new_conversation_in_a_switched_project_runs_without_asking(tmp_path: Path) -> None:
    from chimera.api.code_api import turn_bypass_switch

    home = tmp_path / "home"
    shop = _repo(tmp_path, "shop")
    projects = CodeProjectRegistry(home / "code_projects.json")
    projects.set_bypass(str(shop), True)
    # A conversation that did not exist until now: no saved switch of its own, only the project's.
    switch = turn_bypass_switch(
        via_screen=True, via_bridge=False, session_id="created-later", store=_Store(),
        project_on=lambda: projects.bypass_for(shop),
    )
    calls: list[Any] = []
    owner = _owner_for(home, shop, switch, calls)

    assert switch() is True
    assert owner(_question(), "run_shell: git status") is True
    assert calls == [], "a turn of a switched project must not put a card to the owner"


def test_a_conversation_of_another_project_keeps_asking(tmp_path: Path) -> None:
    from chimera.api.code_api import turn_bypass_switch

    home = tmp_path / "home"
    shop = _repo(tmp_path, "shop")
    other = _repo(tmp_path, "other")
    projects = CodeProjectRegistry(home / "code_projects.json")
    projects.set_bypass(str(shop), True)
    switch = turn_bypass_switch(
        via_screen=True, via_bridge=False, session_id="elsewhere", store=_Store(),
        project_on=lambda: projects.bypass_for(other),
    )
    calls: list[Any] = []
    owner = _owner_for(home, other, switch, calls)

    assert switch() is False
    assert owner(_question(), "run_shell: git status") is False
    assert len(calls) == 1, "another project's conversation is still asked"


def test_turning_the_project_off_asks_again(tmp_path: Path) -> None:
    from chimera.api.code_api import turn_bypass_switch

    home = tmp_path / "home"
    shop = _repo(tmp_path, "shop")
    projects = CodeProjectRegistry(home / "code_projects.json")
    projects.set_bypass(str(shop), True)
    switch = turn_bypass_switch(
        via_screen=True, via_bridge=False, session_id="s", store=_Store(),
        project_on=lambda: projects.bypass_for(shop),
    )
    assert switch() is True

    projects.set_bypass(str(shop), False)

    assert switch() is False


def test_a_worktree_of_a_switched_project_inherits_the_switch_without_a_shell_grant(tmp_path: Path) -> None:
    from chimera.core.project_place import resolve_place

    home = tmp_path / "home"
    shop = _repo(tmp_path, "shop")
    projects = CodeProjectRegistry(home / "code_projects.json")
    projects.set_bypass(str(shop), True)
    placed = resolve_place(str(shop), worktree="new", branch="feature-x", home=home)
    worktree = Path(placed.workspace)

    assert projects.project_for(worktree) is None, "the shell grant is not what this switch needs"
    assert projects.bypass_for(worktree) is True
    assert projects.project_key_for(worktree) == projects.project_key_for(shop) != ""


def test_a_hidden_project_cannot_be_switched_on(tmp_path: Path) -> None:
    home = tmp_path / "home"
    shop = _repo(tmp_path, "shop")
    projects = CodeProjectRegistry(home / "code_projects.json")
    projects.set_flags(str(shop), hidden=True)

    with pytest.raises(ValueError):
        projects.set_bypass(str(shop), True)
    assert projects.bypass_for(shop) is False


def test_hiding_a_project_revokes_its_switch(tmp_path: Path) -> None:
    home = tmp_path / "home"
    shop = _repo(tmp_path, "shop")
    projects = CodeProjectRegistry(home / "code_projects.json")
    projects.set_bypass(str(shop), True)

    projects.set_flags(str(shop), hidden=True)

    assert projects.bypass_for(shop) is False


# ---- both switches survive a restart --------------------------------------------------------------

def test_both_switches_are_saved_and_wait_for_the_screen_after_a_restart(tmp_path: Path) -> None:
    home = tmp_path / "home"
    shop = _repo(tmp_path, "shop")
    code_session.load_session_bypass(home / "code_bypass_switches.json")
    code_session.set_session_bypass("saved-conversation", True)
    CodeProjectRegistry(home / "code_projects.json").set_bypass(str(shop), True)

    # A restart: the process forgets every switch and reads the file once, as the server does.
    forget_all_switches()
    code_session.load_session_bypass(home / "code_bypass_switches.json")

    # Saved, but not on: the file alone never switches anything on (bypass_switches.py).
    assert code_session.session_bypass("saved-conversation") is False
    assert CodeProjectRegistry(home / "code_projects.json").bypass_for(shop) is False
    assert code_session.active_switches().saved_counts() == (1, 1)

    # The owner's one click confirms them, and then both are on.
    shown_projects, shown_conversations = code_session.active_switches().saved_entries()
    code_session.active_switches().reactivate(shown_projects, shown_conversations)
    assert code_session.session_bypass("saved-conversation") is True
    assert CodeProjectRegistry(home / "code_projects.json").bypass_for(shop) is True


def test_a_conversation_switched_off_stays_off_after_a_restart(tmp_path: Path) -> None:
    home = tmp_path / "home"
    code_session.load_session_bypass(home / "code_bypass_switches.json")
    code_session.set_session_bypass("conv", True)
    code_session.set_session_bypass("conv", False)

    forget_all_switches()
    code_session.load_session_bypass(home / "code_bypass_switches.json")

    assert code_session.session_bypass("conv") is False
    assert code_session.active_switches().saved_counts() == (0, 0)


def test_the_saved_conversation_switch_is_not_written_into_the_session_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    code_session.load_session_bypass(home / "code_bypass_switches.json")
    code_session.set_session_bypass("conv", True)

    saved = (home / "code_bypass_switches.json").read_text(encoding="utf-8")
    assert '"conv"' in saved
    assert not any(p.name.startswith("conv") for p in home.rglob("*.json") if p.name != "code_bypass_switches.json")


# ---- the owner's screen only ----------------------------------------------------------------------

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


def _app(tmp_path: Path) -> Any:
    from chimera.api.app import build_api_app
    from chimera.api.screen_credential import ScreenCredential
    from chimera.config import Settings
    from chimera.interface import ChatSession

    return build_api_app(
        lambda: ChatSession(None),
        settings=Settings(CHIMERA_HOME=str(tmp_path / "home")),
        workspace=tmp_path,
        screen=ScreenCredential("screen-secret"),
    )


def _as(app: Any, marker: dict[str, Any]) -> TestClient:
    async def marked(scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] == "http":
            scope = {**scope, **marker}
        await app(scope, receive, send)

    return TestClient(marked)


def test_the_bridge_and_a_paired_device_cannot_switch_a_project_on(tmp_path: Path) -> None:
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
    from chimera.api.devices import DEVICE_SCOPE_KEY
    from chimera.api.screen_credential import HEADER_NAME

    app = _app(tmp_path)
    shop = _repo(tmp_path, "shop")
    body = {"path": str(shop), "bypass_permissions": True}

    bridged = _as(app, {VIA_BRIDGE_SCOPE_KEY: True}).put(
        "/api/code/workspaces/bypass", json=body, headers={HEADER_NAME: "screen-secret"},
    )
    paired = _as(app, {DEVICE_SCOPE_KEY: "device-1"}).put(
        "/api/code/workspaces/bypass", json=body, headers={HEADER_NAME: "screen-secret"},
    )
    bare = TestClient(app).put("/api/code/workspaces/bypass", json=body)

    assert bridged.status_code == 403
    assert paired.status_code == 403
    assert bare.status_code == 403
    assert CodeProjectRegistry(tmp_path / "home" / "code_projects.json").bypass_for(shop) is False


def test_the_owner_screen_switches_the_project_on_and_off(tmp_path: Path) -> None:
    from chimera.api.screen_credential import HEADER_NAME

    app = _app(tmp_path)
    shop = _repo(tmp_path, "shop")
    client = TestClient(app)
    headers = {HEADER_NAME: "screen-secret"}

    on = client.put(
        "/api/code/workspaces/bypass", json={"path": str(shop), "bypass_permissions": True}, headers=headers,
    )
    assert on.status_code == 200, on.text
    assert on.json() == {
        "path": str(shop), "bypass_permissions": True, "approved_waiting": 0, "left_waiting": 0, "warning": None,
    }
    rows = {row["path"]: row for row in client.get("/api/code/workspaces").json()}
    assert rows[str(shop)]["bypass_permissions"] is True

    off = client.put(
        "/api/code/workspaces/bypass", json={"path": str(shop), "bypass_permissions": False}, headers=headers,
    )
    assert off.json()["bypass_permissions"] is False


def test_the_list_says_where_a_conversations_switch_comes_from(tmp_path: Path) -> None:
    from chimera.api.screen_credential import HEADER_NAME

    app = _app(tmp_path)
    shop = _repo(tmp_path, "shop")
    headers = {HEADER_NAME: "screen-secret"}
    app.state.live_turns.start(
        turn_id="turn-list", session_id="list-1", workspace=str(shop), message="work", live_since=0,
    )
    client = TestClient(app)
    client.put(
        "/api/code/workspaces/bypass", json={"path": str(shop), "bypass_permissions": True}, headers=headers,
    )
    rows = {row["id"]: row for row in client.get("/api/code/sessions").json()}
    assert rows["list-1"]["bypass_permissions"] is True
    assert rows["list-1"]["bypass_source"] == "project"

    client.patch(
        "/api/code/sessions/list-1/permissions", json={"bypass_permissions": True}, headers=headers,
    )
    rows = {row["id"]: row for row in client.get("/api/code/sessions").json()}
    assert rows["list-1"]["bypass_source"] == "conversation", "its own switch is the one the label names"


# ---- turning the project on answers the cards already waiting in its conversations ----------------

def _waiting_turn(home: Path, *, run_id: str, command: str) -> tuple[threading.Thread, dict[str, Any]]:
    outcome: dict[str, Any] = {}

    def turn() -> None:
        with asking_for_call("run_shell", {"command": command}):
            outcome["approved"] = ask_durably(
                home, f"run_shell: {command}", "review",
                facts={"run_id": run_id, "tool": "run_shell"}, poll_seconds=0.02, wait_seconds=10,
            )

    thread = threading.Thread(target=turn, daemon=True)
    thread.start()
    return thread, outcome


def _wait_for(home: Path, count: int) -> list[Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if len(pending(home)) >= count:
            return pending(home)
        time.sleep(0.01)
    raise AssertionError(f"expected {count} waiting card(s)")


def test_turning_the_project_on_answers_the_safe_card_of_its_conversation_only(tmp_path: Path) -> None:
    from chimera.api.screen_credential import HEADER_NAME

    app = _app(tmp_path)
    home = tmp_path / "home"
    shop = _repo(tmp_path, "shop")
    other = _repo(tmp_path, "other")
    app.state.live_turns.start(
        turn_id="turn-shop", session_id="shop-1", workspace=str(shop), message="x", live_since=0,
    )
    app.state.live_turns.start(
        turn_id="turn-other", session_id="other-1", workspace=str(other), message="y", live_since=0,
    )
    shop_turn, shop_outcome = _waiting_turn(home, run_id="turn-shop", command="git status")
    _wait_for(home, 1)
    other_turn, _ = _waiting_turn(home, run_id="turn-other", command="git status")
    _wait_for(home, 2)

    response = TestClient(app).put(
        "/api/code/workspaces/bypass", json={"path": str(shop), "bypass_permissions": True},
        headers={HEADER_NAME: "screen-secret"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["approved_waiting"] == 1
    assert response.json()["left_waiting"] == 0
    shop_turn.join(10)
    assert shop_outcome["approved"] is True
    remaining = pending(home)
    assert len(remaining) == 1, "the other project's card is still the owner's to answer"
    assert other_turn.is_alive()
    answer(home, remaining[0].id, False, via="test")
    other_turn.join(10)


def test_a_project_card_the_switch_must_not_release_is_counted_as_left(tmp_path: Path) -> None:
    from chimera.api.screen_credential import HEADER_NAME

    app = _app(tmp_path)
    home = tmp_path / "home"
    shop = _repo(tmp_path, "shop")
    app.state.live_turns.start(
        turn_id="turn-shop", session_id="shop-1", workspace=str(shop), message="x", live_since=0,
    )
    thread, outcome = _waiting_turn(home, run_id="turn-shop", command="rm -rf /")
    card = _wait_for(home, 1)[0]

    response = TestClient(app).put(
        "/api/code/workspaces/bypass", json={"path": str(shop), "bypass_permissions": True},
        headers={HEADER_NAME: "screen-secret"},
    )

    assert response.json()["approved_waiting"] == 0
    assert response.json()["left_waiting"] == 1
    assert any(c.id == card.id for c in pending(home))
    answer(home, card.id, False, via="test")
    thread.join(10)
    assert outcome["approved"] is False
