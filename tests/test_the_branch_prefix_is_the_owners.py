"""`CHIMERA_BRANCH_PREFIX`: the first segment of an isolated run's branch, and the cleanup that follows it.

Study 29, P8.1. The branches a run makes land in the owner's repository, so their name is the owner's
to choose. The half that is easy to get wrong is the cleanup: `prune_orphans` looked only under one
prefix, so renaming it would leave every branch a killed run made under the old name in the owner's
`git branch` for good — the leak that function exists to close, reopened by a setting.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from chimera.api.config_api import is_editable, patch_config, read_config
from chimera.api.schemas import StorageCfgOut
from chimera.config import Settings, get_settings
from chimera.core.worktree import GitWorktree, known_branch_prefixes, prune_orphans


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(["init", "-q"], root)
    git(["config", "user.email", "t@example.com"], root)
    git(["config", "user.name", "t"], root)
    (root / "a.txt").write_text("hello\n", encoding="utf-8")
    git(["add", "--", "a.txt"], root)
    git(["commit", "-qm", "init"], root)
    return root


def attempts(root: Path) -> list[str]:
    out = git(["branch", "--list", "*/attempt-*", "--format=%(refname:short)"], root).stdout
    return sorted(b.strip() for b in out.splitlines() if b.strip())


def _prefix(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("CHIMERA_BRANCH_PREFIX", value)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _fresh_settings() -> object:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_the_default_is_what_it_always_was(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHIMERA_BRANCH_PREFIX", raising=False)
    wt = GitWorktree.create(repo)
    try:
        assert wt.branch.startswith("chimera/attempt-")
    finally:
        wt.remove()


def test_a_run_names_its_branch_with_the_owners_prefix(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _prefix(monkeypatch, "bot")
    wt = GitWorktree.create(repo)
    try:
        assert wt.branch.startswith("bot/attempt-")
        assert attempts(repo) == [wt.branch]
    finally:
        wt.remove()
    assert attempts(repo) == []


def test_a_branch_left_under_the_previous_prefix_is_still_collected(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two killed runs, one under each name, then the name changes again. Both are found."""
    left: list[str] = []
    for name in ("chimera", "bot"):
        _prefix(monkeypatch, name)
        wt = GitWorktree.create(repo)
        shutil.rmtree(wt.path)  # killed: nothing called remove()
        left.append(wt.branch)
    _prefix(monkeypatch, "zed")
    assert attempts(repo) == sorted(left), "precondition: both branches are still there"
    assert {"chimera", "bot", "zed"} <= set(known_branch_prefixes())

    removed = prune_orphans(repo)

    assert attempts(repo) == []
    assert removed["branches"] == 2


def test_a_branch_the_owner_made_that_only_looks_like_ours_is_left_alone(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only prefixes this home has made branches under are swept, never every `*/attempt-*`."""
    _prefix(monkeypatch, "bot")
    git(["branch", "feature/attempt-1"], repo)
    prune_orphans(repo)
    assert attempts(repo) == ["feature/attempt-1"]


@pytest.mark.parametrize(
    "bad",
    ["a/b", "has space", "-x", "dots.lock", "x" * 41, "é",
     # Windows device names, in any case: git there cannot make `refs/heads/aux/`.
     "aux", "NUL", "Con", "prn", "com1", "LPT9", "com0"],
)
def test_a_prefix_git_would_refuse_is_read_as_the_default(bad: str, tmp_path: Path) -> None:
    settings = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_BRANCH_PREFIX=bad)  # type: ignore[arg-type]
    assert settings.branch_prefix == "chimera"


@pytest.mark.parametrize("good", ["con-x", "auxx", "nul_2", "com10", "lpt", "team"])
def test_a_name_that_only_starts_like_a_device_is_an_ordinary_prefix(good: str, tmp_path: Path) -> None:
    assert Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_BRANCH_PREFIX=good).branch_prefix == good  # type: ignore[arg-type]


def test_an_empty_prefix_is_the_default(tmp_path: Path) -> None:
    assert Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_BRANCH_PREFIX="  ").branch_prefix == "chimera"  # type: ignore[arg-type]


def test_the_screen_reads_and_writes_it_and_refuses_a_bad_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert is_editable("CHIMERA_BRANCH_PREFIX")
    settings = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_BRANCH_PREFIX="team")  # type: ignore[arg-type]
    assert read_config(settings)["storage"]["branch_prefix"] == "team"
    assert StorageCfgOut().branch_prefix == "chimera", "a server without the field reads as the default"

    monkeypatch.setenv("CHIMERA_BRANCH_PREFIX", "chimera")  # own the name; patch_config writes it
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="CHIMERA_BRANCH_PREFIX"):
        patch_config({"CHIMERA_BRANCH_PREFIX": "a/b"}, env_path=env)
    # A device name passes the shape and fails every isolated run on Windows: refused here too, so
    # the screen never reports as saved a prefix that would be read as the default.
    for device in ("aux", "Nul", "COM1"):
        with pytest.raises(ValueError, match="Windows reserves"):
            patch_config({"CHIMERA_BRANCH_PREFIX": device}, env_path=env)
    assert env.read_text(encoding="utf-8") == "", "nothing was written"
    patch_config({"CHIMERA_BRANCH_PREFIX": "team"}, env_path=env)
    assert "CHIMERA_BRANCH_PREFIX=team" in env.read_text(encoding="utf-8")
