"""The routes around `.worktreeinclude` consent and the thirty-day cleanup.

The owner's screen answers the consent; a bearer token alone (the agent's shell can read it) and the
desktop bridge cannot. A conversation unarchived after its folder was removed says so, and the
switch that deletes folders is not one the bridge can write.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.api import screen_credential
from chimera.core.code_session_marks import CodeSessionMarks
from chimera.core.worktree_include_consent import IncludeConsent, consent_path
from tests.conversation_fakes import Agent, App


@pytest.fixture
def agent() -> Any:
    Agent.hold.clear()
    Agent.held.clear()
    yield Agent
    Agent.hold.set()


def _screen(app: App) -> dict[str, str]:
    return {screen_credential.HEADER_NAME: str(app.app.state.screen_credential.value)}


def test_only_the_screen_can_agree_to_a_rules_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent)
    project = tmp_path / "repo"
    project.mkdir()
    IncludeConsent(consent_path(app.home)).request(project, "d1", {"files": [".env"], "more": 0, "bytes": 3})

    listed = app.client.get("/api/code/worktree-include/pending").json()["pending"]
    assert [p["digest"] for p in listed] == ["d1"]
    body = {"project": str(project), "digest": "d1"}

    refused = app.client.post("/api/code/worktree-include/consent", json=body)
    assert refused.status_code == 403
    assert IncludeConsent(consent_path(app.home)).granted(project, "d1") is False

    allowed = app.client.post("/api/code/worktree-include/consent", json=body, headers=_screen(app))
    assert allowed.status_code == 200 and allowed.json() == {"pending": []}
    assert IncludeConsent(consent_path(app.home)).granted(project, "d1") is True


def test_a_digest_nobody_asked_about_cannot_be_agreed_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent)
    answer = app.client.post(
        "/api/code/worktree-include/consent",
        json={"project": str(tmp_path), "digest": "made-up"},
        headers=_screen(app),
    )
    assert answer.status_code == 409


def test_unarchiving_after_the_folder_was_removed_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent)
    sid = app.turn("work")
    assert app.client.post(f"/api/code/sessions/{sid}/archive").status_code == 200
    marks_path = next(app.home.rglob("*marks*.json"), None)
    assert marks_path is not None
    CodeSessionMarks(marks_path).worktree_verdict(sid, at=1.0, removed=True)

    back = app.client.post(f"/api/code/sessions/{sid}/unarchive")

    assert back.json() == {"id": sid, "archived_at": None, "worktree_removed": True}


def test_the_bridge_cannot_write_the_switch_that_deletes_folders() -> None:
    from chimera.api.bridge_routes import OWNER_ONLY_SETTINGS

    assert "CHIMERA_WORKTREE_CLEANUP" in OWNER_ONLY_SETTINGS
