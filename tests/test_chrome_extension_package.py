from __future__ import annotations

import hashlib
import json
import runpy
import struct
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
EXTENSION = ROOT / "extensions/chrome"
EXPECTED_FILES = {
    "manifest.json", "service_worker.js", "popup.html", "popup.js",
    "icons/icon16.png", "icons/icon32.png", "icons/icon48.png", "icons/icon128.png",
}
EXPECTED_PERMISSIONS = ["debugger", "nativeMessaging", "storage", "tabGroups", "tabs"]


def _package_module() -> dict[str, Any]:
    return runpy.run_path(str(ROOT / "scripts/package_chrome_extension.py"))


def test_manifest_has_least_privilege_permissions_and_local_popup() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["action"]["default_popup"] == "popup.html"
    assert (EXTENSION / manifest["action"]["default_popup"]).is_file()
    assert manifest["permissions"] == EXPECTED_PERMISSIONS
    assert manifest["optional_host_permissions"] == ["http://*/*", "https://*/*"]
    assert not {"externally_connectable", "content_scripts", "web_accessible_resources"} & manifest.keys()
    assert "<all_urls>" not in json.dumps(manifest)
    assert len(manifest["description"]) <= 132
    for icon in (*manifest["icons"].values(), *manifest["action"]["default_icon"].values()):
        assert (EXTENSION / icon).is_file()


def test_package_validation_rejects_manifest_that_static_check_would_reject(tmp_path: Path) -> None:
    validate = _package_module()["validate_manifest"]
    copied = tmp_path / "extension"
    copied.mkdir()
    for item in EXPECTED_FILES:
        destination = copied / item
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((EXTENSION / item).read_bytes())
    manifest_path = copied / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["permissions"].append("activeTab")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    try:
        validate(copied)
    except ValueError as exc:
        assert "permissions" in str(exc)
    else:
        raise AssertionError("packager accepted permissions rejected by the static manifest check")


def test_package_contains_only_expected_files_and_is_byte_deterministic(tmp_path: Path) -> None:
    package = _package_module()["package"]
    first = package(tmp_path / "first")
    first_bytes = first.read_bytes()
    second = package(tmp_path / "second")
    second_bytes = second.read_bytes()
    assert hashlib.sha256(first_bytes).digest() == hashlib.sha256(second_bytes).digest()
    with zipfile.ZipFile(first) as archive:
        assert set(archive.namelist()) == EXPECTED_FILES
        assert archive.namelist() == list(_package_module()["PACKAGE_FILES"])
        assert all(entry.date_time == (2020, 1, 1, 0, 0, 0) for entry in archive.infolist())


def test_extension_icons_have_required_png_dimensions() -> None:
    for size in (16, 32, 48, 128):
        data = (EXTENSION / "icons" / f"icon{size}.png").read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", data[16:24]) == (size, size)
