"""The pin file: which commit of each public catalogue the app reads, and the sha256 of every file.

``pins.json`` ships inside the package. For each catalogue it names the repository, one full commit
sha, and the sha256 of each file the app reads at that commit. The loader fetches nothing else and
parses nothing whose bytes do not hash to the recorded value, so a catalogue cannot change under the
owner: a push to the repository, a force-push that rewrites the commit, or a tampered response all
read as "refused", never as a different catalogue.

Updating the file is a release's deliberate act (``scripts/bump_catalog_pins.py``, reviewed in a pull
request); nothing at runtime writes it. ``chimera catalog pins --check`` only reports which
repositories moved.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CatalogKind = Literal["connectors", "skills", "plugins"]
KINDS: tuple[CatalogKind, ...] = ("connectors", "skills", "plugins")

PINS_FILE = Path(__file__).with_name("pins.json")

#: One catalogue file's cap. The largest today (the connectors' generated ``catalog.json``) is well
#: under 100 KB; a response past this is refused, not truncated and parsed.
MAX_CATALOG_FILE_BYTES = 1024 * 1024

_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_PATH_PART = re.compile(r"^[A-Za-z0-9._-]+$")


def _safe_path(path: str) -> bool:
    """A relative POSIX path of plain segments: no ``..``, no root, no drive, no backslash."""
    pure = PurePosixPath(path)
    return (
        bool(path)
        and not path.startswith("/")
        and "\\" not in path
        and all(part not in (".", "..") and _PATH_PART.match(part) for part in pure.parts)
        and str(pure) == path
    )


class CatalogPin(BaseModel):
    """One catalogue at one commit, and the hash of every file read from it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    owner: str
    repo: str
    sha: str
    files: dict[str, str] = Field(min_length=1)

    @field_validator("owner", "repo")
    @classmethod
    def _name(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError(f"not a GitHub owner or repository name: {value!r}")
        return value

    @field_validator("sha")
    @classmethod
    def _commit(cls, value: str) -> str:
        if not _SHA1.match(value):
            raise ValueError("the commit must be a full 40-character lowercase sha")
        return value

    @field_validator("files", mode="before")
    @classmethod
    def _file_list(cls, value: object) -> object:
        """``pins.json`` lists files as ``{"path", "sha256"}`` objects, one key per line.

        A ``{"path": "digest"}`` mapping put each hash on the same line as its file name, and a secret
        scanner reads ``"catalog/sentry.json": "<64 hex>"`` as a Sentry token and any name with ``api``
        in it as an API key. The list keeps the digest on a line of its own, so the scanner needs no
        exception for this file and still catches a real key placed in it.
        """
        if not isinstance(value, list):
            return value
        files: dict[str, str] = {}
        for entry in value:
            if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
                raise ValueError('each file must be an object with exactly "path" and "sha256"')
            path, digest = entry["path"], entry["sha256"]
            if not isinstance(path, str) or not isinstance(digest, str):
                raise ValueError('"path" and "sha256" must be strings')
            if path in files:
                raise ValueError(f"{path}: listed twice")
            files[path] = digest
        return files

    @field_validator("files")
    @classmethod
    def _files(cls, value: dict[str, str]) -> dict[str, str]:
        for path, digest in value.items():
            if not _safe_path(path):
                raise ValueError(f"not a safe relative path: {path!r}")
            if not _SHA256.match(digest):
                raise ValueError(f"{path}: the sha256 must be 64 lowercase hex characters")
        return value

    @property
    def label(self) -> str:
        return f"{self.owner}/{self.repo}@{self.sha[:12]}"


class PinFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal[1]
    catalogs: dict[CatalogKind, CatalogPin]


def parse_pins(text: str) -> PinFile:
    """Validate a pin file's text. Raises ``ValueError`` (pydantic's included) on any defect."""
    return PinFile.model_validate(json.loads(text))


@lru_cache(maxsize=1)
def load_pins() -> PinFile:
    """The shipped pin file. A broken one is a packaging bug, and the tests read it on every run."""
    return parse_pins(PINS_FILE.read_text(encoding="utf-8"))


def pinned_digest(pins: PinFile, owner: str, repo: str, sha: str, path: str) -> str | None:
    """The recorded sha256 when ``(owner, repo, sha, path)`` is exactly a pinned tuple, else None."""
    for pin in pins.catalogs.values():
        if (pin.owner, pin.repo, pin.sha) == (owner, repo, sha):
            return pin.files.get(path)
    return None


__all__ = [
    "KINDS",
    "MAX_CATALOG_FILE_BYTES",
    "PINS_FILE",
    "CatalogKind",
    "CatalogPin",
    "PinFile",
    "load_pins",
    "parse_pins",
    "pinned_digest",
]
