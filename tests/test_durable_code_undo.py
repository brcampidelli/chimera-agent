"""Durable Code undo acts only on changed files and survives rebuilding its store.

The in-memory undo offers expire with the API process and can only cover one turn. Persisting each
turn's changed-path before/after states enables a range revert without reverting unrelated edits.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from chimera.api.durable_undo import DurableUndo
from chimera.core.checkpoint import WorkspaceGuard


def _write(root: Path, name: str, value: str) -> None:
    (root / name).write_text(value, encoding="utf-8")


def _record(store: DurableUndo, guard: WorkspaceGuard, session: str, turn: str, root: Path,
            edits: dict[str, str | None]) -> None:
    before = guard.snapshot()
    for name, value in edits.items():
        path = root / name
        if value is None:
            path.unlink(missing_ok=True)
        else:
            _write(root, name, value)
    store.record(session, turn, root, guard.diff_since(before))


def test_revert_to_first_turn_restores_changed_files_and_survives_restart(tmp_path: Path) -> None:
    home, root = tmp_path / "home", tmp_path / "project"
    root.mkdir()
    _write(root, "existing.txt", "initial")
    store, guard = DurableUndo(home), WorkspaceGuard(root)
    _record(store, guard, "conversation", "turn-1", root, {"existing.txt": "one"})
    _record(store, guard, "conversation", "turn-2", root, {"existing.txt": "two", "created.txt": "new"})
    _record(store, guard, "conversation", "turn-3", root, {"existing.txt": "three"})

    # A newly constructed service models the process losing every in-memory object.
    restarted = DurableUndo(home)
    assert [row["turn_id"] for row in restarted.list_turns("conversation", root)] == ["turn-1", "turn-2", "turn-3"]
    result = restarted.revert_to("conversation", "turn-1", root)

    assert result["ok"] is True
    assert (root / "existing.txt").read_text(encoding="utf-8") == "one"
    assert not (root / "created.txt").exists()
    # Existing file is restored once for each later turn; the created path is removed once.
    assert result["restored"] == 3
    assert result["reverted_turns"] == ["turn-3", "turn-2"]  # in the order they were undone


def test_range_revert_preserves_external_edit_and_lists_it(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _write(root, "a.txt", "initial")
    store, guard = DurableUndo(tmp_path / "home"), WorkspaceGuard(root)
    _record(store, guard, "s", "1", root, {"a.txt": "one"})
    _record(store, guard, "s", "2", root, {"a.txt": "two"})
    _record(store, guard, "s", "3", root, {"a.txt": "three"})
    _write(root, "a.txt", "external")

    result = store.revert_to("s", "1", root)

    assert (root / "a.txt").read_text(encoding="utf-8") == "external"
    assert result["kept"] == ["a.txt"]
    assert result["restored"] == 0


def test_redo_restores_the_state_before_the_range_revert(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _write(root, "a.txt", "initial")
    store, guard = DurableUndo(tmp_path / "home"), WorkspaceGuard(root)
    _record(store, guard, "s", "1", root, {"a.txt": "one"})
    _record(store, guard, "s", "2", root, {"a.txt": "two"})

    assert store.revert_to("s", "1", root)["restored"] == 1
    assert (root / "a.txt").read_text(encoding="utf-8") == "one"
    assert DurableUndo(tmp_path / "home").redo("s", root)["restored"] == 1
    assert (root / "a.txt").read_text(encoding="utf-8") == "two"


def test_redo_preserves_files_edited_after_the_revert(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _write(root, "a.txt", "initial")
    store, guard = DurableUndo(tmp_path / "home"), WorkspaceGuard(root)
    _record(store, guard, "s", "1", root, {"a.txt": "one"})
    _record(store, guard, "s", "2", root, {"a.txt": "two"})

    store.revert_to("s", "1", root)
    _write(root, "a.txt", "external")
    result = store.redo("s", root)

    assert result["restored"] == 0
    assert result["kept"] == ["a.txt"]
    assert (root / "a.txt").read_text(encoding="utf-8") == "external"


def test_new_file_removed_and_prior_file_restored_without_changing_git_index(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _write(root, "existing.txt", "before")
    subprocess.run(["git", "-C", str(root), "add", "existing.txt"], check=True)
    before_index = subprocess.run(["git", "-C", str(root), "diff", "--cached", "--binary"],
                                  check=True, capture_output=True).stdout
    store, guard = DurableUndo(tmp_path / "home"), WorkspaceGuard(root)
    _record(store, guard, "s", "1", root, {"existing.txt": "first turn"})
    _record(store, guard, "s", "2", root, {"existing.txt": "after", "created.txt": "created"})

    result = store.revert_to("s", "1", root)

    assert result["restored"] == 2
    assert (root / "existing.txt").read_text(encoding="utf-8") == "first turn"
    assert not (root / "created.txt").exists()
    after_index = subprocess.run(["git", "-C", str(root), "diff", "--cached", "--binary"],
                                 check=True, capture_output=True).stdout
    assert before_index == after_index
    log = subprocess.run(["git", "-C", str(root), "log", "--oneline"], check=False, capture_output=True)
    assert log.returncode != 0 or not log.stdout.strip()


def test_new_turn_consumes_redo_even_when_it_has_no_file_changes(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _write(root, "a.txt", "initial")
    store, guard = DurableUndo(tmp_path / "home"), WorkspaceGuard(root)
    _record(store, guard, "s", "1", root, {"a.txt": "one"})
    _record(store, guard, "s", "2", root, {"a.txt": "two"})

    store.revert_to("s", "1", root)
    store.invalidate_redo("s", root)

    assert store.redo("s", root) == {"ok": False, "reason": "nothing to redo"}
    assert (root / "a.txt").read_text(encoding="utf-8") == "one"


def test_project_budget_trims_oldest_turn_and_reports_cutoff(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    store, guard = DurableUndo(tmp_path / "home", max_bytes=2600), WorkspaceGuard(root)
    _record(store, guard, "s", "oldest", root, {"a.txt": "a" * 700})
    _record(store, guard, "s", "middle", root, {"a.txt": "b" * 700})
    _record(store, guard, "s", "newest", root, {"a.txt": "c" * 700})

    assert store.cutoff(root) is not None
    assert len(store.list_turns("s", root)) < 3
    assert store.list_turns("s", root)[-1]["turn_id"] == "newest"


def test_forget_session_removes_redo_even_when_range_removed_all_turns(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _write(root, "a.txt", "initial")
    store, guard = DurableUndo(tmp_path / "home"), WorkspaceGuard(root)
    _record(store, guard, "s", "1", root, {"a.txt": "one"})
    _record(store, guard, "s", "2", root, {"a.txt": "two"})
    store.revert_to("s", "1", root)

    assert store.forget_session("s") == 1
    assert store.redo("s", root) == {"ok": False, "reason": "nothing to redo"}


def test_list_reports_paths_and_deleting_session_forgets_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    store, guard = DurableUndo(tmp_path / "home"), WorkspaceGuard(root)
    _record(store, guard, "s", "1", root, {"one.txt": "one"})
    assert store.list_turns("s", root)[0]["files"] == ["one.txt"]
    assert store.forget_session("s") == 1
    assert store.list_turns("s", root) == []
