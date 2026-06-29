"""Tests for the Spine context assembler and the experience buffer."""

from __future__ import annotations

from pathlib import Path

from chimera.core.spine import assemble_spine
from chimera.evolution import ExperienceBuffer


def test_spine_includes_referenced_file(tmp_path: Path) -> None:
    (tmp_path / "foo.py").write_text("def foo(): pass", encoding="utf-8")
    (tmp_path / "bar.py").write_text("def bar(): pass", encoding="utf-8")

    spine = assemble_spine(tmp_path, "fix the bug in foo.py")
    assert "foo.py" in spine
    assert "def foo()" in spine
    assert "bar.py" not in spine


def test_spine_empty_when_no_file_referenced(tmp_path: Path) -> None:
    (tmp_path / "foo.py").write_text("x = 1", encoding="utf-8")
    assert assemble_spine(tmp_path, "write a poem about the sea") == ""


def test_spine_ignores_vcs_dirs(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config.py").write_text("secret", encoding="utf-8")
    # even though 'config.py' is mentioned, files under .git are skipped
    assert assemble_spine(tmp_path, "look at config.py") == ""


def test_experience_buffer_record_and_filter(tmp_path: Path) -> None:
    buf = ExperienceBuffer(tmp_path / "exp.json")
    buf.record("task A", "success", "all green")
    buf.record("task B", "failure", "tests failed")

    assert len(buf) == 2
    assert [e.task for e in buf.successes()] == ["task A"]
    assert [e.task for e in buf.failures()] == ["task B"]
    assert buf.all()[0].seq == 0


def test_experience_buffer_persists(tmp_path: Path) -> None:
    path = tmp_path / "exp.json"
    ExperienceBuffer(path).record("t", "failure", "d")
    reopened = ExperienceBuffer(path)
    assert len(reopened) == 1
    assert reopened.failures()[0].detail == "d"
