"""The design store: revisions only move forward, a stale save is refused, history is append-only."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from chimera.design import store as store_module
from chimera.design.migrate import UPGRADES, DesignMigrationError, load_document, migrate
from chimera.design.model import SCHEMA_VERSION, DesignError
from chimera.design.store import DesignConflict, DesignNotFound, DesignStore
from tests.design_fixtures import sample_dict, text


def _store(tmp_path: Path) -> DesignStore:
    return DesignStore(tmp_path / "designs")


def _png(label: bytes = b"", side: int = 1) -> bytes:
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side + 1, 8, 2, 0, 0, 0))
    return out + (chunk(b"tEXt", label) if label else b"")


def test_assets_are_content_addressed_deduplicated_and_listed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = store.create("assets")
    data = _png(side=2)
    first = store.put_asset(doc.id, data, "image/png")
    second = store.put_asset(doc.id, data, "image/png")
    assert first == second
    assert len(list((store._dir(doc.id) / "assets").glob("a_*"))) == 1
    assert store.list_assets(doc.id) == [{"id": first, "mime": "image/png", "size": len(data), "width": 2, "height": 3}]
    stored, info = store.get_asset(doc.id, first)
    assert stored == data and info.width == 2 and info.height == 3
    # A different byte sequence is a different asset.
    assert store.put_asset(doc.id, _png(b"other", side=2), "image/png") != first


def test_asset_upload_rejects_svg_false_mime_and_unsafe_ids(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = store.create("assets")
    with pytest.raises(DesignError, match="SVG"):
        store.put_asset(doc.id, b"<svg><script>alert(1)</script></svg>", "image/svg+xml")
    with pytest.raises(DesignError, match="SVG"):
        store.put_asset(doc.id, b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>', None)
    with pytest.raises(DesignError, match="declared as"):
        store.put_asset(doc.id, _png(), "image/jpeg")
    with pytest.raises(DesignError):
        store.put_asset(doc.id, b"MZ\x90\x00 not an image", "image/png")
    for bad in ("../escape", "..\\escape", "a_/../../x", "index.json", ""):
        with pytest.raises(DesignNotFound):
            store.get_asset(doc.id, bad)
    assert store.list_assets(doc.id) == []


def test_asset_upload_obeys_per_file_size_limit_before_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    doc = store.create("assets")
    monkeypatch.setattr(store_module, "MAX_ASSET_BYTES", len(_png()) - 1)
    with pytest.raises(DesignError, match="5 MB"):
        store.put_asset(doc.id, _png(), "image/png")
    assert not (store._dir(doc.id) / "assets").exists()


def test_asset_upload_obeys_per_design_size_limit_but_allows_a_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    doc = store.create("assets")
    first, second = _png(b"one"), _png(b"two")
    monkeypatch.setattr(store_module, "MAX_DESIGN_ASSET_BYTES", len(first) + len(second) - 1)
    asset = store.put_asset(doc.id, first, "image/png")
    with pytest.raises(DesignError, match="50 MB"):
        store.put_asset(doc.id, second, "image/png")
    assert store.put_asset(doc.id, first, "image/png") == asset  # same content costs nothing
    assert [row["id"] for row in store.list_assets(doc.id)] == [asset]


def test_concurrent_asset_uploads_all_land_in_the_index(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    store = _store(tmp_path)
    doc = store.create("assets")
    payloads = [_png(bytes([65 + i]) * 3) for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda data: store.put_asset(doc.id, data, "image/png"), payloads))
    assert len(set(ids)) == len(payloads)
    assert sorted(row["id"] for row in store.list_assets(doc.id)) == sorted(ids)


def test_create_get_list_and_delete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = store.create("  Launch  ")
    assert doc.revision == 1 and doc.title == "Launch" and doc.id.startswith("d_")
    assert doc.tokens.colors  # the default system
    assert store.get(doc.id) == doc
    assert [s.id for s in store.list_designs()] == [doc.id]
    assert [v.revision for v in store.versions(doc.id)] == [1]
    assert store.delete(doc.id) is True
    assert store.list_designs() == []
    assert store.delete(doc.id) is False
    with pytest.raises(DesignNotFound):
        store.get(doc.id)


def test_a_stale_save_is_refused_with_the_current_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = store.create("A")
    first, _ = store.apply(doc.id, [{"type": "document.rename", "title": "B"}], expected_revision=1)
    assert first.revision == 2
    with pytest.raises(DesignConflict) as caught:
        store.apply(doc.id, [{"type": "document.rename", "title": "C"}], expected_revision=1)
    assert caught.value.current == 2
    with pytest.raises(DesignConflict):
        store.save(doc, expected_revision=1)
    assert store.get(doc.id).title == "B"


def test_restore_makes_a_new_revision_and_never_rewrites_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = store.create("v1")
    store.apply(doc.id, [{"type": "document.rename", "title": "v2"}], expected_revision=1)
    store.apply(doc.id, [{"type": "document.rename", "title": "v3"}], expected_revision=2)
    snapshot_2 = (tmp_path / "designs" / doc.id / "versions" / "000002.json").read_bytes()
    restored = store.restore(doc.id, 1, expected_revision=3)
    assert (restored.revision, restored.title) == (4, "v1")
    assert [v.revision for v in store.versions(doc.id)] == [4, 3, 2, 1]
    assert (tmp_path / "designs" / doc.id / "versions" / "000002.json").read_bytes() == snapshot_2
    with pytest.raises(DesignNotFound):
        store.get_version(doc.id, 99)


def test_only_the_newest_versions_are_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_module, "MAX_VERSIONS", 3)
    store = _store(tmp_path)
    doc = store.create("x")
    for revision in range(1, 6):
        store.apply(doc.id, [{"type": "document.rename", "title": f"t{revision}"}],
                    expected_revision=revision)
    assert [v.revision for v in store.versions(doc.id)] == [6, 5, 4]


def test_a_failed_write_leaves_the_previous_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    doc = store.create("kept")
    path = tmp_path / "designs" / doc.id / "doc.json"
    before = path.read_bytes()

    def broken(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError):
        store.apply(doc.id, [{"type": "document.rename", "title": "lost"}], expected_revision=1)
    monkeypatch.undo()
    assert path.read_bytes() == before
    assert not [p for p in path.parent.iterdir() if p.suffix == ".tmp"]
    assert store.get(doc.id).revision == 1


def test_ids_that_are_not_plain_are_refused_not_sanitised(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for bad in ("../escape", "a/b", "", "d_é", "x" * 65):
        with pytest.raises(DesignNotFound):
            store.get(bad)


def test_secrets_are_redacted_before_they_reach_disk(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = store.create("x")
    secret = "sk-ant-api03-" + "A" * 40
    store.apply(doc.id, [
        {"type": "page.add", "page": {"id": "p1", "artboards": [
            {"id": "a1", "width": 100, "height": 100, "nodeIds": ["n1"]}]},
         "nodes": [text("n1", f"key: {secret}")]},
    ], expected_revision=1)
    raw = (tmp_path / "designs" / doc.id / "doc.json").read_text(encoding="utf-8")
    assert secret not in raw


def test_an_unreadable_design_is_skipped_by_the_list(tmp_path: Path) -> None:
    store = _store(tmp_path)
    good = store.create("good")
    broken = tmp_path / "designs" / "d_broken"
    broken.mkdir()
    (broken / "doc.json").write_text("{not json", encoding="utf-8")
    assert [s.id for s in store.list_designs()] == [good.id]
    with pytest.raises(DesignError):
        store.get("d_broken")


def test_migration_is_identity_today_and_refuses_the_future() -> None:
    raw = sample_dict()
    assert migrate(raw) == raw and migrate(raw) is not raw
    assert load_document(raw).id == "d_sample"
    assert all(v in UPGRADES for v in range(1, SCHEMA_VERSION))
    with pytest.raises(DesignMigrationError, match="newer Chimera"):
        migrate({**raw, "schemaVersion": SCHEMA_VERSION + 1})
    for bad in (None, "1", 0, True):
        with pytest.raises(DesignMigrationError):
            migrate({**raw, "schemaVersion": bad})


def test_a_stored_document_from_a_newer_build_is_not_opened(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = store.create("x")
    path = tmp_path / "designs" / doc.id / "doc.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schemaVersion"] = 99
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DesignMigrationError):
        store.get(doc.id)
