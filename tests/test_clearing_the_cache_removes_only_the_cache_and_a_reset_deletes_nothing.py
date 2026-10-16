"""The troubleshooting actions (C7) touch only what they name.

"Clear cache" removes ``<home>/cache`` and nothing beside it: a fake home full of conversations,
memory, history, approvals, a ``.env`` and a key file is compared byte for byte before and after.
A link at ``cache`` is removed as a link — following it is how a cache button empties a folder that
was never the app's.

"Reset" never deletes from the backend: the route only leaves the desktop shell the word
``reset_data``, and only when the owner typed the reset word exactly. The move itself is the shell's,
tested in Rust (`apps/desktop/src-tauri/src/shell_request.rs`).

And no request carries a path: the shell is asked for a folder by NAME, and a body that names a
path is refused before anything is written.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chimera.api.shell_requests import ACTIONS, REQUEST_ENV, RESULT_ENV
from chimera.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("CHIMERA_WORKTREE_DIR", "")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "browsers"))
    # Owned here, then removed, so a test that sets them does not leak into the next.
    for name in (REQUEST_ENV, RESULT_ENV):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def client_for(home: Path, tmp_path: Path) -> TestClient:
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")  # type: ignore[call-arg]
    return TestClient(build_api_app(lambda: None, workspace=ws, settings=settings))  # type: ignore[arg-type]


#: Everything a home holds that is NOT cache, with content worth losing.
PRECIOUS = {
    "sessions/conv-1.json": '{"messages": ["the conversation"]}',
    "code_sessions/c-1.json": '{"turns": 3}',
    "memory.json": '[{"fact": "the owner prefers PT-BR"}]',
    "approvals/history.jsonl": '{"allowed": "git push"}\n',
    "scheduler/jobs.json": "[]",
    ".env": "OPENROUTER_API_KEY=sk-or-v1-real\n",
    "keys/vault.json": '{"key": "secret"}',
    ".installation-id": "a" * 32 + "\n",
    "traces.jsonl": "{}\n",
}


def _fake_home(home: Path) -> None:
    """The precious files, plus a real SQLite history the app opens at startup (a fake one would
    stop it starting). Fingerprints are taken AFTER the app is built, so its own startup writes
    are not mistaken for the action's."""
    home.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(home / "history.db") as db:
        db.execute("CREATE TABLE owners_rows (v TEXT)")
        db.execute("INSERT INTO owners_rows VALUES ('a conversation turn')")
    for rel, text in PRECIOUS.items():
        path = home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (home / "cache").mkdir(parents=True)
    (home / "cache" / "completions.json").write_text('{"k": "' + "x" * 4000 + '"}', encoding="utf-8")


def _fingerprint(home: Path) -> dict[str, str]:
    return {
        str(p.relative_to(home)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(home.rglob("*"))
        if p.is_file() and "cache" not in p.relative_to(home).parts
    }


def test_clearing_the_cache_removes_only_the_cache(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _fake_home(home)
    client = client_for(home, tmp_path)
    before = _fingerprint(home)
    assert set(before) >= set(PRECIOUS) | {"history.db"}, "the probe must see every precious file"

    response = client.post("/api/storage/cache/clear", json={"confirm": True})

    assert response.status_code == 200
    body = response.json()
    assert body["removed"] == 1 and body["failed"] == 0
    assert body["bytes_freed"] > 4000
    assert not (home / "cache").exists()
    assert _fingerprint(home) == before, "something that is not cache changed"


@pytest.mark.parametrize("body", [{}, {"confirm": False}])
def test_a_cache_clear_without_confirm_removes_nothing(body: dict[str, bool], tmp_path: Path) -> None:
    home = tmp_path / "home"
    _fake_home(home)

    response = client_for(home, tmp_path).post("/api/storage/cache/clear", json=body)

    assert response.status_code == 400
    assert (home / "cache" / "completions.json").exists()


def _link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        pass
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return
    pytest.skip("this machine can make neither a symlink nor a junction")


def test_a_cache_that_is_a_link_is_unlinked_and_its_target_left_alone(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    elsewhere = tmp_path / "owners-documents"
    elsewhere.mkdir()
    (elsewhere / "thesis.docx").write_text("years of work", encoding="utf-8")
    _link(home / "cache", elsewhere)

    response = client_for(home, tmp_path).post("/api/storage/cache/clear", json={"confirm": True})

    assert response.status_code == 200
    assert not os.path.lexists(home / "cache")
    assert (elsewhere / "thesis.docx").read_text(encoding="utf-8") == "years of work"


def test_a_cleared_cache_asks_the_shell_to_restart_and_only_then(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _fake_home(home)
    client = client_for(home, tmp_path)

    # No shell: the cache still goes, and the answer does not promise a restart.
    alone = client.post("/api/storage/cache/clear", json={"confirm": True}).json()
    assert alone["restarting"] is False

    request_file = tmp_path / "app-data" / "shell-request.txt"
    monkeypatch.setenv(REQUEST_ENV, str(request_file))
    (home / "cache").mkdir()
    kept = client.post("/api/storage/cache/clear", json={"confirm": True, "restart": False}).json()
    assert kept["restarting"] is False and not request_file.exists()

    asked = client.post("/api/storage/cache/clear", json={"confirm": True}).json()
    assert asked["restarting"] is True
    assert request_file.read_text(encoding="ascii") == "restart\n"


def test_the_shell_is_asked_for_a_folder_by_name_never_by_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    request_file = tmp_path / "app-data" / "shell-request.txt"
    client = client_for(home, tmp_path)

    unavailable = client.post("/api/troubleshoot/open-folder", json={"folder": "logs"})
    assert unavailable.status_code == 409, "no shell: refused, not written into a file nobody reads"

    monkeypatch.setenv(REQUEST_ENV, str(request_file))
    for folder in ("C:\\Windows", "/etc", "../..", "cache", ""):
        refused = client.post("/api/troubleshoot/open-folder", json={"folder": folder})
        assert refused.status_code == 422, folder
        assert not request_file.exists(), f"{folder!r} reached the shell"

    smuggled = client.post(
        "/api/troubleshoot/open-folder", json={"folder": "logs", "path": "C:\\Users\\someone"}
    )
    assert smuggled.status_code == 200
    assert request_file.read_text(encoding="ascii") == "open_logs\n"

    client.post("/api/troubleshoot/open-folder", json={"folder": "data"})
    assert request_file.read_text(encoding="ascii") == "open_data\n"


@pytest.mark.parametrize("word", ["", "reset", "Reset", " RESET", "RESET please", "REDEFINIR"])
def test_a_reset_without_the_exact_word_asks_nothing(
    word: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _fake_home(home)
    request_file = tmp_path / "app-data" / "shell-request.txt"
    monkeypatch.setenv(REQUEST_ENV, str(request_file))

    response = client_for(home, tmp_path).post("/api/troubleshoot/reset", json={"word": word})

    assert response.status_code == 400
    assert not request_file.exists()


def test_a_reset_only_asks_the_shell_and_deletes_nothing_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _fake_home(home)
    request_file = tmp_path / "app-data" / "shell-request.txt"
    monkeypatch.setenv(REQUEST_ENV, str(request_file))
    client = client_for(home, tmp_path)
    before = _fingerprint(home)
    word = client.get("/api/troubleshoot").json()["reset_word"]

    response = client.post("/api/troubleshoot/reset", json={"word": word})

    assert response.status_code == 200 and response.json() == {"requested": True}
    assert request_file.read_text(encoding="ascii") == "reset_data\n"
    assert request_file.read_text(encoding="ascii").strip() in ACTIONS
    assert _fingerprint(home) == before and (home / "cache").exists(), "the backend moved or deleted"


def test_the_last_reset_is_read_back_from_the_shells_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    client = client_for(home, tmp_path)
    assert client.get("/api/troubleshoot").json() == {
        "available": False,
        "reset_word": "RESET",
        "last_reset": None,
    }

    result = tmp_path / "app-data" / "shell-request-result.txt"
    result.parent.mkdir()
    monkeypatch.setenv(REQUEST_ENV, str(tmp_path / "app-data" / "shell-request.txt"))
    monkeypatch.setenv(RESULT_ENV, str(result))
    backup = str(tmp_path / "app-data" / "data-backup-20261016-134507")
    result.write_text(f"moved {backup}\n1792158307\n", encoding="utf-8")

    body = client.get("/api/troubleshoot").json()
    assert body["available"] is True
    assert body["last_reset"] == {"moved": True, "detail": backup, "at": 1792158307}

    result.write_text("garbled", encoding="utf-8")
    assert client.get("/api/troubleshoot").json()["last_reset"] is None
