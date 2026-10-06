"""A turn that continues a conversation runs in that conversation's folder, not the server's default.

Measured 2026-10-06 through the desktop bridge: the first turn of a conversation was sent with a
workspace, the second with only its session id. The second ran in `<app data>/workspace`, where
none of the first turn's files existed, and the agent went looking for them up the app's data
folder. A continued turn now runs where the conversation lives — and through the bridge that stored
folder is held to the same fence as a named one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chimera.core.code_session import CodeSessionStore
from tests.test_the_bridge_never_hands_a_run_chimeras_env_or_data import _desktop
from tests.test_the_desktop_bridge_reaches_only_its_table import _call


def _store_session(home: Path, session_id: str, workspace: str) -> None:
    folder = home / "code_sessions"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{session_id}.json").write_text(
        json.dumps({"session_id": session_id, "workspace": workspace, "messages": []}),
        encoding="utf-8",
    )


def test_the_store_reads_back_the_folder_a_conversation_belongs_to(tmp_path: Path) -> None:
    _store_session(tmp_path, "abc", "C:/projects/x")
    store = CodeSessionStore(tmp_path / "code_sessions")
    assert store.stored_workspace("abc") == "C:/projects/x"
    assert store.stored_workspace("missing") == ""
    (tmp_path / "code_sessions" / "bad.json").write_text("{not json", encoding="utf-8")
    assert store.stored_workspace("bad") == ""


def test_a_continued_turn_uses_the_stored_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proved through the validation that runs before anything launches: a conversation whose
    stored folder is gone is refused as `workspace not found` — under the old code the turn would
    have run in the default folder instead, and no error would have said so."""
    app = _desktop(tmp_path, monkeypatch)
    _store_session(tmp_path / "appdata" / "data", "s1", str(tmp_path / "gone"))
    with TestClient(app) as client:
        got = client.post("/api/code/turn", json={"message": "continue", "session_id": "s1"})
    assert got.status_code == 400
    assert "workspace not found" in got.text


def test_the_bridge_holds_a_stored_folder_to_the_same_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conversation that lives in the install folder (where Chimera's `.env` is) cannot be driven
    from the bridge by its id alone: the stored folder is checked as if it had been named."""
    app = _desktop(tmp_path, monkeypatch)
    _store_session(tmp_path / "appdata" / "data", "s2", str(tmp_path / "install"))
    with TestClient(app) as client:
        got = _call(
            client, app, "conversations.send", body={"message": "hi", "session_id": "s2"},
            wait_seconds=0,
        )
    assert got.status_code == 403, got.text
    assert "own data or its .env" in got.json()["detail"]
