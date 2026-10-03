"""The commit suggestion asks git about the turn's own files, where both paths are known (study 29, P4.5).

Under an answer the Code screen offers "commit <files>" for the files the turn wrote that git still
reports as changed. It first decided that on the client, by comparing the agent's path for a file
with git's path for it, and the comparison was wrong three ways without ever raising:

- the agent may name a file by an absolute path, which never equals git's repo-relative one;
- a new file in a new folder is reported by plain ``git status`` as the folder, which no file equals;
- a suffix match made ``a.py`` the same file as a dirty ``vendor/a.py``.

`git_uncommitted` decides it next to the workspace and the repository root, and each of the three
is pinned here on a real repository.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.git_api import git_uncommitted
from chimera.config import Settings
from chimera.interface import ChatSession

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True)


def _repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "commit.gpgsign", "false")
    for name, text in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


def test_a_file_the_agent_named_by_its_absolute_path_is_found(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "proj", {"src/a.py": "x = 1\n"})
    (repo / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")

    out = git_uncommitted(repo, [str(repo / "src" / "a.py")])

    assert out == {"is_repo": True, "files": ["src/a.py"]}


def test_a_new_file_in_a_new_folder_is_found_as_the_file_not_the_folder(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "proj", {"README.md": "hi\n"})
    (repo / "newdir").mkdir()
    (repo / "newdir" / "a.py").write_text("x = 1\n", encoding="utf-8")

    assert git_uncommitted(repo, ["newdir/a.py"])["files"] == ["newdir/a.py"]


def test_a_clean_file_is_not_the_dirty_file_of_the_same_name_in_another_folder(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "proj", {"a.py": "x = 1\n", "vendor/a.py": "y = 1\n"})
    (repo / "vendor" / "a.py").write_text("y = 2\n", encoding="utf-8")

    # The turn wrote a.py and it is clean (committed, say, by the turn itself): nothing to offer.
    # Held twice over — git is asked about `a.py` alone, and its answer is compared exactly — so this
    # turns red only when both go, which is the comparison the client used to make.
    assert git_uncommitted(repo, ["a.py"])["files"] == []
    assert git_uncommitted(repo, ["vendor/a.py"])["files"] == ["vendor/a.py"]


def test_a_workspace_inside_the_repository_gets_its_own_relative_paths_back(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "proj", {"web/src/a.ts": "1\n", "web/src/b.ts": "1\n"})
    ws = repo / "web"
    (ws / "src" / "a.ts").write_text("2\n", encoding="utf-8")
    (ws / "src" / "c.ts").write_text("new\n", encoding="utf-8")

    out = git_uncommitted(ws, ["src/a.ts", "src/b.ts", "src/c.ts"])

    # In the order the turn wrote them, and named the way the workspace names them.
    assert out["files"] == ["src/a.ts", "src/c.ts"]


def test_a_deleted_and_a_staged_file_both_count_as_uncommitted(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "proj", {"a.py": "1\n", "b.py": "1\n"})
    (repo / "a.py").unlink()
    (repo / "b.py").write_text("2\n", encoding="utf-8")
    _git(repo, "add", "b.py")

    assert git_uncommitted(repo, ["a.py", "b.py"])["files"] == ["a.py", "b.py"]


def test_a_path_outside_the_workspace_and_a_glob_looking_name_are_not_guessed(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "proj", {"web/a.py": "1\n", "api/b.py": "1\n"})
    (repo / "api" / "b.py").write_text("2\n", encoding="utf-8")
    (repo / "web" / "a.py").write_text("2\n", encoding="utf-8")

    # `../api/b.py` is dirty, but it is not this workspace's change to offer.
    assert git_uncommitted(repo / "web", ["../api/b.py"])["files"] == []
    # A pathspec is literal: `*.py` names a file called that, which does not exist, not every .py.
    assert git_uncommitted(repo / "web", ["*.py"])["files"] == []


def test_outside_a_repository_nothing_is_known(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.py").write_text("1\n", encoding="utf-8")

    assert git_uncommitted(plain, ["a.py"]) == {"is_repo": False, "files": []}


class _FakeAgent:
    def run(self, task: str, **_kw: Any) -> Any:  # pragma: no cover - never reached here
        raise AssertionError("no turn runs in these tests")


def test_the_route_answers_for_the_workspace_it_is_given(tmp_path: Path) -> None:
    from chimera.api import build_api_app

    repo = _repo(tmp_path / "proj", {"src/a.py": "1\n"})
    (repo / "src" / "a.py").write_text("2\n", encoding="utf-8")
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings, workspace=repo))

    resp = client.post("/api/git/uncommitted", json={"paths": [str(repo / "src" / "a.py"), "README.md"]})

    assert resp.status_code == 200
    assert resp.json() == {"is_repo": True, "files": ["src/a.py"]}
