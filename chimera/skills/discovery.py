"""Discover local Agent Skills without treating their presence as the owner's approval.

A discovered skill is copied through the same bounded, tainted import path as an uploaded bundle.
The copy gives the running agent a stable, auditable snapshot; the source path and hash remain on
its record so a later listing can notice edits. Discovery is deliberately read-only with respect to
source trees and never activates a skill.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from chimera.skills.bundle_upload import (
    MAX_DESCRIPTION_CHARS,
    _frontmatter,
    _text_field,
    import_upload,
)
from chimera.skills.bundles import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    BundleError,
    InstalledBundle,
    bundles_root,
    installed,
)

MAX_SCAN_DEPTH = 12
MAX_SCAN_ENTRIES = 5_000


@dataclass(frozen=True)
class _Source:
    root: Path
    priority: int
    label: str


@dataclass(frozen=True)
class _Candidate:
    manifest: Path
    skill_root: Path | None
    source: _Source
    name: str
    manifest_bytes: bytes
    digest: str


def _sources(home: Path, workspace: Path | None) -> list[_Source]:
    roots: list[tuple[Path, str]] = []
    if workspace is not None:
        roots.extend([
            (Path(workspace) / ".claude" / "skills", "workspace:.claude"),
            (Path(workspace) / ".agents" / "skills", "workspace:.agents"),
        ])
    roots.extend([
        (Path.home() / ".claude" / "skills", "user:.claude"),
        (Path.home() / ".agents" / "skills", "user:.agents"),
    ])
    sources: list[_Source] = []
    seen: set[Path] = set()
    for priority, (root, label) in enumerate(roots):
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved not in seen and resolved.is_dir():
            seen.add(resolved)
            sources.append(_Source(resolved, priority, label))
    return sources


def _walk(source: _Source) -> list[Path]:
    """Find manifests without following links or allowing an accidental unbounded walk."""
    found: list[Path] = []
    visited = 0
    for current, dirs, files in os.walk(source.root, followlinks=False):
        visited += len(dirs) + len(files)
        if visited > MAX_SCAN_ENTRIES:
            break
        directory = Path(current)
        try:
            depth = len(directory.relative_to(source.root).parts)
        except ValueError:
            dirs[:] = []
            continue
        dirs[:] = sorted(
            name for name in dirs
            if depth < MAX_SCAN_DEPTH and not (directory / name).is_symlink()
        )
        for filename in files:
            path = directory / filename
            if path.is_symlink() or not path.is_file():
                continue
            if filename.casefold() == "skill.md" or (depth == 0 and path.suffix.casefold() == ".md"):
                found.append(path)
    return sorted(found, key=lambda path: path.as_posix().casefold())


def _candidates(sources: list[_Source]) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for source in sources:
        manifests = _walk(source)
        for manifest in manifests:
            try:
                raw = manifest.read_bytes()
                if len(raw) > MAX_FILE_BYTES:
                    continue
                text = raw.decode("utf-8-sig")
                front = _frontmatter(text)
                name = _text_field(front, "name").strip()
                description = _text_field(front, "description").strip()
                if (
                    not name
                    or len(name) > 64
                    or not name[0].islower()
                    or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in name)
                    or not description
                    or len(description) > MAX_DESCRIPTION_CHARS
                ):
                    continue
                skill_root = (
                    manifest.parent
                    if manifest.name.casefold() == "skill.md" and manifest.parent != source.root
                    else None
                )
                candidates.append(_Candidate(
                    manifest=manifest,
                    skill_root=skill_root,
                    source=source,
                    name=name,
                    manifest_bytes=raw,
                    digest=hashlib.sha256(raw).hexdigest(),
                ))
            except Exception:  # noqa: BLE001 -- one malformed local file must not break the listing
                # An invalid manifest is isolated to itself; a local skills directory is not a
                # trust boundary, and one malformed card must not erase the rest of the listing.
                continue
    return sorted(candidates, key=lambda item: (item.source.priority, item.manifest.as_posix().casefold()))


def _uploads(candidate: _Candidate, all_candidates: list[_Candidate]) -> list[tuple[str, bytes]]:
    manifest = candidate.manifest
    if candidate.skill_root is None:
        return [("SKILL.md", candidate.manifest_bytes)]

    root = candidate.skill_root
    nested_roots = {
        other.skill_root for other in all_candidates
        if other.skill_root is not None and other.skill_root != root
        and root in other.skill_root.parents
    }
    nested_manifests = {
        other.manifest for other in all_candidates
        if other.skill_root is None and root in other.manifest.parent.parents
    }
    uploads: list[tuple[str, bytes]] = []
    total = 0
    visited = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        visited += len(dirs) + len(files)
        if visited > MAX_SCAN_ENTRIES:
            raise BundleError("local skill exceeds the bundle file limits")
        directory = Path(current)
        dirs[:] = sorted(
            name for name in dirs
            if not (directory / name).is_symlink()
            and not any(
                (directory / name) == child or (directory / name) in child.parents
                for child in nested_roots
            )
            and not any(
                (directory / name) == manifest_path.parent
                or (directory / name) in (manifest_path.parent, *manifest_path.parent.parents)
                for manifest_path in nested_manifests
            )
        )
        for filename in sorted(files):
            path = directory / filename
            if path.is_symlink() or not path.is_file():
                continue
            try:
                relative = path.relative_to(root).as_posix()
                blob = candidate.manifest_bytes if path == manifest else path.read_bytes()
            except (OSError, ValueError):
                continue
            if len(blob) > MAX_FILE_BYTES or len(uploads) >= MAX_FILES:
                raise BundleError("local skill exceeds the bundle file limits")
            total += len(blob)
            if total > MAX_TOTAL_BYTES:
                raise BundleError("local skill exceeds the bundle size limit")
            uploads.append((relative, blob))
    return uploads


def discover_local_skills(home: Path, workspace: Path | None = None) -> list[InstalledBundle]:
    """Import or refresh discovered skills, preserving pending approval and provenance.

    Workspace sources precede user sources. When two candidates declare the same bundle id, the
    first wins, and every colliding source path is kept on the winner for the owner to inspect.
    A source hash change forces a fresh pending import even if its previous snapshot was approved.
    """
    sources = _sources(home, workspace)
    candidates = _candidates(sources)
    by_name: dict[str, list[_Candidate]] = {}
    for candidate in candidates:
        by_name.setdefault(candidate.name, []).append(candidate)

    # A separately installed bundle owns its id even when discovery has several candidates; never
    # let an incidental local file replace a catalogue/upload install.
    existing = {record.name: record for record in installed(home)}
    for name, matches in by_name.items():
        winner = matches[0]
        record = existing.get(name)
        source_path = str(winner.manifest)
        if record is not None and record.origin != "discovered":
            # A local scan never claims or overwrites a separately installed skill just because
            # its manifest happens to use the same id.
            continue
        if (
            record is not None
            and record.source_path == source_path and record.source_hash == winner.digest
        ):
            updated = record
            if record.status == "active" and not record.switched_on_at:
                # `installed()` deliberately surfaces legacy activation as pending. Persist that
                # state before a rescan so an unchanged source cannot restore the stale approval.
                meta = bundles_root(home) / record.name / "bundle.json"
                try:
                    raw = json.loads(meta.read_text(encoding="utf-8"))
                    if raw.get("status") == "active" and not raw.get("switched_on_at"):
                        raw["status"] = "pending"
                        meta.write_text(
                            json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8"
                        )
                except (OSError, ValueError, TypeError):
                    pass
        else:
            try:
                updated = import_upload(
                    _uploads(winner, candidates), home, replace=record is not None,
                    label=f"discovered: {source_path}", origin="discovered",
                    source_path=source_path, source_hash=winner.digest,
                )
            except (BundleError, OSError, UnicodeError, ValueError, TypeError):
                continue
        collisions = [str(item.manifest) for item in matches[1:]]
        if updated.collisions != collisions:
            updated.collisions = collisions
            meta = bundles_root(home) / updated.name / "bundle.json"
            try:
                raw = json.loads(meta.read_text(encoding="utf-8"))
                raw["collisions"] = collisions
                meta.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
            except (OSError, ValueError, TypeError):
                pass
        existing[name] = updated
    return installed(home)
