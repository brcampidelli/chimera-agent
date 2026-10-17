"""Removing a plugin the owner uploaded removes its ``<home>/plugin-uploads/<digest>`` folder — and nothing else.

Owner's decision of 2026-10-17: the upload folder is the plugin's pinned source, so it goes with the
plugin. Only that plugin's own folder: inside ``plugin-uploads``, named as the upload wrote it, the one
the record names, reached without a link, and not still named by another installed plugin. Every
refusal below is checked on a folder that EXISTS afterwards, and the last test switches the guard off
to show the same scenarios would have deleted — a guard nothing could make fire guards nothing.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from chimera.plugins import upload as upload_module
from chimera.plugins.registry import RECORD, get, install, plugin_dir, remove
from chimera.plugins.upload import UPLOADS, import_upload
from tests.plugin_trees import text_plugin, zipped


def _upload(home: Path, tmp_path: Path, name: str = "notes") -> Path:
    source = text_plugin(tmp_path / "src", name)
    import_upload([(f"{name}.zip", zipped(source, top=f"{name}-main"))], home)
    record = get(home, name)
    assert record is not None
    folder = Path(record.source)
    assert folder.parent.name == UPLOADS and folder.is_dir()
    return folder


def _point(home: Path, name: str, source: Path) -> None:
    """Rewrite a record's source — how a second plugin, or a tampered record, would name a folder."""
    meta = plugin_dir(home, name) / RECORD
    data = json.loads(meta.read_text(encoding="utf-8"))
    data["source"] = str(source)
    data["source_kind"] = "local"
    meta.write_text(json.dumps(data), encoding="utf-8")


def test_removing_an_uploaded_plugin_deletes_its_upload_folder(tmp_path: Path) -> None:
    home = tmp_path / "home"
    folder = _upload(home, tmp_path)
    assert remove(home, "notes")
    assert not folder.exists()
    assert (home / UPLOADS).is_dir()  # the folder of uploads stays; only this one went


def test_a_folder_another_installed_plugin_still_names_is_kept(tmp_path: Path) -> None:
    home = tmp_path / "home"
    folder = _upload(home, tmp_path)
    install(str(text_plugin(tmp_path / "other", "brief")), home)
    _point(home, "brief", folder)
    assert remove(home, "notes")
    assert folder.is_dir()


def test_a_plugin_installed_from_a_folder_of_the_owners_leaves_that_folder(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source = text_plugin(tmp_path / "mine", "brief")
    install(str(source), home)
    assert remove(home, "brief")
    assert source.is_dir() and (source / ".claude-plugin" / "plugin.json").is_file()


def test_a_digest_named_folder_outside_the_uploads_is_never_deleted(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _upload(home, tmp_path)
    outside = tmp_path / "victim" / ("a" * 24)
    outside.mkdir(parents=True)
    (outside / "keep.txt").write_text("x", encoding="utf-8")
    for spelling in (outside, home / UPLOADS / ".." / ".." / "victim" / ("a" * 24)):
        _point(home, "notes", spelling)
        assert upload_module.upload_folder_of(home, get(home, "notes")) is None  # type: ignore[arg-type]
    assert remove(home, "notes")
    assert (outside / "keep.txt").is_file()


def test_a_folder_inside_uploads_that_is_not_an_upload_name_is_kept(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _upload(home, tmp_path)
    odd = home / UPLOADS / "not-a-digest"
    odd.mkdir()
    _point(home, "notes", odd)
    assert remove(home, "notes")
    assert odd.is_dir()


def _link(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_a_link_in_the_uploads_is_never_followed_to_its_target(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _upload(home, tmp_path)
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("x", encoding="utf-8")
    link = home / UPLOADS / ("b" * 24)
    try:
        _link(link, victim)
    except OSError as exc:
        pytest.skip(f"this machine cannot make a directory link: {exc}")
    _point(home, "notes", link)
    assert remove(home, "notes")
    assert (victim / "keep.txt").is_file()


def test_the_guard_is_what_keeps_those_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sabotage: accept any recorded source. The shared folder and the outside folder now go —
    so the tests above fail for the reason they are named, not because nothing could delete."""
    home = tmp_path / "home"
    folder = _upload(home, tmp_path)
    install(str(text_plugin(tmp_path / "other", "brief")), home)
    _point(home, "brief", folder)
    outside = tmp_path / "victim" / ("c" * 24)
    outside.mkdir(parents=True)
    install(str(text_plugin(tmp_path / "third", "third")), home)
    _point(home, "third", outside)

    monkeypatch.setattr(upload_module, "upload_folder_of", lambda home, record: Path(record.source))
    monkeypatch.setattr(upload_module, "forget_upload", _no_sharing_check)
    assert remove(home, "notes")
    assert remove(home, "third")
    assert not folder.exists()
    assert not outside.exists()


def _no_sharing_check(home: Path, record: object, remaining: object) -> bool:
    folder = upload_module.upload_folder_of(home, record)  # type: ignore[arg-type]
    if folder is None:
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return True
