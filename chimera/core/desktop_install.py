"""How to start the installed desktop backend from a terminal (S1, decision D9).

The frozen backend the desktop app launches writes ``~/.chimera/desktop-install.json`` when it comes
up (``$CHIMERA_SERVICE_DIR`` overrides the folder, the same folder as the service record):
``{exe, cwd, env, version, port}``. ``chimera service start`` reads it so the service it spawns is
the app's own backend, started the way the app starts it, and not whatever ``python`` happens to be
on the PATH.

``cwd`` is not decoration. A frozen build reads only the working directory's ``.env``
(:func:`chimera.config.config_env_files`), and that file is where the Settings screen keeps the
provider keys. A backend started from any other folder boots keyless; recording the folder the app's
backend actually ran in is what makes the keys follow.

``env`` carries four names and no others — :data:`ALLOWED_ENV`. They are paths the desktop shell
hands its backend (the data folder, the tools' root, the shell's preference and state files), and
none of them is a secret. A token, a key or anything else in the environment never reaches this
file, and a file that names any other variable is refused on read, not filtered: it was not written
by this code.

The file is a claim anyone with write access to the folder could have edited, so :func:`load` checks
it before anything runs it: the executable must exist, be a file and be named ``chimera-backend``;
the working directory must be a directory; the version must be a string. An edited file must not be
able to make ``chimera service start`` run another program without saying so.

Standard library only, like :mod:`chimera.core.service_record`.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from chimera.api.bridge_discovery import write_discovery
from chimera.core.service_record import service_dir

INSTALL_FILE = "desktop-install.json"
#: The only environment the record may carry: paths the desktop shell gives its backend
#: (``apps/desktop/src-tauri/src/main.rs``). Never a secret — keys stay in the install's ``.env``.
ALLOWED_ENV = ("CHIMERA_HOME", "CHIMERA_WORKSPACE", "CHIMERA_SHELL_PREFS", "CHIMERA_SHELL_STATE")
#: What the desktop bundle names its sidecar, per platform.
BACKEND_NAME = "chimera-backend.exe" if sys.platform == "win32" else "chimera-backend"


@dataclass(frozen=True)
class InstallRecord:
    exe: str
    cwd: str
    version: str
    port: int | None = None
    env: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InvalidInstallRecord(ValueError):
    """The record exists and must not be used; the message says why."""


def install_path() -> Path:
    return service_dir() / INSTALL_FILE


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def current_record(port: int | None) -> InstallRecord:
    """This process, as a record: its executable, its working directory, the allowed env set."""
    from chimera import __version__

    env = {name: os.environ[name] for name in ALLOWED_ENV if os.environ.get(name)}
    return InstallRecord(
        exe=str(Path(sys.executable).resolve()),
        cwd=str(Path.cwd().resolve()),
        version=__version__,
        port=port,
        env=env,
    )


def write_install(record: InstallRecord, path: Path | None = None) -> None:
    """0600 and atomic, through the writer the service record and the bridge use."""
    unknown = set(record.env) - set(ALLOWED_ENV)
    if unknown:  # a guard on the writer too: nothing outside the list is ever put on disk
        raise ValueError(f"not an install variable: {sorted(unknown)}")
    write_discovery(path or install_path(), record.to_dict())


def record_this_install(port: int | None) -> bool:
    """Called by the backend once bound. Only a frozen backend is an install; a checkout is not."""
    if not is_frozen():
        return False
    write_install(current_record(port))
    return True


def load(path: Path | None = None) -> InstallRecord | None:
    """The validated record; ``None`` when there is none; :class:`InvalidInstallRecord` otherwise."""
    target = path or install_path()
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise InvalidInstallRecord(f"cannot be read ({exc})") from None
    try:
        data = json.loads(text)
    except ValueError:
        raise InvalidInstallRecord("is not JSON") from None
    return validate(data)


def validate(data: Any) -> InstallRecord:
    if not isinstance(data, dict):
        raise InvalidInstallRecord("is not an object")
    exe, cwd, version = data.get("exe"), data.get("cwd"), data.get("version")
    if not isinstance(version, str) or not version:
        raise InvalidInstallRecord("has no version string")
    if not isinstance(exe, str) or not exe:
        raise InvalidInstallRecord("names no executable")
    exe_path = Path(exe)
    if not exe_path.is_absolute():
        raise InvalidInstallRecord(f"names a relative executable ({exe})")
    # The name is checked on the path as written AND on what it resolves to: a link called
    # chimera-backend that points at a shell is still a shell.
    names = {exe_path.name, exe_path.resolve().name}
    if any(name.lower() != BACKEND_NAME for name in names):
        raise InvalidInstallRecord(f"names {exe}, which is not a {BACKEND_NAME}")
    if not exe_path.is_file():
        raise InvalidInstallRecord(f"names {exe}, which does not exist (was the app uninstalled?)")
    if not isinstance(cwd, str) or not cwd or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
        raise InvalidInstallRecord(f"names a working folder that is not a directory ({cwd!r})")
    env = data.get("env") or {}
    if not isinstance(env, dict):
        raise InvalidInstallRecord("has an env that is not an object")
    unknown = sorted(str(name) for name in env if name not in ALLOWED_ENV)
    if unknown:
        raise InvalidInstallRecord(f"sets variables an install never sets: {', '.join(unknown)}")
    if not all(isinstance(value, str) for value in env.values()):
        raise InvalidInstallRecord("has an env value that is not a string")
    port = data.get("port")
    if port is not None and (isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535):
        raise InvalidInstallRecord(f"has an invalid port ({port!r})")
    return InstallRecord(exe=exe, cwd=cwd, version=version, port=port, env=dict(env))
