"""The owner's agreement to a project's ``.worktreeinclude``, per project and per version of the file.

The rules file comes from the repository, so whoever can push to it chooses which of the owner's
ignored files (``.env``, local databases) are copied into every new conversation worktree. Reading it
is not consent: the first time a project's rules file is met — and every time its content changes —
nothing is copied, the files it WOULD copy are recorded here as pending, and the owner's own screen
agrees (``POST /api/code/worktree-include/consent``) or does not. A turn with no screen behind it
(a bridge client, a paired device) can only create the pending record.

The record is a small JSON file in the app's home: ``{"granted": {project: digest}, "pending":
{project: {digest, files, more, bytes, at}}}``. A grant names the digest it was given for, so editing
the rules file asks again.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from chimera.core.filelock import atomic_write_text
from chimera.telemetry import get_logger

_log = get_logger("core.worktree_include_consent")

FILE_NAME = "worktreeinclude_consent.json"

#: Projects remembered as waiting for an answer. Older ones are dropped first.
MAX_PENDING = 50


def consent_path(home: Path | str) -> Path:
    return Path(home) / FILE_NAME


def _key(project: Path | str) -> str:
    try:
        resolved = str(Path(project).resolve())
    except OSError:
        resolved = str(Path(project).absolute())
    return os.path.normcase(resolved)


class IncludeConsent:
    """Reads and writes the record. Every change is under one lock and one atomic write."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"granted": {}, "pending": {}}
        if not isinstance(raw, dict):
            return {"granted": {}, "pending": {}}
        granted = raw.get("granted") if isinstance(raw.get("granted"), dict) else {}
        pending = raw.get("pending") if isinstance(raw.get("pending"), dict) else {}
        return {"granted": granted, "pending": pending}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(data))

    def granted(self, project: Path | str, digest: str) -> bool:
        """Whether the owner agreed to exactly this version of this project's rules file."""
        return bool(digest) and self._read()["granted"].get(_key(project)) == digest

    def request(self, project: Path | str, digest: str, preview: dict[str, Any]) -> None:
        """Record that a turn wanted the copy and was not allowed, with what it would have copied."""
        with self._lock:
            data = self._read()
            pending = data["pending"]
            pending[_key(project)] = {
                "project": str(project),
                "digest": digest,
                "files": list(preview.get("files", [])),
                "more": int(preview.get("more", 0)),
                "bytes": int(preview.get("bytes", 0)),
                "at": time.time(),
            }
            if len(pending) > MAX_PENDING:
                for old in sorted(pending, key=lambda k: pending[k].get("at", 0.0))[:-MAX_PENDING]:
                    del pending[old]
            self._write(data)

    def pending(self) -> list[dict[str, Any]]:
        """The projects waiting for an answer, newest first. A grant for the same digest removes it."""
        data = self._read()
        out = [
            p for k, p in data["pending"].items()
            if isinstance(p, dict) and data["granted"].get(k) != p.get("digest")
        ]
        return sorted(out, key=lambda p: -float(p.get("at", 0.0)))

    def grant(self, project: Path | str, digest: str) -> bool:
        """Agree to ``digest`` for ``project``. Only a digest a turn asked about can be agreed to, so
        a caller cannot pre-approve a file it has not shown."""
        key = _key(project)
        with self._lock:
            data = self._read()
            asked = data["pending"].get(key)
            if not isinstance(asked, dict) or asked.get("digest") != digest:
                return False
            data["granted"][key] = digest
            del data["pending"][key]
            self._write(data)
        _log.info("the owner agreed to the .worktreeinclude of %s", project)
        return True

    def revoke(self, project: Path | str) -> None:
        with self._lock:
            data = self._read()
            data["granted"].pop(_key(project), None)
            self._write(data)


__all__ = ["FILE_NAME", "IncludeConsent", "consent_path"]
