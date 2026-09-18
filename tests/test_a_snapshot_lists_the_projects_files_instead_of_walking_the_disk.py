"""The snapshot a coding turn takes before it calls the model, on a real-sized repository.

Measured on 2026-09-17: `rglob("*")` over the project's own checkout walked 142,000 entries (most
of them `node_modules`, `.venv-win`, a Rust `target`) and read the first 5,000 files it met — 8.7 s
before every turn, spoken or typed, with the source not necessarily among the 5,000. Inside a git
repository the guard now asks git which files are the project's; outside one, the walk does not
enter the directories it would have ignored anyway.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from chimera.core import checkpoint
from chimera.core.checkpoint import WorkspaceGuard, git_listed

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)


def test_inside_a_repository_the_snapshot_is_what_git_calls_the_project(tmp_path: Path) -> None:
    ws = tmp_path / "proj"
    ws.mkdir()
    _repo(ws)
    (ws / ".gitignore").write_text("data/\n.venv-win/\n*.log\n", encoding="utf-8")
    (ws / "src").mkdir()
    (ws / "src" / "a.py").write_text("print(1)\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-q", "-m", "one"], check=True)
    # After the commit: a new untracked source file (the agent's kind of file), and three kinds of
    # file git ignores — a data dump, a second virtualenv, a log.
    (ws / "src" / "b.py").write_text("print(2)\n", encoding="utf-8")
    (ws / "data").mkdir()
    (ws / "data" / "dump.json").write_text("{}" * 1000, encoding="utf-8")
    (ws / ".venv-win" / "Lib").mkdir(parents=True)
    (ws / ".venv-win" / "Lib" / "x.py").write_text("x", encoding="utf-8")
    (ws / "run.log").write_text("log", encoding="utf-8")

    listed = git_listed(ws)
    assert listed is not None
    snap = WorkspaceGuard(ws).snapshot()
    assert {"src/a.py", "src/b.py", ".gitignore"} <= snap.present
    assert not any(rel.startswith(("data/", ".venv-win/")) or rel.endswith(".log") for rel in snap.present)
    assert snap.files["src/b.py"] == "print(2)\n"


def test_a_folder_inside_a_repository_gets_that_folders_files_only(tmp_path: Path) -> None:
    root = tmp_path / "mono"
    root.mkdir()
    _repo(root)
    (root / "other").mkdir()
    (root / "other" / "o.py").write_text("o", encoding="utf-8")
    (root / "app").mkdir()
    (root / "app" / "a.py").write_text("a", encoding="utf-8")

    snap = WorkspaceGuard(root / "app").snapshot()
    assert snap.present == {"a.py"}


def test_outside_a_repository_the_walk_does_not_enter_what_it_would_ignore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "plain"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "a.py").write_text("a", encoding="utf-8")
    for junk in ("node_modules/pkg", ".venv-win/Lib", "venv311/Lib", "target/debug", "build", "pkg.egg-info"):
        (ws / junk).mkdir(parents=True)
        (ws / junk / "x.txt").write_text("x", encoding="utf-8")

    entered: list[str] = []
    real_walk = os.walk

    def counting_walk(top: str) -> Iterator[tuple[str, list[str], list[str]]]:
        for dirpath, dirnames, filenames in real_walk(top):
            entered.append(dirpath)
            yield dirpath, dirnames, filenames

    monkeypatch.setattr(os, "walk", counting_walk)
    # Not a repository (pinned, so a temp dir that happens to sit under a git-managed home does not
    # turn this into the other test): the walk is what runs.
    monkeypatch.setattr(checkpoint, "git_listed", lambda _ws: None)
    snap = WorkspaceGuard(ws).snapshot()
    assert snap.present == {"src/a.py"}
    assert all(not any(part in ("node_modules", "target", "build") or part.startswith((".venv", "venv")) for part in Path(d).parts) for d in entered)


def test_the_cap_still_holds_and_still_disables_the_delete_pass(tmp_path: Path) -> None:
    ws = tmp_path / "many"
    ws.mkdir()
    for i in range(6):
        (ws / f"f{i}.txt").write_text(str(i), encoding="utf-8")
    guard = WorkspaceGuard(ws, max_files=4)
    snap = guard.snapshot()
    assert len(snap.present) == 4
    assert guard.deletes_new_files(snap) is False
