"""Tests for the grep / glob search tools (no model)."""

from __future__ import annotations

from pathlib import Path

from chimera.tools.search import GlobTool, GrepTool


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "def handler():\n    return login_user()\n", encoding="utf-8"
    )
    (tmp_path / "src" / "auth.py").write_text("def login_user():\n    pass\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# project\nlogin flow docs\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "junk.py").write_text("login_user ignored", encoding="utf-8")
    return tmp_path


def test_grep_finds_matches_with_line_numbers(tmp_path: Path) -> None:
    out = GrepTool(_workspace(tmp_path)).run(pattern=r"login_user")
    assert "src/app.py:2:" in out
    assert "src/auth.py:1:" in out
    assert "junk.py" not in out  # __pycache__ is ignored


def test_grep_glob_filter_restricts_to_matching_files(tmp_path: Path) -> None:
    out = GrepTool(_workspace(tmp_path)).run(pattern=r"login", glob="*.md")
    assert "README.md" in out
    assert "app.py" not in out


def test_grep_no_matches_and_bad_regex(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    assert GrepTool(ws).run(pattern="zzz_absent") == "no matches"
    assert "invalid regex" in GrepTool(ws).run(pattern="(unclosed")


def test_glob_finds_files_by_pattern(tmp_path: Path) -> None:
    out = GlobTool(_workspace(tmp_path)).run(pattern="**/*.py")
    paths = out.splitlines()
    assert "src/app.py" in paths
    assert "src/auth.py" in paths
    assert all("__pycache__" not in p for p in paths)  # ignored dir excluded


def test_glob_no_match(tmp_path: Path) -> None:
    assert GlobTool(_workspace(tmp_path)).run(pattern="**/*.rs") == "no files match"


def test_glob_cannot_escape_workspace(tmp_path: Path) -> None:
    # A '../' pattern resolves to a file OUTSIDE the workspace; pathlib.glob happily returns it,
    # so the tool must drop any match whose real path isn't under the workspace root.
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "inside.txt").write_text("ok", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
    out = GlobTool(ws).run(pattern="../*.txt")
    assert out == "no files match"
    assert "secret" not in out
