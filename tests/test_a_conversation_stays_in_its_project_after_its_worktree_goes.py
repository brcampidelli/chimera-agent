"""A Code conversation carries the repository and branch of its folder, and keeps them.

The point of stamping at the turn instead of deriving on every read (study 03, §3.1): an
orchestrator runs each task in a `git worktree add` folder and removes it when the task is merged.
After that the folder cannot say what it was, and a conversation that only knew its folder string
would fall out of its project at exactly the moment someone goes looking for it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from chimera.core.code_session import CodeSession, CodeSessionStore
from tests.conversation_fakes import Agent, App
from tests.test_a_folder_knows_which_repository_it_checks_out import git, make_repo, real

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs the git binary")


class _NoAgent:
    def run(self, task: str, **_: Any) -> Any:  # pragma: no cover - never called here
        raise AssertionError("listing must not need an agent")


def _repo_with_worktree(tmp_path: Path, name: str = "w-task") -> tuple[Path, Path]:
    repo = make_repo(tmp_path / "repo")
    wt = tmp_path / "wt" / name
    git(repo, "worktree", "add", "-q", "-b", f"feat/{name}", str(wt))
    return repo, wt


def test_the_stamp_survives_the_removal_of_the_worktree(tmp_path: Path) -> None:
    repo, wt = _repo_with_worktree(tmp_path)
    store = CodeSessionStore(tmp_path / "code_sessions")
    session = CodeSession(_NoAgent(), session_id="s", workspace=str(wt))
    session.stamp_checkout(wt)
    store.save(session)

    git(repo, "worktree", "remove", str(wt))
    assert not wt.exists()
    # The next turn's stamp reads a folder that is gone: it must keep the old answer, not erase it.
    later = store.load("s", _NoAgent())
    later.stamp_checkout(wt)
    store.save(later)

    (row,) = CodeSessionStore(tmp_path / "code_sessions").list_meta()  # a fresh process's view
    assert row["workspace"] == str(wt)
    assert row["project"] == real(repo)
    assert row["project_name"] == "repo"
    assert row["checkout_kind"] == "worktree"
    assert row["branch"] == "feat/w-task"


def test_a_later_turn_records_a_branch_switch(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    session = CodeSession(_NoAgent(), session_id="s", workspace=str(repo))
    session.stamp_checkout(repo)
    assert session.checkout is not None and session.checkout.branch == "trunk"
    git(repo, "checkout", "-q", "-b", "other")
    session.stamp_checkout(repo)
    assert session.checkout is not None and session.checkout.branch == "other"


def test_an_old_conversation_without_the_stamp_reads_as_before(tmp_path: Path) -> None:
    folder = tmp_path / "code_sessions"
    folder.mkdir()
    old = {
        "session_id": "old",
        "workspace": str(tmp_path / "removed-long-ago"),
        "messages": [{"role": "user", "content": "fix it"}],
    }
    (folder / "old.json").write_text(json.dumps(old), encoding="utf-8")
    store = CodeSessionStore(folder)

    (row,) = store.list_meta()
    assert row["title"] == "fix it" and row["workspace"] == old["workspace"]
    # Unknown, so a client groups by `workspace` — exactly how this row was listed before.
    assert (row["project"], row["project_name"], row["branch"], row["checkout_kind"]) == (
        "", "", None, None,
    )
    loaded = store.load("old", _NoAgent())
    assert loaded.checkout is None
    # Saving it back writes no stamp it never had.
    store.save(loaded)
    assert "checkout" not in json.loads((folder / "old.json").read_text(encoding="utf-8"))


def test_an_unstamped_conversation_whose_folder_still_exists_is_filed_under_its_repository(
    tmp_path: Path,
) -> None:
    repo, wt = _repo_with_worktree(tmp_path)
    folder = tmp_path / "code_sessions"
    folder.mkdir()
    (folder / "legacy.json").write_text(
        json.dumps({"session_id": "legacy", "workspace": str(wt), "messages": []}), encoding="utf-8"
    )
    (row,) = CodeSessionStore(folder).list_meta()
    assert row["project"] == real(repo) and row["checkout_kind"] == "worktree"
    # Derived on read only — the file itself is not rewritten by listing it.
    assert "checkout" not in json.loads((folder / "legacy.json").read_text(encoding="utf-8"))


def test_a_fork_stays_in_the_project_of_the_conversation_it_came_from(tmp_path: Path) -> None:
    repo, wt = _repo_with_worktree(tmp_path)
    store = CodeSessionStore(tmp_path / "code_sessions")
    session = CodeSession(_NoAgent(), session_id="s", workspace=str(wt))
    session.stamp_checkout(wt)
    store.save(session)
    git(repo, "worktree", "remove", str(wt))
    new_id = store.fork("s")
    assert new_id is not None
    row = next(r for r in store.list_meta() if r["id"] == new_id)
    assert row["project"] == real(repo)


def test_a_turn_stamps_its_conversation_and_the_list_shows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the real route: the turn stamps, the file keeps it, the API row exposes it."""
    app = App(tmp_path, monkeypatch, Agent)
    repo, wt = _repo_with_worktree(tmp_path / "git")
    app.folder = wt
    sid = app.turn("hello")

    row = app.rows()[sid]
    assert row["workspace"] == str(wt)
    assert row["project"] == real(repo)
    assert row["project_name"] == "repo"
    assert row["branch"] == "feat/w-task"
    assert row["checkout_kind"] == "worktree"
    stored = json.loads((app.home / "code_sessions" / f"{sid}.json").read_text("utf-8"))
    assert stored["checkout"]["project_root"] == real(repo)

    git(repo, "worktree", "remove", str(wt))
    app.client.close()
    again = App(tmp_path, monkeypatch, Agent)  # a restarted server, nothing remembered in memory
    assert again.rows()[sid]["project"] == real(repo)
    again.client.close()


def test_a_turn_in_the_servers_own_folder_gets_no_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"" is the list's "the server's own folder": a stamp would give that group a project."""
    app = App(tmp_path, monkeypatch, Agent)
    body = {"message": "hi", "stream": False}
    text = app.client.post("/api/code/turn", json=body).text
    sid = json.loads(text.split("data: ", 1)[1].splitlines()[0])["session_id"]
    row = app.rows()[sid]
    assert row["workspace"] == "" and row["project"] == "" and row["checkout_kind"] is None
    app.client.close()
