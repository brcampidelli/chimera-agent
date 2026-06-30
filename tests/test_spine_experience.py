"""Tests for the Spine context assembler and the experience buffer."""

from __future__ import annotations

from pathlib import Path

from chimera.core.spine import assemble_spine
from chimera.evolution import Experience, ExperienceBuffer, format_lessons


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


def test_experience_relevant_ranks_by_overlap_and_favours_failures(tmp_path: Path) -> None:
    buf = ExperienceBuffer(tmp_path / "exp.json")
    buf.record("write a parser for csv files", "success", "")
    buf.record("write a parser for csv files", "failure", "off-by-one error")
    buf.record("bake a chocolate cake", "success", "")

    rel = buf.relevant("write a csv parser", k=2)
    assert [e.task for e in rel] == [
        "write a parser for csv files",  # the failure ranks first (favoured at tie)
        "write a parser for csv files",
    ]
    assert rel[0].outcome == "failure"
    assert buf.relevant("xylophone zebra quokka") == []  # nothing overlaps


def test_format_lessons_renders_block_and_is_empty_for_none() -> None:
    assert format_lessons([]) == ""
    block = format_lessons([Experience(seq=0, task="do X", outcome="failure", detail="why it broke")])
    assert "FAILED" in block and "do X" in block and "why it broke" in block
