"""Print or register the Chrome Native Messaging host manifest (Windows only)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HOST_NAME = "app.chimera.browser"


def manifest(extension_id: str, executable: str) -> dict[str, object]:
    return {
        "name": HOST_NAME,
        "description": "Chimera read-only Chrome browser host",
        "path": str(Path(executable).resolve()),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }


def _on_windows() -> bool:
    # One seam for the platform check: a test that fakes os.name globally breaks pathlib itself.
    return os.name == "nt"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extension_id", help="Chrome extension ID allowed to connect")
    parser.add_argument("--python", default=sys.executable, help="Python interpreter used by the host wrapper")
    parser.add_argument("--directory", help="Host installation directory (default: %%LOCALAPPDATA%%\\Chimera\\browser-host)")
    parser.add_argument("--manifest", help="Manifest output path (defaults to the installation directory)")
    parser.add_argument("--apply", action="store_true", help="Write the wrapper, manifest and HKCU registration")
    args = parser.parse_args()
    if re.fullmatch(r"[a-p]{32}", args.extension_id) is None:
        parser.error("extension_id must contain exactly 32 lowercase letters from a through p")
    if args.apply and not _on_windows():
        parser.error("--apply is supported only on Windows")

    python = Path(args.python).resolve()
    if args.apply and not python.is_file():
        parser.error(f"Python interpreter does not exist: {python}")
    root = Path(args.directory) if args.directory else Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Chimera" / "browser-host"
    root = root.resolve()
    manifest_path = Path(args.manifest).resolve() if args.manifest else root / "app.chimera.browser.json"
    wrapper = root / "chimera-browser-host.cmd"
    payload = manifest(args.extension_id, str(wrapper))
    registry_key = rf"HKCU\Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}"
    print(json.dumps({
        "manifest_path": str(manifest_path), "manifest": payload, "wrapper_path": str(wrapper),
        "wrapper": f'@echo off\r\n"{python}" -I -m chimera.browser_host\r\n',
        "registry_key": registry_key,
    }, indent=2))
    if args.apply:
        import winreg

        from chimera.browser_link import load_or_create_pairing_key
        from chimera.config import get_settings

        load_or_create_pairing_key(Path(get_settings().home), create=True)
        root.mkdir(parents=True, exist_ok=True)
        # newline="" keeps the \r\n as written; text mode on Windows would turn each one into \r\r\n.
        wrapper.write_text(f'@echo off\r\n"{python}" -I -m chimera.browser_host\r\n', encoding="utf-8", newline="")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
