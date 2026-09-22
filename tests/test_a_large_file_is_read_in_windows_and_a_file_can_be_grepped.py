"""Two tool shapes a cheap model could not get past, measured in the desktop agent's own transcript
(2026-09-19, the Chimera desktop working on this repository): `read_file` cut a 137k-character module
at 20,000 characters with a notice that named the total and no way to reach the rest, and `grep` with
a file as `path` answered "not a directory" — which the model read as "try again", eleven times in one
forty-step turn. The turn ended on the step ceiling with the region it had to edit unread.

Held here: a window into a file by line, a truncation notice that names the next window, and a grep
that searches the file it was pointed at.
"""

from __future__ import annotations

from pathlib import Path

from chimera.tools import files
from chimera.tools.files import ReadFileTool
from chimera.tools.search import GrepTool


def _big(tmp_path: Path) -> Path:
    body = "".join(f"line {i}: " + "x" * 60 + "\n" for i in range(1, 2001))  # ~140k chars
    (tmp_path / "big.py").write_text(body, encoding="utf-8")
    return tmp_path


def test_a_window_by_line_returns_exactly_those_lines_and_says_where_the_next_one_starts(tmp_path: Path) -> None:
    tool = ReadFileTool(_big(tmp_path))
    out = tool.run(path="big.py", start_line=740, max_lines=3)
    assert out.startswith("line 740:") and "line 742:" in out and "line 743:" not in out
    assert "continue with start_line=743" in out
    tail = tool.run(path="big.py", start_line=1999, max_lines=50)
    assert "line 2000:" in tail and "continue with" not in tail  # the end: nothing to continue to


def test_a_truncated_read_names_the_next_window_instead_of_only_the_total(tmp_path: Path) -> None:
    tool = ReadFileTool(_big(tmp_path))
    out = tool.run(path="big.py")
    assert "[truncated, lines 1–" in out and "continue with start_line=" in out
    next_line = int(out.rsplit("start_line=", 1)[1].rstrip("]"))
    assert 200 < next_line < 400  # ~70 chars a line against a 20,000-char ceiling
    rest = tool.run(path="big.py", start_line=next_line)
    assert rest.startswith(f"line {next_line}:")


def test_a_small_file_reads_as_before_and_bad_windows_say_why(tmp_path: Path) -> None:
    (tmp_path / "s.txt").write_text("a\nb\nc\n", encoding="utf-8")
    tool = ReadFileTool(tmp_path)
    assert tool.run(path="s.txt") == "a\nb\nc\n"
    assert tool.run(path="s.txt", start_line=2) == "b\nc\n"
    assert tool.run(path="s.txt", start_line=0).startswith("error: start_line is 1-based")
    assert tool.run(path="s.txt", start_line=9).startswith("error: start_line 9 is past the end (3 lines)")


def test_the_ceiling_is_what_it_was(monkeypatch: object) -> None:
    assert files._MAX_READ_CHARS == 20_000


def test_grep_pointed_at_a_file_searches_that_file(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def alpha_two():\n    return 2\n", encoding="utf-8")
    tool = GrepTool(tmp_path)
    assert tool.run(pattern="def alpha", path="a.py") == "a.py:1: def alpha():"
    assert sorted(tool.run(pattern="def alpha").splitlines()) == ["a.py:1: def alpha():", "b.py:1: def alpha_two():"]
    assert tool.run(pattern="x", path="nope.py").startswith("error: no such file or directory")
