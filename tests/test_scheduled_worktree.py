"""A scheduled task in a worktree of its folder: removed when it left nothing, kept when it left work.

Real git in a temporary repository; the run itself is a function the test passes in, so what it
leaves behind is exactly what the assertion names.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chimera.config import get_settings
from chimera.core.pull_request import run_quiet
from chimera.scheduler.history import CronHistory
from chimera.scheduler.job_worktree import run_in_job_worktree
from chimera.scheduler.models import CronJob, JobOutcome


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    )
    return done.stdout


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_WORKTREE_DIR", str(tmp_path / "wts"))
    get_settings.cache_clear()
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _job() -> CronJob:
    return CronJob(id="j1", name="nightly", schedule="0 3 * * *", action="x", worktree=True)


def _history(tmp_path: Path) -> CronHistory:
    return CronHistory(tmp_path / "history.jsonl")


def test_a_run_that_left_nothing_has_its_worktree_removed(repo: Path, tmp_path: Path) -> None:
    seen: list[Path] = []

    def run(root: Path) -> JobOutcome:
        seen.append(root)
        assert root != repo and (root / "a.txt").exists()
        (root / "__pycache__").mkdir()
        (root / "__pycache__" / "x.pyc").write_bytes(b"0")  # bytecode is not work
        return JobOutcome("report")

    outcome = run_in_job_worktree(_job(), repo, _history(tmp_path), run)

    assert outcome.answer == "report"
    assert not seen[0].exists(), "a clean worktree was left on disk"
    assert _history(tmp_path).load() == []
    assert "chimera-cron" not in _git(repo, "branch")


def test_a_run_that_edited_a_file_keeps_its_worktree_and_says_where(
    repo: Path, tmp_path: Path
) -> None:
    def run(root: Path) -> JobOutcome:
        (root / "a.txt").write_text("two\n", encoding="utf-8")
        return JobOutcome("edited")

    run_in_job_worktree(_job(), repo, _history(tmp_path), run)

    rows = _history(tmp_path).load(job_id="j1")
    assert [(r.outcome, r.reason) for r in rows] == [("note", "worktree_kept")]
    kept = Path(rows[0].worktree)
    assert kept.exists() and (kept / "a.txt").read_text(encoding="utf-8") == "two\n"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "one\n", "the owner's folder was touched"


def test_a_run_that_committed_keeps_its_worktree_even_with_a_clean_tree(
    repo: Path, tmp_path: Path
) -> None:
    def run(root: Path) -> JobOutcome:
        (root / "b.txt").write_text("new\n", encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "work")
        return JobOutcome("committed")

    run_in_job_worktree(_job(), repo, _history(tmp_path), run)

    rows = _history(tmp_path).load()
    assert len(rows) == 1 and Path(rows[0].worktree).exists()
    assert "no_upstream" in rows[0].detail, "a commit no branch knows is work, and the reason says so"


def test_a_run_that_raises_after_writing_still_keeps_the_work(repo: Path, tmp_path: Path) -> None:
    def run(root: Path) -> JobOutcome:
        (root / "partial.txt").write_text("half\n", encoding="utf-8")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run_in_job_worktree(_job(), repo, _history(tmp_path), run)

    rows = _history(tmp_path).load()
    assert len(rows) == 1 and (Path(rows[0].worktree) / "partial.txt").exists()


def test_a_folder_that_is_not_a_repository_is_an_error_not_an_in_place_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    plain = tmp_path / "plain"
    plain.mkdir()
    ran: list[Path] = []

    with pytest.raises(RuntimeError, match="not a git repository"):
        run_in_job_worktree(_job(), plain, _history(tmp_path), lambda r: ran.append(r) or JobOutcome(""))

    assert ran == []


# --------------------------------------------------------------------------- what is never deleted


def _kept(tmp_path: Path) -> list[str]:
    return [r.reason for r in _history(tmp_path).load()]


def test_an_ignored_file_keeps_the_worktree(repo: Path, tmp_path: Path) -> None:
    """A `.env`, a report or a local database is in no `git status`; removing the folder loses it."""
    (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "ignore")

    def run(root: Path) -> JobOutcome:
        (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
        return JobOutcome("x")

    run_in_job_worktree(_job(), repo, _history(tmp_path), run)

    rows = _history(tmp_path).load()
    assert [r.reason for r in rows] == ["worktree_kept"] and "ignored_files" in rows[0].detail
    assert (Path(rows[0].worktree) / ".env").read_text(encoding="utf-8") == "SECRET=1\n"


def test_a_new_file_in_dist_keeps_the_worktree(repo: Path, tmp_path: Path) -> None:
    """`dist/` and `build/` are in the checkpoint's ignore list; they are still somebody's output."""

    def run(root: Path) -> JobOutcome:
        (root / "dist").mkdir()
        (root / "dist" / "bundle.js").write_text("x", encoding="utf-8")
        return JobOutcome("x")

    run_in_job_worktree(_job(), repo, _history(tmp_path), run)

    rows = _history(tmp_path).load()
    assert len(rows) == 1 and (Path(rows[0].worktree) / "dist" / "bundle.js").exists()


def test_only_caches_are_removed(repo: Path, tmp_path: Path) -> None:
    def run(root: Path) -> JobOutcome:
        for name in ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"):
            (root / name).mkdir()
            (root / name / "f").write_text("x", encoding="utf-8")
        (root / "__pycache__" / "m.cpython-312.pyc").write_bytes(b"0")
        return JobOutcome("x")

    run_in_job_worktree(_job(), repo, _history(tmp_path), run)

    assert _history(tmp_path).load() == []


def test_removal_never_forces_and_never_deletes_a_branch_with_dash_d(
    repo: Path, tmp_path: Path
) -> None:
    argvs: list[list[str]] = []

    def recording(argv, cwd):  # type: ignore[no-untyped-def]
        argvs.append(list(argv))
        return run_quiet(argv, cwd)

    run_in_job_worktree(
        _job(), repo, _history(tmp_path), lambda root: JobOutcome("x"), runner=recording
    )

    flat = [a for argv in argvs for a in argv]
    assert "worktree" in flat and "remove" in flat, "the removal did not go through the runner"
    assert "--force" not in flat and "-f" not in flat and "-D" not in flat
    assert any("branch" in argv and "-d" in argv for argv in argvs), "the branch was not deleted with -d"


def test_a_subfolder_job_runs_in_the_same_subfolder_of_the_worktree(
    repo: Path, tmp_path: Path
) -> None:
    sub = repo / "pkg" / "inner"
    sub.mkdir(parents=True)
    (sub / "f.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "sub")
    seen: list[Path] = []

    def run(root: Path) -> JobOutcome:
        seen.append(root)
        # While it runs: the folder is the repo's own subfolder, and the worktree root sits above it.
        assert (root / "f.txt").exists()
        assert (root.parent.parent / "a.txt").exists()
        return JobOutcome("x")

    run_in_job_worktree(_job(), sub, _history(tmp_path), run)

    assert seen[0].parts[-2:] == ("pkg", "inner") and seen[0] != sub
