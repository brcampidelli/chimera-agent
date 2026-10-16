"""Undo puts a file back byte for byte, line endings included.

The checkpoint read files with ``Path.read_text`` and wrote them back with ``Path.write_text``. Both
translate newlines: reading turns ``\\r\\n`` (and a lone ``\\r``) into ``\\n``, and on Windows writing
turns every ``\\n`` into ``\\r\\n``. So on Windows undoing a turn rewrote every LF file it restored
with CRLF endings, and everywhere a CRLF file came back as LF. The text looked identical in an
editor and the diff the person saw afterwards was every line of the file. A turn that only changed
line endings was also invisible to the change it recorded, since both sides read the same.

These tests write and compare bytes, so they fail on Linux too: the CRLF cases catch the read side,
which translates on every platform.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.core.checkpoint import WorkspaceGuard

ORIGINALS = {
    "lf.txt": b"one\ntwo\nthree\n",
    "crlf.txt": b"one\r\ntwo\r\nthree\r\n",
    "mixed.txt": b"one\r\ntwo\nthree\rfour\n",
}


def _seed(root: Path) -> None:
    for rel, data in ORIGINALS.items():
        (root / rel).write_bytes(data)


def _edit_all(root: Path) -> None:
    for rel in ORIGINALS:
        (root / rel).write_bytes(b"changed by the turn\n")


@pytest.mark.parametrize("rel", sorted(ORIGINALS))
def test_undoing_a_turn_restores_the_exact_bytes(tmp_path: Path, rel: str) -> None:
    _seed(tmp_path)
    guard = WorkspaceGuard(tmp_path)
    before = guard.snapshot()
    _edit_all(tmp_path)

    report = guard.restore_change(guard.diff_since(before))

    assert (tmp_path / rel).read_bytes() == ORIGINALS[rel]
    assert rel not in report.kept


@pytest.mark.parametrize("rel", sorted(ORIGINALS))
def test_restoring_a_snapshot_restores_the_exact_bytes(tmp_path: Path, rel: str) -> None:
    _seed(tmp_path)
    guard = WorkspaceGuard(tmp_path)
    before = guard.snapshot()
    _edit_all(tmp_path)

    guard.restore(before)

    assert (tmp_path / rel).read_bytes() == ORIGINALS[rel]


def test_a_restore_that_finds_the_file_unchanged_leaves_it_alone(tmp_path: Path) -> None:
    _seed(tmp_path)
    guard = WorkspaceGuard(tmp_path)

    assert guard.restore(guard.snapshot()) == 0
    assert {rel: (tmp_path / rel).read_bytes() for rel in ORIGINALS} == ORIGINALS


def test_a_turn_that_only_changes_line_endings_is_a_change_and_undoes(tmp_path: Path) -> None:
    _seed(tmp_path)
    guard = WorkspaceGuard(tmp_path)
    before = guard.snapshot()
    (tmp_path / "lf.txt").write_bytes(ORIGINALS["lf.txt"].replace(b"\n", b"\r\n"))

    change = guard.diff_since(before)
    assert change.paths == ["lf.txt"]

    guard.restore_change(change)
    assert (tmp_path / "lf.txt").read_bytes() == ORIGINALS["lf.txt"]
