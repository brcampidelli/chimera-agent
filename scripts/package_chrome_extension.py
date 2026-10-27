#!/usr/bin/env python3
"""Build a reproducible Chrome extension ZIP after validating its store manifest."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
EXTENSION_DIR = ROOT / "extensions/chrome"
DIST_DIR = ROOT / "dist"
EXPECTED_PERMISSIONS = ["debugger", "downloads", "nativeMessaging", "storage", "tabGroups", "tabs"]
EXPECTED_OPTIONAL_HOSTS = ["http://*/*", "https://*/*"]
PACKAGE_FILES = (
    "manifest.json",
    "service_worker.js",
    "popup.html",
    "popup.css",
    "popup.js",
    "icons/icon16.png",
    "icons/icon32.png",
    "icons/icon48.png",
    "icons/icon128.png",
)
ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)


def validate_manifest(extension_dir: Path = EXTENSION_DIR) -> dict[str, object]:
    """Raise ValueError if the static store/privacy requirements are violated."""
    manifest_path = extension_dir / "manifest.json"
    try:
        decoded = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read extension manifest: {exc}") from exc
    if not isinstance(decoded, dict):
        raise ValueError("Extension manifest must be a JSON object")
    manifest = cast(dict[str, object], decoded)
    action = manifest.get("action")
    if not isinstance(action, dict) or action.get("default_popup") != "popup.html":
        raise ValueError('action.default_popup must be "popup.html"')
    if not (extension_dir / "popup.html").is_file():
        raise ValueError("popup.html is missing")
    if manifest.get("permissions") != EXPECTED_PERMISSIONS:
        raise ValueError(f"permissions must be exactly {EXPECTED_PERMISSIONS!r}")
    if manifest.get("optional_host_permissions") != EXPECTED_OPTIONAL_HOSTS:
        raise ValueError(f"optional_host_permissions must be exactly {EXPECTED_OPTIONAL_HOSTS!r}")
    forbidden = ("externally_connectable", "content_scripts", "web_accessible_resources")
    present = [key for key in forbidden if key in manifest]
    if present or "<all_urls>" in json.dumps(manifest):
        raise ValueError(f"Forbidden manifest entries: {present or ['<all_urls>']}")
    description = manifest.get("description")
    if not isinstance(description, str) or len(description) > 132:
        raise ValueError("description must be a string no longer than 132 characters")
    icon_paths: set[str] = set()
    for key in ("icons",):
        icons = manifest.get(key)
        if not isinstance(icons, dict):
            raise ValueError(f"manifest.{key} must map icon sizes to paths")
        icon_paths.update(value for value in icons.values() if isinstance(value, str))
    default_icon = action.get("default_icon")
    if not isinstance(default_icon, dict):
        raise ValueError("action.default_icon must map icon sizes to paths")
    icon_paths.update(value for value in default_icon.values() if isinstance(value, str))
    if not icon_paths or any(not (extension_dir / path).is_file() for path in icon_paths):
        raise ValueError("Manifest icon paths must all reference existing files")
    missing = [name for name in PACKAGE_FILES if not (extension_dir / name).is_file()]
    if missing:
        raise ValueError(f"Required package files are missing: {', '.join(missing)}")
    return manifest


def package(output_dir: Path = DIST_DIR, extension_dir: Path = EXTENSION_DIR) -> Path:
    manifest = validate_manifest(extension_dir)
    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("manifest.version must be a non-empty string")
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"chimera-chrome-extension-{version}.zip"
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in PACKAGE_FILES:
            info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.flag_bits = 0
            archive.writestr(info, (extension_dir / name).read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DIST_DIR)
    args = parser.parse_args()
    print(package(args.output_dir))


if __name__ == "__main__":
    main()
