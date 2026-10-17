"""The project-wide worktree cleanup: what it lists, what it refuses, and that only the owner prunes.

Real git repositories in ``tmp_path``. Held here:

* every refusal in `worktree_prune.REFUSALS` has a test that names its reason AND checks the disk is
  intact — each one fails when its refusal is switched off (the sabotage run is in the PR body);
* a dry run leaves every byte of the repository, its worktrees and its remote as they were;
* a worktree Chimera did not make is listed with ``not_chimera_worktree`` and is never removed,
  whatever the request says — the owner's decision of 2026-10-17;
* removal never forces: no ``--force``, no ``-f``, no ``branch -D`` in any argv;
* a removed worktree's conversations are archived, never deleted;
* the desktop bridge lists the worktrees and cannot prune at any tier.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api.bridge_routes import OWNER_DECISION_ROUTES, ROUTES
from chimera.config import Settings, get_settings
from chimera.core import worktree as wt
from chimera.core.code_session import CodeSession, CodeSessionStore
from chimera.core.pull_request import run_quiet
from chimera.core.worktree_prune import (
    REFUSALS,
    WorktreeRow,
    list_worktrees,
    prune_worktrees,
)
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun

NO_AGENT = cast(Any, None)


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=T", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return done.stdout.strip()


class Repo:
    """A repository with a bare remote, a folder Chimera makes worktrees in, and helpers."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.main = base / "proj"
        self.remote = base / "remote.git"
        self.roots = base / "wts"
        self.roots.mkdir(parents=True)
        self.main.mkdir()
        git(base, "init", "--bare", "-b", "main", str(self.remote))
        git(self.main, "init", "-b", "main")
        (self.main / "a.txt").write_text("one\n", encoding="utf-8")
        git(self.main, "add", "a.txt")
        git(self.main, "commit", "-m", "first")
        git(self.main, "remote", "add", "origin", str(self.remote))
        git(self.main, "push", "-u", "origin", "main")
        git(self.main, "remote", "set-head", "origin", "main")

    def owned(self, name: str, branch: str | None = None) -> Path:
        """A worktree as `GitWorktree.create` leaves it: prefixed, in the worktree folder, with the
        owner record — made by this process, and no run of this process working in it."""
        path = self.roots / f"{wt.WORKTREE_PREFIX}{name}"
        git(self.main, "worktree", "add", "-b", branch or f"chimera/attempt-{name}", str(path))
        wt._write_owner(path)
        return path

    def external(self, path: Path, branch: str) -> Path:
        git(self.main, "worktree", "add", "-b", branch, str(path))
        return path


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


def rows(repo: Repo, **kw: Any) -> dict[str, WorktreeRow]:
    found = list_worktrees(repo.main, roots=[repo.roots], **kw)
    assert found is not None
    return {os.path.normcase(str(Path(r.path).resolve())): r for r in found[1]}


def row_of(repo: Repo, path: Path, **kw: Any) -> WorktreeRow:
    return rows(repo, **kw)[os.path.normcase(str(path.resolve()))]


def prune(repo: Repo, paths: Sequence[Path], *, dry_run: bool, **kw: Any) -> list[Any]:
    out = prune_worktrees(
        repo.main, [str(p) for p in paths], dry_run=dry_run, roots=[repo.roots], **kw
    )
    assert out is not None
    return out


def snapshot(root: Path) -> str:
    """Every path and every byte under ``root``, as one digest."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def assert_kept(repo: Repo, path: Path, reason: str, **kw: Any) -> None:
    """Listed with ``reason``, and a REAL prune leaves the folder and its branch."""
    row = row_of(repo, path, **kw)
    assert row.kept == reason and not row.removable
    branch = row.branch
    result = prune(repo, [path], dry_run=False, **kw)
    assert [r.kept for r in result] == [reason]
    assert not result[0].removed and not result[0].would_remove
    assert path.is_dir()
    if branch:
        assert git(repo.main, "branch", "--list", branch)


# ---- the happy path, and that it never forces ----------------------------------------------------


def test_a_finished_chimera_worktree_is_removable_and_goes_without_force(repo: Repo) -> None:
    path = repo.owned("done")
    row = row_of(repo, path)
    assert row.owned and row.merged and row.clean and row.removable and row.kept == ""

    argvs: list[list[str]] = []

    def spy(argv: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        argvs.append(list(argv))
        return run_quiet(argv, cwd)

    result = prune(repo, [path], dry_run=False, runner=spy)
    assert result[0].removed and result[0].branch_deleted and result[0].kept == ""
    assert not path.exists()
    assert git(repo.main, "branch", "--list", "chimera/attempt-done") == ""
    assert argvs[-1][-2:] == ["worktree", "prune"]
    for argv in argvs:
        assert "--force" not in argv and "-f" not in argv and "-D" not in argv
    assert any(a[-3:] == ["branch", "-d", "chimera/attempt-done"] for a in argvs)


def test_a_dry_run_never_touches_the_disk(repo: Repo) -> None:
    removable = repo.owned("dry")
    kept = repo.owned("dirty")
    (kept / "new.txt").write_text("x", encoding="utf-8")
    before = snapshot(repo.base)

    result = prune(repo, [removable, kept, repo.main], dry_run=True)
    listed = rows(repo)

    assert [(r.would_remove, r.removed, r.kept) for r in result] == [
        (True, False, ""),
        (False, False, "dirty"),
        (False, False, "main_checkout"),
    ]
    assert len(listed) == 3
    assert snapshot(repo.base) == before


def test_dry_run_is_the_default(repo: Repo) -> None:
    path = repo.owned("default")
    out = prune_worktrees(repo.main, [str(path)], roots=[repo.roots])
    assert out is not None and out[0].would_remove and not out[0].removed
    assert path.is_dir()


def test_a_pushed_but_unmerged_branch_may_go(repo: Repo) -> None:
    path = repo.owned("pushed")
    (path / "b.txt").write_text("b", encoding="utf-8")
    git(path, "add", "b.txt")
    git(path, "commit", "-m", "b")
    git(path, "push", "-u", "origin", "chimera/attempt-pushed")
    row = row_of(repo, path)
    assert not row.merged and row.upstream and row.ahead == 0 and row.removable


# ---- each refusal: named, and the disk intact ----------------------------------------------------


def test_the_main_checkout_is_never_removed(repo: Repo) -> None:
    assert_kept(repo, repo.main, "main_checkout")
    assert (repo.main / ".git").is_dir()


def test_a_folder_already_gone_is_reported_not_acted_on(repo: Repo) -> None:
    path = repo.owned("gone")
    shutil.rmtree(path)
    row = row_of(repo, path)
    assert row.kept == "missing"
    result = prune(repo, [path], dry_run=False)
    assert result[0].kept == "missing" and not result[0].removed
    assert git(repo.main, "branch", "--list", "chimera/attempt-gone")


def test_a_worktree_made_outside_chimera_is_listed_and_never_removed(repo: Repo) -> None:
    """The owner's decision: the interface does not remove what Chimera did not make. Three ways a
    folder fails the ownership record, each clean and merged — removable in every other respect."""
    by_hand = repo.external(repo.base / "w-by-hand", "w-by-hand")
    prefixed_no_record = repo.external(repo.roots / f"{wt.WORKTREE_PREFIX}fake", "fake")
    elsewhere = repo.base / "other"
    elsewhere.mkdir()
    recorded_elsewhere = repo.external(elsewhere / f"{wt.WORKTREE_PREFIX}far", "far")
    wt._write_owner(recorded_elsewhere)
    for path in (by_hand, prefixed_no_record, recorded_elsewhere):
        row = row_of(repo, path)
        assert not row.owned and row.merged and row.clean
        assert_kept(repo, path, "not_chimera_worktree")


def test_a_locked_worktree_is_kept(repo: Repo) -> None:
    path = repo.owned("locked")
    git(repo.main, "worktree", "lock", str(path))
    assert_kept(repo, path, "locked")


def test_a_worktree_in_use_is_kept(repo: Repo) -> None:
    path = repo.owned("busy")
    assert_kept(repo, path, "in_use", busy=lambda p: p.resolve() == path.resolve())


def test_a_worktree_a_run_of_this_process_works_in_is_kept(repo: Repo) -> None:
    path = repo.owned("live")
    wt._live_here.add(path.resolve())
    try:
        assert_kept(repo, path, "in_use")
    finally:
        wt._live_here.discard(path.resolve())


def test_a_worktree_whose_maker_still_runs_is_kept(repo: Repo) -> None:
    path = repo.owned("parent")
    admin = wt._admin_dir(path)
    assert admin is not None
    import json

    record = {"pid": os.getppid(), "created": 0.0, **wt.process_identity(os.getppid())}
    (admin / wt.OWNER_FILE).write_text(json.dumps(record), encoding="utf-8")
    assert_kept(repo, path, "in_use")


def test_a_detached_worktree_is_kept(repo: Repo) -> None:
    path = repo.owned("detached")
    git(path, "checkout", "--detach")
    assert_kept(repo, path, "detached")


def test_the_branch_the_main_checkout_has_out_is_kept(repo: Repo) -> None:
    path = repo.owned("shared", branch="shared")
    git(repo.main, "checkout", "--ignore-other-worktrees", "shared")
    assert_kept(repo, path, "current_branch")


def test_a_worktree_with_tracked_changes_is_kept(repo: Repo) -> None:
    path = repo.owned("edited")
    (path / "a.txt").write_text("changed\n", encoding="utf-8")
    row = row_of(repo, path)
    assert not row.clean
    assert_kept(repo, path, "dirty")
    assert (path / "a.txt").read_text(encoding="utf-8") == "changed\n"


def test_untracked_files_are_listed_and_keep_the_worktree(repo: Repo) -> None:
    path = repo.owned("untracked")
    (path / "notes.txt").write_text("mine", encoding="utf-8")
    row = row_of(repo, path)
    assert row.clean and row.untracked == ("notes.txt",)
    assert_kept(repo, path, "dirty")
    assert (path / "notes.txt").read_text(encoding="utf-8") == "mine"


def test_unmerged_work_with_no_upstream_is_kept(repo: Repo) -> None:
    path = repo.owned("local")
    (path / "c.txt").write_text("c", encoding="utf-8")
    git(path, "add", "c.txt")
    git(path, "commit", "-m", "c")
    assert_kept(repo, path, "no_upstream")


def test_unmerged_commits_not_pushed_are_kept(repo: Repo) -> None:
    path = repo.owned("ahead")
    git(path, "push", "-u", "origin", "chimera/attempt-ahead")
    (path / "d.txt").write_text("d", encoding="utf-8")
    git(path, "add", "d.txt")
    git(path, "commit", "-m", "d")
    assert row_of(repo, path).ahead == 1
    assert_kept(repo, path, "unpushed")


def test_a_path_outside_the_repositorys_worktrees_is_refused(repo: Repo, tmp_path: Path) -> None:
    stranger = tmp_path / "stranger"
    stranger.mkdir()
    (stranger / "keep.txt").write_text("k", encoding="utf-8")
    result = prune(repo, [stranger], dry_run=False)
    assert result[0].kept == "not_registered" and not result[0].removed
    assert (stranger / "keep.txt").is_file()


def test_every_refusal_has_a_test_above() -> None:
    """Adding a refusal without a test for it fails here; the sabotage run reads this list."""
    assert [name for name, _ in REFUSALS] == [
        "main_checkout",
        "missing",
        "external",
        "locked",
        "in_use",
        "detached",
        "current_branch",
        "dirty",
        "unpushed",
    ]


# ---- the routes and the bridge -------------------------------------------------------------------


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Repo) -> tuple[TestClient, Path]:
    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_WORKTREE_DIR", str(repo.roots))
    get_settings.cache_clear()
    settings = Settings(_env_file=None, CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    app = build_api_app(
        lambda: ChatSession(cast(SupportsRun, None)), workspace=repo.main, settings=settings
    )
    return TestClient(app), home


def _conversation(home: Path, workspace: Path, sid: str) -> CodeSessionStore:
    store = CodeSessionStore(home / "code_sessions")
    store.save(
        CodeSession(
            NO_AGENT,
            session_id=sid,
            workspace=str(workspace),
            messages=[{"role": "user", "content": "hi"}],
        )
    )
    return store


def test_the_route_lists_conversations_and_archives_them_never_deletes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Repo
) -> None:
    path = repo.owned("conv")
    client, home = _app(tmp_path, monkeypatch, repo)
    store = _conversation(home, path, "c1")
    with client:
        listed = client.get("/api/code/projects/worktrees", params={"project": str(repo.main)})
        assert listed.status_code == 200, listed.text
        body = listed.json()
        row = next(r for r in body["worktrees"] if Path(r["path"]).resolve() == path.resolve())
        assert row["removable"] and row["owned"] and row["kept"] == ""
        assert [c["id"] for c in row["conversations"]] == ["c1"]

        preview = client.post(
            "/api/code/projects/worktrees/prune",
            json={"project": str(repo.main), "paths": [str(path)]},
        ).json()
        assert preview["dry_run"] is True and preview["results"][0]["would_remove"] is True
        assert path.is_dir() and store.marks.get("c1").archived_at is None

        done = client.post(
            "/api/code/projects/worktrees/prune",
            json={"project": str(repo.main), "paths": [str(path)], "dry_run": False},
        ).json()
    result = done["results"][0]
    assert result["removed"] and result["branch_deleted"] and result["archived"] == ["c1"]
    assert not path.exists()
    assert (home / "code_sessions" / "c1.json").is_file()
    assert store.marks.get("c1").archived_at is not None
    get_settings.cache_clear()


def test_the_route_keeps_a_worktree_whose_conversation_has_a_message_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Repo
) -> None:
    from chimera.api.code_session_queue import CodeTurnQueue

    path = repo.owned("queued")
    client, home = _app(tmp_path, monkeypatch, repo)
    _conversation(home, path, "q1")
    with client:
        CodeTurnQueue(home / "code_sessions").enqueue("q1", "client-1", {"message": "later"})
        done = client.post(
            "/api/code/projects/worktrees/prune",
            json={"project": str(repo.main), "paths": [str(path)], "dry_run": False},
        ).json()
    assert done["results"][0]["kept"] == "in_use"
    assert path.is_dir()
    get_settings.cache_clear()


def test_the_route_never_removes_an_external_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Repo
) -> None:
    path = repo.external(repo.base / "w-mine", "w-mine")
    client, _ = _app(tmp_path, monkeypatch, repo)
    with client:
        done = client.post(
            "/api/code/projects/worktrees/prune",
            json={"project": str(repo.main), "paths": [str(path)], "dry_run": False},
        ).json()
    assert done["results"][0]["kept"] == "not_chimera_worktree"
    assert path.is_dir() and git(repo.main, "branch", "--list", "w-mine")
    get_settings.cache_clear()


def test_a_folder_that_is_not_a_repository_is_a_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Repo
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    client, _ = _app(tmp_path, monkeypatch, repo)
    with client:
        assert client.get(
            "/api/code/projects/worktrees", params={"project": str(plain)}
        ).status_code == 400
        assert client.get(
            "/api/code/projects/worktrees", params={"project": "relative"}
        ).status_code == 400
    get_settings.cache_clear()


def test_the_bridge_lists_at_operate_and_cannot_prune_at_any_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Repo
) -> None:
    assert ROUTES["projects.worktrees"].tier == "operate"
    assert ROUTES["projects.worktrees"].method == "GET"
    assert "projects.worktrees_prune" in OWNER_DECISION_ROUTES
    assert not any(r.path.endswith("/worktrees/prune") for r in ROUTES.values())

    path = repo.owned("bridge")
    for full in (False, True):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("CHIMERA_WORKTREE_DIR", str(repo.roots))
        monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
        monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
        monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "true" if full else "false")
        get_settings.cache_clear()
        app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
        app.state.desktop_bridge.attach("http://127.0.0.1:65011")
        headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
        with TestClient(app) as client:
            listed = client.post(
                "/api/bridge/call",
                json={"route": "projects.worktrees", "params": {"project": str(repo.main)}},
                headers=headers,
            )
            refused = client.post(
                "/api/bridge/call",
                json={
                    "route": "projects.worktrees_prune",
                    "body": {"project": str(repo.main), "paths": [str(path)], "dry_run": False},
                },
                headers=headers,
            )
        assert listed.status_code == 200, listed.text
        assert any(
            Path(r["path"]).resolve() == path.resolve() for r in listed.json()["data"]["worktrees"]
        )
        assert refused.status_code == 403
        assert "owner's decision" in refused.json()["detail"]
        assert path.is_dir()
    get_settings.cache_clear()
