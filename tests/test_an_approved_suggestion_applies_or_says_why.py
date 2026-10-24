"""The owner's yes on a settings suggestion either writes it or says why it did not.

Observed on 2026-10-09/10 (Chimera Desktop 0.64.5, Windows): the owner approved a suggestion to
change ``CHIMERA_DEFAULT_MODEL``, the card stayed in the queue, the setting did not change, and only
a second yes applied it. The history file shows one line per card, so the first yes ended nothing.

`setting_suggestions.resolve` named every refusal it expected — ``stale``, ``invalid``, ``expired``,
``changed`` — and caught only ``ValueError`` around the write. Anything else the write raised (an
``OSError`` from ``.env`` held open by another reader, a temporary file the scanner had not let go
of) escaped the route as a 500. The card's file was untouched, so it stayed listed; the screen's
answer handler had no ``catch``, so it said nothing; and the next poll drew the same card again. To
the owner that is exactly "I approved it and it is still pending".

Held here: such a failure answers ``failed`` with the reason, writes nothing, and leaves the card
waiting so the owner's next yes is a real retry — not a write the owner never saw happen.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.config import get_settings
from chimera.governance import setting_suggestions
from chimera.governance.pending import history, pending
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun

URL = "http://127.0.0.1:65011"
KEY = "CHIMERA_DEFAULT_MODEL"
BEFORE = "openrouter/vendor/before"
AFTER = "openrouter/vendor/after"


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "true")
    monkeypatch.setenv("CHIMERA_BRIDGE_FULL_CONTROL", "true")
    monkeypatch.setenv("CHIMERA_BRIDGE_OPERATE", "")
    monkeypatch.setenv(KEY, BEFORE)
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    app.state.desktop_bridge.attach(URL)
    return app


def _suggest(client: TestClient, app: Any) -> str:
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    made = client.post(
        "/api/bridge/call",
        json={"route": "settings.suggest_model", "body": {"model": AFTER, "scope": "global"}},
        headers=headers,
    )
    assert made.status_code == 200 and made.json()["status"] == 202, made.text
    return str(made.json()["data"]["suggestion"])


def _yes(client: TestClient, tmp_path: Path, request_id: str) -> Any:
    s = setting_suggestions.read(tmp_path / "home", request_id)
    assert s is not None
    return client.post(
        f"/api/approvals/{request_id}",
        json={"approved": True, "digest": setting_suggestions.digest(s)},
    )


def _env_file(tmp_path: Path) -> str:
    env = tmp_path / ".env"
    return env.read_text(encoding="utf-8") if env.exists() else ""


def test_scoped_model_suggestion_applies_only_to_project_and_turns_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chimera.core.code_projects import CodeProjectRegistry

    app = _app(tmp_path, monkeypatch)
    home = tmp_path / "home"
    project, other = tmp_path / "project", tmp_path / "other"
    project.mkdir()
    other.mkdir()
    registry = CodeProjectRegistry(home / "code_projects.json")
    registry.register(str(project))
    registry.register(str(other))
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    with TestClient(app) as client:
        made = client.post(
            "/api/bridge/call",
            json={"route": "settings.suggest_model", "body": {
                "model": AFTER, "scope": "project", "project": str(project),
            }}, headers=headers,
        )
        assert made.status_code == 200, made.text
        assert made.json()["status"] == 202
        request_id = str(made.json()["data"]["suggestion"])
        assert os.environ[KEY] == BEFORE
        assert registry.model_for(project) == ""
        assert _yes(client, tmp_path, request_id).json()["outcome"] == "applied"
        assert registry.model_for(project) == AFTER
        assert registry.model_for(other) == ""
        assert os.environ[KEY] == BEFORE

        stale = client.post(
            "/api/bridge/call",
            json={"route": "settings.suggest_model", "body": {
                "model": BEFORE, "scope": "project", "project": str(project),
            }}, headers=headers,
        )
        stale_id = str(stale.json()["data"]["suggestion"])
        registry.set_model(str(project), "project/changed")
        suggestion = setting_suggestions.read(home, stale_id)
        assert suggestion is not None
        answered = client.post(
            f"/api/approvals/{stale_id}",
            json={"approved": True, "digest": setting_suggestions.digest(suggestion)},
        )
        assert answered.json()["outcome"] == "stale"
        assert history(home)[-1]["result"] == "stale"
        assert registry.model_for(project) == "project/changed"
        assert os.environ[KEY] == BEFORE


def test_a_forged_change_with_a_different_scope_or_target_is_not_listed_or_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(tmp_path, monkeypatch)
    home = tmp_path / "home"
    request_id = setting_suggestions.suggest(
        home,
        [setting_suggestions.Change("project_model", "", AFTER, "project", "/registered/project", "Project")],
        suggested_by="desktop_bridge", scope="project", target="/registered/project", display_target="Project",
    )
    path = setting_suggestions._ask_path(home, request_id)
    forged = json.loads(path.read_text(encoding="utf-8"))
    forged["suggestion"]["changes"][0]["target"] = "/different/project"
    path.write_text(json.dumps(forged), encoding="utf-8")

    assert setting_suggestions.read(home, request_id) is None
    with TestClient(app) as client:
        assert all(row["id"] != request_id for row in client.get("/api/approvals").json())
        decision = client.post(
            f"/api/approvals/{request_id}",
            json={"approved": True, "digest": "forged"},
        )
    assert decision.status_code == 200
    assert decision.json().get("outcome") != "applied"
    assert os.environ[KEY] == BEFORE


def test_scoped_conversation_suggestion_does_not_change_install_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.core.code_session import CodeSessionStore

    app = _app(tmp_path, monkeypatch)
    home = tmp_path / "home"
    sid = "conversation1"
    store = CodeSessionStore(home / "code_sessions")
    store.set_stored_model(sid, "")
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    with TestClient(app) as client:
        made = client.post(
            "/api/bridge/call",
            json={"route": "settings.suggest_model", "body": {
                "model": AFTER, "scope": "conversation", "session_id": sid,
            }}, headers=headers,
        )
        assert made.status_code == 200, made.text
        request_id = str(made.json()["data"]["suggestion"])
        assert os.environ[KEY] == BEFORE
        assert _yes(client, tmp_path, request_id).json()["outcome"] == "applied"
        assert store.stored_model(sid) == AFTER
        assert os.environ[KEY] == BEFORE

def test_conversation_suggestion_that_went_stale_does_not_replace_the_new_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chimera.core.code_session import CodeSessionStore

    app = _app(tmp_path, monkeypatch)
    home = tmp_path / "home"
    sid = "conversation-stale"
    store = CodeSessionStore(home / "code_sessions")
    store.set_stored_model(sid, "")
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    with TestClient(app) as client:
        made = client.post(
            "/api/bridge/call",
            json={"route": "settings.suggest_model", "body": {
                "model": AFTER, "scope": "conversation", "session_id": sid,
            }}, headers=headers,
        )
        assert made.status_code == 200, made.text
        request_id = str(made.json()["data"]["suggestion"])
        store.set_stored_model(sid, "owner/new-pin")
        answered = _yes(client, tmp_path, request_id)
        assert answered.status_code == 200, answered.text
        assert answered.json()["outcome"] == "stale"
        assert store.stored_model(sid) == "owner/new-pin"
        assert os.environ[KEY] == BEFORE


def test_approvals_list_returns_each_change_scope_target_and_display_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(tmp_path, monkeypatch)
    home = tmp_path / "home"
    conversation_id = setting_suggestions.suggest(
        home,
        [setting_suggestions.Change(
            "conversation_model", "", AFTER, "conversation", "session-1", "Fix login (session-1)"
        )],
        suggested_by="desktop_bridge", scope="conversation", target="session-1",
        display_target="Fix login (session-1)",
    )
    project_id = setting_suggestions.suggest(
        home,
        [setting_suggestions.Change(
            "project_model", "", AFTER, "project", str(tmp_path / "project"), "Project Alpha"
        )],
        suggested_by="desktop_bridge", scope="project", target=str(tmp_path / "project"),
        display_target="Project Alpha",
    )
    with TestClient(app) as client:
        response = client.get("/api/approvals")
    assert response.status_code == 200, response.text
    cards = {card["id"]: card for card in response.json()}
    conversation = cards[conversation_id]["suggestion"]["changes"][0]
    assert (conversation["scope"], conversation["target"], conversation["display_target"]) == (
        "conversation", "session-1", "Fix login (session-1)"
    )
    project = cards[project_id]["suggestion"]["changes"][0]
    assert (project["scope"], project["target"], project["display_target"]) == (
        "project", str(tmp_path / "project"), "Project Alpha"
    )


def test_a_mixed_scope_change_card_is_not_listed_or_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    app = _app(tmp_path, monkeypatch)
    home = tmp_path / "home"
    request_id = setting_suggestions.suggest(
        home,
        [setting_suggestions.Change(KEY, BEFORE, AFTER, "global", "")],
        suggested_by="desktop_bridge",
    )
    path = home / "approvals" / f"{request_id}.ask.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["suggestion"]["changes"][0].update(
        {"scope": "project", "target": str(tmp_path / "forged")}
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert setting_suggestions.read(home, request_id) is None
    with TestClient(app) as client:
        listed = client.get("/api/approvals")
        response = client.post(
            f"/api/approvals/{request_id}", json={"approved": True, "digest": "forged"}
        )
    assert request_id not in {card["id"] for card in listed.json()}
    assert response.json().get("outcome") != "applied"
    assert os.environ[KEY] == BEFORE


def test_the_bridge_cannot_resolve_an_approval_card_with_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.core.code_projects import CodeProjectRegistry

    app = _app(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    CodeProjectRegistry(tmp_path / "home" / "code_projects.json").register(str(project))
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    with TestClient(app) as client:
        made = client.post("/api/bridge/call", json={
            "route": "settings.suggest_model", "body": {
                "model": AFTER, "scope": "project", "project": str(project),
            },
        }, headers=headers)
        request_id = str(made.json()["data"]["suggestion"])
        response = client.post("/api/bridge/call", json={
            "route": "approve.approval", "body": {"id": request_id, "approved": True},
        }, headers=headers)
        assert response.status_code in {400, 403}


def test_scope_less_legacy_card_is_read_as_global(tmp_path: Path) -> None:
    home = tmp_path / "home"
    request_id = setting_suggestions.suggest(
        home, [setting_suggestions.Change(KEY, BEFORE, AFTER)], suggested_by="desktop_bridge",
    )
    suggestion = setting_suggestions.read(home, request_id)
    assert suggestion is not None
    path = home / "approvals" / f"{request_id}.ask.json"
    payload = __import__("json").loads(path.read_text(encoding="utf-8"))
    payload["suggestion"].pop("scope", None)
    payload["suggestion"].pop("target", None)
    path.write_text(__import__("json").dumps(payload), encoding="utf-8")
    suggestion = setting_suggestions.read(home, request_id)
    assert suggestion is not None
    assert suggestion.changes[0].scope == "global"


def test_a_suggestion_digest_includes_the_rendered_scope_target_and_detects_change(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    request_id = setting_suggestions.suggest(
        tmp_path, [setting_suggestions.Change(KEY, BEFORE, AFTER, "conversation", "session", "Title (sess)")],
        suggested_by="desktop_bridge", scope="conversation", target="session",
        display_target="Title (sess)",
    )
    suggestion = setting_suggestions.read(tmp_path, request_id)
    assert suggestion is not None
    assert setting_suggestions.digest(suggestion) != setting_suggestions.digest(
        replace(suggestion, changes=(replace(suggestion.changes[0], display_target="Different title"),))
    )

def test_approval_rejects_invalid_and_missing_conversation_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    with TestClient(app) as client:
        for session_id in ["missing-conversation", "../invalid"]:
            made = client.post("/api/bridge/call", json={
                "route": "settings.suggest_model", "body": {
                    "model": AFTER, "scope": "conversation", "session_id": session_id,
                },
            }, headers=headers)
            assert made.status_code == 400, made.text


def test_approval_rejects_unregistered_and_traversal_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    with TestClient(app) as client:
        for project in [str(tmp_path / "unknown"), str(tmp_path / ".." / "outside")]:
            response = client.post("/api/bridge/call", json={
                "route": "settings.suggest_model", "body": {
                    "model": AFTER, "scope": "project", "project": project,
                },
            }, headers=headers)
            assert response.status_code == 400, response.text


def test_approval_rejects_credentials_in_suggested_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    with TestClient(app) as client:
        response = client.post("/api/bridge/call", json={
            "route": "settings.suggest_model", "body": {
                "model": "https://secret:password@example.com/model", "scope": "global",
            },
        }, headers=headers)
        assert response.status_code == 400, response.text


def test_global_scope_approval_updates_the_install_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    with TestClient(app) as client:
        made = client.post("/api/bridge/call", json={
            "route": "settings.suggest_model", "body": {"model": AFTER, "scope": "global"},
        }, headers=headers)
        assert made.status_code == 200, made.text
        request_id = str(made.json()["data"]["suggestion"])
        assert _yes(client, tmp_path, request_id).json()["outcome"] == "applied"
        assert os.environ[KEY] == AFTER


def test_a_write_that_fails_says_so_and_the_card_waits_for_another_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api import config_api

    app = _app(tmp_path, monkeypatch)
    real_write = config_api._write_env_var
    attempts: list[str] = []

    def locked_once(path: Path, key: str, value: str) -> None:
        attempts.append(key)
        if len(attempts) == 1:
            # What Windows answers while another process holds the file open without sharing it.
            raise PermissionError(13, "The process cannot access the file", str(path))
        real_write(path, key, value)

    monkeypatch.setattr(config_api, "_write_env_var", locked_once)
    with TestClient(app, raise_server_exceptions=False) as client:
        request_id = _suggest(client, app)
        first = _yes(client, tmp_path, request_id)
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["ok"] is True and body["outcome"] == "failed"
        # The reason, so the screen has something to say — and never the value.
        assert "PermissionError" in body["detail"] and AFTER not in body["detail"]
        assert f"{KEY}={AFTER}" not in _env_file(tmp_path)
        assert os.environ[KEY] == BEFORE
        # Still waiting, and nothing in the history claims it was answered.
        assert [q.id for q in pending(tmp_path / "home")] == [request_id]
        assert not any(row.get("id") == request_id for row in history(tmp_path / "home"))

        second = _yes(client, tmp_path, request_id)
    assert second.json() == {"ok": True, "outcome": "applied", "detail": KEY}
    assert os.environ[KEY] == AFTER
    assert pending(tmp_path / "home") == []
    get_settings.cache_clear()


def test_a_reading_of_the_current_value_that_fails_says_so_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The comparison against the value the card showed reads the live settings; a read that raises
    is the same unseen 500 one step earlier."""
    home = tmp_path / "home"
    request_id = setting_suggestions.suggest(
        home, [setting_suggestions.Change(KEY, BEFORE, AFTER)], suggested_by="desktop_bridge"
    )
    s = setting_suggestions.read(home, request_id)
    assert s is not None

    def unreadable(key: str, scope: str, target: str) -> str:
        raise OSError(5, "Access is denied")

    written: list[dict[str, str]] = []
    outcome, detail = setting_suggestions.resolve(
        home,
        request_id,
        True,
        via="app",
        current_of=unreadable,
        check=lambda updates, scope, target: None,
        apply=lambda updates, scope, target: written.append(updates),
        allowed=lambda key, scope, target: True,
        shown=setting_suggestions.digest(s),
    )
    assert outcome == "failed" and "OSError" in detail
    assert written == []
    assert setting_suggestions.is_suggestion(home, request_id)


# The history recorded every approved card as `approved`, including the ones that wrote nothing
# because the setting had moved (`stale`) or was no longer suggestable (`invalid`): it could not
# tell "approved and applied" from "approved and not applied" (noted in #852). `outcome` keeps the
# shared vocabulary; `result` says what the yes led to.
def test_the_history_says_an_approved_suggestion_was_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        request_id = _suggest(client, app)
        assert _yes(client, tmp_path, request_id).status_code == 200
    row = [r for r in history(tmp_path / "home") if r["id"] == request_id][-1]
    assert row["outcome"] == "approved" and row["result"] == "applied"


def test_the_history_says_an_approved_suggestion_that_went_stale_wrote_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        request_id = _suggest(client, app)
        # Someone changed the setting after the card was raised: the yes was given to a change
        # from a value that is no longer there.
        monkeypatch.setenv(KEY, "openrouter/vendor/someone-else")
        get_settings.cache_clear()
        _yes(client, tmp_path, request_id)
    row = [r for r in history(tmp_path / "home") if r["id"] == request_id][-1]
    assert row["outcome"] == "approved" and row["result"] == "stale"


def test_a_refused_suggestion_carries_no_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        request_id = _suggest(client, app)
        s = setting_suggestions.read(tmp_path / "home", request_id)
        assert s is not None
        client.post(
            f"/api/approvals/{request_id}",
            json={"approved": False, "digest": setting_suggestions.digest(s)},
        )
    row = [r for r in history(tmp_path / "home") if r["id"] == request_id][-1]
    assert row["outcome"] == "refused" and "result" not in row
