"""The Storage card's prune (study 29, P5.3) — and the rule that outranks the disk it frees.

A worktree is a full checkout. Killed runs leave theirs behind in the temp folder, on the drive that
has already filled once on this machine, and nothing collected a worktree git still knew about. The
card's prune does — and a prune that removed the worktree of a run still working would destroy that
run's uncommitted edits, which is worse than any amount of disk. So the decision is three-way:

* ``live`` — this process made it, or the process that made it is running, or it is too new to tell;
* ``orphan`` — git no longer knows it, or the process that made it is gone;
* ``kept`` — nobody recorded who made it (an older version did), so it is not called dead on a guess.

Only ``orphan`` is ever removed. Every test here drives real git and real processes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

import chimera.core.worktree as wt
from chimera.config import get_settings
from chimera.core.worktree import (
    OWNER_FILE,
    GitWorktree,
    classify_worktree_dir,
    live_worktree_paths,
    prune_orphans,
    prune_worktree_dirs,
    worktree_parent,
)

pytest.importorskip("psutil")

HOUR_AND_A_BIT = 3 * 3600


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    git(["init", "-q"], root)
    git(["config", "user.email", "t@example.com"], root)
    git(["config", "user.name", "t"], root)
    (root / "a.txt").write_text("hello\n", encoding="utf-8")
    git(["add", "-A"], root)
    git(["commit", "-qm", "init"], root)
    return root


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Temp is the test's own: a prune that scanned the real temp folder would collect this
    machine's leftovers as a side effect of running the suite."""
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("CHIMERA_WORKTREE_DIR", "")
    monkeypatch.setattr(wt, "_live_here", set())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "repo")


def age(path: Path) -> None:
    """Make a directory look like it was made hours ago, past the creation-window gate."""
    old = time.time() - HOUR_AND_A_BIT
    os.utime(path, (old, old))


def as_if_another_process_made_it(tree: GitWorktree, pid: int, **record: object) -> None:
    """This process forgets the worktree, and its owner record names someone else — written the way
    `_write_owner` writes it for that process, with ``record`` overriding any field."""
    wt._live_here.discard(tree.path.resolve())
    admin = wt._admin_dir(tree.path)
    assert admin is not None
    owner = {"pid": pid, "created": time.time(), **wt.process_identity(pid), **record}
    (admin / OWNER_FILE).write_text(json.dumps(owner), encoding="utf-8")
    age(tree.path)


def sleeper() -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])


def test_a_run_in_this_process_is_never_collected_however_old(repo: Path) -> None:
    tree = GitWorktree.create(repo)
    try:
        age(tree.path)
        assert classify_worktree_dir(tree.path).reason == "this_process"
        result = prune_worktree_dirs()
        assert result["removed"] == 0 and result["live"] == 1
        assert (tree.path / "a.txt").read_text(encoding="utf-8") == "hello\n"
    finally:
        tree.remove()


def test_a_run_in_another_process_is_left_while_that_process_lives_and_collected_after(
    repo: Path,
) -> None:
    """The case the card exists for, both halves. While the maker runs, its worktree is live at any
    age; once it is gone, the worktree, its git registration and its attempt branch all go."""
    tree = GitWorktree.create(repo)
    (tree.path / "work-in-progress.txt").write_text("unsaved\n", encoding="utf-8")
    child = sleeper()
    try:
        as_if_another_process_made_it(tree, child.pid)
        assert classify_worktree_dir(tree.path).state == "live"
        assert prune_worktree_dirs()["removed"] == 0
        assert (tree.path / "work-in-progress.txt").exists(), "a live run's edits were deleted"
    finally:
        child.kill()
        child.wait(30)

    state = classify_worktree_dir(tree.path)
    assert (state.state, state.reason) == ("orphan", "owner_gone")
    result = prune_worktree_dirs()
    assert result["removed"] == 1 and result["bytes_freed"] > 0
    assert not tree.path.exists()
    assert tree.path.resolve() not in live_worktree_paths(repo)
    assert tree.branch not in git(["branch", "--list"], repo).stdout


def test_a_reused_pid_does_not_keep_a_dead_run_alive(repo: Path) -> None:
    """The pid is running — but it runs another program, started at another time: not the maker."""
    tree = GitWorktree.create(repo)
    as_if_another_process_made_it(
        tree,
        os.getpid(),
        started=time.time() - 10 * 24 * 3600,
        exe=str(Path(tempfile.gettempdir()) / "some-other-program.exe"),
        cmdline=["some-other-program", "--serve"],
    )
    assert classify_worktree_dir(tree.path).reason == "owner_gone"


@pytest.mark.parametrize("skew", [-5.0, 5.0])
def test_a_clock_that_jumped_does_not_make_a_live_maker_look_dead(
    skew: float, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding, reproduced: the maker's start time (psutil) was compared with the WALL CLOCK
    when the worktree was made, within one second. A wall clock stepped back five seconds made a live
    run read as a reused pid, and the prune deleted its edits with the run still going.

    Both shapes of the jump: the record's wall-clock time off by five seconds, and the OS start time
    itself shifted (Linux/WSL compute it from boot time, which moves with the clock). The maker is
    alive and runs the same program; the worktree may be live or kept, never collected."""
    import psutil

    tree = GitWorktree.create(repo)
    (tree.path / "wip.txt").write_text("unsaved\n", encoding="utf-8")
    child = sleeper()
    try:
        as_if_another_process_made_it(tree, child.pid, created=time.time() - skew)
        real = psutil.Process.create_time
        monkeypatch.setattr(psutil.Process, "create_time", lambda self: real(self) + skew)

        state = classify_worktree_dir(tree.path)
        assert state.state in ("live", "kept"), state
        assert prune_worktree_dirs()["removed"] == 0
        assert (tree.path / "wip.txt").exists(), "a live run's edits were deleted"
    finally:
        child.kill()
        child.wait(30)
        shutil.rmtree(tree.path, ignore_errors=True)


def test_the_same_program_with_a_disagreeing_start_time_is_kept_not_guessed_dead(repo: Path) -> None:
    """A clock jump and a pid reused by another copy of the same program look the same from here.
    Nothing says which, so the worktree is kept — the cost is disk, the other cost is a run's work."""
    tree = GitWorktree.create(repo)
    as_if_another_process_made_it(tree, os.getpid(), started=time.time() - 10 * 24 * 3600)
    state = classify_worktree_dir(tree.path)
    assert (state.state, state.reason) == ("kept", "owner_uncertain")


def test_the_owner_record_carries_the_os_start_time_of_its_maker(repo: Path) -> None:
    import psutil

    tree = GitWorktree.create(repo)
    try:
        admin = wt._admin_dir(tree.path)
        assert admin is not None
        owner = json.loads((admin / OWNER_FILE).read_text(encoding="utf-8"))
        assert owner["pid"] == os.getpid()
        assert owner["started"] == psutil.Process(os.getpid()).create_time()
        assert owner["exe"] and owner["cmdline"]
    finally:
        tree.remove()


def test_a_worktree_with_no_owner_record_is_kept_not_guessed_dead(repo: Path) -> None:
    """Made by a version that recorded nothing. Its maker may be running; the card says so and
    leaves it, rather than trade someone's work for disk."""
    tree = GitWorktree.create(repo)
    wt._live_here.discard(tree.path.resolve())
    admin = wt._admin_dir(tree.path)
    assert admin is not None
    (admin / OWNER_FILE).unlink()
    age(tree.path)

    state = classify_worktree_dir(tree.path)
    assert (state.state, state.reason) == ("kept", "no_owner")
    result = prune_worktree_dirs()
    assert result["removed"] == 0 and result["kept"] == 1
    assert tree.path.exists()


def test_a_folder_git_no_longer_knows_is_collected_when_old_and_left_when_new(tmp_path: Path) -> None:
    old = Path(tempfile.mkdtemp(prefix="chimera-wt-"))
    (old / "leftover.txt").write_text("x" * 100, encoding="utf-8")
    age(old)
    new = Path(tempfile.mkdtemp(prefix="chimera-wt-"))

    result = prune_worktree_dirs()

    assert not old.exists() and result["removed"] == 1 and result["bytes_freed"] == 100
    assert new.exists(), "a folder inside the creation window belongs to a run that is starting"


def test_nothing_without_our_prefix_is_touched(tmp_path: Path) -> None:
    other = Path(tempfile.mkdtemp(prefix="someone-else-"))
    age(other)
    prune_worktree_dirs()
    assert other.exists()


def test_the_first_run_in_one_repository_leaves_a_long_run_in_another_alone(tmp_path: Path) -> None:
    """The defect this item found in the existing boot-time prune.

    Every worktree lives in one shared temp folder, and `prune_orphans(A)` kept only the folders in
    A's own worktree list — so starting a run in repository A deleted the worktree of a run that had
    been working in repository B for more than an hour.
    """
    repo_a = make_repo(tmp_path / "a")
    repo_b = make_repo(tmp_path / "b")
    tree_b = GitWorktree.create(repo_b)
    child = sleeper()
    try:
        (tree_b.path / "edit.txt").write_text("in progress\n", encoding="utf-8")
        # Aged last: a write updates the folder's time, and a fresh folder is protected by the
        # creation window rather than by the rule this test is about.
        as_if_another_process_made_it(tree_b, child.pid)

        prune_orphans(repo_a)

        assert (tree_b.path / "edit.txt").exists(), "repository A's prune deleted B's live worktree"
        assert tree_b.path.resolve() in live_worktree_paths(repo_b)
    finally:
        child.kill()
        child.wait(30)
        shutil.rmtree(tree_b.path, ignore_errors=True)


def test_the_configured_folder_is_where_worktrees_go_and_where_the_prune_looks(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "other-drive" / "worktrees"
    monkeypatch.setenv("CHIMERA_WORKTREE_DIR", str(target))
    get_settings.cache_clear()

    tree = GitWorktree.create(repo)
    try:
        assert tree.path.parent == target.resolve() or tree.path.parent == target
        assert tree.path.name.startswith("chimera-wt-")
        assert tree.path in wt.find_worktree_dirs()
    finally:
        tree.remove()


@pytest.mark.parametrize("value", ["relative/folder", "inside"])
def test_a_folder_that_breaks_the_rules_falls_back_to_temp(
    value: str, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Relative resolves against wherever the backend started; inside the project, the project's own
    status and search would read the run's checkout. Both fall back to temp, with a warning."""
    configured = str(repo / "wts") if value == "inside" else value
    monkeypatch.setenv("CHIMERA_WORKTREE_DIR", configured)
    get_settings.cache_clear()
    assert worktree_parent(repo) == Path(tempfile.gettempdir())


def test_empty_is_the_temp_folder_as_before(repo: Path) -> None:
    assert worktree_parent(repo) == Path(tempfile.gettempdir())


def test_the_reported_worktree_folder_is_where_the_next_worktree_actually_goes(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding: with the folder set inside the project, the card said "the next worktree goes
    to <project>/wts" while `create` used temp — the report asked without the project, the run with
    it. Both now ask with it."""
    from chimera.core.storage import measure

    monkeypatch.setenv("CHIMERA_WORKTREE_DIR", str(repo / "wts"))
    get_settings.cache_clear()

    reported = Path(measure(tmp_path / "home", repo)["worktree_dir"])
    tree = GitWorktree.create(repo)
    try:
        assert tree.path.parent == reported == Path(tempfile.gettempdir())
    finally:
        tree.remove()


def test_asking_where_worktrees_go_creates_no_folder(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding: every read — the storage route, diagnostics, `chimera doctor`, the boot-time
    prune — made the configured folder, inside the owner's project when that is where it pointed.
    Only a worktree being made may create it."""
    from chimera.core.storage import measure

    inside = repo / "wts"
    elsewhere = tmp_path / "elsewhere" / "wts"
    for configured in (inside, elsewhere):
        monkeypatch.setenv("CHIMERA_WORKTREE_DIR", str(configured))
        get_settings.cache_clear()
        measure(tmp_path / "home", repo)
        measure(tmp_path / "home")
        wt.find_worktree_dirs()
        prune_orphans(repo)
        assert not configured.exists(), f"a read created {configured}"

    tree = GitWorktree.create(repo)
    try:
        assert elsewhere.is_dir() and tree.path.parent in (elsewhere, elsewhere.resolve())
    finally:
        tree.remove()


def test_a_git_file_that_cannot_be_read_for_a_moment_keeps_the_worktree(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding: an antivirus or indexer holding the `.git` file for a moment (a sharing
    violation on Windows) made the read fail, the failure read as "no `.git` file", and an hour-old
    checkout of a live run was classified unregistered and removed. Unreadable is not absent."""
    tree = GitWorktree.create(repo)
    (tree.path / "wip.txt").write_text("unsaved\n", encoding="utf-8")
    wt._live_here.discard(tree.path.resolve())
    age(tree.path)
    real = Path.read_text

    def held_by_another_process(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == ".git":
            raise PermissionError(13, "The process cannot access the file", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", held_by_another_process)
    try:
        state = classify_worktree_dir(tree.path)
        assert (state.state, state.reason) == ("kept", "unreadable")
        assert prune_worktree_dirs()["removed"] == 0
        assert (tree.path / "wip.txt").exists(), "a checkout was removed over a read error"
    finally:
        monkeypatch.undo()
        shutil.rmtree(tree.path, ignore_errors=True)


def test_a_worktree_whose_repository_cannot_be_reached_is_kept(tmp_path: Path) -> None:
    """Its `.git` points into a git directory that is not there right now — a drive unmounted for a
    moment looks exactly like this. Nothing says git forgot it, so it is not collected."""
    folder = Path(tempfile.mkdtemp(prefix="chimera-wt-"))
    gone = tmp_path / "unmounted-drive" / "repo" / ".git" / "worktrees" / folder.name
    (folder / ".git").write_text(f"gitdir: {gone}\n", encoding="utf-8")
    age(folder)

    state = classify_worktree_dir(folder)
    assert (state.state, state.reason) == ("kept", "unreachable")
    assert prune_worktree_dirs()["removed"] == 0 and folder.exists()


def test_a_worktree_git_has_forgotten_is_still_collected(repo: Path) -> None:
    """The repository is there and its admin entry is not: git pruned it. That is an answer."""
    tree = GitWorktree.create(repo)
    wt._live_here.discard(tree.path.resolve())
    admin = wt._admin_dir(tree.path)
    assert admin is not None
    shutil.rmtree(admin)
    age(tree.path)

    state = classify_worktree_dir(tree.path)
    assert (state.state, state.reason) == ("orphan", "unregistered")
    assert prune_worktree_dirs()["removed"] == 1 and not tree.path.exists()
