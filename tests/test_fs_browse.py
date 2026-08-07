"""Listing folders so a person can PICK a project.

The tree endpoint is scoped inside a chosen workspace, so it cannot answer "which workspace?". This
one is not scoped, which is the point and also the whole risk — so it is cut down to the least that
answers the question, and these pin that it stays cut down.
"""

from __future__ import annotations

from pathlib import Path

from chimera.api.fs_api import browse_dirs


def test_it_lists_subdirectories(tmp_path: Path) -> None:
    (tmp_path / "api").mkdir()
    (tmp_path / "web").mkdir()

    got = browse_dirs(str(tmp_path))

    assert [e["name"] for e in got["entries"]] == ["api", "web"]
    assert got["path"] == str(tmp_path.resolve())


def test_files_are_not_listed_at_all(tmp_path: Path) -> None:
    """This enumerates folder NAMES. Listing files would make it a second way to read the disk."""
    (tmp_path / "api").mkdir()
    (tmp_path / "secrets.env").write_text("KEY=1", encoding="utf-8")

    assert [e["name"] for e in browse_dirs(str(tmp_path))["entries"]] == ["api"]


def test_hidden_directories_stay_hidden(tmp_path: Path) -> None:
    """Nobody browsing for a project asked to be shown `.ssh`."""
    (tmp_path / ".ssh").mkdir()
    (tmp_path / "project").mkdir()

    assert [e["name"] for e in browse_dirs(str(tmp_path))["entries"]] == ["project"]


def test_it_offers_the_way_back_up(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()

    assert browse_dirs(str(child))["parent"] == str(tmp_path.resolve())


def test_a_path_that_does_not_exist_lists_nothing_rather_than_raising(tmp_path: Path) -> None:
    got = browse_dirs(str(tmp_path / "nope"))
    assert got["entries"] == []


def test_an_empty_path_starts_at_home() -> None:
    assert browse_dirs("")["path"] == str(Path.home().resolve())


def test_the_listing_is_capped(tmp_path: Path) -> None:
    for i in range(12):
        (tmp_path / f"d{i:02d}").mkdir()

    got = browse_dirs(str(tmp_path), max_entries=5)

    assert len(got["entries"]) == 5
    assert got["capped"] is True
