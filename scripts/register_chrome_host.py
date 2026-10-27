"""Print or register the Chrome Native Messaging host manifest (Windows only)."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PureWindowsPath

HOST_NAME = "app.chimera.browser"
# The desktop app's bundle identifier (apps/desktop/src-tauri/tauri.conf.json) names its data folder
# under %APPDATA%, and `service-bin` inside it holds one copy of the service per version (attach.rs).
DESKTOP_IDENTIFIER = "app.chimera.desktop"
SERVICE_BIN = "service-bin"
SERVICE_EXE = "chimera-service.exe"
_SERVICE_VERSION = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?")


def manifest(
    extension_id: str,
    executable: str,
    expected_server_executable: str | None = None,
    expected_service_root: str | None = None,
    home: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "name": HOST_NAME,
        "description": "Chimera browser host for reading and interacting with owner-authorized Chrome tabs",
        "path": str(Path(executable).resolve()),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }
    if expected_server_executable is not None:
        value["expected_server_executable"] = str(Path(expected_server_executable).resolve())
    if expected_service_root is not None:
        value["expected_service_root"] = str(Path(expected_service_root).resolve())
    if home is not None:
        value["home"] = home
    return value


def desktop_data_home(service_root: Path | None) -> str | None:
    """The desktop app's data folder, `<app.chimera.desktop>\\data`, derived from its service root.

    The app runs its backend with CHIMERA_HOME set to this folder (apps/desktop backend_env), and the
    host's pipe endpoint and pairing key are per home. Without it the host looks under `~/.chimera`.
    """
    if service_root is None:
        return None
    return str(PureWindowsPath(str(service_root)).parent / "data")


def installed_service_root(server: Path) -> Path | None:
    """The `service-bin` folder when `server` is the desktop app's copy of its service, else None.

    The app runs `%APPDATA%\\app.chimera.desktop\\service-bin\\<version>\\chimera-service.exe`. A new
    version lands in a new folder on every update, so the host pins the `service-bin` root and the
    version folder is matched by the host (see `_installed_service_image_matches`). Any other path
    keeps the exact-image expectation.
    """
    image = PureWindowsPath(str(server))
    if image.name.lower() != SERVICE_EXE or _SERVICE_VERSION.fullmatch(image.parent.name) is None:
        return None
    root = image.parent.parent
    if root.name.lower() != SERVICE_BIN or root.parent.name.lower() != DESKTOP_IDENTIFIER:
        return None
    return Path(str(root))


def _wrapper_text(python: Path, server: Path, service_root: Path | None, home: str | None) -> str:
    """The .cmd the browser runs; it carries the expectation the host checks the pipe server against.

    `home` goes in as CHIMERA_HOME so the host reads the same data folder as the app it pairs with.
    """
    if service_root is None:
        expectation = f'set "CHIMERA_BROWSER_HOST_EXPECTED_EXE={server}"'
    else:
        expectation = f'set "CHIMERA_BROWSER_HOST_EXPECTED_SERVICE_ROOT={service_root}"'
    lines = ["@echo off", expectation]
    if home is not None:
        lines.append(f'set "CHIMERA_HOME={home}"')
    lines.append(f'"{python}" -I -m chimera.browser_host')
    return "\r\n".join(lines)


# Run by the host's own interpreter with `-I`, the way the wrapper runs the host. It answers the one
# question the host depends on: can it reach the OS vault where the desktop app keeps the pairing key.
_VAULT_PROBE = """
try:
    import chimera.config_vault as vault
except ImportError:
    print("chimera-missing")
else:
    try:
        import keyring
    except ImportError:
        print("keyring-missing")
    else:
        print("ok" if vault.available() else "no-backend")
"""
# verdict -> (why the vault is unusable, the package that fixes it)
_VAULT_VERDICTS: dict[str, tuple[str, str]] = {
    "chimera-missing": ("Chimera is not installed in this interpreter", "chimera-agent[secrets]"),
    "keyring-missing": ("keyring is not installed in this interpreter", "keyring>=25"),
    "no-backend": ("keyring has no usable OS backend here", "keyring>=25"),
}


def vault_probe(python: Path) -> tuple[bool, str, str]:
    """Whether `python` can read the OS vault; when not, the reason and the package that fixes it.

    The desktop app keeps the pairing key in the vault whenever `keyring` works. A host whose
    interpreter cannot reach the vault reads no key, stays unpaired, and says nothing about why.
    """
    try:
        result = subprocess.run(
            [str(python), "-I", "-c", _VAULT_PROBE],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False, "the interpreter did not start", "chimera-agent[secrets]"
    verdict = result.stdout.strip()
    if verdict == "ok" and result.returncode == 0:
        return True, "", ""
    if verdict not in _VAULT_VERDICTS or result.returncode != 0:
        return False, "the vault check did not run in this interpreter", "chimera-agent[secrets]"
    reason, package = _VAULT_VERDICTS[verdict]
    return False, reason, package


def vault_warning(python: Path, reason: str, package: str) -> str:
    """One message that names the install which fixes the vault for `python`."""
    return (
        f"the Chrome host cannot read the OS vault: {reason}. The desktop app keeps the pairing key "
        f"there, so the host would stay unpaired without saying why. Fix: "
        f'uv pip install --python "{python}" "{package}"'
    )


def _on_windows() -> bool:
    # One seam for the platform check: a test that fakes os.name globally breaks pathlib itself.
    return os.name == "nt"


def _base_interpreter(python: Path) -> Path:
    """The image a venv interpreter actually runs as, which is what the pipe server reports.

    On Windows a venv's `Scripts\\python.exe` is a launcher that starts the base interpreter, and
    `GetNamedPipeServerProcessId` names that base image. Recording the launcher made every real app
    fail the check and the host retry forever (E5 review).
    """
    if python == Path(sys.executable).resolve():
        return Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    config = python.parent.parent / "pyvenv.cfg"
    try:
        for line in config.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "home":
                candidate = Path(value.strip()) / python.name
                if candidate.is_file():
                    return candidate.resolve()
    except OSError:
        pass
    return python


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extension_id", help="Chrome extension ID allowed to connect")
    parser.add_argument("--python", default=sys.executable, help="Python interpreter used by the host wrapper")
    parser.add_argument("--directory", help="Host installation directory (default: %%LOCALAPPDATA%%\\Chimera\\browser-host)")
    parser.add_argument(
        "--server-executable",
        help=(
            "Image of the process that serves the pipe, i.e. the running Chimera app. Default: the base "
            "interpreter behind --python (a venv's python.exe only relaunches it). For the desktop build, "
            "pass the installed service executable, <data>\\service-bin\\<version>\\chimera-service.exe: the "
            "registration then pins the service-bin folder, so later app updates keep pairing working."
        ),
    )
    parser.add_argument(
        "--home",
        help=(
            "Chimera data folder (CHIMERA_HOME) the host pairs with. Default: the desktop app's data folder "
            "when --server-executable is its service copy, otherwise unset (the host uses its own settings home)."
        ),
    )
    parser.add_argument("--manifest", help="Manifest output path (defaults to the installation directory)")
    parser.add_argument("--apply", action="store_true", help="Write the wrapper, manifest and HKCU registration")
    parser.add_argument(
        "--skip-vault-check",
        action="store_true",
        help=(
            "Register even though --python cannot read the OS vault. Correct only when the desktop app also "
            "keeps its pairing key in a file (no OS vault there either); otherwise the host stays unpaired."
        ),
    )
    args = parser.parse_args()
    if re.fullmatch(r"[a-p]{32}", args.extension_id) is None:
        parser.error("extension_id must contain exactly 32 lowercase letters from a through p")
    if args.apply and not _on_windows():
        parser.error("--apply is supported only on Windows")

    python = Path(args.python).resolve()
    if args.apply and not python.is_file():
        parser.error(f"Python interpreter does not exist: {python}")
    vault_ok, vault_reason, vault_package = vault_probe(python)
    if not vault_ok:
        warning = vault_warning(python, vault_reason, vault_package)
        if args.apply and not args.skip_vault_check:
            parser.exit(2, f"refusing --apply: {warning}\n(pass --skip-vault-check only for a file-only app)\n")
        print(f"warning: {warning}", file=sys.stderr)
        if args.skip_vault_check:
            print("warning: --skip-vault-check given, registering without the vault", file=sys.stderr)
    server =Path(args.server_executable).resolve() if args.server_executable else _base_interpreter(python)
    service_root = installed_service_root(server) if args.server_executable else None
    root = Path(args.directory) if args.directory else Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Chimera" / "browser-host"
    root = root.resolve()
    manifest_path = Path(args.manifest).resolve() if args.manifest else root / "app.chimera.browser.json"
    wrapper = root / "chimera-browser-host.cmd"
    home = str(Path(args.home).resolve()) if args.home else desktop_data_home(service_root)
    payload = manifest(
        args.extension_id,
        str(wrapper),
        None if service_root else str(server),
        str(service_root) if service_root else None,
        home,
    )
    wrapper_text = _wrapper_text(python, server, service_root, home)
    registry_key = rf"HKCU\Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}"
    print(json.dumps({
        "manifest_path": str(manifest_path), "manifest": payload, "wrapper_path": str(wrapper),
        "wrapper": wrapper_text,
        "registry_key": registry_key,
        "vault": {"usable": vault_ok, "reason": vault_reason},
    }, indent=2))
    if args.apply:
        import winreg
        from typing import Protocol, cast

        class _WinregKey(Protocol):
            def __enter__(self) -> _WinregKey: ...
            def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...

        class _Winreg(Protocol):
            HKEY_CURRENT_USER: object
            REG_SZ: int

            def CreateKey(self, key: object, sub_key: str) -> _WinregKey: ...
            def SetValueEx(
                self, key: _WinregKey, value_name: str, reserved: int, value_type: int, value: str
            ) -> None: ...

        registry = cast(_Winreg, winreg)
        root.mkdir(parents=True, exist_ok=True)
        wrapper.write_bytes(wrapper_text.encode())
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        with registry.CreateKey(registry.HKEY_CURRENT_USER, rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}") as key:
            registry.SetValueEx(key, "", 0, registry.REG_SZ, str(manifest_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
