"""Workspace snapshot/restore — the mechanism behind verify-or-revert.

Before an autonomous attempt, take a snapshot; if verification fails, restore it.
This is a text-file checkpoint (dependency-free, fully testable): it records the
contents of text files and the set of all files present, then on restore deletes
files created since, rewrites changed ones, and recreates deleted ones.

Binary files are tracked for presence (so they are not deleted) but their contents
are not snapshotted. Large files and common build/VCS dirs are skipped.

Which files, and how they are found, is what a snapshot costs — and every coding turn
takes one before it calls the model. Measured on 2026-09-17 on the project's own
repository (142,000 entries on disk, most of them under ``node_modules``, ``.venv-win``
and a Rust ``target``): ``rglob("*")`` walked all of them in 5.5 s and then read the
first 5,000 files it met, 58 MB, for 8.7 s per turn — a spoken "answer with one word"
reached its first token 9.7 s after the request, with the model's own call at 2.7 s.
And the 5,000 it kept were whichever came first in walk order, which need not include
the source at all. Inside a git repository the files are now the ones git considers
the project's — tracked, and untracked but not ignored — listed by ``git ls-files`` in
40 ms and read in 0.29 s; that set is also the honest one for an undo, since ignored
files are build output and data by the repository's own definition. Outside a
repository the walk prunes the ignored directories instead of entering them.

The "delete files created since" pass is destructive, so it is skipped whenever it
cannot be done safely: when the snapshot was truncated at the file cap, and when the
workspace is anywhere inside a git repository (``.git`` at the workspace **or any
ancestor**) — verify-or-revert must never delete a repo's files.

That last guard is deliberately broad, and the trade-off is explicit: running against
a workspace inside a repo (``chimera solve -w .`` in your own project, or a scratch dir
under a git-managed ``$HOME``) keeps files a failed attempt created, instead of pruning
them. Leftover junk is recoverable; deleting a developer's tracked files is not. Content
*restoration* is unaffected — only the deletion pass is skipped.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import traceback
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from chimera.core.target_path import validate_target
from chimera.telemetry import get_logger

_log = get_logger("core.checkpoint")

_IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".chimera",
}
_MAX_FILE_BYTES = 1_000_000
_GIT_LIST_TIMEOUT = 15.0
_SNAPSHOT_READ_WARNING_LIMIT = 200


def _pruned(name: str) -> bool:
    """A directory the walk does not enter: the ignore list, any virtualenv (``.venv-win``,
    ``venv311``), egg metadata, and a Rust/Java ``target``."""
    return (
        name in _IGNORE_DIRS
        or name.startswith((".venv", "venv"))
        or name.endswith(".egg-info")
        or name == "target"
    )


def git_listed(workspace: Path) -> list[Path] | None:
    """The files git counts as this directory's — tracked, plus untracked and not ignored — or
    None when the directory is not in a repository or git is not there to ask.

    Relative to the directory itself (``-C``), so a workspace that is a folder inside a larger
    repository gets that folder's files and not the repository's. ``-z`` for raw names: no
    quoting, whatever the file is called.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(workspace), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            timeout=_GIT_LIST_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    paths = []
    for part in proc.stdout.split(b"\0"):
        if not part:
            continue
        rel = part.decode("utf-8", "surrogateescape").replace("\\", "/")
        paths.append(workspace / rel)
    return paths


def _read_exact(path: Path, *, errors: str = "strict") -> str:
    r"""A file's text with its line endings as they are on disk.

    ``Path.read_text`` turns ``\r\n`` and a lone ``\r`` into ``\n``, and ``Path.write_text`` on
    Windows turns every ``\n`` into ``\r\n``: an undo that went through both rewrote every LF
    file it restored with CRLF endings, and lost CRLF everywhere else. Decoding the bytes keeps
    them; ``read_text(newline="")`` would too, but only from Python 3.13.
    """
    return path.read_bytes().decode("utf-8", errors=errors)


def _write_exact(path: Path, text: str) -> None:
    """Write ``text`` as it is, with no newline translation (see :func:`_read_exact`)."""
    path.write_bytes(text.encode("utf-8"))


def _safe_target(workspace: Path, rel: str) -> Path:
    """Validate a relative target without allowing symlink traversal."""
    return validate_target(workspace, rel)


class _SnapshotFiles(Mapping[str, str]):
    """Lazy file-content view: enumerating keys never reads every file."""

    def __init__(self, keys: set[str], reader: Callable[[str], str | None], seed: Mapping[str, str] | None = None) -> None:
        self._keys = keys
        self._reader = reader
        self._cache: dict[str, str] = dict(seed or {})
        self._individual_reads = 0
        self._read_warning_emitted = False

    def __getitem__(self, key: str) -> str:
        self._individual_reads += 1
        if (
            self._individual_reads > _SNAPSHOT_READ_WARNING_LIMIT
            and not self._read_warning_emitted
        ):
            self._read_warning_emitted = True
            callers = traceback.extract_stack(limit=5)[:-1]
            stack = " <- ".join(f"{frame.name}:{frame.lineno}" for frame in callers[-3:])
            _log.warning(
                "snapshot lazy mapping exceeded %d individual reads; caller stack: %s",
                _SNAPSHOT_READ_WARNING_LIMIT,
                stack,
            )
        if key not in self._keys:
            raise KeyError(key)
        if key not in self._cache:
            value = self._reader(key)
            if value is None:
                raise KeyError(key)
            self._cache[key] = value
        return self._cache[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)




@dataclass
class FileSnapshot:
    """A point-in-time capture with a lazy mapping of project paths to textual contents."""

    files: Mapping[str, str] = field(default_factory=dict)
    present: set[str] = field(default_factory=set)
    git_head: str | None = None
    git_dirty: set[str] = field(default_factory=set)
    incomplete: bool = False
    workspace: Path | None = None
    git_prefix: str = ""
    disk_view: bool = False
    git_status: str = ""
    git_index: str = ""
    incomplete_reason: str = ""


#: A file's state as a turn left it: whether it is there, and its text (None when it is absent, or
#: present but binary or unreadable, which a snapshot can see only by presence).
FileState = tuple[bool, str | None]


@dataclass
class TurnChange:
    """What one turn changed in a workspace, and both sides of each change.

    Every file whose text or presence differs between the snapshot taken before the turn and the
    workspace right after it. Measured on the folder, not taken from the edit tool's reports, so a
    file written through the shell is part of the turn too.
    """

    #: The changed paths only, as they were before the turn. Not the whole folder: an offer used to
    #: hold a copy of every file, for as many offers as the app kept.
    before: FileSnapshot
    #: Each changed path, as the turn left it.
    after: dict[str, FileState] = field(default_factory=dict)
    #: The snapshot before the turn hit the file cap, so "created by the turn" cannot be told from
    #: "existed but was not captured", and nothing is deleted on an undo.
    truncated: bool = False
    #: Set when the captured checkout's HEAD differs from the one at restore time.
    incomplete_reason: str = ""

    @property
    def paths(self) -> list[str]:
        return sorted(self.after)


@dataclass
class ChangeRestore:
    """What undoing one turn did."""

    restored: int = 0
    #: Files that changed again after the turn (another conversation, the person, a formatter): left
    #: as they are, because putting the old text back would erase work that is not the turn's.
    kept: list[str] = field(default_factory=list)
    #: Files the turn created that were not removed: inside a git repository, or after a truncated
    #: snapshot, a restore never deletes (see `restore`).
    left_new: list[str] = field(default_factory=list)


def fingerprint(snap: FileSnapshot) -> str:
    """A stable digest of everything a snapshot captured — content AND presence.

    Exists so a receipt can be checked rather than believed. A run reported ``verified: True`` with
    the verifier's own output showing ``Ran 20 tests ... OK``, and the tree it left on disk failed
    all twenty, deterministically: the diff the receipt stored contained one line the delivered file
    did not have. Something wrote after the moment the verdict describes.

    Comparing two of these answers "is what was delivered still what was verified?" without needing
    to know which step wrote afterwards — which is the point. A guard that requires you to have
    already found the culprit is not a guard.

    Presence is in the digest and not only content: a file that appears or disappears changes what
    the verify command would do, and a digest over content alone would call that no change. Binary
    and unreadable files are in ``present`` without content (see :meth:`WorkspaceGuard.snapshot`),
    so they are covered by presence and not by their bytes — a limit worth knowing rather than
    hiding, and it matches exactly what the snapshot could see in the first place.
    """
    h = hashlib.sha256()
    if snap.git_head is not None:
        h.update(snap.git_head.encode("ascii", "replace"))
        h.update(b"\x00")
        h.update(snap.git_index.encode("utf-8", "surrogateescape"))
        h.update(b"\x00")
        dirty = snap.git_dirty
        if snap.workspace is not None:
            try:
                status = subprocess.run(
                    ["git", "-C", str(snap.workspace), "-c", "core.quotepath=false", "status", "--relative",
                     "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"],
                    capture_output=True, timeout=_GIT_LIST_TIMEOUT, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                status = None
            if status is not None and status.returncode == 0:
                dirty = set()
                for entry in status.stdout.split(b"\0"):
                    if len(entry) >= 4 and entry[2:3] == b" ":
                        dirty.add(entry[3:].decode("utf-8", "surrogateescape").replace("\\", "/"))
        for rel in sorted(dirty):
            h.update(rel.encode("utf-8", "surrogateescape"))
            h.update(b"\x00")
            try:
                value = _read_exact(snap.workspace / rel) if snap.workspace is not None else snap.files.get(rel)
            except (OSError, UnicodeDecodeError, subprocess.SubprocessError):
                value = None
            h.update(b"?" if value is None else hashlib.sha256(value.encode("utf-8")).digest())
            h.update(b"\x00")
        return h.hexdigest()
    for rel in sorted(snap.present):
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        try:
            conteudo = snap.files.get(rel)
        except (OSError, subprocess.SubprocessError):
            conteudo = None
        # A file present-but-unread is distinguished from one that is present and empty. Hashing
        # both as b"" would make replacing a text file with a binary of the same name invisible.
        h.update(b"?" if conteudo is None else hashlib.sha256(conteudo.encode("utf-8")).digest())
        h.update(b"\x00")
    return h.hexdigest()


def snapshot_changed_paths(before: FileSnapshot, after: FileSnapshot) -> set[str]:
    """Return paths whose captured state differs, using Git metadata to avoid clean files.

    Git-backed snapshots are immutable at their captured HEAD apart from the paths listed dirty.
    Comparing their HEADs adds committed changes to that small candidate set. If the metadata is
    insufficient or Git cannot provide the diff, fall back to the full snapshot comparison.
    """
    candidates: set[str] | None = None
    if (
        before.git_head is not None
        and after.git_head is not None
        and before.workspace is not None
        and before.workspace == after.workspace
    ):
        candidates = set(before.git_dirty) | set(after.git_dirty)
        if before.git_head != after.git_head:
            try:
                proc = subprocess.run(
                    [
                        "git", "-C", str(before.workspace), "diff", "--relative",
                        "--no-renames", "--name-only", "-z", before.git_head, after.git_head,
                    ],
                    capture_output=True,
                    timeout=_GIT_LIST_TIMEOUT,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                proc = None
            if proc is None or proc.returncode != 0:
                candidates = None
            else:
                candidates.update(
                    path.decode("utf-8", "surrogateescape").replace("\\", "/")
                    for path in proc.stdout.split(b"\0") if path
                )
    if candidates is None:
        candidates = before.present | after.present
    else:
        candidates.update(before.present ^ after.present)

    return {
        rel for rel in candidates
        if (rel in before.present, before.files.get(rel))
        != (rel in after.present, after.files.get(rel))
    }


class WorkspaceGuard:
    """Snapshots and restores a workspace directory."""

    def __init__(self, workspace: Path, *, max_files: int = 5000) -> None:
        self.workspace = Path(workspace).resolve()
        self.max_files = max_files
        try:
            prefix = subprocess.run(["git", "-C", str(self.workspace), "rev-parse", "--show-prefix"],
                                    capture_output=True, timeout=_GIT_LIST_TIMEOUT, check=False)
            self._cached_prefix = (prefix.stdout.decode("utf-8", "surrogateescape").strip()
                                   if prefix.returncode == 0 else "")
        except (OSError, subprocess.TimeoutExpired):
            self._cached_prefix = ""

    def _walk(self) -> Iterator[Path]:
        """Every file under the workspace, never entering a pruned directory. Sorted, so the cap
        keeps the same files from one snapshot to the next."""
        for dirpath, dirnames, filenames in os.walk(self.workspace):
            dirnames[:] = sorted(d for d in dirnames if not _pruned(d))
            for name in sorted(filenames):
                yield Path(dirpath) / name

    def _iter_files(self) -> Iterator[Path]:
        listed = git_listed(self.workspace)
        if listed is not None:
            dirty = self._git_dirty_paths() or set()
            # Deleted tracked entries are absent from the index path listing but part of the
            # snapshot's project universe; restore needs their HEAD0 presence and contents.
            for rel in dirty:
                candidate = self.workspace / rel
                if candidate not in listed:
                    listed.append(candidate)
        for path in listed if listed is not None else self._walk():
            rel_parts = path.relative_to(self.workspace).parts
            if any(part in _IGNORE_DIRS for part in rel_parts):
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            # git lists a submodule as a path that is a directory; a walk lists only files.
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_FILE_BYTES:
                continue
            yield path

    def _git_head(self) -> str | None:
        try:
            proc = subprocess.run(
                ["git", "-C", str(self.workspace), "rev-parse", "--verify", "HEAD"],
                capture_output=True, timeout=_GIT_LIST_TIMEOUT, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return proc.stdout.decode("ascii", "ignore").strip() if proc.returncode == 0 else None

    def _to_workspace_rel(self, full: str) -> str | None:
        """Convert a repository-root Git path to this workspace's relative path."""
        path = full.replace("\\", "/")
        prefix = self._cached_prefix.replace("\\", "/").strip("/")
        if prefix:
            marker = f"{prefix}/"
            if not path.startswith(marker):
                return None
            path = path[len(marker):]
        return path or None

    def _git_dirty_paths(self) -> set[str] | None:
        """Paths that differ from the index/worktree, including untracked non-ignored files."""
        try:
            proc = subprocess.run(
                ["git", "-C", str(self.workspace), "-c", "core.quotepath=false", "status", "--porcelain=v1", "-z",
                 "--untracked-files=all", "--no-renames"],
                capture_output=True, timeout=_GIT_LIST_TIMEOUT, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        paths: set[str] = set()
        for entry in proc.stdout.split(b"\0"):
            if len(entry) < 4 or entry[2:3] != b" ":
                continue
            path = self._to_workspace_rel(entry[3:].decode("utf-8", "surrogateescape"))
            if path is not None:
                paths.add(path)
        return paths

    def _head_paths(self, head: str) -> set[str]:
        try:
            proc = subprocess.run(["git", "-C", str(self.workspace), "ls-tree", "-r", "-z", "--full-name", head, "--", "."],
                                  capture_output=True, timeout=_GIT_LIST_TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return set()
        if proc.returncode != 0:
            return set()
        return {path for part in proc.stdout.split(b"\0") if part
                if (path := self._to_workspace_rel(part.decode("utf-8", "surrogateescape"))) is not None}

    def _head_state(self, head: str, rel: str) -> FileState:
        """Read the captured HEAD path using checkout filters and workspace-relative addressing."""
        return self._head_states_bulk(head, {rel}).get(rel, (False, None))

    def _head_states_bulk(self, head: str, paths: set[str]) -> dict[str, FileState]:
        """Fetch blobs in batches, applying ordinary Git checkout EOL conversion in Python."""
        if not paths:
            return {}
        root = ["git", "-C", str(self.workspace)]
        result: dict[str, FileState] = {}
        try:
            tree = subprocess.run([*root, "ls-tree", "-r", "-z", "--full-name", head, "--", "."], capture_output=True,
                                  timeout=_GIT_LIST_TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired):
            tree = None
        object_ids: dict[str, str] = {}
        if tree is not None and tree.returncode == 0:
            for record in tree.stdout.split(b"\0"):
                metadata, separator, raw_path = record.partition(b"\t")
                fields = metadata.split()
                if separator and len(fields) == 3:
                    rel = self._to_workspace_rel(raw_path.decode("utf-8", "surrogateescape"))
                    if rel is not None and rel in paths and fields[1] == b"blob":
                        object_ids[rel] = fields[2].decode("ascii")
        result.update({rel: (False, None) for rel in paths - object_ids.keys()})
        try:
            attrs = subprocess.run(
                [*root, "check-attr", "--stdin", "-z", "text", "eol", "filter"],
                input=b"".join(rel.encode("utf-8", "surrogateescape") + b"\0" for rel in paths),
                capture_output=True, timeout=_GIT_LIST_TIMEOUT, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            attrs = None
        attributes: dict[str, dict[str, str]] = {}
        if attrs is not None and attrs.returncode == 0:
            fields = attrs.stdout.split(b"\0")
            for index in range(0, len(fields) - 2, 3):
                rel, name, value = (part.decode("utf-8", "surrogateescape") for part in fields[index:index + 3])
                attributes.setdefault(rel.replace("\\", "/"), {})[name] = value
        try:
            config_proc = subprocess.run([*root, "config", "--null", "--list"],
                                         capture_output=True, timeout=_GIT_LIST_TIMEOUT, check=False)
            config_values: dict[bytes, bytes] = {}
            if config_proc.returncode == 0:
                for item in config_proc.stdout.split(b"\0"):
                    config_key, separator, config_value = item.partition(b"\n")
                    if separator and config_key.lower() in {b"core.autocrlf", b"core.eol"}:
                        config_values[config_key.lower()] = config_value
            autocrlf = config_values.get(b"core.autocrlf", b"false").decode("ascii", "ignore").lower()
            core_eol_defined = b"core.eol" in config_values
        except (OSError, subprocess.TimeoutExpired):
            autocrlf = "false"
            core_eol_defined = False
        filtered = {rel for rel in object_ids if attributes.get(rel, {}).get("filter", "unspecified")
                    != "unspecified"}
        raw_paths = set(object_ids) - filtered
        batch_data = b"".join(object_ids[rel].encode("ascii") + b"\n" for rel in sorted(raw_paths))
        blobs: dict[str, bytes] = {}
        if batch_data:
            try:
                batch = subprocess.run([*root, "cat-file", "--batch"], input=batch_data,
                                       capture_output=True, timeout=_GIT_LIST_TIMEOUT, check=False)
            except (OSError, subprocess.TimeoutExpired):
                batch = None
            if batch is not None and batch.returncode == 0:
                cursor = 0
                for rel in sorted(raw_paths):
                    end = batch.stdout.find(b"\n", cursor)
                    if end < 0:
                        break
                    header = batch.stdout[cursor:end].split()
                    cursor = end + 1
                    if len(header) != 3 or header[1] != b"blob":
                        continue
                    try:
                        size = int(header[2])
                    except ValueError:
                        continue
                    content = batch.stdout[cursor:cursor + size]
                    if len(content) != size:
                        break
                    cursor += size + 1
                    blobs[rel] = content
        for rel in raw_paths:
            blob_content = blobs.get(rel)
            if blob_content is None:
                result[rel] = (True, None)
                continue
            attr = attributes.get(rel, {})
            checkout_content = blob_content
            has_attributes = any(attr.get(name, "unspecified") != "unspecified"
                                 for name in ("text", "eol", "filter"))
            safe_autocrlf_lf = (
                not has_attributes and not core_eol_defined and autocrlf == "true"
                and b"\0" not in blob_content and b"\r" not in blob_content
            )
            if has_attributes or core_eol_defined or (
                autocrlf in {"true", "input"} and (b"\r" in blob_content or b"\0" in blob_content)
            ):
                try:
                    proc = subprocess.run([*root, "cat-file", "--filters", f"--path={rel}", f"{head}:./{rel}"],
                                          capture_output=True, timeout=_GIT_LIST_TIMEOUT, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    result[rel] = (True, None)
                    continue
                if proc.returncode != 0:
                    result[rel] = (False, None)
                    continue
                checkout_content = proc.stdout
            elif safe_autocrlf_lf:
                checkout_content = blob_content.replace(b"\n", b"\r\n")
            if len(checkout_content) > _MAX_FILE_BYTES:
                result[rel] = (True, None)
                continue
            try:
                result[rel] = (True, checkout_content.decode("utf-8"))
            except UnicodeDecodeError:
                result[rel] = (True, None)
        for rel in filtered:
            try:
                proc = subprocess.run([*root, "cat-file", "--filters", f"--path={rel}", f"{head}:./{rel}"],
                                      capture_output=True, timeout=_GIT_LIST_TIMEOUT, check=False)
            except (OSError, subprocess.TimeoutExpired):
                result[rel] = (True, None)
                continue
            if proc.returncode != 0:
                result[rel] = (False, None)
            elif len(proc.stdout) > _MAX_FILE_BYTES:
                result[rel] = (True, None)
            else:
                try:
                    result[rel] = (True, proc.stdout.decode("utf-8"))
                except UnicodeDecodeError:
                    result[rel] = (True, None)
        return result


    @property
    def _prefix(self) -> str:
        return self._cached_prefix

    def _head_file_text(self, head: str, rel: str) -> str | None:
        """Expose logical text to snapshot consumers while keeping raw smudged bytes for restore."""
        text = self._head_state(head, rel)[1]
        return text.replace("\r\n", "\n") if text is not None else None

    def _snapshot_state(self, snapshot: FileSnapshot, rel: str) -> FileState:
        if rel not in snapshot.present:
            return (False, None)
        if snapshot.disk_view:
            return self._workspace_state(rel)
        if rel in snapshot.git_dirty or snapshot.git_head is None:
            return (True, snapshot.files.get(rel))
        return self._head_state(snapshot.git_head, rel)

    def _baseline_state(self, snapshot: FileSnapshot, rel: str) -> FileState:
        return self._snapshot_state(snapshot, rel)

    def _workspace_state(self, rel: str) -> FileState:
        path = self.workspace / rel
        try:
            info = path.stat()
        except OSError:
            return (False, None)
        if not stat.S_ISREG(info.st_mode):
            return (False, None)
        if info.st_size > _MAX_FILE_BYTES:
            return (True, None)
        try:
            return (True, _read_exact(path))
        except (UnicodeDecodeError, OSError):
            return (True, None)

    def snapshot(self) -> FileSnapshot:
        head = self._git_head()
        dirty = self._git_dirty_paths() if head is not None else None
        if head is not None and dirty is not None:
            listed = git_listed(self.workspace) or []
            base_paths = self._head_paths(head) if self._head_reachable(head) else set()
            workspace_paths = {path.relative_to(self.workspace).as_posix() for path in listed}
            listed.extend(self.workspace / rel for rel in sorted(base_paths & dirty)
                          if rel not in workspace_paths and not (self.workspace / rel).exists())
            paths: set[str] = set()
            dirty_text: dict[str, str] = {}
            for path in listed:
                rel = path.relative_to(self.workspace).as_posix()
                try:
                    info = path.lstat()
                except OSError:
                    # A path deleted before the snapshot belongs to the repo universe, not to the
                    # snapshot's present files; otherwise restore resurrects the user's deletion.
                    continue
                if stat.S_ISREG(info.st_mode) and info.st_size <= _MAX_FILE_BYTES:
                    paths.add(rel)
                    if rel in dirty:
                        from contextlib import suppress

                        with suppress(UnicodeDecodeError, OSError):
                            dirty_text[rel] = _read_exact(path)

            index = subprocess.run(["git", "-C", str(self.workspace), "ls-files", "-s"],
                                   capture_output=True, timeout=_GIT_LIST_TIMEOUT, check=False)
            files = _SnapshotFiles(
                paths,
                lambda rel: dirty_text.get(rel) if rel in dirty else self._head_file_text(head, rel),
                dirty_text,
            )
            return FileSnapshot(files=files, present=paths, git_head=head, git_dirty=dirty,
                                workspace=self.workspace, git_prefix=self._cached_prefix,
                                git_status="\n".join(sorted(dirty)),
                                git_index=index.stdout.decode("utf-8", "surrogateescape"))

        snap = FileSnapshot()
        disk_paths = iter(self._iter_files())
        for path in disk_paths:
            if len(snap.present) >= self.max_files:
                snap.incomplete = True
                break
            snap.present.add(path.relative_to(self.workspace).as_posix())
        fallback_text = {rel: self._workspace_state(rel)[1] for rel in snap.present}
        snap.files = _SnapshotFiles(snap.present, lambda rel: fallback_text.get(rel),
                                    {rel: text for rel, text in fallback_text.items() if text is not None})
        snap.workspace = self.workspace
        snap.disk_view = True
        return snap

    def _skip_reasons(self, snapshot: FileSnapshot) -> tuple[bool, bool]:
        """(truncated, in_git_repo) — reasons to skip deleting new files."""
        return (
            len(snapshot.present) >= self.max_files,
            any((parent / ".git").exists() for parent in (self.workspace, *self.workspace.parents)),
        )

    def deletes_new_files(self, snapshot: FileSnapshot) -> bool:
        """Would a restore remove files created since ``snapshot``?

        A truncated snapshot or a workspace inside a Git repository disables broad cleanup.
        """
        return not any(self._skip_reasons(snapshot))

    def restore(self, snapshot: FileSnapshot) -> int:
        """Restore the workspace to ``snapshot``. Returns the number of changes."""
        if snapshot.git_head is not None:
            changes = 0
            if not self._head_reachable(snapshot.git_head):
                snapshot.incomplete = True
                snapshot.incomplete_reason = (
                    f"HEAD mudou de {snapshot.git_head} para {self._git_head()} durante o turno"
                )
                return 0

            candidates = self._paths_since_head(snapshot.git_head) | snapshot.git_dirty
            baselines = self._head_states_bulk(snapshot.git_head, candidates & snapshot.present)
            for rel in sorted(candidates):
                if rel not in snapshot.present:
                    continue
                if rel in snapshot.git_dirty:
                    was_present, content = self._baseline_state(snapshot, rel)
                else:
                    was_present, content = baselines.get(rel, (False, None))
                try:
                    target = self._safe_target(rel)
                except (OSError, ValueError):
                    continue
                if was_present:
                    if content is None:
                        continue
                    if self._workspace_state(rel) != (True, content):
                        target.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            target = self._safe_target(rel)
                        except (OSError, ValueError):
                            continue
                        _write_exact(target, content)
                        changes += 1
                # Untracked paths inside a repository remain protected from broad cleanup.
            return changes

        changes = 0
        # Two conditions make the destructive delete-new pass unsafe; in either case we skip it and
        # only restore the content we captured (never worse than the pre-restore state):
        #
        #  1. TRUNCATED snapshot (hit the file cap): some pre-existing files were never captured, so we
        #     cannot tell "created since" from "existed but uncaptured". Deleting the difference would
        #     destroy untouched user data — the opposite of verify-or-revert's job.
        #  2. The workspace is INSIDE A GIT REPOSITORY (a `.git` entry sits at it or any ancestor).
        #     verify-or-revert's delete pass exists to clean up files an agent CREATED in a throwaway
        #     workspace; a task workspace is never inside a repo. If a path/config bug ever points the
        #     guard at a real repo (or a SUBDIR of one), "files not in my snapshot" spans the user's
        #     untracked work and — if the snapshot predates them — committed files, so a revert could
        #     wipe the repo. It has (bench harness, 2026-07-17). Refuse to delete anywhere inside a repo,
        #     unconditionally — checking ancestors too, not just the immediate directory.
        truncated, in_git_repo = self._skip_reasons(snapshot)
        if truncated:
            _log.warning(
                "snapshot truncated at %d files; skipping delete-new pass on restore to avoid "
                "removing uncaptured pre-existing files", self.max_files,
            )
        elif in_git_repo:
            _log.warning(
                "workspace %s is inside a git repository; skipping delete-new pass on restore so "
                "verify-or-revert can never delete tracked or untracked repo files", self.workspace,
            )
        else:
            current = {p.relative_to(self.workspace).as_posix() for p in self._iter_files()}
            for rel in current - snapshot.present:
                try:
                    target = self._safe_target(rel)
                    target.unlink(missing_ok=True)
                    changes += 1
                except (OSError, ValueError):
                    continue

        for rel in snapshot.present:
            content = snapshot.files.get(rel)
            if content is None:
                continue
            try:
                target = self._safe_target(rel)
            except (OSError, ValueError):
                continue
            if self._workspace_state(rel) != (True, content):
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_exact(target, content)
                changes += 1

        if changes:
            _log.debug("restored workspace (%d changes)", changes)
        return changes

    def _state(self, rel: str) -> FileState:
        path = self.workspace / rel
        if not path.is_file():
            return (False, None)
        try:
            return (True, _read_exact(path))
        except (UnicodeDecodeError, OSError):
            return (True, None)

    def _head_reachable(self, head: str) -> bool:
        """Whether the captured commit remains an ancestor of the current HEAD."""
        try:
            ancestor = subprocess.run(
                ["git", "-C", str(self.workspace), "merge-base", "--is-ancestor", head, "HEAD"],
                capture_output=True, timeout=_GIT_LIST_TIMEOUT, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return ancestor.returncode == 0

    def _paths_since_head(self, head: str) -> set[str]:
        try:
            diff = subprocess.run(["git", "-C", str(self.workspace), "diff", "--relative", "--no-renames", "--name-only", "-z", head],
                                  capture_output=True, timeout=_GIT_LIST_TIMEOUT, check=False)
            staged = subprocess.run(["git", "-C", str(self.workspace), "diff", "--relative", "--no-renames", "--cached", "--name-only", "-z", head],
                                    capture_output=True, timeout=_GIT_LIST_TIMEOUT, check=False)
            untracked = subprocess.run(["git", "-C", str(self.workspace), "ls-files", "--others", "--exclude-standard", "-z"],
                                       capture_output=True, timeout=_GIT_LIST_TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return set()
        if diff.returncode != 0 or staged.returncode != 0 or untracked.returncode != 0:
            return set()
        def decode(data: bytes) -> set[str]:
            return {p.decode("utf-8", "surrogateescape").replace("\\", "/")
                    for p in data.split(b"\0") if p}

        return decode(diff.stdout) | decode(staged.stdout) | decode(untracked.stdout)

    def _safe_target(self, rel: str) -> Path:
        return _safe_target(self.workspace, rel)

    def diff_since(self, before: FileSnapshot) -> TurnChange:
        """What changed in the workspace since ``before``: the turn that ran in between."""
        after = self.snapshot()
        changed: dict[str, FileState] = {}
        if before.git_head is not None:
            current_head = self._git_head()
            if not self._head_reachable(before.git_head):
                before.incomplete = True
                before.incomplete_reason = (
                    f"HEAD mudou de {before.git_head} para {current_head} durante o turno"
                )
                return TurnChange(before=FileSnapshot(incomplete=True), after={}, truncated=True,
                                  incomplete_reason=before.incomplete_reason)
            candidates = self._paths_since_head(before.git_head) | before.git_dirty
            baselines = self._head_states_bulk(before.git_head, candidates & before.present)
            dirty_candidates = candidates & before.git_dirty
            baselines.update({rel: self._baseline_state(before, rel) for rel in dirty_candidates})
            for rel in sorted(candidates):
                if rel not in before.present:
                    now = self._workspace_state(rel)
                    if now[0]:
                        changed[rel] = now
                        baselines[rel] = (False, None)
                    continue
                was = baselines.get(rel, (False, None))
                now = self._workspace_state(rel)
                if was != now:
                    changed[rel] = now
                    baselines[rel] = was
            kept_files = {rel: content for rel in changed
                          if (state := baselines.get(rel)) is not None and state[0]
                          and (content := state[1]) is not None}
            kept = FileSnapshot(
                files=kept_files,
                present={rel for rel in changed if rel in baselines and baselines[rel][0]},
                git_head=before.git_head,
                git_dirty=set(changed),
                incomplete=before.incomplete,
                workspace=self.workspace,
            )
            return TurnChange(before=kept, after=changed, truncated=before.incomplete,
                              incomplete_reason=before.incomplete_reason)

        for rel in before.present | after.present:
            was_fallback: FileState = (rel in before.present, before.files.get(rel))
            now_fallback: FileState = (rel in after.present, after.files.get(rel))
            if was_fallback != now_fallback:
                changed[rel] = now_fallback
        kept = FileSnapshot(
            files={rel: before.files[rel] for rel in changed if rel in before.files},
            present={rel for rel in changed if rel in before.present},
            incomplete=before.incomplete,
        )
        return TurnChange(before=kept, after=changed, truncated=before.incomplete)

    def restore_change(self, change: TurnChange) -> ChangeRestore:
        """Undo one turn: put back only the files it changed, as they were before it.

        Until this, undoing a turn restored the whole folder to the snapshot taken before it, so with
        two conversations in one folder, undoing one also reverted what the other had edited since,
        and whatever the person had typed; outside a git repository it deleted the other's new files.

        A file that no longer holds what the turn left in it changed again afterwards, by someone or
        something else, and is left as it is and named in ``kept``. The rules that keep a restore from
        deleting inside a git repository or after a truncated snapshot still apply, to the files the
        turn created.
        """
        report = ChangeRestore()
        if change.before.git_head is not None and not self._head_reachable(change.before.git_head):
            change.truncated = True
            change.incomplete_reason = (
                f"HEAD mudou de {change.before.git_head} para {self._git_head()} durante o turno"
            )
            return report
        truncated = change.truncated
        in_git_repo = self._skip_reasons(change.before)[1]
        for rel in change.paths:
            if self._state(rel) != change.after[rel]:
                report.kept.append(rel)
                continue
            try:
                target = self._safe_target(rel)
            except (OSError, ValueError):
                report.kept.append(rel)
                continue
            if rel in change.before.present:
                text = change.before.files.get(rel)
                if text is None:
                    # Present before but never read (binary or unreadable): nothing to put back.
                    report.kept.append(rel)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_exact(target, text)
                report.restored += 1
            elif truncated or in_git_repo:
                report.left_new.append(rel)
            else:
                target.unlink(missing_ok=True)
                report.restored += 1
        return report
