"""Tests for the read-only filesystem helpers behind the Code screen (tree + file viewer)."""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.api.fs_api import list_tree, read_file, write_file
from chimera.tools.workspace import PathEscapesWorkspaceError


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / ".git").mkdir()  # pruned
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# hello\n", encoding="utf-8")
    return tmp_path


def test_tree_lists_immediate_children_dirs_first_and_prunes_ignored(tmp_path: Path) -> None:
    tree = list_tree(_workspace(tmp_path), "")
    names = [e["name"] for e in tree["entries"]]
    assert ".git" not in names  # ignored dir pruned
    assert names == ["src", "README.md"]  # dirs first, then files, alphabetical
    src = next(e for e in tree["entries"] if e["name"] == "src")
    assert src["is_dir"] is True and src["path"] == "src"
    assert tree["capped"] is False


def test_tree_of_a_subdir_returns_its_children(tmp_path: Path) -> None:
    tree = list_tree(_workspace(tmp_path), "src")
    assert [e["path"] for e in tree["entries"]] == ["src/app.py"]


def test_tree_caps_at_max_entries(tmp_path: Path) -> None:
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    tree = list_tree(tmp_path, "", max_entries=4)
    assert len(tree["entries"]) == 4
    assert tree["capped"] is True


def test_read_file_returns_content_and_no_note(tmp_path: Path) -> None:
    out = read_file(_workspace(tmp_path), "src/app.py")
    assert out["content"] == "print('hi')\n"
    assert out["truncated"] is False and out["note"] == ""


def test_read_file_truncates_over_the_cap(tmp_path: Path) -> None:
    big = "a" * 25_000
    (tmp_path / "big.txt").write_text(big, encoding="utf-8")
    out = read_file(tmp_path, "big.txt")
    assert out["truncated"] is True
    assert len(out["content"]) == 20_000


def test_read_a_directory_returns_a_note_not_a_crash(tmp_path: Path) -> None:
    out = read_file(_workspace(tmp_path), "src")
    assert out["content"] == "" and out["note"] == "binary or non-text"


def test_read_a_binary_file_returns_a_note(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00\x01binary\x00")
    out = read_file(tmp_path, "blob.bin")
    assert out["content"] == "" and out["note"] == "binary or non-text"


def test_read_a_missing_file_returns_not_found(tmp_path: Path) -> None:
    out = read_file(tmp_path, "nope.txt")
    assert out["note"] == "not found"


def test_path_escape_raises_for_tree_and_file(tmp_path: Path) -> None:
    with pytest.raises(PathEscapesWorkspaceError):
        list_tree(tmp_path, "../..")
    with pytest.raises(PathEscapesWorkspaceError):
        read_file(tmp_path, "../secret.txt")


# --- write_file (editable viewer save) ------------------------------------------------------------


def test_write_creates_a_new_file_and_parent_dirs(tmp_path: Path) -> None:
    out = write_file(tmp_path, "pkg/sub/new.txt", "hello\nworld\n")
    assert out == {"path": "pkg/sub/new.txt", "bytes": 12}
    written = tmp_path / "pkg" / "sub" / "new.txt"
    assert written.read_bytes() == b"hello\nworld\n"  # new file gets plain \n


def test_write_preserves_crlf_on_an_existing_crlf_file(tmp_path: Path) -> None:
    # The editor loads content \n-normalized (as read_file returns it); saving it back must restore
    # the file's own CRLF convention, not flip untouched lines to the platform ending.
    target = tmp_path / "win.txt"
    target.write_bytes(b"a\r\nb\r\n")
    out = write_file(tmp_path, "win.txt", "a\nb\nc\n")
    assert target.read_bytes() == b"a\r\nb\r\nc\r\n"
    assert out["bytes"] == 9  # 3 lines × ("x" + "\r\n") == 9 bytes on disk


def test_write_rejects_a_path_escape(tmp_path: Path) -> None:
    with pytest.raises(PathEscapesWorkspaceError):
        write_file(tmp_path, "../evil.txt", "x")


def test_write_rejects_oversize_content(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_file(tmp_path, "big.txt", "a" * 50, max_bytes=10)
    assert not (tmp_path / "big.txt").exists()  # rejected before any write
