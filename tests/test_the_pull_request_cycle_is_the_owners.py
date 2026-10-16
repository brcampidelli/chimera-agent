"""The PR a conversation opened is kept on it, its options are the owner's, and the bridge only reads.

C1 (study of the Claude desktop app, 2026-10-16). The Git panel opened a pull request and forgot it
the moment the card re-rendered: the number lived in React state. It is now recorded on the Code
conversation it was opened from (`CodeSessionStore.record_pull_request`), and two options hang off
it, both OFF by default: offer a ready-made "fix the CI" turn, and clean up after the merge.

Held here:

* the record survives a reload, an older file without it reads as "no PR", and a turn that saves its
  transcript cannot erase it (the turn holds the copy it loaded before the PR existed);
* a different PR number resets the options — consent for one PR is not consent for the next;
* opening a PR through the API records it only on an existing conversation of the same folder;
* the options and the cleanup are owner routes: the desktop bridge's table carries the GET and
  nothing that writes, and a bridge call naming them is refused;
* the cleanup route refuses without the option, before the merge, and archives only when the
  worktree and the local branch actually went.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api.bridge_routes import ROUTES
from chimera.config import Settings, get_settings
from chimera.core.code_session import CodeSession, CodeSessionStore
from chimera.core.pull_request_cleanup import DIRTY, Cleanup
from chimera.core.pull_request_status import PullRequestStatus
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun

NO_AGENT = cast(Any, None)
URL = "https://github.com/o/r/pull/42"


def _conversation(store: CodeSessionStore, workspace: str, sid: str = "abc123") -> CodeSession:
    session = CodeSession(
        NO_AGENT, session_id=sid, workspace=workspace, messages=[{"role": "user", "content": "hi"}]
    )
    store.save(session)
    return session


# ---- the record ---------------------------------------------------------------------------------


def test_the_record_survives_a_reload_and_an_old_file_reads_as_no_pr(tmp_path: Path) -> None:
    store = CodeSessionStore(tmp_path / "code_sessions")
    _conversation(store, "/p", "old")
    assert store.pull_request_of("old") is None
    assert store.load("old", NO_AGENT).pull_request is None

    assert store.record_pull_request("old", number=42, url=URL)
    reread = CodeSessionStore(tmp_path / "code_sessions")
    expected = {"number": 42, "url": URL, "fix_on_ci_failure": False, "cleanup_on_merge": False}
    assert reread.pull_request_of("old") == expected
    assert reread.load("old", NO_AGENT).pull_request == expected


def test_a_turn_saving_its_transcript_cannot_erase_the_record(tmp_path: Path) -> None:
    store = CodeSessionStore(tmp_path / "code_sessions")
    stale = _conversation(store, "/p")  # what a running turn loaded before the PR was opened
    assert store.record_pull_request("abc123", number=42, url=URL)
    store.set_pull_request_options("abc123", {"cleanup_on_merge": True})

    stale.messages.append({"role": "assistant", "content": "done"})
    store.save(stale)

    assert store.pull_request_of("abc123") == {
        "number": 42, "url": URL, "fix_on_ci_failure": False, "cleanup_on_merge": True,
    }
    assert len(store.load("abc123", NO_AGENT).messages) == 2  # and the turn's exchange is kept too


def test_refresh_brings_the_record_into_a_session_object(tmp_path: Path) -> None:
    store = CodeSessionStore(tmp_path / "code_sessions")
    live = _conversation(store, "/p")
    store.record_pull_request("abc123", number=7, url="https://github.com/o/r/pull/7")
    store.refresh(live)
    assert live.pull_request is not None and live.pull_request["number"] == 7


def test_no_record_is_written_for_a_conversation_that_does_not_exist(tmp_path: Path) -> None:
    store = CodeSessionStore(tmp_path / "code_sessions")
    assert not store.record_pull_request("ghost", number=1, url=URL)
    assert not (tmp_path / "code_sessions" / "ghost.json").exists()
    assert store.set_pull_request_options("ghost", {"cleanup_on_merge": True}) is None


def test_a_new_pr_number_turns_the_options_back_off(tmp_path: Path) -> None:
    store = CodeSessionStore(tmp_path / "code_sessions")
    _conversation(store, "/p")
    store.record_pull_request("abc123", number=42, url=URL)
    store.set_pull_request_options("abc123", {"fix_on_ci_failure": True, "cleanup_on_merge": True})

    store.record_pull_request("abc123", number=42, url=URL)  # the same PR again: options kept
    kept = store.pull_request_of("abc123")
    assert kept is not None and kept["cleanup_on_merge"] and kept["fix_on_ci_failure"]

    store.record_pull_request("abc123", number=43, url="https://github.com/o/r/pull/43")
    fresh = store.pull_request_of("abc123")
    assert fresh is not None and not fresh["cleanup_on_merge"] and not fresh["fix_on_ci_failure"]


def test_options_store_only_known_switches_and_only_a_true_turns_one_on(tmp_path: Path) -> None:
    store = CodeSessionStore(tmp_path / "code_sessions")
    _conversation(store, "/p")
    assert store.set_pull_request_options("abc123", {"cleanup_on_merge": True}) is None  # no PR yet
    store.record_pull_request("abc123", number=42, url=URL)
    record = store.set_pull_request_options(
        "abc123", cast(dict[str, bool], {"cleanup_on_merge": "yes", "evil": True})
    )
    assert record == {"number": 42, "url": URL, "fix_on_ci_failure": False, "cleanup_on_merge": False}


def test_a_fork_does_not_inherit_the_pr(tmp_path: Path) -> None:
    store = CodeSessionStore(tmp_path / "code_sessions")
    _conversation(store, "/p")
    store.record_pull_request("abc123", number=42, url=URL)
    forked = store.fork("abc123")
    assert forked is not None and store.pull_request_of(forked) is None


# ---- the routes ---------------------------------------------------------------------------------


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path, Path]:
    home = tmp_path / "home"
    folder = tmp_path / "proj"
    folder.mkdir()
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    settings = Settings(_env_file=None, CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    app = build_api_app(
        lambda: ChatSession(cast(SupportsRun, None)), workspace=folder, settings=settings
    )
    return TestClient(app), home, folder


HEAD = "a" * 40


def _open_pr(client: TestClient, folder: Path, session_id: str) -> Any:
    return client.post(
        "/api/git/pull-request",
        json={"workspace": str(folder), "title": "t", "head": HEAD, "session_id": session_id},
    )


def test_opening_a_pr_records_it_on_its_conversation_and_the_card_reads_it_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.core.pull_request as pr

    client, home, folder = _app(tmp_path, monkeypatch)
    _conversation(CodeSessionStore(home / "code_sessions"), str(folder))
    monkeypatch.setattr(pr, "open_pull_request", lambda *a, **k: {"ok": True, "url": URL, "output": "", "error": ""})

    assert _open_pr(client, folder, "abc123").json()["ok"] is True
    read = client.get("/api/code/sessions/abc123/pull-request").json()
    assert read == {"number": 42, "url": URL, "fix_on_ci_failure": False, "cleanup_on_merge": False}

    switched = client.patch(
        "/api/code/sessions/abc123/pull-request", json={"fix_on_ci_failure": True}
    ).json()
    assert switched["fix_on_ci_failure"] is True and switched["cleanup_on_merge"] is False


def test_a_pr_from_another_folder_is_not_recorded_on_the_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.core.pull_request as pr

    client, home, folder = _app(tmp_path, monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    _conversation(CodeSessionStore(home / "code_sessions"), str(other))
    monkeypatch.setattr(pr, "open_pull_request", lambda *a, **k: {"ok": True, "url": URL, "output": "", "error": ""})

    assert _open_pr(client, folder, "abc123").json()["ok"] is True
    assert client.get("/api/code/sessions/abc123/pull-request").json()["number"] is None


def test_options_need_a_recorded_pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, home, folder = _app(tmp_path, monkeypatch)
    _conversation(CodeSessionStore(home / "code_sessions"), str(folder))
    answer = client.patch("/api/code/sessions/abc123/pull-request", json={"cleanup_on_merge": True})
    assert answer.status_code == 409


def test_the_cleanup_refuses_without_the_option_and_before_the_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.core.pull_request_cleanup as cleanup
    import chimera.core.pull_request_status as status

    client, home, folder = _app(tmp_path, monkeypatch)
    store = CodeSessionStore(home / "code_sessions")
    _conversation(store, str(folder))
    removed: list[Path] = []
    monkeypatch.setattr(
        cleanup, "remove_merged_worktree", lambda p, **k: removed.append(p) or Cleanup(True)
    )
    monkeypatch.setattr(status, "pull_request_status", lambda *a, **k: PullRequestStatus("open"))

    assert client.post("/api/code/sessions/abc123/pull-request/cleanup").status_code == 409  # no PR
    store.record_pull_request("abc123", number=42, url=URL)
    assert client.post("/api/code/sessions/abc123/pull-request/cleanup").status_code == 409  # off
    store.set_pull_request_options("abc123", {"cleanup_on_merge": True})
    assert client.post("/api/code/sessions/abc123/pull-request/cleanup").status_code == 409  # open
    assert removed == []
    assert store.marks.get("abc123").archived_at is None


def test_the_cleanup_archives_only_when_the_worktree_and_branch_went(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.core.pull_request_cleanup as cleanup
    import chimera.core.pull_request_status as status

    client, home, folder = _app(tmp_path, monkeypatch)
    store = CodeSessionStore(home / "code_sessions")
    _conversation(store, str(folder))
    store.record_pull_request("abc123", number=42, url=URL)
    store.set_pull_request_options("abc123", {"cleanup_on_merge": True})
    monkeypatch.setattr(status, "pull_request_status", lambda *a, **k: PullRequestStatus("merged"))

    monkeypatch.setattr(cleanup, "remove_merged_worktree", lambda p, **k: Cleanup(False, DIRTY))
    kept = client.post("/api/code/sessions/abc123/pull-request/cleanup").json()
    assert kept == {"archived": False, "removed_worktree": False, "deleted_branch": False, "kept": DIRTY}
    assert store.marks.get("abc123").archived_at is None

    monkeypatch.setattr(cleanup, "remove_merged_worktree", lambda p, **k: Cleanup(True))
    done = client.post("/api/code/sessions/abc123/pull-request/cleanup").json()
    assert done == {"archived": True, "removed_worktree": True, "deleted_branch": True, "kept": ""}
    assert store.marks.get("abc123").archived_at is not None


# ---- the bridge reaches none of it ---------------------------------------------------------------
#
# `tests/test_a_pull_request_is_asked_every_time.py` already holds that no bridge route touches a
# pull request at all, and that line is kept rather than narrowed to "GET only": even the status read
# runs gh with the OWNER's GitHub credentials, so a bridge able to name any workspace and PR number
# could read the owner's private repositories through them. Reading is the bridge's job for the app's
# own data, not for a third party's API under the owner's account.

ROUTES_HERE = {
    ("GET", "/api/code/sessions/{session_id}/pull-request"),
    ("PATCH", "/api/code/sessions/{session_id}/pull-request"),
    ("POST", "/api/code/sessions/{session_id}/pull-request/cleanup"),
    ("GET", "/api/git/pull-request/status"),
}


def test_no_route_of_the_pr_cycle_is_in_the_bridge_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = _app(tmp_path, monkeypatch)
    served = {(m, r.path) for r in client.app.routes for m in (getattr(r, "methods", None) or ())}  # type: ignore[attr-defined]
    assert served >= ROUTES_HERE, "the routes moved: this test no longer guards them"

    in_table = {(r.method, r.path) for r in ROUTES.values()}
    assert not (ROUTES_HERE & in_table)
    assert not any("pull" in rid or "pull-request" in r.path for rid, r in ROUTES.items())


def test_a_bridge_call_cannot_reach_the_options_or_the_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At full control too: no route id reaches them, and nothing about the PR changes."""
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "true")
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    app.state.desktop_bridge.attach("http://127.0.0.1:65004")
    store = CodeSessionStore(tmp_path / "home" / "code_sessions")
    _conversation(store, str(tmp_path))
    store.record_pull_request("abc123", number=42, url=URL)
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}

    with TestClient(app) as client:
        for name in (
            "conversations.pull_request",
            "conversations.pull_request_options",
            "conversations.pull_request_cleanup",
            "git.pull_request_status",
        ):
            answer = client.post(
                "/api/bridge/call",
                json={"route": name, "params": {"session_id": "abc123"},
                      "body": {"cleanup_on_merge": True}},
                headers=headers,
            )
            assert answer.status_code == 404, answer.text
    record = store.pull_request_of("abc123")
    assert record is not None and record["cleanup_on_merge"] is False
    assert store.marks.get("abc123").archived_at is None
