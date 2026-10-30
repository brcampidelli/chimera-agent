"""Design documents on disk: ``<home>/designs/<id>/doc.json`` plus a snapshot per saved revision.

Layout::

    designs/
      <id>/doc.json                the current revision
      <id>/versions/000007.json    one snapshot per saved revision, the newest MAX_VERSIONS kept
      <id>.lock                    the advisory lock for that document (beside the folder, so deleting
                                   the folder never fights an open lock handle on Windows)

**Optimistic concurrency.** ``save`` takes the revision the caller started from. If the file moved on
in the meantime — another window, the agent, an undo in a second tab — the save is refused with
:class:`DesignConflict` carrying the current revision, rather than one writer silently overwriting
the other. The check and the write happen under :func:`chimera.core.filelock.exclusively`, so two
processes cannot both pass the check.

**History is append-only.** ``restore`` writes the old content as a *new* revision; nothing ever
rewrites a snapshot that exists. Retention drops the oldest snapshots past :data:`MAX_VERSIONS`.

**Secrets.** Strings go through :func:`chimera.core.redact.redact_json` before they reach disk, as
code sessions do: a key pasted into a text box comes back as ``[redacted]``. A credential-shaped
identifier or font family would not survive that and the save is refused instead.

The storage card counts this folder as its own ``designs`` category (``chimera.core.storage``).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from chimera.core.filelock import atomic_write_text, exclusively, read_text
from chimera.core.redact import redact_json
from chimera.design.assets import (
    MAX_ASSET_BYTES,
    MAX_DESIGN_ASSET_BYTES,
    AssetInfo,
    is_safe_asset_id,
    validate_asset,
)
from chimera.design.ids import new_id
from chimera.design.migrate import load_document
from chimera.design.model import DesignDocument, DesignError, DesignTokens, wire
from chimera.design.ops import apply_operations, short_errors
from chimera.design.tokens import default_tokens
from chimera.telemetry import get_logger

_log = get_logger("design.store")

#: Snapshots kept per document. Enough for a working session's worth of autosaves to step back
#: through; a document of the maximum size times this stays well under a gigabyte.
MAX_VERSIONS = 50
#: A saved document larger than this is refused: far above anything a person composes, and it keeps
#: one runaway proposal from filling the disk through the snapshots.
MAX_DOCUMENT_BYTES = 5_000_000
DEFAULT_TITLE = "Untitled design"


class DesignNotFound(DesignError, LookupError):
    """No document (or no such revision) under that id."""


class DesignConflict(DesignError):
    """The document moved on since the caller read it."""

    def __init__(self, expected: int, current: int) -> None:
        self.expected = expected
        self.current = current
        super().__init__(
            f"the design is at revision {current}, not {expected}; reload it and apply again"
        )


@dataclass(frozen=True)
class DesignSummary:
    id: str
    title: str
    updated_at: str
    revision: int


@dataclass(frozen=True)
class VersionInfo:
    revision: int
    title: str
    updated_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class DesignStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # ---- paths -------------------------------------------------------------------------------

    def _dir(self, design_id: str) -> Path:
        # Same sanitising as CodeSessionStore._path, plus one rule: an id that sanitising would CHANGE
        # is refused rather than mapped, so "a/b" and "ab" can never alias the same folder.
        safe = "".join(ch for ch in design_id if ch.isalnum() or ch in "-_")[:64]
        if not safe or safe != design_id or not safe.isascii():
            raise DesignNotFound(f"not a design id: {design_id!r}")
        return self.root / safe

    def _doc_path(self, design_id: str) -> Path:
        return self._dir(design_id) / "doc.json"

    def _version_path(self, design_id: str, revision: int) -> Path:
        return self._dir(design_id) / "versions" / f"{revision:06d}.json"

    def put_asset(self, design_id: str, data: bytes, mime_hint: str | None = None) -> str:
        """Validate and content-address an asset, storing each byte sequence only once per design."""
        if not isinstance(data, bytes):
            raise DesignError("asset data must be bytes")
        info = validate_asset(data, declared_mime=mime_hint, max_bytes=MAX_ASSET_BYTES)
        self.get(design_id)  # Do not create an asset namespace for a nonexistent design.
        folder = self._dir(design_id)
        asset_id = "a_" + hashlib.sha256(data).hexdigest()[:32]
        if not is_safe_asset_id(asset_id):
            raise DesignError("generated an unsafe asset id")
        asset_dir = folder / "assets"
        index_path = asset_dir / "index.json"
        with exclusively(self.root / f"{design_id}.lock"):
            index = self._asset_index(index_path)
            current = sum(int(row["size"]) for row in index.values())
            if current > MAX_DESIGN_ASSET_BYTES:
                raise DesignError("design asset index exceeds the 50 MB limit")
            if asset_id not in index and current + len(data) > MAX_DESIGN_ASSET_BYTES:
                raise DesignError(f"design asset limit is 50 MB (currently {current} bytes)")
            asset_dir.mkdir(parents=True, exist_ok=True)
            asset_path = asset_dir / asset_id
            existed = asset_path.exists()
            if existed and asset_path.read_bytes() != data:
                raise DesignError("asset hash collision; refusing to overwrite different content")
            if not asset_path.exists():
                # Binary assets use an atomic same-directory replace, matching the store's JSON writes.
                handle, tmp_name = tempfile.mkstemp(dir=str(asset_dir), prefix=asset_id + ".", suffix=".tmp")
                try:
                    with os.fdopen(handle, "wb") as stream:
                        stream.write(data)
                    os.replace(tmp_name, asset_path)
                except BaseException:
                    Path(tmp_name).unlink(missing_ok=True)
                    raise
            index[asset_id] = {"mime": info.mime, "size": len(data), "width": info.width, "height": info.height}
            try:
                atomic_write_text(index_path, json.dumps(index, separators=(",", ":"), sort_keys=True))
            except OSError:
                if not existed:
                    asset_path.unlink(missing_ok=True)
                raise
        return asset_id

    def _asset_index(self, path: Path) -> dict[str, dict[str, Any]]:
        if not path.exists():
            return {}
        try:
            raw = json.loads(read_text(path))
            if not isinstance(raw, dict):
                raise ValueError("index is not an object")
            result: dict[str, dict[str, Any]] = {}
            for key, value in raw.items():
                if not is_safe_asset_id(key) or not isinstance(value, dict):
                    raise ValueError("unsafe asset index row")
                size = value.get("size")
                mime = value.get("mime")
                if not isinstance(size, int) or isinstance(size, bool) or size < 1 or size > MAX_ASSET_BYTES:
                    raise ValueError("invalid asset size")
                if not isinstance(mime, str):
                    raise ValueError("invalid asset MIME")
                width, height = value.get("width"), value.get("height")
                if set(value) != {"mime", "size", "width", "height"}:
                    raise ValueError("invalid asset index fields")
                if any(
                    dimension is not None and (not isinstance(dimension, int) or isinstance(dimension, bool))
                    for dimension in (width, height)
                ):
                    raise ValueError("invalid asset dimensions")
                if mime in {"font/woff2", "font/woff", "font/ttf"} and (width is not None or height is not None):
                    raise ValueError("font assets cannot have dimensions")
                if mime.startswith("image/") and (
                    not isinstance(width, int) or isinstance(width, bool)
                    or not isinstance(height, int) or isinstance(height, bool)
                ):
                    raise ValueError("image dimensions are missing")
                if mime not in {
                    "image/png", "image/jpeg", "image/webp", "image/gif",
                    "font/woff2", "font/woff", "font/ttf",
                }:
                    raise ValueError("unsupported asset MIME")
                result[key] = value
            if sum(int(row["size"]) for row in result.values()) > MAX_DESIGN_ASSET_BYTES:
                raise ValueError("asset index exceeds the design limit")
            return result
        except (ValueError, TypeError):
            raise DesignError("asset index is invalid") from None

    def get_asset(self, design_id: str, asset_id: str) -> tuple[bytes, AssetInfo]:
        """Return validated asset bytes and sniffed metadata, refusing unsafe ids."""
        self.get(design_id)
        if not is_safe_asset_id(asset_id):
            raise DesignNotFound("not an asset id")
        folder = self._dir(design_id) / "assets"
        self.root.mkdir(parents=True, exist_ok=True)
        with exclusively(self.root / f"{design_id}.lock"):
            index = self._asset_index(folder / "index.json")
            row = index.get(asset_id)
            if row is None:
                raise DesignNotFound("asset not found")
            try:
                data = (folder / asset_id).read_bytes()
            except OSError:
                raise DesignError("asset index points to a missing or unreadable file") from None
            info = validate_asset(data, declared_mime=str(row.get("mime", "")))
            if info.size != row["size"]:
                raise DesignError("asset size does not match its index")
        return data, info

    def list_assets(self, design_id: str) -> list[dict[str, Any]]:
        """List asset metadata only; never includes asset contents."""
        self.get(design_id)
        folder = self._dir(design_id) / "assets"
        self.root.mkdir(parents=True, exist_ok=True)
        with exclusively(self.root / f"{design_id}.lock"):
            index = self._asset_index(folder / "index.json")
        result = []
        for asset_id, row in sorted(index.items()):
            result.append({
                "id": asset_id,
                "mime": row["mime"],
                "size": row["size"],
                "width": row.get("width"),
                "height": row.get("height"),
            })
        return result

    # ---- reading -----------------------------------------------------------------------------

    @staticmethod
    def _read(path: Path) -> DesignDocument:
        text = read_text(path)
        if not text:
            raise DesignNotFound(f"no design at {path.parent.name}")
        try:
            raw = json.loads(text)
        except ValueError as exc:
            raise DesignError(f"{path.name} is not valid JSON") from exc
        try:
            return load_document(raw)
        except ValidationError as exc:
            raise DesignError(f"{path.name} is not a valid design: {short_errors(exc)}") from None

    def get(self, design_id: str) -> DesignDocument:
        return self._read(self._doc_path(design_id))

    def list_designs(self) -> list[DesignSummary]:
        """Every readable design, most recently updated first. Unreadable ones are skipped, logged."""
        if not self.root.is_dir():
            return []
        out: list[DesignSummary] = []
        for folder in self.root.iterdir():
            if not folder.is_dir() or not (folder / "doc.json").is_file():
                continue
            try:
                doc = self.get(folder.name)
            except DesignError as exc:
                _log.warning("design %s skipped: %s", folder.name, exc)
                continue
            out.append(DesignSummary(doc.id, doc.title, doc.updated_at, doc.revision))
        out.sort(key=lambda s: s.updated_at, reverse=True)
        return out

    def versions(self, design_id: str) -> list[VersionInfo]:
        """Saved revisions, newest first, never past the current one."""
        current = self.get(design_id).revision
        folder = self._dir(design_id) / "versions"
        out: list[VersionInfo] = []
        for path in sorted(folder.glob("*.json"), reverse=True) if folder.is_dir() else []:
            if not path.stem.isdigit() or int(path.stem) > current:
                continue
            try:
                doc = self._read(path)
            except DesignError as exc:
                _log.warning("version %s of %s skipped: %s", path.stem, design_id, exc)
                continue
            out.append(VersionInfo(doc.revision, doc.title, doc.updated_at))
        return out

    def get_version(self, design_id: str, revision: int) -> DesignDocument:
        path = self._version_path(design_id, revision)
        if not path.is_file():
            raise DesignNotFound(f"design {design_id!r} has no saved revision {revision}")
        return self._read(path)

    # ---- writing -----------------------------------------------------------------------------

    def create(
        self,
        title: str = DEFAULT_TITLE,
        *,
        tokens: DesignTokens | None = None,
        design_system_id: str | None = None,
        session_id: str | None = None,
    ) -> DesignDocument:
        """A new, empty design at revision 1, with a server-generated id and its first snapshot."""
        design_id = new_id("document")
        try:
            doc = DesignDocument(
                id=design_id,
                title=title.strip()[:160] or DEFAULT_TITLE,
                revision=0,
                design_system_id=design_system_id,
                session_id=session_id,
                tokens=tokens if tokens is not None else default_tokens(),
            )
        except ValidationError as exc:
            raise DesignError(short_errors(exc)) from None
        with exclusively(self._dir(design_id)):
            return self._write(doc, revision=1)

    def save(self, doc: DesignDocument, *, expected_revision: int) -> DesignDocument:
        """Store ``doc`` as revision ``expected_revision + 1``, or raise :class:`DesignConflict`."""
        with exclusively(self._dir(doc.id)):
            self._check_revision(doc.id, expected_revision)
            return self._write(doc, revision=expected_revision + 1)

    def apply(
        self,
        design_id: str,
        operations: list[Any],
        *,
        expected_revision: int,
        allow_lock_changes: bool = True,
        max_ops: int | None = None,
    ) -> tuple[DesignDocument, list[Any]]:
        """Apply operations and save, under one lock. Returns the saved document and the inverse."""
        kwargs: dict[str, Any] = {"allow_lock_changes": allow_lock_changes}
        if max_ops is not None:
            kwargs["max_ops"] = max_ops
        with exclusively(self._dir(design_id)):
            current = self._check_revision(design_id, expected_revision)
            changed, inverse = apply_operations(current, operations, **kwargs)
            return self._write(changed, revision=expected_revision + 1), inverse

    def restore(self, design_id: str, revision: int, *, expected_revision: int) -> DesignDocument:
        """Make an old revision's content the newest revision. History is never rewritten."""
        with exclusively(self._dir(design_id)):
            self._check_revision(design_id, expected_revision)
            old = self.get_version(design_id, revision)
            return self._write(old, revision=expected_revision + 1)

    def delete(self, design_id: str) -> bool:
        """Remove a design and its history. False when there was nothing to remove."""
        folder = self._dir(design_id)
        with exclusively(folder):
            if not folder.is_dir():
                return False
            shutil.rmtree(folder)
        # Another process may hold the lock file open; a leftover lock file is harmless.
        with contextlib.suppress(OSError):
            Path(str(folder) + ".lock").unlink(missing_ok=True)
        return True

    def _check_revision(self, design_id: str, expected: int) -> DesignDocument:
        current = self.get(design_id)
        if current.revision != expected:
            raise DesignConflict(expected, current.revision)
        return current

    def _write(self, doc: DesignDocument, *, revision: int) -> DesignDocument:
        """Caller holds the lock. Writes doc.json first, then the snapshot, then prunes."""
        stamped = wire(doc)
        stamped.update(revision=revision, updatedAt=_now())
        try:
            saved = DesignDocument.model_validate(redact_json(stamped))
        except ValidationError as exc:
            raise DesignError(
                "the design holds a credential-shaped value where only plain identifiers are "
                f"allowed: {short_errors(exc)}"
            ) from None
        text = json.dumps(wire(saved), ensure_ascii=False, indent=1)
        if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise DesignError(f"the design is over {MAX_DOCUMENT_BYTES} bytes")
        atomic_write_text(self._doc_path(saved.id), text)
        # After doc.json, so a failed snapshot leaves a saved document with a gap in its history
        # rather than a history entry for a revision that was never saved.
        try:
            atomic_write_text(self._version_path(saved.id, revision), text)
            self._prune(saved.id)
        except OSError as exc:
            _log.warning("snapshot %s of %s not written: %s", revision, saved.id, exc)
        return saved

    def record_approval(
        self, design_id: str, *, revision: int, decision: str, by: str, note: str = ""
    ) -> DesignDocument:
        """Record the owner's decision on ``revision``. Raises :class:`DesignConflict` unless that is
        still the current revision, so an answer given while the design moved on approves nothing.

        An approval sets ``approved_revision``; an adjustment request only appends to the history.
        The revision itself does not change.
        """
        with exclusively(self.root / f"{design_id}.lock"):
            doc = self._read(self._doc_path(design_id))
            if doc.revision != revision:
                raise DesignConflict(revision, doc.revision)
            history = list(doc.approval_history)
            history.append({"revision": revision, "decision": decision, "by": by[:120],
                            "at": _now(), "note": note[:1000]})
            update: dict[str, Any] = {"approval_history": history, "updated_at": _now()}
            if decision == "approved":
                update["approved_revision"] = revision
            stamped = doc.model_copy(update=update)
            return self._write(stamped, revision=doc.revision)

    def stamp_session(self, design_id: str, session_id: str) -> None:
        """Name the session that just changed this design, so its later turns can find it."""
        with exclusively(self.root / f"{design_id}.lock"):
            doc = self._read(self._doc_path(design_id))
            if doc.session_id == session_id:
                return
            self._write(doc.model_copy(update={"session_id": session_id}), revision=doc.revision)

    def unapproved_for_session(self, session_id: str) -> DesignDocument | None:
        """A design this session changed whose current revision the owner has not approved."""
        for summary in self.list_designs():
            doc = self.get(summary.id)
            if doc.session_id == session_id and doc.approved_revision != doc.revision:
                return doc
        return None

    def _prune(self, design_id: str) -> None:
        folder = self._dir(design_id) / "versions"
        snapshots = sorted(p for p in folder.glob("*.json") if p.stem.isdigit())
        for path in snapshots[:-MAX_VERSIONS]:
            path.unlink(missing_ok=True)
