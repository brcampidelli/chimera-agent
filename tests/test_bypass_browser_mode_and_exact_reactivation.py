"""The "Ignore permissions" switches, from the three ways the audit found them open.

1. In the browser mode (``chimera app``) the screen credential is drawn for a page that any loopback
   caller can read, the agent's own ``curl`` included. So that credential cannot be the owner's yes to
   switching bypass ON: the routes that enable it refuse, and turning it OFF stays allowed.
2. Turning a switch OFF applies in memory whatever the disk does, and a failed write is a warning, not
   a refusal. Turning ON still writes first and changes nothing when the write fails.
3. The one click confirms exactly the entries the owner was shown. A saved entry that was not shown
   is dropped from the file, and an id that was never saved cannot be turned on by naming it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from chimera.core import bypass_switches, code_session  # noqa: E402
from chimera.core.bypass_switches import (  # noqa: E402
    OFF_NOT_SAVED,
    SWITCH_FILE_NAME,
    BypassSaveError,
    forget_all_switches,
)
from chimera.core.code_projects import CodeProjectRegistry  # noqa: E402

SCREEN = "screen-secret-for-tests-0123456789"


@pytest.fixture(autouse=True)
def _fresh_switches() -> Any:
    forget_all_switches()
    code_session.load_session_bypass(None)
    yield
    code_session.load_session_bypass(None)
    forget_all_switches()


def _folder(tmp_path: Path, name: str = "shop") -> Path:
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _conversation_file(home: Path, session_id: str, *, first_message: str = "") -> None:
    """A stored conversation, so the start does not prune its switch, with an optional first message."""
    folder = home / "code_sessions"
    folder.mkdir(parents=True, exist_ok=True)
    messages = [{"role": "user", "content": first_message}] if first_message else []
    (folder / f"{session_id}.json").write_text(
        json.dumps({"session_id": session_id, "workspace": str(home), "messages": messages}),
        encoding="utf-8",
    )


def _disk(path: Path, *, conversations: list[str] = (), projects: list[str] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"conversations": list(conversations), "projects": list(projects)}), encoding="utf-8",
    )


def _app(tmp_path: Path, *, in_page: bool) -> Any:
    from chimera.api.app import build_api_app
    from chimera.api.screen_credential import ScreenCredential
    from chimera.config import Settings
    from chimera.interface import ChatSession

    return build_api_app(
        lambda: ChatSession(None),
        settings=Settings(CHIMERA_HOME=str(tmp_path / "home")),
        workspace=tmp_path,
        screen=ScreenCredential(SCREEN, in_page=in_page),
    )


def _screen_headers() -> dict[str, str]:
    from chimera.api.screen_credential import HEADER_NAME

    return {HEADER_NAME: SCREEN}


# ---- 1. the browser's screen cannot switch bypass on ---------------------------------------------

def test_the_guard_refuses_a_browser_screen_and_a_missing_one() -> None:
    from chimera.api.screen_credential import ScreenCredential, may_switch_bypass_on

    assert may_switch_bypass_on(ScreenCredential(SCREEN, in_page=True)) is False
    assert may_switch_bypass_on(ScreenCredential(SCREEN, in_page=False)) is True
    # No credential is no screen vouched for: the guard fails closed, it does not allow.
    assert may_switch_bypass_on(None) is False


def test_browser_mode_refuses_turning_a_conversation_on_and_says_why(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _conversation_file(home, "c-browser")
    client = TestClient(_app(tmp_path, in_page=True))

    response = client.patch(
        "/api/code/sessions/c-browser/permissions", json={"bypass_permissions": True}, headers=_screen_headers(),
    )

    assert response.status_code == 403
    assert "desktop app" in response.json()["detail"]
    assert code_session.session_bypass("c-browser") is False


def test_browser_mode_still_turns_a_conversation_off(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _conversation_file(home, "c-off")
    _disk(home / SWITCH_FILE_NAME, conversations=["c-off"])  # saved on disk; the app start reads it once
    client = TestClient(_app(tmp_path, in_page=True))
    assert code_session.active_switches().saved_entries() == ([], ["c-off"])

    response = client.patch(
        "/api/code/sessions/c-off/permissions", json={"bypass_permissions": False}, headers=_screen_headers(),
    )

    assert response.status_code == 200, response.text
    assert response.json()["warning"] is None
    assert code_session.active_switches().saved_counts() == (0, 0)


def test_browser_mode_refuses_turning_a_project_on(tmp_path: Path) -> None:
    home = tmp_path / "home"
    shop = _folder(tmp_path)
    client = TestClient(_app(tmp_path, in_page=True))

    response = client.put(
        "/api/code/workspaces/bypass",
        json={"path": str(shop), "bypass_permissions": True},
        headers=_screen_headers(),
    )

    assert response.status_code == 403
    assert "desktop app" in response.json()["detail"]
    assert CodeProjectRegistry(home / "code_projects.json").bypass_for(shop) is False


def test_browser_mode_refuses_a_reactivation_that_would_turn_something_on(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _disk(home / SWITCH_FILE_NAME, conversations=["c1"])
    client = TestClient(_app(tmp_path, in_page=True))

    response = client.post(
        "/api/code/bypass/reactivate",
        json={"projects": [], "conversations": ["c1"]},
        headers=_screen_headers(),
    )

    assert response.status_code == 403
    assert code_session.session_bypass("c1") is False


def test_browser_mode_may_discard_the_saved_list_because_that_turns_nothing_on(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _disk(home / SWITCH_FILE_NAME, conversations=["c1"])
    code_session.load_session_bypass(home / SWITCH_FILE_NAME)
    client = TestClient(_app(tmp_path, in_page=True))

    response = client.post(
        "/api/code/bypass/reactivate",
        json={"projects": [], "conversations": []},
        headers=_screen_headers(),
    )

    assert response.status_code == 200, response.text
    assert code_session.session_bypass("c1") is False
    assert code_session.active_switches().saved_entries() == ([], [])


def test_the_desktop_screen_still_turns_a_conversation_on(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _conversation_file(home, "c-desk")
    client = TestClient(_app(tmp_path, in_page=False))

    response = client.patch(
        "/api/code/sessions/c-desk/permissions", json={"bypass_permissions": True}, headers=_screen_headers(),
    )

    assert response.status_code == 200, response.text
    assert code_session.session_bypass("c-desk") is True


# ---- 2. turning OFF applies in memory whatever the disk does ------------------------------------

def test_turning_a_conversation_off_applies_even_when_the_file_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    code_session.load_session_bypass(tmp_path / "switches.json")
    code_session.set_session_bypass("c1", True)

    def refuse_write(self: object, conversations: Any, projects: Any) -> None:
        raise BypassSaveError("the disk is full")

    monkeypatch.setattr(bypass_switches.BypassSwitches, "_write", refuse_write)

    warning = code_session.set_session_bypass("c1", False)

    assert warning == OFF_NOT_SAVED
    assert code_session.session_bypass("c1") is False


def test_turning_a_project_off_applies_even_when_the_file_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    switches = bypass_switches.open_switches(tmp_path / "switches.json")
    switches.set_project("/repo/shop", True)

    def refuse_write(self: object, conversations: Any, projects: Any) -> None:
        raise BypassSaveError("the disk is full")

    monkeypatch.setattr(bypass_switches.BypassSwitches, "_write", refuse_write)

    warning = switches.set_project("/repo/shop", False)

    assert warning == OFF_NOT_SAVED
    assert switches.project_on("/repo/shop") is False


def test_a_refused_turn_on_still_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    switches = bypass_switches.open_switches(tmp_path / "switches.json")

    def refuse_write(self: object, conversations: Any, projects: Any) -> None:
        raise BypassSaveError("the disk is full")

    monkeypatch.setattr(bypass_switches.BypassSwitches, "_write", refuse_write)

    with pytest.raises(BypassSaveError):
        switches.set_project("/repo/shop", True)
    with pytest.raises(BypassSaveError):
        switches.set_conversation("c1", True)

    assert switches.project_on("/repo/shop") is False
    assert switches.conversation_on("c1") is False


def test_the_route_answers_off_with_a_warning_not_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _conversation_file(home, "c-warn")
    client = TestClient(_app(tmp_path, in_page=False))
    assert client.patch(
        "/api/code/sessions/c-warn/permissions", json={"bypass_permissions": True}, headers=_screen_headers(),
    ).status_code == 200

    def refuse_write(self: object, conversations: Any, projects: Any) -> None:
        raise BypassSaveError("the disk is full")

    monkeypatch.setattr(bypass_switches.BypassSwitches, "_write", refuse_write)

    response = client.patch(
        "/api/code/sessions/c-warn/permissions", json={"bypass_permissions": False}, headers=_screen_headers(),
    )

    assert response.status_code == 200, response.text
    assert response.json()["bypass_permissions"] is False
    assert response.json()["warning"] == OFF_NOT_SAVED
    assert code_session.session_bypass("c-warn") is False


# ---- 4. the project's registration and switch: an enable that fails changes nothing -------------

def test_a_failed_switch_write_leaves_the_registration_but_not_the_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shop = _folder(tmp_path)

    def refuse_write(self: object, conversations: Any, projects: Any) -> None:
        raise BypassSaveError("the disk is full")

    monkeypatch.setattr(bypass_switches.BypassSwitches, "_write", refuse_write)

    with pytest.raises(BypassSaveError):
        CodeProjectRegistry(home / "code_projects.json").set_bypass(str(shop), True)

    assert bypass_switches.open_switches(home / SWITCH_FILE_NAME).project_on(str(shop.resolve())) is False
    assert CodeProjectRegistry(home / "code_projects.json").bypass_for(shop) is False


def test_the_route_says_the_switch_is_unchanged_when_the_enable_cannot_be_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    shop = _folder(tmp_path)
    client = TestClient(_app(tmp_path, in_page=False))

    def refuse_write(self: object, conversations: Any, projects: Any) -> None:
        raise BypassSaveError("the disk is full")

    monkeypatch.setattr(bypass_switches.BypassSwitches, "_write", refuse_write)

    response = client.put(
        "/api/code/workspaces/bypass",
        json={"path": str(shop), "bypass_permissions": True},
        headers=_screen_headers(),
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "could not save the permission switch; the switch is unchanged"
    assert code_session.active_switches().project_on(str(shop.resolve())) is False


def test_a_failed_registration_write_is_not_followed_by_a_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    shop = _folder(tmp_path)

    def refuse_registry(self: object, rows: Any) -> None:
        raise OSError("registry locked")

    monkeypatch.setattr(CodeProjectRegistry, "_write", refuse_registry)

    with pytest.raises(OSError):
        CodeProjectRegistry(home / "code_projects.json").set_bypass(str(shop), True)

    assert bypass_switches.open_switches(home / SWITCH_FILE_NAME).project_on(str(shop.resolve())) is False
    assert not (home / SWITCH_FILE_NAME).exists(), "the switch file was never reached"


# ---- 3. the one click confirms exactly what the owner was shown ---------------------------------

def test_the_listing_names_each_folder_and_conversation_with_its_title(tmp_path: Path) -> None:
    home = tmp_path / "home"
    shop = _folder(tmp_path)
    _disk(home / SWITCH_FILE_NAME, conversations=["titled", "untitled"], projects=[str(shop.resolve())])
    _conversation_file(home, "titled", first_message="Corrigir o login")
    _conversation_file(home, "untitled")
    client = TestClient(_app(tmp_path, in_page=False))

    listed = client.get("/api/code/bypass/saved").json()

    assert listed["projects"] == [str(shop.resolve())]
    by_id = {row["id"]: row["title"] for row in listed["conversations"]}
    assert by_id["titled"] == "Corrigir o login"
    assert by_id["untitled"] == "untitled", "no title: the id is shown"


def test_the_click_confirms_only_the_listed_entries_and_drops_the_rest(tmp_path: Path) -> None:
    home = tmp_path / "home"
    shop = _folder(tmp_path)
    shown_project = str(shop.resolve())
    _disk(home / SWITCH_FILE_NAME, conversations=["shown", "not-shown"], projects=[shown_project, "/elsewhere"])
    _conversation_file(home, "shown")
    _conversation_file(home, "not-shown")
    client = TestClient(_app(tmp_path, in_page=False))

    response = client.post(
        "/api/code/bypass/reactivate",
        json={"projects": [shown_project], "conversations": ["shown"]},
        headers=_screen_headers(),
    )

    assert response.status_code == 200, response.text
    assert response.json()["reactivated_conversations"] == 1
    assert response.json()["reactivated_projects"] == 1
    assert code_session.session_bypass("shown") is True
    assert code_session.session_bypass("not-shown") is False
    assert CodeProjectRegistry(home / "code_projects.json").bypass_for(shop) is False  # not registered: no project

    on_disk = json.loads((home / SWITCH_FILE_NAME).read_text(encoding="utf-8"))
    assert on_disk == {"conversations": ["shown"], "projects": [shown_project]}, (
        "the entry that was saved and not shown is dropped from the file"
    )
    assert client.get("/api/code/bypass/saved").json() == {"projects": [], "conversations": []}


def test_naming_an_id_that_was_never_saved_turns_nothing_on(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _disk(home / SWITCH_FILE_NAME, conversations=["real"])
    _conversation_file(home, "real")
    client = TestClient(_app(tmp_path, in_page=False))

    response = client.post(
        "/api/code/bypass/reactivate",
        json={"projects": [], "conversations": ["never-saved", "real"]},
        headers=_screen_headers(),
    )

    assert response.status_code == 200, response.text
    assert response.json()["reactivated_conversations"] == 1
    assert code_session.session_bypass("never-saved") is False
    assert code_session.session_bypass("real") is True


def test_an_entry_written_to_the_file_mid_session_is_not_listed_and_not_reactivated(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _disk(home / SWITCH_FILE_NAME, conversations=["shown"])
    _conversation_file(home, "shown")
    _conversation_file(home, "injected")
    client = TestClient(_app(tmp_path, in_page=False))
    listed = client.get("/api/code/bypass/saved").json()

    # What an agent's shell would do after the start: write a switch into the file.
    _disk(home / SWITCH_FILE_NAME, conversations=["shown", "injected"])

    assert [row["id"] for row in listed["conversations"]] == ["shown"]
    response = client.post(
        "/api/code/bypass/reactivate",
        json={"projects": [], "conversations": ["shown", "injected"]},
        headers=_screen_headers(),
    )

    assert response.status_code == 200, response.text
    assert code_session.session_bypass("injected") is False, (
        "the file says so, but the screen never saw it in the list, and the saved set is read once"
    )
    assert code_session.session_bypass("shown") is True


def test_a_dismiss_discards_every_saved_switch_and_turns_nothing_on(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _disk(home / SWITCH_FILE_NAME, conversations=["c1", "c2"])
    _conversation_file(home, "c1")
    _conversation_file(home, "c2")
    client = TestClient(_app(tmp_path, in_page=False))

    response = client.post(
        "/api/code/bypass/reactivate", json={"projects": [], "conversations": []}, headers=_screen_headers(),
    )

    assert response.status_code == 200, response.text
    assert code_session.session_bypass("c1") is False
    assert code_session.session_bypass("c2") is False
    assert json.loads((home / SWITCH_FILE_NAME).read_text(encoding="utf-8")) == {"conversations": [], "projects": []}


def test_the_reactivation_body_must_name_both_lists(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, in_page=False))

    response = client.post(
        "/api/code/bypass/reactivate", json={"conversations": ["c1"]}, headers=_screen_headers(),
    )

    assert response.status_code == 422
