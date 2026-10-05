"""The bridge never hands a run, a repository or a listing Chimera's own `.env` or data folder.

Final review of 2026-10-04 (MEDIUM, pre-existing) and an owner's decision of the same day.

* The bridge refused a workspace inside or around the DATA folder, but not one holding Chimera's own
  ``.env``, and never a path that contained either. Desktop layout (the install folder, where the
  app runs, holds ``.env``): ``git.init`` on it ran ``git add -A`` and committed the ``.env``, and
  ``git.diff`` then returned its changed lines. An app run from its own folder: ``git.revert`` of
  ``.`` ran ``git clean -fd`` over the data folder, ``git.commit`` of ``.`` committed everything.
  Now every workspace a call names (at any depth) is refused when it holds either; git routes hold
  the app's own folder to the same rule and refuse a path that contains either; and git status and
  diff output leave both out.
* Owner's decision: a run, turn, chat, cron job or board run started through the bridge NEVER gets
  Chimera's own ``.env``, whatever ``CHIMERA_AGENT_READS_OWN_ENV`` says for the owner's own runs. The
  routes that carry the run seams get ``hide_own_env`` set by the bridge (a client's ``false`` is
  overwritten), and every file tool of the run is stamped; the others may not run in a folder
  holding Chimera's files at all.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api.bridge_routes import ROUTES
from chimera.config import get_settings
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun
from tests.test_the_desktop_bridge_reaches_only_its_table import _app, _call, _capture_route

URL = "http://127.0.0.1:65014"
KEY = "sk-or-v1-" + "beef" * 12


def _desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, full: bool = True) -> Any:
    """The desktop's layout: the app runs in the install folder (its `.env` there), keeps its data in
    `<app data>/data` and works by default in `<app data>/workspace` beside it."""
    install, appdata = tmp_path / "install", tmp_path / "appdata"
    tmp_path.mkdir(parents=True, exist_ok=True)
    install.mkdir()
    (appdata / "workspace").mkdir(parents=True)
    (install / ".env").write_text(f"OPENROUTER_API_KEY={KEY}\n", encoding="utf-8")
    monkeypatch.chdir(install)
    monkeypatch.setenv("CHIMERA_HOME", str(appdata / "data"))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "true" if full else "false")
    get_settings.cache_clear()
    app = build_api_app(
        lambda: ChatSession(cast(SupportsRun, None)), workspace=appdata / "workspace"
    )
    app.state.desktop_bridge.attach(URL)
    return app


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.mark.parametrize("full", [False, True], ids=["operate", "full"])
def test_no_bridge_route_takes_the_install_folder_as_a_workspace(
    full: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _desktop(tmp_path, monkeypatch, full=full)
    install = str(tmp_path / "install")
    calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
        ("git.init", {}, {"workspace": install}),
        ("git.status", {"workspace": install}, {}),
        ("git.diff", {"workspace": install}, {}),
        ("git.commit", {}, {"workspace": install, "message": "m", "paths": ["notes.txt"]}),
        ("files.tree", {"workspace": install, "path": ""}, {}),
        ("conversations.send", {}, {"message": "hi", "workspace": install}),
        ("runs.start", {}, {"task": "x", "workspace": install}),
        ("cron.create", {}, {"name": "j", "schedule": "0 7 * * *", "action": "x",
                             "workspace": install}),
        ("kanban.run", {}, {"workspace": install}),
        ("agents.batch", {}, {"tasks": [{"task": "x"}], "workspace": install}),
    ]
    with TestClient(app) as client:
        for route, params, body in calls:
            if ROUTES[route].tier == "full" and not full:
                continue
            got = _call(client, app, route, params=params, body=body, wait_seconds=0)
            assert got.status_code == 403, (route, got.text)
            assert "own data or its .env" in got.json()["detail"], (route, got.text)
    assert not (tmp_path / "install" / ".git").exists()
    get_settings.cache_clear()


def test_git_through_the_bridge_never_runs_over_chimeras_files_in_the_apps_own_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An app run from its own folder (the CLI): the app's workspace holds the data folder."""
    app = _app(tmp_path, monkeypatch, full=True)
    home = tmp_path / "home"
    (home / "approvals").mkdir(parents=True)
    (home / "memory.json").write_text("{}", encoding="utf-8")
    project = tmp_path / "proj"
    project.mkdir()
    with TestClient(app) as client:
        init = _call(client, app, "git.init", body={})
        revert = _call(client, app, "git.revert", body={"paths": ["."]})
        commit = _call(client, app, "git.commit", body={"message": "m", "paths": ["."]})
        escape = _call(
            client, app, "git.revert", body={"workspace": str(project), "paths": [".."]}
        )
    assert [r.status_code for r in (init, revert, commit, escape)] == [403, 403, 403, 403]
    assert (home / "memory.json").exists()
    assert not (tmp_path / ".git").exists()


def test_work_without_run_seams_never_starts_where_chimeras_files_are(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chat, a board run, a hierarchy or a cron job carries no `hide_own_env`; so through the
    bridge it may not run in a folder holding Chimera's files — including the app's own folder
    when the call names none (an app run from its install folder). The desktop's own folder holds
    neither, and there they run as before."""
    (tmp_path / "cli").mkdir()
    cli_app = _app(tmp_path / "cli", monkeypatch, full=True)  # its folder holds the data folder
    bodies: list[tuple[str, dict[str, Any]]] = [
        ("chat.send", {"message": "hi"}),
        ("kanban.run", {}),
        ("orchestration.hierarchy", {"task": "x"}),
        ("cron.create", {"name": "j", "schedule": "0 7 * * *", "action": "x"}),
    ]
    with TestClient(cli_app) as client:
        for route, body in bodies:
            got = _call(client, cli_app, route, body=body, wait_seconds=0)
            assert got.status_code == 403, (route, got.text)
            assert "own data or its .env" in got.json()["detail"], route
    desktop = _desktop(tmp_path / "desk", monkeypatch)
    with TestClient(desktop) as client:
        made = _call(
            client, desktop, "cron.create", body={"name": "j", "schedule": "0 7 * * *", "action": "x"}
        )
    assert made.status_code == 200 and made.json()["status"] == 200, made.text
    get_settings.cache_clear()


def test_status_and_diff_leave_credential_files_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=True)
    project = tmp_path / "proj"
    project.mkdir()
    _git(project, "init", "-q")
    _git(project, "config", "user.email", "t@example.invalid")
    _git(project, "config", "user.name", "t")
    (project / "a.txt").write_text("one\n", encoding="utf-8")
    (project / ".env").write_text("SECRET=one\n", encoding="utf-8")
    _git(project, "add", "-f", "a.txt", ".env")
    _git(project, "commit", "-q", "-m", "init")
    (project / "a.txt").write_text("two\n", encoding="utf-8")
    (project / ".env").write_text("SECRET=two\n", encoding="utf-8")
    with TestClient(app) as client:
        status = _call(client, app, "git.status", params={"workspace": str(project)})
        diff = _call(client, app, "git.diff", params={"workspace": str(project)})
    assert [f["path"] for f in status.json()["data"]["files"]] == ["a.txt"]
    patch = diff.json()["data"]["patch"]
    assert "a.txt" in patch and "SECRET" not in patch and ".env" not in patch


@pytest.mark.parametrize("full", [False, True], ids=["operate", "full"])
def test_a_run_the_bridge_starts_always_carries_hide_own_env(
    full: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=full)
    seen = _capture_route(app, monkeypatch)
    with TestClient(app) as client:
        _call(client, app, "test.seams", body={"task": "x"})
        _call(client, app, "test.seams", body={"task": "x", "hide_own_env": False})
        _call(
            client,
            app,
            "test.seams",
            body={"task": "x", "posture": {"reach": "read_only", "approval": "always"}},
        )
    assert [b["hide_own_env"] for b in seen] == [True, True, True]


def test_every_route_that_starts_work_is_covered_one_way_or_the_other() -> None:
    """A seams route gets the flag; every other route that starts or schedules work is in the set
    that may not run where Chimera's files are. A new start route has to land on one side."""
    from chimera.api.desktop_bridge import _UNSEAMED_STARTS

    starts = {rid for rid, r in ROUTES.items() if r.stream} | {
        "cron.create",
        "spec_projects.create",
        "spec_projects.step",
    }
    uncovered = sorted(rid for rid in starts if not ROUTES[rid].seams and rid not in _UNSEAMED_STARTS)
    # `settings.exec_cancel` and the like start nothing; the streamed routes are the runs.
    assert uncovered == [], uncovered


@pytest.mark.parametrize(
    "route, body",
    [
        ("conversations.send", {"message": "hi"}),
        ("runs.start", {"task": "x"}),
        ("agents.batch", {"tasks": [{"task": "x"}]}),
        ("orchestration.crew", {"task": "x", "workers": [{"name": "a", "instruction": "b"}]}),
    ],
)
def test_each_seams_route_builds_its_tools_with_the_flag(
    route: str, body: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: the registry each real route assembles is told to hide Chimera's .env.

    `lifecycle.start` is left out: it plans with a model before it assembles a registry, and this
    suite makes no model call. It passes its own request — a `CodeSeams` the bridge stamped like
    these — to the same `assemble_registry`."""
    import chimera.api.app as app_module
    import chimera.api.code_api as code_api

    seen: list[bool] = []

    def recorder(seams: Any, *a: Any, **k: Any) -> Any:
        seen.append(bool(getattr(seams, "hide_own_env", False)))
        raise RuntimeError("stop after recording")

    monkeypatch.setattr(code_api, "assemble_registry", recorder)
    monkeypatch.setattr(app_module, "assemble_registry", recorder)
    app = _app(tmp_path, monkeypatch, full=True)
    project = tmp_path / "proj"
    project.mkdir()
    # A repository with one commit: a batch cuts each task a worktree from it.
    _git(project, "init", "-q")
    _git(project, "config", "user.email", "t@example.invalid")
    _git(project, "config", "user.name", "t")
    (project / "a.txt").write_text("x\n", encoding="utf-8")
    _git(project, "add", "a.txt")
    _git(project, "commit", "-q", "-m", "init")
    with TestClient(app) as client:
        _call(client, app, route, body={**body, "workspace": str(project)}, wait_seconds=10)
    assert seen and all(seen), (route, seen)


def test_a_stamped_tool_hides_the_file_whatever_the_setting_and_the_owners_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.tools.workspace import PathEscapesWorkspaceError

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_AGENT_READS_OWN_ENV", "true")
    get_settings.cache_clear()
    (tmp_path / ".env").write_text(f"OPENROUTER_API_KEY={KEY}\n", encoding="utf-8")

    def read_env(hide: bool) -> str:
        registry, _ = assemble_registry(
            CodeSeams(hide_own_env=hide), tmp_path, get_settings(), None, steps=4
        )
        try:
            return str(registry.get("read_file").run(path=".env"))
        except PathEscapesWorkspaceError as exc:
            return f"error: {exc}"

    assert KEY in read_env(False), "the owner's run follows the setting (on)"
    assert KEY not in read_env(True), "a bridge run never gets it"
    get_settings.cache_clear()
