"""Where an MCP server's OAuth sign-in is kept: the OS vault when there is one, else a 0600 file.

One record per configured server, holding the tokens, the client Chimera registered (or was given)
and the endpoints they belong to. Three rules, each the reason something here looks the way it does:

* **Never in ``mcp.json``.** That file is configuration the CLI, the app and the VPS all read and
  rewrite, and the MCP screen lists from it. A token there is a token in every copy of it. The
  record is keyed by a hash of the server's name, so a listing of the vault or of the folder below
  names no server either.
* **The vault first.** ``keyring`` (the optional ``secrets`` extra) puts the record in the macOS
  Keychain, the Windows Credential Manager or the Secret Service. A JSON record with a JWT access
  token and a refresh token passes Windows' 2,560-byte limit per entry
  (:data:`chimera.config_vault.WINDOWS_BLOB_LIMIT`), so it is written in chunks a single entry can
  hold, with a head entry naming how many there are.
* **Else ``<home>/mcp_oauth/<hash>.json``, mode 0600 in a 0700 folder.** A container and a VPS have
  no keychain, and the bots there still need the token an owner obtained elsewhere. The folder is
  refused to the agent's read tools and named by the shell fence
  (:func:`chimera.core.queue_fence.reaches_mcp_oauth_store`); on Windows the mode bits are advisory
  and the folder inherits the profile's ACL, which already admits only this user.

Nothing here logs a value, and nothing here is reachable from a route: :mod:`chimera.api.mcp_api`
reads :func:`summary`, which carries no token.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("integrations.mcp_oauth_store")

#: The folder under the data folder that holds the file fallback. A literal the shell fence and the
#: read tools also name, so it lives in one place.
FOLDER = "mcp_oauth"
#: A chunk of the JSON record per vault entry. UTF-16 doubles it: 900 characters is 1,800 bytes,
#: under Windows' 2,560 with room for the non-ASCII an identity may carry.
_CHUNK = 900
#: The head entry's value says how many chunks follow; anything else in it is not ours.
_HEAD = "chunks:"
_MAX_CHUNKS = 64

#: One lock for every read-modify-write of a record in this process: a refresh that rotates the
#: refresh token and a second refresh racing it would otherwise each write their own and lose one.
LOCK = threading.RLock()


def _digest(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:32]


def _account(name: str) -> str:
    # `mcp-oauth-` (no 2) is the older single-token entry of `config_vault.store_mcp_token`; a new
    # prefix keeps a record from being read back as a bare token, and the reverse.
    return f"mcp-oauth2-{_digest(name)}"


def store_folder(home: Path) -> Path:
    return Path(home).expanduser() / FOLDER


def _file(home: Path, name: str) -> Path:
    return store_folder(home) / f"{_digest(name)}.json"


def _keyring() -> Any | None:
    from chimera import config_vault

    return config_vault._keyring()


def _vault_read(kr: Any, name: str) -> str | None:
    from chimera.config_vault import SERVICE

    account = _account(name)
    try:
        head = kr.get_password(SERVICE, account)
    except Exception:  # noqa: BLE001 — an unreadable entry is an absent one
        return None
    if not head or not str(head).startswith(_HEAD):
        return None
    try:
        count = int(str(head)[len(_HEAD):])
    except ValueError:
        return None
    if not 0 < count <= _MAX_CHUNKS:
        return None
    parts: list[str] = []
    for index in range(count):
        try:
            part = kr.get_password(SERVICE, f"{account}.{index}")
        except Exception:  # noqa: BLE001
            return None
        if part is None:
            return None
        parts.append(str(part))
    return "".join(parts)


def _vault_delete(kr: Any, name: str) -> bool:
    from chimera.config_vault import SERVICE

    account = _account(name)
    removed = False
    try:
        head = kr.get_password(SERVICE, account)
    except Exception:  # noqa: BLE001
        head = None
    count = _MAX_CHUNKS
    if head and str(head).startswith(_HEAD):
        try:
            count = min(int(str(head)[len(_HEAD):]), _MAX_CHUNKS)
        except ValueError:
            count = _MAX_CHUNKS
    for key in [account, *(f"{account}.{i}" for i in range(count))]:
        try:
            kr.delete_password(SERVICE, key)
            removed = True
        except Exception:  # noqa: BLE001 — "not found" is the common case
            continue
    return removed


def _vault_write(kr: Any, name: str, text: str) -> bool:
    from chimera.config_vault import SERVICE

    chunks = [text[i : i + _CHUNK] for i in range(0, len(text), _CHUNK)] or [""]
    if len(chunks) > _MAX_CHUNKS:
        return False
    account = _account(name)
    try:
        _vault_delete(kr, name)
        for index, chunk in enumerate(chunks):
            kr.set_password(SERVICE, f"{account}.{index}", chunk)
        # The head last: a write that dies halfway leaves no head, which reads as no record rather
        # than as a record cut short.
        kr.set_password(SERVICE, account, f"{_HEAD}{len(chunks)}")
    except Exception:  # noqa: BLE001 — never the value in a message; a locked vault is a refusal
        _log.warning("could not write an MCP OAuth record to the OS vault")
        _vault_delete(kr, name)
        return False
    return True


def _file_write(home: Path, name: str, text: str) -> None:
    folder = store_folder(home)
    folder.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):  # a file system without modes
        os.chmod(folder, 0o700)
    target = _file(home, name)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".", suffix=".tmp")
    try:
        # mkstemp already creates the file 0600 on POSIX; said again so a future change to how the
        # temporary file is made cannot widen it silently.
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, target)
        os.chmod(target, 0o600)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load(home: Path, name: str) -> dict[str, Any] | None:
    """The stored record of server ``name``, or None. The vault's copy wins over the file's."""
    kr = _keyring()
    text = _vault_read(kr, name) if kr is not None else None
    if text is None:
        try:
            text = _file(home, name).read_text(encoding="utf-8")
        except OSError:
            return None
    try:
        record = json.loads(text)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def save(home: Path, name: str, record: dict[str, Any]) -> str:
    """Store ``record`` for ``name``; returns where it went (``"vault"`` or ``"file"``)."""
    text = json.dumps(record, sort_keys=True, separators=(",", ":"))
    kr = _keyring()
    if kr is not None and _vault_write(kr, name, text):
        # A file left from before the vault existed would be read by nothing, and still be a token
        # on disk.
        _file(home, name).unlink(missing_ok=True)
        return "vault"
    _file_write(home, name, text)
    return "file"


def delete(home: Path, name: str) -> bool:
    """Remove every stored copy of ``name``'s record. True when something was removed."""
    removed = False
    kr = _keyring()
    if kr is not None:
        removed = _vault_delete(kr, name)
    path = _file(home, name)
    if path.exists():
        path.unlink(missing_ok=True)
        removed = True
    return removed


def summary(record: dict[str, Any] | None) -> dict[str, Any]:
    """What a route may say about a record: whether it holds a usable sign-in and as whom. No token."""
    if not record:
        return {"connected": False, "identity": None}
    raw = record.get("tokens")
    tokens: dict[str, Any] = raw if isinstance(raw, dict) else {}
    connected = bool(tokens.get("access_token") or tokens.get("refresh_token"))
    identity = record.get("identity")
    return {
        "connected": connected,
        "identity": identity if isinstance(identity, str) and identity else None,
    }
