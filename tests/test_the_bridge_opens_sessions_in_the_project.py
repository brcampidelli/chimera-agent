"""The bridge opens a conversation IN A PROJECT, and Chimera makes and owns the worktree (study 03, A5).

An orchestrator that runs tasks in parallel used to make one ``git worktree add`` folder per task in
its own scratch directory and pass each as ``workspace``: every folder became a project on the
owner's sidebar, and the app could never clean them up, because it removes only what it made.

Held here, through the real route and real git repositories in ``tmp_path``:

* a project the desktop does not know is refused — registered (path or alias), or the root of a
  repository a conversation already ran in; a hidden one is refused too;
* ``worktree: "new"`` makes the worktree with Chimera's own `GitWorktree.create`: ``chimera-wt-``
  prefix, in ``CHIMERA_WORKTREE_DIR``, durable owner record; the conversation is stamped with the
  project's root and ``checkout_kind = worktree``, and the reply carries ``workspace`` and
  ``branch`` so the caller continues the same conversation;
* bad branch names, a run's ``…/attempt-…`` name, an existing branch, and a workspace outside the
  project are refused, and a refused call leaves no folder behind;
* a restart (the maker process gone, or its pid alive under another program) keeps the worktree
  Chimera's, the storage sweep never collects it, and the A6 cleanup lists and prunes it after merge;
* the bridge's place guard holds the project and the worktree folder to its rule.

Each guard has a test that fails with the guard switched off; the sabotage run is in the PR body.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.core import worktree as wt
from chimera.core.project_place import PlaceRefused, check_branch, derived_branch, slug
from chimera.core.worktree_prune import list_worktrees, prune_worktrees
from chimera.server.desktop_mcp import DesktopMCP
from tests.conversation_fakes import Agent, App
from tests.test_a_folder_knows_which_repository_it_checks_out import git, make_repo, real

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs the git binary")

URL = "http://127.0.0.1:65021"


class Setup:
    """An app with the bridge on, a repository, and Chimera's worktree folder outside it."""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        full: bool = False,
        configured: bool = True,
    ):
        Agent.hold.set()
        # With `configured`, CHIMERA_WORKTREE_DIR points here; without it, durable worktrees go to
        # the folder beside the repository and this one stays empty.
        self.roots = tmp_path / "wts"
        self.roots.mkdir()
        env = {"CHIMERA_WORKTREE_DIR": str(self.roots)} if configured else {}
        if not configured:
            monkeypatch.delenv("CHIMERA_WORKTREE_DIR", raising=False)
        self.app = App(
            tmp_path,
            monkeypatch,
            Agent,
            CHIMERA_DESKTOP_BRIDGE="true",
            CHIMERA_DESKTOP_BRIDGE_FULL="true" if full else "false",
            **env,
        )
        self.app.app.state.desktop_bridge.attach(URL)
        self.repo = make_repo(tmp_path / "repo")
        self.client = TestClient(self.app.app)
        self.client.__enter__()

    def close(self) -> None:
        self.client.__exit__(None, None, None)
        self.app.client.close()

    def register(self, path: Path | str, alias: str | None = None) -> None:
        body: dict[str, Any] = {"path": str(path)}
        if alias:
            body["alias"] = alias
        got = self.client.post("/api/code/workspaces", json=body)
        assert got.status_code == 200, got.text

    def send(self, **body: Any) -> dict[str, Any]:
        """``conversations.send`` through the bridge, waited to its end; the job record."""
        token = self.app.app.state.desktop_bridge.token
        got = self.client.post(
            "/api/bridge/call",
            json={"route": "conversations.send", "body": body, "wait_seconds": 30},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert got.status_code in (200, 403), got.text
        data: dict[str, Any] = got.json()
        return data

    def job(self, **body: Any) -> dict[str, Any]:
        data = self.send(**body)
        assert data.get("job") is not None, data
        job: dict[str, Any] = data["job"]
        return job

    def made(self) -> list[Path]:
        return sorted(p for p in self.roots.iterdir() if p.is_dir())


@pytest.fixture
def s(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    setup = Setup(tmp_path, monkeypatch)
    try:
        yield setup
    finally:
        setup.close()


def _key(path: Path | str) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _record(path: Path) -> dict[str, Any]:
    admin = wt._admin_dir(path)
    assert admin is not None
    data: dict[str, Any] = json.loads((admin / wt.OWNER_FILE).read_text(encoding="utf-8"))
    return data


def _dead_pid() -> int:
    """The pid of a process that has ended."""
    done = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True)
    return int(done.stdout.strip())


# ---- which project ---------------------------------------------------------------------------


def test_an_unknown_project_is_refused_with_a_sentence_and_nothing_runs(s: Setup) -> None:
    job = s.job(message="hi", project=str(s.repo))
    assert job["session_id"] == ""
    assert "unknown project" in job["error"], job["error"]
    assert job["error"].startswith("HTTP 400")
    assert s.app.rows() == {} and s.made() == []


def test_worktree_or_branch_without_a_project_is_refused(s: Setup) -> None:
    for body in ({"worktree": "new"}, {"branch": "feat/x"}):
        job = s.job(message="hi", **body)
        assert "need a project" in job["error"], job
    assert s.made() == []


def test_a_registered_project_alone_runs_in_its_main_checkout(s: Setup) -> None:
    s.register(s.repo)
    job = s.job(message="hi", project=str(s.repo))
    assert job["error"] == "" and job["session_id"]
    assert job["workspace"] == str(s.repo) and job["branch"] == "trunk"
    row = s.app.rows()[job["session_id"]]
    assert row["workspace"] == str(s.repo)
    assert row["project"] == real(s.repo) and row["checkout_kind"] == "main"


def test_a_registered_project_is_found_by_its_alias(s: Setup) -> None:
    s.register(s.repo, alias="My repo")
    job = s.job(message="hi", project="My repo")
    assert job["error"] == "" and job["workspace"] == str(s.repo)


def test_a_repository_a_conversation_already_ran_in_is_known(s: Setup) -> None:
    first = s.job(message="hi", workspace=str(s.repo))  # the owner's own folder, never registered
    assert first["error"] == ""
    job = s.job(message="again", project=str(s.repo))
    assert job["error"] == "" and job["workspace"] == str(s.repo)


def test_a_project_the_owner_hid_is_refused(s: Setup) -> None:
    s.register(s.repo)
    got = s.client.patch("/api/code/workspaces", json={"path": str(s.repo), "hidden": True})
    assert got.status_code == 200, got.text
    job = s.job(message="hi", project=str(s.repo))
    assert job["error"].startswith("HTTP 403") and "hid the project" in job["error"]


# ---- a worktree Chimera makes ------------------------------------------------------------------


def test_worktree_new_is_chimeras_own_and_files_under_the_project(s: Setup) -> None:
    s.register(s.repo)
    job = s.job(message="hi", project=str(s.repo), worktree="new", title="Abas (C-tabs) — fase 2")
    assert job["error"] == "", job
    path = Path(job["workspace"])
    branch = job["branch"]
    # Made by `GitWorktree.create`: prefix, place, durable record — what A6 and the post-merge
    # cleanup accept as Chimera's.
    assert path.name.startswith(wt.WORKTREE_PREFIX + "abas-c-tabs-fase-2")
    assert _key(path.parent) == _key(s.roots)
    assert _record(path)["durable"] is True and int(_record(path)["pid"]) == os.getpid()
    assert branch.startswith("chimera/abas-c-tabs-fase-2-") and "/attempt-" not in branch
    assert git(path, "symbolic-ref", "--short", "HEAD") == branch
    session_frame = next(event for event in job["events"] if event["event"] == "session")
    assert session_frame["data"]["base"] == git(path, "rev-parse", "HEAD")
    assert session_frame["data"]["base_source"] == "local"
    assert "warning" not in session_frame["data"]
    # Not counted as a live run of this process: a conversation's worktree is busy only while
    # something of that conversation runs in it.
    assert path.resolve() not in wt._live_here
    row = s.app.rows()[job["session_id"]]
    assert row["workspace"] == str(path)
    assert row["project"] == real(s.repo)
    assert row["checkout_kind"] == "worktree" and row["branch"] == branch
    assert row["title"] == "Abas (C-tabs) — fase 2"


def test_the_caller_continues_in_the_same_worktree_by_session_id(s: Setup) -> None:
    s.register(s.repo)
    first = s.job(message="hi", project=str(s.repo), worktree="new", branch="feat/tabs")
    assert first["error"] == "" and first["branch"] == "feat/tabs"
    again = s.job(message="more", session_id=first["session_id"], project=str(s.repo))
    assert again["error"] == "", again
    assert again["session_id"] == first["session_id"]
    assert again["workspace"] == first["workspace"]
    plain = s.job(message="and more", session_id=first["session_id"])
    assert plain["error"] == ""
    assert s.app.rows()[first["session_id"]]["workspace"] == first["workspace"]
    assert len(s.made()) == 1


def test_worktree_new_starts_a_conversation_and_takes_no_workspace(s: Setup) -> None:
    s.register(s.repo)
    first = s.job(message="hi", project=str(s.repo))
    refused = [
        s.job(message="x", project=str(s.repo), worktree="new", session_id=first["session_id"]),
        s.job(message="x", project=str(s.repo), worktree="new", workspace=str(s.repo / "src")),
    ]
    for job in refused:
        assert job["error"].startswith("HTTP 422"), job
    assert s.made() == []


@pytest.mark.parametrize(
    "branch",
    ["-x", "a..b", "x.lock", "feat/x.", "a b", "a~1", "a^", "a:b", "a\\b", "HEAD", "../x",
     "feat//x", "/x", "x/", "feat/@{u}", "chimera/attempt-1234", "attempt-x", "x" * 101],
)
def test_a_bad_branch_name_is_refused_and_leaves_no_folder(s: Setup, branch: str) -> None:
    s.register(s.repo)
    job = s.job(message="hi", project=str(s.repo), worktree="new", branch=branch)
    assert job["error"].startswith("HTTP 422"), (branch, job["error"])
    assert s.made() == []
    assert git(s.repo, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_an_existing_branch_is_refused_not_reused(s: Setup) -> None:
    s.register(s.repo)
    git(s.repo, "branch", "feat/taken")
    job = s.job(message="hi", project=str(s.repo), worktree="new", branch="feat/taken")
    assert job["error"].startswith("HTTP 409") and "pick another name" in job["error"]
    assert s.made() == []


def test_a_project_that_is_not_a_repository_has_no_worktrees(s: Setup, tmp_path: Path) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    s.register(folder)
    job = s.job(message="hi", project=str(folder), worktree="new")
    assert job["error"].startswith("HTTP 400") and "not a git repository" in job["error"]


def test_branch_names_and_slugs() -> None:
    assert check_branch("feat/a-1_b.c") == "feat/a-1_b.c"
    with pytest.raises(PlaceRefused):
        check_branch("feat/attempt-1")
    assert slug("Corrigir — ação ÚNICA!") == "corrigir-acao-unica"
    assert slug("") == ""
    assert derived_branch("attempt to fix").startswith("chimera/task-attempt-to-fix-")
    assert derived_branch(None).startswith("chimera/session-")


# ---- a workspace with a project ----------------------------------------------------------------


def test_a_workspace_outside_the_project_is_refused(s: Setup, tmp_path: Path) -> None:
    s.register(s.repo)
    other = make_repo(tmp_path / "other")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    for folder in (other, outside):
        job = s.job(message="hi", project=str(s.repo), workspace=str(folder))
        assert job["error"].startswith("HTTP 400") and "outside the project" in job["error"], job
    # The folder ABOVE the project, through the app's own route (the bridge refuses this one
    # earlier: in the tests it holds the app's data folder).
    direct = s.client.post(
        "/api/code/turn",
        json={"message": "hi", "project": str(s.repo), "workspace": str(s.repo.parent),
              "stream": False},
    )
    assert direct.status_code == 400 and "outside the project" in direct.json()["detail"]
    assert s.app.rows() == {}


def test_a_folder_of_the_project_or_a_checkout_of_it_is_accepted(s: Setup, tmp_path: Path) -> None:
    s.register(s.repo)
    inner = s.job(message="hi", project=str(s.repo), workspace=str(s.repo / "src"))
    assert inner["error"] == "" and inner["workspace"] == str(s.repo / "src")
    relative = s.job(message="hi", project=str(s.repo), workspace="src")
    assert relative["error"] == "" and _key(relative["workspace"]) == _key(s.repo / "src")
    linked = tmp_path / "w-hand"
    git(s.repo, "worktree", "add", "-q", "-b", "feat/hand", str(linked))
    own = s.job(message="hi", project=str(s.repo), workspace=str(linked))
    assert own["error"] == "" and own["branch"] == "feat/hand"


def test_continuing_a_conversation_from_another_folder_inside_a_project_is_refused(
    s: Setup, tmp_path: Path
) -> None:
    s.register(s.repo)
    other = make_repo(tmp_path / "other")
    first = s.job(message="hi", workspace=str(other))
    job = s.job(message="next", session_id=first["session_id"], project=str(s.repo))
    assert job["error"].startswith("HTTP 400") and "outside the project" in job["error"]


# ---- ownership across a restart, and the cleanups ------------------------------------------------


def test_a_restart_keeps_it_chimeras_and_the_storage_sweep_never_takes_it(
    s: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    s.register(s.repo)
    made = Path(s.job(message="hi", project=str(s.repo), worktree="new")["workspace"])
    run = wt.GitWorktree.create(s.repo)  # an ordinary run's worktree, for contrast
    wt._live_here.discard(run.path.resolve())
    old = os.path.getmtime(made) - 3600  # past the sweep's age gate
    for path in (made, run.path):
        record = _record(path)
        record["pid"] = _dead_pid()  # the app that made them has exited
        admin = wt._admin_dir(path)
        assert admin is not None
        (admin / wt.OWNER_FILE).write_text(json.dumps(record), encoding="utf-8")
        os.utime(path, (old, old))

    assert wt.classify_worktree_dir(made).reason == wt.DURABLE
    assert wt.classify_worktree_dir(run.path).state == "orphan"  # the guard is what tells them apart
    wt.prune_worktree_dirs()
    assert made.is_dir() and not run.path.exists()

    found = list_worktrees(s.repo, roots=[s.roots])
    assert found is not None
    (row,) = [r for r in found[1] if _key(r.path) == _key(made)]
    assert row.owned and not row.in_use and row.removable


def test_a_durable_worktree_is_not_held_by_a_live_maker(s: Setup) -> None:
    """Its maker's pid alive — the app still open, or the pid reused by another program — says
    nothing about the conversation; by pid it read `in_use` for as long as the app stayed open."""
    s.register(s.repo)
    made = Path(s.job(message="hi", project=str(s.repo), worktree="new")["workspace"])
    record = {"pid": os.getppid(), "created": 0.0, "durable": True,
              **wt.process_identity(os.getppid())}
    admin = wt._admin_dir(made)
    assert admin is not None
    (admin / wt.OWNER_FILE).write_text(json.dumps(record), encoding="utf-8")
    found = list_worktrees(s.repo, roots=[s.roots])
    assert found is not None
    (row,) = [r for r in found[1] if _key(r.path) == _key(made)]
    assert row.owned and not row.in_use, row
    # A conversation turn running there still keeps it: the caller's answer is what decides.
    held = list_worktrees(s.repo, roots=[s.roots], busy=lambda p: _key(p) == _key(made))
    assert held is not None
    (busy_row,) = [r for r in held[1] if _key(r.path) == _key(made)]
    assert busy_row.kept == "in_use"


def test_the_cleanup_dialog_lists_and_prunes_it_once_merged(s: Setup) -> None:
    s.register(s.repo)
    job = s.job(message="hi", project=str(s.repo), worktree="new", branch="feat/done")
    made = Path(job["workspace"])
    (made / "b.txt").write_text("work\n", encoding="utf-8")
    git(made, "add", "b.txt")
    git(made, "commit", "-q", "-m", "work")
    listed = s.client.get("/api/code/projects/worktrees", params={"project": str(s.repo)}).json()
    (row,) = [w for w in listed["worktrees"] if _key(w["path"]) == _key(made)]
    assert row["owned"] and row["kept"] == "no_upstream"  # not merged yet: kept
    assert [c["id"] for c in row["conversations"]] == [job["session_id"]]
    git(s.repo, "merge", "-q", "--ff-only", "feat/done")
    out = s.client.post(
        "/api/code/projects/worktrees/prune",
        json={"project": str(s.repo), "paths": [str(made)], "dry_run": False},
    ).json()
    (result,) = out["results"]
    assert result["removed"] and result["branch_deleted"], result
    assert result["archived"] == [job["session_id"]]
    assert not made.exists()
    # Still filed under the project after its folder went (A1's stamp).
    archived = s.app.rows(archived="true")
    assert archived[job["session_id"]]["project"] == real(s.repo)


def test_prune_worktrees_with_the_default_roots_recognises_it(s: Setup) -> None:
    """No `roots` override: the setting the app runs with is what makes it Chimera's."""
    s.register(s.repo)
    made = Path(s.job(message="hi", project=str(s.repo), worktree="new")["workspace"])
    results = prune_worktrees(s.repo, [str(made)], dry_run=True)
    assert results is not None and results[0].would_remove, results


# ---- the bridge -----------------------------------------------------------------------------------


def test_desktop_send_forwards_project_worktree_and_branch() -> None:
    calls: list[Any] = []

    def http(method: str, url: str, token: str, body: Any, timeout: float) -> tuple[int, Any]:
        calls.append(body)
        return 200, {"status": 200, "job": {"done": True, "workspace": "/w", "branch": "feat/x"}}

    found = {"url": "http://127.0.0.1:1", "token": "tok-0123456789abcdef", "full": False}
    mcp = DesktopMCP(discover=lambda: found, http=http)
    send = next(spec for spec in mcp.tool_specs() if spec["name"] == "desktop_send")
    props = send["inputSchema"]["properties"]
    assert props["worktree"]["enum"] == ["new"] and "project" in props and "branch" in props
    assert 'worktree="new"' in send["description"]
    text = mcp.dispatch(
        "desktop_send", {"message": "go", "project": "/p", "worktree": "new", "branch": "feat/x"}
    )
    assert calls[0]["body"] == {
        "message": "go", "project": "/p", "worktree": "new", "branch": "feat/x"
    }
    assert '"workspace": "/w"' in text and '"branch": "feat/x"' in text


def test_the_place_guard_holds_the_project_to_its_rule(s: Setup, tmp_path: Path) -> None:
    home = s.app.home
    for project in (str(home), str(tmp_path), "\\\\?\\" + str(s.repo)):
        got = s.send(message="hi", project=project)
        assert got.get("detail") and got.get("job") is None, (project, got)
    assert s.made() == []


def test_a_worktree_folder_inside_the_apps_data_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = Setup(tmp_path, monkeypatch)
    try:
        setup.register(setup.repo)
        inside = setup.app.home / "wts"
        inside.mkdir(parents=True)
        monkeypatch.setenv("CHIMERA_WORKTREE_DIR", str(inside))
        from chimera.config import get_settings

        get_settings.cache_clear()
        got = setup.send(message="hi", project=str(setup.repo), worktree="new")
        assert "CHIMERA_WORKTREE_DIR" in str(got.get("detail")), got
        assert list(inside.iterdir()) == []
        # The same project with no worktree is not this refusal's business.
        ok = setup.job(message="hi", project=str(setup.repo))
        assert ok["error"] == ""
    finally:
        setup.close()


def test_a_granted_project_lends_its_grant_to_a_turn_in_it_and_never_to_a_new_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api.bridge_routes import ROUTES
    from tests.test_the_desktop_bridge_reaches_only_its_table import _capture_route, _grant

    setup = Setup(tmp_path, monkeypatch)
    try:
        _grant(setup.app.home, setup.repo)
        seen = _capture_route(setup.app.app, monkeypatch)
        assert "test.seams" in ROUTES
        token = setup.app.app.state.desktop_bridge.token
        for body in ({"project": str(setup.repo)}, {"project": str(setup.repo), "worktree": "new"}):
            got = setup.client.post(
                "/api/bridge/call",
                json={"route": "test.seams", "body": {"task": "x", **body}},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert got.status_code == 200, got.text
        assert [b["posture"]["reach"] for b in seen] == ["workspace_shell", "workspace"]
    finally:
        setup.close()


# ---- where a durable worktree goes when no folder is configured ---------------------------------


@pytest.fixture
def bare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    setup = Setup(tmp_path, monkeypatch, configured=False)
    try:
        yield setup
    finally:
        setup.close()


def test_without_a_configured_folder_it_goes_beside_the_repository(
    bare: Setup, tmp_path: Path
) -> None:
    """Not temp (the OS cleans it: Storage Sense), not CHIMERA_HOME (the protected data folder)."""
    import tempfile

    bare.register(bare.repo)
    job = bare.job(message="hi", project=str(bare.repo), worktree="new")
    assert job["error"] == "", job
    made = Path(job["workspace"])
    assert _key(made.parent) == _key(tmp_path / wt.DURABLE_DIR / "repo")
    assert _key(made.parent) != _key(tempfile.gettempdir())
    assert not wt.is_inside(made, bare.app.home)
    assert not wt.is_inside(made, bare.repo)  # beside, never inside
    assert bare.made() == []  # nothing in the folder a configured setting would have used
    assert _record(made)["durable"] is True
    row = bare.app.rows()[job["session_id"]]
    assert row["project"] == real(bare.repo) and row["checkout_kind"] == "worktree"


def test_a_run_worktree_keeps_its_old_place(bare: Setup) -> None:
    """Only a conversation's worktree moves out of temp; a run's is removed when the run ends."""
    import tempfile

    run = wt.GitWorktree.create(bare.repo)
    try:
        assert _key(run.path.parent) == _key(tempfile.gettempdir())
    finally:
        run.remove()


def test_a_configured_folder_still_overrides_the_sibling(s: Setup, tmp_path: Path) -> None:
    s.register(s.repo)
    made = Path(s.job(message="hi", project=str(s.repo), worktree="new")["workspace"])
    assert _key(made.parent) == _key(s.roots)
    assert not (tmp_path / wt.DURABLE_DIR).exists()


def _temp_elsewhere(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Move the "system temp" Chimera's worktree rules read away from ``tmp_path``.

    The test folders live under the real temp, which is itself a place worktrees are made in — so
    without this, anything under ``tmp_path`` would pass the place rule because of temp, and a test
    of the sibling folder would prove nothing about it."""
    import tempfile
    import types

    elsewhere = tmp_path / "system-temp"
    elsewhere.mkdir(exist_ok=True)
    fake = types.SimpleNamespace(gettempdir=lambda: str(elsewhere), mkdtemp=tempfile.mkdtemp)
    monkeypatch.setattr(wt, "tempfile", fake)


def test_a6_and_the_post_merge_cleanup_recognise_the_sibling_folder(
    bare: Setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.core.pull_request_cleanup import check

    _temp_elsewhere(monkeypatch, tmp_path)
    bare.register(bare.repo)
    job = bare.job(message="hi", project=str(bare.repo), worktree="new", branch="feat/beside")
    made = Path(job["workspace"])
    listed = bare.client.get(
        "/api/code/projects/worktrees", params={"project": str(bare.repo)}
    ).json()
    (row,) = [w for w in listed["worktrees"] if _key(w["path"]) == _key(made)]
    assert row["owned"] and row["removable"], row  # at main's HEAD: merged, clean
    verdict, _main = check(made)  # the post-merge cleanup's own place rule, no roots override
    assert verdict.kept != "not_chimera_worktree", verdict
    out = bare.client.post(
        "/api/code/projects/worktrees/prune",
        json={"project": str(bare.repo), "paths": [str(made)], "dry_run": False},
    ).json()
    assert out["results"][0]["removed"], out
    assert not made.exists()


def test_another_repositorys_sibling_folder_is_not_a_place_for_this_one(
    bare: Setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.chimera-worktrees/<name>` belongs to the repository named <name> beside it, and only it."""
    from chimera.core.pull_request_cleanup import check

    _temp_elsewhere(monkeypatch, tmp_path)
    elsewhere = tmp_path / wt.DURABLE_DIR / "other" / f"{wt.WORKTREE_PREFIX}stray"
    elsewhere.parent.mkdir(parents=True)
    git(bare.repo, "worktree", "add", "-q", "-b", "feat/stray", str(elsewhere))
    wt._write_owner(elsewhere, durable=True)
    found = list_worktrees(bare.repo)
    assert found is not None
    (row,) = [r for r in found[1] if _key(r.path) == _key(elsewhere)]
    assert not row.owned and row.kept == "not_chimera_worktree"
    assert check(elsewhere)[0].kept == "not_chimera_worktree"


def test_a_repository_at_a_drive_root_has_no_sibling_folder() -> None:
    root = Path(Path.cwd().anchor)
    assert wt.durable_worktree_folder(root) is None


# ---- the turn's own guard -------------------------------------------------------------------------


def test_a_request_reaching_the_turn_another_way_with_a_project_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`code_turn` resolves and clears project/worktree/branch. The guest route builds its own
    request and calls the turn directly; one arriving there with them set must be refused, never
    run in the server's folder with the fields silently ignored."""
    import asyncio

    from fastapi import HTTPException

    import chimera.api.guest_api as guest_api
    from chimera.api.code_api import CodeTurnRequest

    captured: dict[str, Any] = {}
    original = guest_api.build_guest_app

    def spy(**kwargs: Any) -> Any:
        captured["start_turn"] = kwargs["start_turn"]
        return original(**kwargs)

    monkeypatch.setattr(guest_api, "build_guest_app", spy)
    app = App(tmp_path, monkeypatch, Agent)
    try:
        start_turn = captured["start_turn"]
        for fields in ({"project": str(tmp_path)}, {"worktree": "new"}, {"branch": "feat/x"}):
            req = CodeTurnRequest(message="hi", **fields)
            with pytest.raises(HTTPException) as refused:
                asyncio.run(start_turn(req))
            assert refused.value.status_code == 422
            assert "/api/code/turn only" in str(refused.value.detail)
        assert app.rows() == {}
    finally:
        app.client.close()
