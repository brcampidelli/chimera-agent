"""`chimera review` reviews the change git shows, and cites the lines of its new version.

The reviewer is asked for ``file:line``. These tests pin the two things that citation rests on:
which change is collected (the working tree against its merge base with main, or a range) and which
number each line carries. They also pin the one filter that needs no model: a line the diff does
not show is not in the change.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from chimera.review.diff import DiffError, collect, parse, untracked
from tests.review_fakes import git, repo_with_change


def test_the_working_tree_is_reviewed_against_its_merge_base_with_main(tmp_path: Path) -> None:
    repo = repo_with_change(tmp_path)

    diff = collect(repo)

    assert [f.path for f in diff.files] == ["calc.py"]
    assert diff.base_label == "merge base with main"
    assert diff.base == git(repo, "rev-parse", "main").strip()
    lines = [line for hunk in diff.files[0].hunks for line in hunk.lines]
    added = {line.new: line.text for line in lines if line.tag == "+"}
    assert added[11] == "    return total(xs) / (len(xs) - 1)"
    assert min(added) == 6


def test_every_new_version_line_carries_its_number_in_what_the_finder_reads(tmp_path: Path) -> None:
    diff = collect(repo_with_change(tmp_path))

    rendered = diff.files[0].render()

    assert rendered[0] == "### calc.py (modified)"
    assert "    11 +     return total(xs) / (len(xs) - 1)" in rendered


def test_a_revision_range_is_reviewed_as_given(tmp_path: Path) -> None:
    repo = repo_with_change(tmp_path, commit_change=True)

    diff = collect(repo, revision_range="main..feature")

    assert diff.target == "main..feature"
    assert [f.path for f in diff.files] == ["calc.py"]


def test_a_line_outside_every_hunk_is_not_in_the_change(tmp_path: Path) -> None:
    diff = collect(repo_with_change(tmp_path), context=0)
    calc = diff.files[0]

    assert calc.anchors(11)
    assert calc.anchors(4)  # within the slack of the hunk that starts at 6
    assert not calc.anchors(1)
    assert not calc.anchors(40)


def test_untracked_files_are_counted_not_reviewed(tmp_path: Path) -> None:
    repo = repo_with_change(tmp_path)
    (repo / "scratch.txt").write_text("notes\n", encoding="utf-8")

    diff = collect(repo)

    assert [f.path for f in diff.files] == ["calc.py"]
    assert untracked(repo) == ["scratch.txt"]


def test_an_unknown_base_and_a_folder_outside_git_are_errors(tmp_path: Path) -> None:
    repo = repo_with_change(tmp_path)
    with pytest.raises(DiffError, match="does not know"):
        collect(repo, base="no-such-branch")
    outside = tmp_path / "plain"
    outside.mkdir()
    with pytest.raises(DiffError, match="not inside a git repository"):
        collect(outside)


def test_a_removed_line_that_starts_with_dashes_is_not_read_as_a_file_header() -> None:
    text = (
        "diff --git a/q.sql b/q.sql\n--- a/q.sql\n+++ b/q.sql\n@@ -1,2 +1,1 @@\n"
        "--- a comment removed\n select 1;\n"
    )

    files = parse(text)

    assert [f.path for f in files] == ["q.sql"]
    assert [(line.tag, line.text) for line in files[0].hunks[0].lines] == [
        ("-", "-- a comment removed"),
        (" ", "select 1;"),
    ]


def test_a_diff_without_git_headers_is_split_into_its_files() -> None:
    first = difflib.unified_diff(["a\n"], ["a\n", "b\n"], "a/one.py", "b/one.py")
    second = difflib.unified_diff(["x\n"], ["y\n"], "a/two.py", "b/two.py")

    files = parse("".join(first) + "".join(second))

    assert [f.path for f in files] == ["one.py", "two.py"]
    assert [line.new for line in files[0].hunks[0].lines if line.tag == "+"] == [2]


def test_a_rename_keeps_one_file_with_its_new_path() -> None:
    text = (
        "diff --git a/old.py b/new.py\nsimilarity index 90%\nrename from old.py\nrename to new.py\n"
        "--- a/old.py\n+++ b/new.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    )

    files = parse(text)

    assert [(f.path, f.status, f.old_path) for f in files] == [("new.py", "renamed", "old.py")]
