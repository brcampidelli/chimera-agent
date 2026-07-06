"""Tests for the surgical edit tools (M13 A1) — exact-match, unique-anchor, atomic patch."""

from __future__ import annotations

from pathlib import Path

from chimera.tools.edit import ApplyPatchTool, EditFileTool, _parse_hunks


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# --- edit_file --------------------------------------------------------------------------


def test_edit_replaces_unique_match(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "x = 1\ny = 2\n")
    out = EditFileTool(tmp_path).run(path="a.py", old="y = 2", new="y = 3")
    assert "edited" in out
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 1\ny = 3\n"


def test_edit_missing_match_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "x = 1\n")
    out = EditFileTool(tmp_path).run(path="a.py", old="nope", new="!")
    assert out.startswith("error:") and "not found" in out
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 1\n"  # untouched


def test_edit_ambiguous_match_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "v = 0\nv = 0\n")
    out = EditFileTool(tmp_path).run(path="a.py", old="v = 0", new="v = 1")
    assert out.startswith("error:") and "appears 2 times" in out
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "v = 0\nv = 0\n"  # untouched


def test_edit_replace_all(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "v = 0\nv = 0\n")
    out = EditFileTool(tmp_path).run(path="a.py", old="v = 0", new="v = 1", replace_all=True)
    assert "2 occurrences" in out
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "v = 1\nv = 1\n"


def test_edit_preserves_indentation(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "def f():\n    return 1\n")
    EditFileTool(tmp_path).run(path="a.py", old="    return 1", new="    return 2")
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "def f():\n    return 2\n"


def test_edit_empty_old_and_noop_are_refused(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "x = 1\n")
    assert EditFileTool(tmp_path).run(path="a.py", old="", new="y").startswith("error:")
    assert EditFileTool(tmp_path).run(path="a.py", old="x = 1", new="x = 1").startswith("error:")


def test_edit_missing_file(tmp_path: Path) -> None:
    assert EditFileTool(tmp_path).run(path="ghost.py", old="a", new="b").startswith("error:")


# --- apply_patch ------------------------------------------------------------------------


_PATCH = """<<<<<<< SEARCH
a = 1
=======
a = 10
>>>>>>> REPLACE
<<<<<<< SEARCH
c = 3
=======
c = 30
>>>>>>> REPLACE"""


def test_apply_patch_multiple_hunks(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "a = 1\nb = 2\nc = 3\n")
    out = ApplyPatchTool(tmp_path).run(path="m.py", patch=_PATCH)
    assert "applied 2 hunk(s)" in out
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == "a = 10\nb = 2\nc = 30\n"


def test_apply_patch_is_atomic_on_failure(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "a = 1\nb = 2\n")  # second hunk (c = 3) won't anchor
    out = ApplyPatchTool(tmp_path).run(path="m.py", patch=_PATCH)
    assert out.startswith("error:") and "hunk 2" in out
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == "a = 1\nb = 2\n"  # untouched


def test_apply_patch_ambiguous_hunk_refused(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "x\nx\n")
    patch = "<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE"
    out = ApplyPatchTool(tmp_path).run(path="m.py", patch=patch)
    assert out.startswith("error:") and "ambiguous" in out
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == "x\nx\n"


def test_apply_patch_malformed(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "a\n")
    out = ApplyPatchTool(tmp_path).run(path="m.py", patch="not a patch")
    assert out.startswith("error:")


def test_parse_hunks_roundtrip() -> None:
    hunks = _parse_hunks(_PATCH)
    assert hunks == [("a = 1", "a = 10"), ("c = 3", "c = 30")]


def test_apply_patch_multiline_hunk(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "def f():\n    return 1\n\nx = 9\n")
    patch = "<<<<<<< SEARCH\ndef f():\n    return 1\n=======\ndef f():\n    return 2\n>>>>>>> REPLACE"
    out = ApplyPatchTool(tmp_path).run(path="m.py", patch=patch)
    assert "applied 1 hunk(s)" in out
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == "def f():\n    return 2\n\nx = 9\n"
