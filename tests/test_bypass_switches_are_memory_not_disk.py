"""The disk is never the authority for "Ignore permissions": the screen confirms what it saved, once.

The audit found that the switches saved in ``code_bypass_switches.json`` and ``code_projects.json``
were read as they stood, so a file rewritten by an injected agent turned the switch on mid-turn. The
rule these tests hold: a saved switch does nothing until the owner's screen confirms it in this
process (one click for all of them, or a toggle for one). The file is read once, at start, and what
the screen approved is the memory, which the file copies after each change, never the reverse.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from chimera.core import code_session  # noqa: E402
from chimera.core.bypass_switches import (  # noqa: E402
    SWITCH_FILE_NAME,
    BypassSaveError,
    forget_all_switches,
    open_switches,
)
from chimera.core.code_projects import CodeProjectRegistry  # noqa: E402
from chimera.core.code_session import CodeSessionStore  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_switches() -> Any:
    forget_all_switches()
    code_session.load_session_bypass(None)
    yield
    code_session.load_session_bypass(None)
    forget_all_switches()


def _disk(path: Path, *, conversations: list[str] = (), projects: list[str] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"conversations": list(conversations), "projects": list(projects)}), encoding="utf-8",
    )


def _conversation_file(home: Path, session_id: str) -> None:
    """A stored conversation, so the start does not prune its switch (M1)."""
    folder = home / "code_sessions"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{session_id}.json").write_text(
        json.dumps({"session_id": session_id, "workspace": str(home), "messages": []}), encoding="utf-8",
    )


def _folder(tmp_path: Path, name: str = "shop") -> Path:
    folder = tmp_path / name
    folder.mkdir(parents=True)
    return folder


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


# ---- a restart and the file ---------------------------------------------------------------------

def test_a_restart_reactivates_nothing_by_itself(tmp_path: Path) -> None:
    home = tmp_path / "home"
    folder = _folder(tmp_path)
    CodeProjectRegistry(home / "code_projects.json").register(str(folder))
    key = str(folder.resolve())
    _disk(home / SWITCH_FILE_NAME, conversations=["saved-1"], projects=[key])

    code_session.load_session_bypass(home / SWITCH_FILE_NAME)

    assert code_session.session_bypass("saved-1") is False
    assert CodeProjectRegistry(home / "code_projects.json").bypass_for(folder) is False
    assert code_session.active_switches().saved_counts() == (1, 1)


def test_rewriting_the_file_mid_session_turns_nothing_on(tmp_path: Path) -> None:
    home = tmp_path / "home"
    folder = _folder(tmp_path)
    CodeProjectRegistry(home / "code_projects.json").register(str(folder))
    code_session.load_session_bypass(home / SWITCH_FILE_NAME)  # the start: nothing saved yet
    assert code_session.session_bypass("c2") is False

    # What an injected command would do: write the switches on, while the turn is running.
    _disk(home / SWITCH_FILE_NAME, conversations=["c2"], projects=[str(folder.resolve())])

    assert code_session.session_bypass("c2") is False
    assert CodeProjectRegistry(home / "code_projects.json").bypass_for(folder) is False
    assert code_session.active_switches().saved_counts() == (0, 0)


def test_a_turn_on_the_saved_conversation_stays_off_until_the_screen_confirms(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _disk(home / SWITCH_FILE_NAME, conversations=["c1", "c2"])
    code_session.load_session_bypass(home / SWITCH_FILE_NAME)

    code_session.set_session_bypass("c1", True)

    assert code_session.session_bypass("c1") is True
    assert code_session.session_bypass("c2") is False, "one toggle confirms that one entry only"
    assert code_session.active_switches().saved_counts() == (0, 1)


# ---- the one click -----------------------------------------------------------------------------

def test_one_click_reactivates_every_saved_switch_and_says_so(tmp_path: Path) -> None:
    from chimera.api.screen_credential import HEADER_NAME

    home = tmp_path / "home"
    folder = _folder(tmp_path)
    CodeProjectRegistry(home / "code_projects.json").register(str(folder))
    _disk(home / SWITCH_FILE_NAME, conversations=["c1"], projects=[str(folder.resolve())])
    _conversation_file(home, "c1")
    app = _app(tmp_path)  # the app's start reads the file once
    client = TestClient(app)
    listed = client.get("/api/code/bypass/saved").json()
    assert listed == {
        "projects": [str(folder.resolve())],
        "conversations": [{"id": "c1", "title": "c1"}],  # no title stored: the id is shown
    }
    assert code_session.session_bypass("c1") is False

    response = client.post(
        "/api/code/bypass/reactivate",
        json={"projects": listed["projects"], "conversations": ["c1"]},
        headers={HEADER_NAME: "screen-secret"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reactivated_projects"] == 1 and body["reactivated_conversations"] == 1
    assert code_session.session_bypass("c1") is True
    assert CodeProjectRegistry(home / "code_projects.json").bypass_for(folder) is True
    assert client.get("/api/code/bypass/saved").json() == {"projects": [], "conversations": []}
    assert code_session.active_switches().restored_ok is True


def test_the_bridge_and_a_paired_device_cannot_reactivate(tmp_path: Path) -> None:
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
    from chimera.api.devices import DEVICE_SCOPE_KEY
    from chimera.api.screen_credential import HEADER_NAME

    home = tmp_path / "home"
    _disk(home / SWITCH_FILE_NAME, conversations=["c1"])
    _conversation_file(home, "c1")
    app = _app(tmp_path)

    body = {"projects": [], "conversations": ["c1"]}
    bridged = _as(app, {VIA_BRIDGE_SCOPE_KEY: True}).post(
        "/api/code/bypass/reactivate", json=body, headers={HEADER_NAME: "screen-secret"},
    )
    paired = _as(app, {DEVICE_SCOPE_KEY: "device-1"}).post(
        "/api/code/bypass/reactivate", json=body, headers={HEADER_NAME: "screen-secret"},
    )
    bare = TestClient(app).post("/api/code/bypass/reactivate", json=body)

    assert bridged.status_code == 403
    assert paired.status_code == 403
    assert bare.status_code == 403
    assert code_session.session_bypass("c1") is False
    assert code_session.active_switches().saved_counts() == (0, 1)


# ---- the file is written before the memory changes (M2) -----------------------------------------

def test_a_switch_the_disk_refuses_changes_nothing_in_memory(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a folder should be", encoding="utf-8")
    code_session.load_session_bypass(blocker / SWITCH_FILE_NAME)

    with pytest.raises(BypassSaveError):
        code_session.set_session_bypass("c1", True)

    assert code_session.session_bypass("c1") is False


def test_a_refused_reactivation_changes_nothing_either(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _disk(home / SWITCH_FILE_NAME, conversations=["c1"])
    switches = open_switches(home / SWITCH_FILE_NAME)
    (home / SWITCH_FILE_NAME).unlink()
    (home / SWITCH_FILE_NAME).mkdir()  # the file cannot be replaced: the write fails

    with pytest.raises(OSError):
        switches.reactivate([], ["c1"])

    assert switches.conversation_on("c1") is False
    assert switches.restored_ok is False
    assert switches.saved_counts() == (0, 1)


# ---- the conversation that is gone takes its switch with it (M1) -------------------------------

def test_deleting_a_conversation_deletes_its_switch(tmp_path: Path) -> None:
    home = tmp_path / "home"
    store = CodeSessionStore(home / "code_sessions")
    (home / "code_sessions").mkdir(parents=True)
    (home / "code_sessions" / "gone-1.json").write_text(
        json.dumps({"session_id": "gone-1", "workspace": str(tmp_path), "messages": []}), encoding="utf-8",
    )
    code_session.load_session_bypass(home / SWITCH_FILE_NAME)
    code_session.set_session_bypass("gone-1", True)

    assert store.delete("gone-1") is True

    assert code_session.session_bypass("gone-1") is False
    assert "gone-1" not in (home / SWITCH_FILE_NAME).read_text(encoding="utf-8")


def test_a_switch_whose_conversation_vanished_is_pruned_at_start(tmp_path: Path) -> None:
    home = tmp_path / "home"
    store = CodeSessionStore(home / "code_sessions")
    _disk(home / SWITCH_FILE_NAME, conversations=["ghost", "kept"])
    (home / "code_sessions").mkdir(parents=True)
    (home / "code_sessions" / "kept.json").write_text(
        json.dumps({"session_id": "kept", "workspace": str(tmp_path), "messages": []}), encoding="utf-8",
    )

    code_session.load_session_bypass(home / SWITCH_FILE_NAME)
    code_session.active_switches().prune_conversations(store.exists)

    assert code_session.active_switches().saved_counts() == (0, 1)
    saved = json.loads((home / SWITCH_FILE_NAME).read_text(encoding="utf-8"))
    assert saved["conversations"] == ["kept"]
