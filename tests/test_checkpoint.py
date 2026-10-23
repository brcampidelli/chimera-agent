"""Tests for workspace snapshot/restore and verifiers."""

from __future__ import annotations

import subprocess
from pathlib import Path

from chimera.core.checkpoint import WorkspaceGuard
from chimera.core.verify import CommandVerifier, NullVerifier


def test_restore_reverts_changes_and_deletes_new(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("original", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    snap = guard.snapshot()

    (tmp_path / "a.txt").write_text("modified", encoding="utf-8")
    (tmp_path / "b.txt").write_text("new file", encoding="utf-8")

    changes = guard.restore(snap)
    assert changes == 2
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "original"
    assert not (tmp_path / "b.txt").exists()


def test_truncated_snapshot_does_not_delete_uncaptured_files(tmp_path: Path) -> None:
    # With more files than the cap, the snapshot is truncated. restore() must NOT delete the
    # uncaptured pre-existing files (they'd look "new") — that would be data loss.
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text(f"content {i}", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path, max_files=2)  # capture only 2 of the 5
    snap = guard.snapshot()
    assert len(snap.present) == 2

    guard.restore(snap)
    survivors = sorted(p.name for p in tmp_path.glob("*.txt"))
    assert survivors == ["f0.txt", "f1.txt", "f2.txt", "f3.txt", "f4.txt"]  # none deleted


def test_repo_root_workspace_is_never_delete_pruned(tmp_path: Path) -> None:
    # If the workspace is a real git repo root, restore() must NOT run the delete-new pass — even for
    # files genuinely absent from the snapshot. verify-or-revert's cleanup is for throwaway task
    # workspaces; pointing it at a repo (a path/config bug did, 2026-07-17) would let a revert wipe the
    # user's tracked and untracked files. A `.git` at the root is the signal.
    (tmp_path / ".git").mkdir()  # marks tmp_path as a repository root
    (tmp_path / "tracked.py").write_text("committed = True", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    snap = guard.snapshot()  # snapshot does NOT contain a file that will appear later

    (tmp_path / "appeared_after.py").write_text("x = 1", encoding="utf-8")

    guard.restore(snap)
    # The would-be-"new" file survives: the delete pass was skipped because this is a repo root.
    assert (tmp_path / "appeared_after.py").exists()
    assert (tmp_path / "tracked.py").exists()


def test_repo_subdirectory_is_also_guarded(tmp_path: Path) -> None:
    # REGRESSION (adversarial review 2026-07-18): the guard checked only for `.git` at the workspace
    # ROOT, so a workspace pointed at a SUBDIR of a repo (.git at an ancestor) still ran the delete
    # pass and wiped tracked files. The guard must scan ancestors, not just the immediate directory.
    (tmp_path / ".git").mkdir()  # repo root
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    (sub / "tracked.py").write_text("committed = True", encoding="utf-8")
    guard = WorkspaceGuard(sub)  # workspace is a subdir, not the repo root
    snap = guard.snapshot()

    (sub / "appeared_after.py").write_text("x = 1", encoding="utf-8")

    guard.restore(snap)
    assert (sub / "appeared_after.py").exists()  # delete pass skipped — inside a repo
    assert (sub / "tracked.py").exists()


def test_non_repo_workspace_still_prunes_new_files(tmp_path: Path) -> None:
    # The guard above must NOT weaken the normal case: without a `.git` root, the delete-new pass still
    # cleans up files the agent created (that is verify-or-revert doing its job in a scratch workspace).
    (tmp_path / "keep.py").write_text("k = 1", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    snap = guard.snapshot()

    (tmp_path / "created_by_agent.py").write_text("junk = 1", encoding="utf-8")

    guard.restore(snap)
    assert not (tmp_path / "created_by_agent.py").exists()  # pruned, as before
    assert (tmp_path / "keep.py").exists()


def test_restore_recreates_deleted_file(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("content", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    snap = guard.snapshot()

    (tmp_path / "keep.txt").unlink()
    guard.restore(snap)
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "content"


def test_restore_noop_when_unchanged(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    snap = guard.snapshot()
    assert guard.restore(snap) == 0


def test_ignored_dirs_are_left_alone(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    snap = guard.snapshot()
    # a file created in an ignored dir is not deleted by restore
    (tmp_path / ".git" / "extra").write_text("y", encoding="utf-8")
    guard.restore(snap)
    assert (tmp_path / ".git" / "extra").exists()


def test_command_verifier_pass_and_fail(tmp_path: Path) -> None:
    assert CommandVerifier("exit 0", tmp_path, source="user").verify().passed is True
    failed = CommandVerifier("exit 1", tmp_path, source="user").verify()
    assert failed.passed is False


def test_null_verifier_passes() -> None:
    assert NullVerifier().verify().passed is True


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _commit_workspace(root: Path) -> None:
    _git("init", "-q", cwd=root)
    _git("config", "user.email", "tests@example.com", cwd=root)
    _git("config", "user.name", "Tests", cwd=root)
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "initial", cwd=root)

def test_subdirectory_workspace_diff_finds_new_and_nested_same_name(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    workspace = root / "proj"
    (workspace / "sub").mkdir(parents=True)
    (workspace / "a.py").write_text("a before\n", encoding="utf-8")
    (workspace / "b.py").write_text("b before\n", encoding="utf-8")
    (workspace / "sub" / "c.py").write_text("c before\n", encoding="utf-8")
    _commit_workspace(root)
    guard = WorkspaceGuard(workspace)
    before = guard.snapshot()
    (workspace / "new.py").write_text("new\n", encoding="utf-8")
    (workspace / "a.py").write_text("a after\n", encoding="utf-8")
    (workspace / "b.py").unlink()
    _git("mv", "sub/c.py", "sub/d.py", cwd=workspace)

    change = guard.diff_since(before)
    assert set(change.paths) == {"a.py", "b.py", "new.py", "sub/c.py", "sub/d.py"}
    guard.restore(before)
    assert (workspace / "a.py").read_text(encoding="utf-8") == "a before\n"
    assert (workspace / "b.py").read_text(encoding="utf-8") == "b before\n"
    assert (workspace / "sub" / "c.py").read_text(encoding="utf-8") == "c before\n"
    assert (workspace / "new.py").exists()
    assert (workspace / "sub" / "d.py").exists()

def test_workspace_dirty_paths_are_relative_and_preserve_user_edits(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    workspace = root / "proj"
    workspace.mkdir(parents=True)
    for name in ("f0.py", "f1.py", "g.py"):
        (workspace / name).write_text(f"HEAD {name}\n", encoding="utf-8")
    _commit_workspace(root)

    guard = WorkspaceGuard(workspace)
    (workspace / "f1.py").write_text("user edit\n", encoding="utf-8")
    (workspace / "g.py").unlink()
    before = guard.snapshot()
    (workspace / "f0.py").write_text("agent edit\n", encoding="utf-8")

    change = guard.diff_since(before)
    assert change.paths == ["f0.py"]
    assert change.before.git_dirty == {"f0.py"}
    guard.restore(before)
    assert (workspace / "f1.py").read_text(encoding="utf-8") == "user edit\n"
    assert not (workspace / "g.py").exists()
def test_nested_workspace_with_same_prefix_name_restores_both_blobs(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    workspace = root / "src"
    (workspace / "src").mkdir(parents=True)
    (workspace / "x.py").write_text("outer before\n", encoding="utf-8")
    (workspace / "src" / "x.py").write_text("inner before\n", encoding="utf-8")
    _commit_workspace(root)

    guard = WorkspaceGuard(workspace)
    before = guard.snapshot()
    (workspace / "x.py").write_text("outer agent\n", encoding="utf-8")
    (workspace / "src" / "x.py").write_text("inner agent\n", encoding="utf-8")

    assert set(guard.diff_since(before).paths) == {"x.py", "src/x.py"}
    guard.restore(before)
    assert (workspace / "x.py").read_text(encoding="utf-8") == "outer before\n"
    assert (workspace / "src" / "x.py").read_text(encoding="utf-8") == "inner before\n"


def test_new_untracked_git_file_survives_restore(tmp_path: Path) -> None:
    (tmp_path / "base.py").write_text("base\n", encoding="utf-8")
    _commit_workspace(tmp_path)
    guard = WorkspaceGuard(tmp_path)
    before = guard.snapshot()
    (tmp_path / "new.py").write_text("new\n", encoding="utf-8")

    guard.restore(before)
    assert (tmp_path / "new.py").exists()

def test_checkout_branch_marks_snapshot_incomplete_with_reason(tmp_path: Path) -> None:
    (tmp_path / "base.py").write_text("base\n", encoding="utf-8")
    _commit_workspace(tmp_path)
    guard = WorkspaceGuard(tmp_path)
    before = guard.snapshot()
    original_head = before.git_head
    _git("checkout", "--orphan", "other", cwd=tmp_path)
    (tmp_path / "other.py").write_text("other\n", encoding="utf-8")
    _git("add", "other.py", cwd=tmp_path)
    _git("commit", "-m", "other", cwd=tmp_path)
    current_head = guard._git_head()

    guard.restore(before)
    assert before.incomplete
    assert getattr(before, "incomplete_reason", "") == (
        f"HEAD mudou de {original_head} para {current_head} durante o turno"
    )
    assert guard._git_head() == current_head

def test_clean_source_with_literal_backslash_crlf_is_unchanged(tmp_path: Path) -> None:
    (tmp_path / "literal.py").write_text('value = "\\\\r\\\\n"\n', encoding="utf-8")
    _commit_workspace(tmp_path)
    guard = WorkspaceGuard(tmp_path)
    before = guard.snapshot()
    original = (tmp_path / "literal.py").read_bytes()
    assert guard.restore(before) == 0
    assert (tmp_path / "literal.py").read_bytes() == original


def test_checkout_filters_are_preserved_when_restoring(tmp_path: Path) -> None:
    (tmp_path / ".gitattributes").write_text("* text eol=crlf\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("before\n", encoding="utf-8", newline="")
    _commit_workspace(tmp_path)
    guard = WorkspaceGuard(tmp_path)
    before = guard.snapshot()
    (tmp_path / "a.py").write_bytes(b"after\n")

    guard.restore(before)
    assert (tmp_path / "a.py").read_bytes() == b"before\r\n"

def test_diff_since_keeps_only_changed_paths_from_dirty_baseline(tmp_path: Path) -> None:
    (tmp_path / "changed.py").write_text("before changed\n", encoding="utf-8")
    (tmp_path / "untouched.py").write_text("user baseline\n", encoding="utf-8")
    _commit_workspace(tmp_path)
    guard = WorkspaceGuard(tmp_path)
    (tmp_path / "untouched.py").write_text("dirty user state\n", encoding="utf-8")
    before = guard.snapshot()
    (tmp_path / "changed.py").write_text("agent state\n", encoding="utf-8")

    change = guard.diff_since(before)
    assert change.paths == ["changed.py"]
    assert change.before.git_dirty == {"changed.py"}
    assert set(change.before.files) == {"changed.py"}

def test_bulk_checkout_falls_back_to_git_filters_for_complex_eol_cases(tmp_path: Path) -> None:
    import pytest

    from chimera.core import checkpoint

    _git("init", "-q", cwd=tmp_path)
    _git("config", "user.email", "tests@example.com", cwd=tmp_path)
    _git("config", "user.name", "Tests", cwd=tmp_path)
    _git("config", "core.autocrlf", "true", cwd=tmp_path)
    (tmp_path / "plain.txt").write_bytes(b"line1\nline2\n")
    (tmp_path / "text.txt").write_bytes(b"line1\nline2\n")
    (tmp_path / ".gitattributes").write_text("text.txt text\n", encoding="utf-8")
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-m", "initial", cwd=tmp_path)
    guard = WorkspaceGuard(tmp_path)
    head = guard._git_head()
    assert head is not None
    expected = b"line1\r\nline2\r\n"
    original = checkpoint.subprocess.run
    filter_calls: list[list[str]] = []

    def count(*args, **kwargs):
        command = args[0]
        if "cat-file" in command and "--filters" in command:
            filter_calls.append(command)
        return original(*args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(checkpoint.subprocess, "run", count)
    try:
        states = guard._head_states_bulk(head, {"plain.txt", "text.txt"})
    finally:
        monkeypatch.undo()
    assert states["plain.txt"] == (True, expected.decode())
    assert len(filter_calls) == 1
    assert filter_calls[0][-1].endswith(":./text.txt")


def test_head_commit_mid_turn_is_diffed_and_restored(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("before\n", encoding="utf-8")
    _commit_workspace(tmp_path)
    guard = WorkspaceGuard(tmp_path)
    before = guard.snapshot()
    (tmp_path / "a.py").write_text("committed mid-turn\n", encoding="utf-8")
    _git("add", "a.py", cwd=tmp_path)
    _git("commit", "-m", "mid turn", cwd=tmp_path)

    change = guard.diff_since(before)
    assert change.paths == ["a.py"]
    assert not change.truncated
    assert guard.restore(before) == 1
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "before\n"


def test_git_fingerprint_does_not_read_clean_file_contents(tmp_path: Path, monkeypatch) -> None:
    from chimera.core import checkpoint

    (tmp_path / "clean.py").write_text("clean\n", encoding="utf-8")
    _commit_workspace(tmp_path)
    snapshot = WorkspaceGuard(tmp_path).snapshot()
    monkeypatch.setattr(snapshot.files, "_reader", lambda *_: (_ for _ in ()).throw(AssertionError("read clean file")))

    checkpoint.fingerprint(snapshot)


def test_lazy_git_file_mapping_uses_few_processes_for_many_files(tmp_path: Path, monkeypatch) -> None:
    from chimera.core import checkpoint

    for i in range(3000):
        (tmp_path / f"f{i}.py").write_text(f"{i}\n", encoding="utf-8")
    _commit_workspace(tmp_path)
    original = checkpoint.subprocess.run
    cat_calls: list[list[str]] = []

    def count(*args, **kwargs):
        command = args[0]
        if "cat-file" in command:
            cat_calls.append(command)
        return original(*args, **kwargs)

    monkeypatch.setattr(checkpoint.subprocess, "run", count)
    guard = WorkspaceGuard(tmp_path)
    snapshot = guard.snapshot()
    assert cat_calls == []
    (tmp_path / "f1.py").write_text("changed one\n", encoding="utf-8")
    (tmp_path / "f2.py").write_text("changed two\n", encoding="utf-8")
    change = guard.diff_since(snapshot)
    assert set(change.paths) == {"f1.py", "f2.py"}
    assert sum("cat-file" in call for call in cat_calls) <= 2


def test_git_snapshot_diff_reads_only_changed_paths_with_bounded_git_processes(
    tmp_path: Path, monkeypatch
) -> None:
    from chimera.core import checkpoint

    for i in range(2000):
        (tmp_path / f"f{i}.py").write_text(f"before {i}\n", encoding="utf-8")
    _commit_workspace(tmp_path)

    original = checkpoint.subprocess.run
    git_calls = 0

    def count(*args, **kwargs):
        nonlocal git_calls
        if args and args[0] and args[0][0] == "git":
            git_calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(checkpoint.subprocess, "run", count)
    guard = WorkspaceGuard(tmp_path)
    passed = guard.snapshot()
    git_calls = 0  # the final capture plus the pass gate are the measured production path
    (tmp_path / "f1.py").write_text("changed one\n", encoding="utf-8")
    (tmp_path / "f2.py").write_text("changed two\n", encoding="utf-8")
    final = guard.snapshot()

    assert checkpoint.snapshot_changed_paths(passed, final) == {"f1.py", "f2.py"}
    assert git_calls <= 20


def test_lazy_snapshot_mapping_warns_once_after_individual_read_limit(monkeypatch) -> None:
    from chimera.core import checkpoint

    warnings: list[str] = []
    monkeypatch.setattr(checkpoint._log, "warning", lambda message, *args: warnings.append(message % args))
    files = checkpoint._SnapshotFiles(
        {f"f{i}" for i in range(checkpoint._SNAPSHOT_READ_WARNING_LIMIT + 2)},
        lambda rel: rel,
    )

    for rel in files:
        assert files[rel] == rel

    assert len(warnings) == 1
    assert "individual reads" in warnings[0]
    assert "test_lazy_snapshot_mapping_warns_once" in warnings[0]


def test_diff_since_500_dirty_files_uses_bounded_git_processes(tmp_path: Path, monkeypatch) -> None:
    from chimera.core import checkpoint

    for i in range(500):
        (tmp_path / f"f{i}.py").write_text(f"before {i}\n", encoding="utf-8")
    _commit_workspace(tmp_path)
    guard = WorkspaceGuard(tmp_path)
    before = guard.snapshot()
    for i in range(500):
        (tmp_path / f"f{i}.py").write_text(f"after {i}\n", encoding="utf-8")

    original = checkpoint.subprocess.run
    git_calls = 0

    def count(*args, **kwargs):
        nonlocal git_calls
        if args and args[0] and args[0][0] == "git":
            git_calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(checkpoint.subprocess, "run", count)
    change = guard.diff_since(before)
    assert len(change.paths) == 500
    assert git_calls <= 15


def test_core_target_validation_is_available_without_api_dependency() -> None:
    import importlib.util

    assert importlib.util.find_spec("chimera.core.target_path") is not None

def test_checkpoint_module_owns_safe_target_without_core_to_api_import() -> None:
    import inspect

    import chimera.core.checkpoint as checkpoint

    assert "chimera.api" not in inspect.getsource(checkpoint._safe_target)


def test_staged_rename_paths_include_old_and_new_and_restore_old_file(tmp_path: Path) -> None:
    (tmp_path / "c.py").write_text("start\n", encoding="utf-8")
    _commit_workspace(tmp_path)
    guard = WorkspaceGuard(tmp_path)
    before = guard.snapshot()
    _git("mv", "c.py", "d.py", cwd=tmp_path)

    assert set(guard._paths_since_head(before.git_head or "")) >= {"c.py", "d.py"}
    guard.restore(before)
    assert (tmp_path / "c.py").read_text(encoding="utf-8") == "start\n"


def test_restore_change_leaves_new_git_files_and_reports_them(tmp_path: Path) -> None:
    (tmp_path / "base.py").write_text("base\n", encoding="utf-8")
    _commit_workspace(tmp_path)
    guard = WorkspaceGuard(tmp_path)
    before = guard.snapshot()
    (tmp_path / "new.py").write_text("new\n", encoding="utf-8")

    receipt = guard.restore_change(guard.diff_since(before))
    assert receipt.left_new == ["new.py"]
    assert (tmp_path / "new.py").exists()


def test_restore_change_keeps_new_file_in_unborn_git_repository(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "staged.py").write_text("staged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "staged.py"], check=True)
    before_index = subprocess.run(
        ["git", "-C", str(tmp_path), "diff", "--cached", "--binary"],
        check=True,
        capture_output=True,
    ).stdout
    guard = WorkspaceGuard(tmp_path)
    before = guard.snapshot()
    (tmp_path / "new.py").write_text("new\n", encoding="utf-8")

    receipt = guard.restore_change(guard.diff_since(before))

    assert receipt.restored == 0
    assert receipt.left_new == ["new.py"]
    assert (tmp_path / "new.py").exists()
    assert subprocess.run(
        ["git", "-C", str(tmp_path), "diff", "--cached", "--binary"],
        check=True,
        capture_output=True,
    ).stdout == before_index
