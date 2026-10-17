"""A plugin handed to the app by its owner: one ``.zip``/``.tar.gz``, or a picked folder's files.

The Plugins screen's "Carregar pasta/.zip". What arrives is hostile input until read, so it goes
through the same bounds as an archive install (:mod:`chimera.plugins.fetch`: entry count, per-file and
total size, no links, no name that leaves the folder) and is written to
``<CHIMERA_HOME>/plugin-uploads/<digest>/``, named by the sha256 of what was received. That folder is
the plugin's recorded source: an upload is pinned by its bytes the way an archive is pinned by its
``sha256``, and ``update`` re-reading it finds the same bytes.

Then it is an ordinary local install — ``pending``, nothing run, nothing switched on. Uploading again
a plugin that is installed is an UPDATE when the owner asks for the replacement: the plugin lands
``held`` or ``pending`` with the diff, never on (:func:`chimera.plugins.registry.update`).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from collections.abc import Iterable
from pathlib import Path

from chimera.plugins.fetch import (
    MAX_FILE_BYTES,
    FetchError,
    _members,
    _strip_top,
    _write,
    local_spec,
)
from chimera.plugins.manifest import read_plugin
from chimera.plugins.registry import (
    InstalledPlugin,
    PluginError,
    PluginExists,
    UpdateResult,
    get,
    install,
    update,
)
from chimera.plugins.scan import MAX_ENTRIES, MAX_TOTAL_BYTES

UPLOADS = "plugin-uploads"
#: The folder name `_unpack` writes: the first 24 hex characters of the upload's digest.
_FOLDER = re.compile(r"[0-9a-f]{24}")
_ARCHIVES = (".zip", ".tar.gz", ".tgz", ".tar")


def _digest(received: list[tuple[str, bytes]]) -> str:
    hasher = hashlib.sha256()
    for name, data in sorted(received):
        hasher.update(name.encode("utf-8", "replace") + b"\0" + hashlib.sha256(data).digest())
    return hasher.hexdigest()


def _entries(received: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """The files of the plugin, from one archive or from a folder's files, top folder stripped."""
    if not received:
        raise PluginError("nothing was uploaded")
    if len(received) == 1 and received[0][0].lower().endswith(_ARCHIVES):
        try:
            members = _members(received[0][1])
        except FetchError as exc:
            raise PluginError(str(exc)) from exc
    else:
        if len(received) > MAX_ENTRIES:
            raise PluginError(f"more than {MAX_ENTRIES} files")
        total = 0
        for name, data in received:
            if len(data) > MAX_FILE_BYTES:
                raise PluginError(f"{name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MiB")
            total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise PluginError(f"the upload is larger than {MAX_TOTAL_BYTES // (1024 * 1024)} MiB")
        members = list(received)
    top = _strip_top([name for name, _ in members])
    out = [(name[len(top):] if top else name, data) for name, data in members]
    return [(name, data) for name, data in out if name]


def _unpack(received: list[tuple[str, bytes]], home: Path) -> tuple[Path, bool]:
    """The upload's folder, and whether this call created it. Named by the digest of the names and
    bytes received, so an existing folder already holds exactly these files — and may be the source
    an installed plugin records, which is why it is reused and never rewritten or removed here."""
    entries = _entries(received)
    folder = Path(home) / UPLOADS / _digest(received)[:24]
    if folder.is_dir():
        return folder, False
    folder.mkdir(parents=True)
    try:
        for rel, data in entries:
            _write(folder, rel, data)
    except FetchError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise PluginError(str(exc)) from exc
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder, True


def import_upload(received: list[tuple[str, bytes]], home: Path, *, replace: bool = False,
                  label: str = "") -> InstalledPlugin | UpdateResult:
    """Install the uploaded plugin ``pending`` — or, with ``replace``, update the installed one.

    :class:`PluginExists` when the name is taken and ``replace`` was not asked for, so the screen can
    offer the replacement instead of only reporting a failure (the skill upload's 409).
    """
    folder, created = _unpack(received, home)
    try:
        report = read_plugin(folder, fallback_name=label or None)
        name = report.manifest.name if report.manifest is not None else ""
        if name and get(home, name) is not None:
            if not replace:
                raise PluginExists(f"{name} is already installed — upload it again as a replacement")
            return update(home, name, local_spec(folder))
        return install(local_spec(folder), home, fallback_name=label or None)
    except BaseException:
        if created:
            shutil.rmtree(folder, ignore_errors=True)
        raise


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def upload_folder_of(home: Path, record: InstalledPlugin) -> Path | None:
    """The upload folder ``record`` was installed from, or None when it is not one.

    Only a folder this module wrote qualifies: a local source whose parent is exactly
    ``<home>/plugin-uploads`` and whose name is a digest. Nothing on the way may be a link — not the
    uploads folder, not the plugin's folder — and the path must resolve to where it says it is, so a
    junction or a ``..`` cannot point the deletion somewhere else.
    """
    if record.source_kind != "local" or not record.source:
        return None
    root = Path(home) / UPLOADS
    folder = Path(record.source)
    try:
        roots = {_key(str(root)), _key(str(root.resolve()))}
    except OSError:
        return None
    # The install records the source resolved, so the parent is compared to both spellings of root.
    if not _FOLDER.fullmatch(folder.name) or _key(str(folder.parent)) not in roots:
        return None
    for path in (root, folder):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) or not path.is_dir():
            return None
    try:
        if _key(str(folder.resolve(strict=True))) != _key(str(root.resolve(strict=True) / folder.name)):
            return None
    except OSError:
        return None
    return folder


def forget_upload(home: Path, record: InstalledPlugin, remaining: Iterable[InstalledPlugin]) -> bool:
    """Delete the removed plugin's own upload folder, unless another installed plugin still names it
    as its source. Returns whether anything was deleted."""
    folder = upload_folder_of(home, record)
    if folder is None:
        return False
    if any(other.source_kind == "local" and other.source and _key(other.source) == _key(str(folder))
           for other in remaining if other.name != record.name):
        return False
    # `rmtree` refuses a link at the top and unlinks (never follows) links inside.
    shutil.rmtree(folder, ignore_errors=True)
    return not folder.exists()


__all__ = ["UPLOADS", "forget_upload", "import_upload", "upload_folder_of"]
