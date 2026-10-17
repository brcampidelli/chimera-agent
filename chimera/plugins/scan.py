"""One bounded walk of a plugin tree, and the path rules every manifest path goes through.

The walk is the only place that lists the disk. It records what is there (files with sizes,
directories) WITHOUT following any link, and everything after it — finding components, checking a
manifest path — asks the index instead of the filesystem. That keeps two promises cheap to audit:
the scan is bounded (entries, depth, bytes), and nothing is ever read through a link, so a plugin
cannot point the reader at ``~/.ssh`` by shipping ``skills -> /home/you``.

Links are detected two ways. ``is_symlink`` catches symbolic links; a directory whose real path is
not the one its position implies catches what ``is_symlink`` misses on Windows (junctions and other
reparse points before Python 3.12's ``is_junction``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from chimera.plugins.model import Code, Problem, Severity

#: Bounds for one plugin. Higher than one skill bundle (`chimera.skills.bundles`: 200 files, 8 MiB,
#: depth 4) because a plugin is several components plus the scripts its servers and hooks call;
#: the entry and depth caps are discovery's (`chimera.skills.discovery`). Hitting one means the tree
#: is not something to vouch for, so it is an error, not a truncated listing presented as complete.
MAX_ENTRIES = 5_000
MAX_DEPTH = 12
MAX_TOTAL_BYTES = 64 * 1024 * 1024
#: The cap on any one file the reader parses or a component is made of (JSON configs, Markdown).
#: It is the bundle cap, so a skill that installs as a bundle reads as a plugin component too.
MAX_FILE_BYTES = 2 * 1024 * 1024

#: Version-control metadata is not plugin content; walking a `.git` would spend the entry budget on
#: objects no component can name.
_SKIPPED_DIRS = frozenset({".git"})


@dataclass
class TreeIndex:
    """What the walk saw. Keys are plugin-relative POSIX paths; the root is ``""``."""

    root: Path
    files: dict[str, int] = field(default_factory=dict)
    dirs: set[str] = field(default_factory=lambda: {""})
    links: dict[str, bool] = field(default_factory=dict)  # path -> target is inside the root
    problems: list[Problem] = field(default_factory=list)

    def is_file(self, rel: str) -> bool:
        return rel in self.files

    def is_dir(self, rel: str) -> bool:
        return rel in self.dirs

    def children(self, rel: str) -> list[str]:
        """Direct children of a directory, files and directories, sorted."""
        prefix = f"{rel}/" if rel else ""
        names = {
            path for path in (*self.files, *self.dirs)
            if path.startswith(prefix) and path != rel and "/" not in path[len(prefix):]
        }
        return sorted(names, key=str.casefold)

    def below(self, rel: str) -> list[str]:
        """Every file under a directory, at any depth, sorted."""
        prefix = f"{rel}/" if rel else ""
        return sorted((path for path in self.files if path.startswith(prefix)), key=str.casefold)

    def link_on(self, rel: str) -> str | None:
        """The first link on the way to ``rel`` (itself included), or None."""
        parts = rel.split("/") if rel else []
        for depth in range(1, len(parts) + 1):
            prefix = "/".join(parts[:depth])
            if prefix in self.links:
                return prefix
        return None


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.normcase(path), os.path.normcase(root)]) == os.path.normcase(root)
    except ValueError:  # different drives on Windows
        return False


def _record_link(index: TreeIndex, rel: str, absolute: str, real_root: str) -> None:
    try:
        target = os.path.realpath(absolute)
    except (OSError, RuntimeError):
        target = ""
    inside = bool(target) and _inside(target, real_root)
    index.links[rel] = inside
    if inside:
        index.problems.append(Problem(
            Code.SYMLINK_IGNORED, Severity.WARNING, rel,
            "a link inside the plugin; it is not followed, so what it points at is not read",
        ))
    else:
        index.problems.append(Problem(
            Code.SYMLINK_OUT, Severity.ERROR, rel,
            "a link that points outside the plugin; never followed",
        ))


def scan(root: Path) -> TreeIndex:
    """Walk ``root`` once, bounded, without following links. Never raises for what the tree contains."""
    real_root = os.path.realpath(root)
    index = TreeIndex(root=Path(real_root))
    total = 0
    seen = 0
    stack: list[tuple[str, str]] = [("", real_root)]
    while stack:
        rel_dir, abs_dir = stack.pop()
        depth = len(rel_dir.split("/")) if rel_dir else 0
        try:
            entries = sorted(os.scandir(abs_dir), key=lambda entry: entry.name)
        except OSError as exc:
            index.problems.append(Problem(Code.PATH_NOT_FOUND, Severity.ERROR, rel_dir or ".", f"cannot list: {exc.strerror}"))
            continue
        for entry in entries:
            seen += 1
            if seen > MAX_ENTRIES:
                index.problems.append(Problem(
                    Code.TOO_MANY_FILES, Severity.ERROR, ".",
                    f"more than {MAX_ENTRIES} entries; the scan stopped and the plugin is not read as complete",
                ))
                return index
            rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
            if entry.is_symlink():
                _record_link(index, rel, entry.path, real_root)
                continue
            if entry.is_dir(follow_symlinks=False):
                if entry.name in _SKIPPED_DIRS:
                    continue
                if os.path.normcase(os.path.realpath(entry.path)) != os.path.normcase(os.path.join(real_root, *rel.split("/"))):
                    _record_link(index, rel, entry.path, real_root)
                    continue
                if depth + 1 > MAX_DEPTH:
                    index.problems.append(Problem(
                        Code.TOO_DEEP, Severity.ERROR, rel,
                        f"nested deeper than {MAX_DEPTH} levels; not scanned",
                    ))
                    continue
                index.dirs.add(rel)
                stack.append((rel, entry.path))
            elif entry.is_file(follow_symlinks=False):
                try:
                    size = entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
                index.files[rel] = size
                total += size
                if total > MAX_TOTAL_BYTES and not any(p.code is Code.PLUGIN_TOO_LARGE for p in index.problems):
                    index.problems.append(Problem(
                        Code.PLUGIN_TOO_LARGE, Severity.ERROR, ".",
                        f"more than {MAX_TOTAL_BYTES // (1024 * 1024)} MiB in total",
                    ))
    return index


@dataclass(frozen=True, slots=True)
class Resolved:
    """A manifest path after the rules: ``rel`` is set only when it is safe AND present."""

    rel: str | None
    problems: tuple[Problem, ...]


def _bad(code: Code, where: str, message: str) -> Resolved:
    return Resolved(None, (Problem(code, Severity.ERROR, where, message),))


def resolve(index: TreeIndex, raw: object, where: str, *, allow_dot: bool = False) -> Resolved:
    """Apply Claude Code's path rules to one manifest path.

    https://code.claude.com/docs/en/plugins-reference#path-rules : every component path is relative
    to the plugin root and starts with ``./`` (``skills`` also takes ``.``); a ``..`` or anything
    resolving outside the root is refused; a missing path is an error. A backslash is refused too:
    Claude Code refuses it in marketplace paths on macOS and Linux, and on Windows it is how
    ``.\\..`` or a drive would slip past a check written for ``/``.
    """
    if not isinstance(raw, str):
        return _bad(Code.BAD_TYPE, where, "a path must be a string")
    if "\x00" in raw:
        return _bad(Code.PATH_ESCAPES_PLUGIN, where, "the path contains a NUL character")
    if "\\" in raw:
        return _bad(Code.BACKSLASH_IN_PATH, where, f"use forward slashes: {raw!r}")
    if raw in (".", "./") and allow_dot:
        return Resolved("", ())
    if not raw.startswith("./"):
        return _bad(Code.PATH_NOT_RELATIVE, where, f"a path must start with './': {raw!r}")
    parts = [part for part in raw[2:].split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        return _bad(Code.PATH_ESCAPES_PLUGIN, where, f"the path contains '..': {raw!r}")
    if parts and ":" in parts[0]:
        return _bad(Code.PATH_ESCAPES_PLUGIN, where, f"a drive or stream name is not a relative path: {raw!r}")
    rel = "/".join(parts)
    link = index.link_on(rel)
    if link is not None:
        if index.links[link]:
            return Resolved(None, (Problem(Code.SYMLINK_IGNORED, Severity.WARNING, where, f"{raw!r} goes through a link, which is not followed"),))
        return _bad(Code.SYMLINK_OUT, where, f"{raw!r} goes through a link that points outside the plugin")
    if not (index.is_file(rel) or index.is_dir(rel)):
        return _bad(Code.PATH_NOT_FOUND, where, f"path not found: {raw!r}")
    return Resolved(rel, ())


def read_text(index: TreeIndex, rel: str, problems: list[Problem]) -> str | None:
    """Read one indexed file as UTF-8, bounded. Data only: the caller parses, nothing is executed."""
    size = index.files.get(rel)
    if size is None:
        return None
    if size > MAX_FILE_BYTES:
        problems.append(Problem(Code.FILE_TOO_LARGE, Severity.ERROR, rel, f"larger than {MAX_FILE_BYTES // (1024 * 1024)} MiB"))
        return None
    # O_NOFOLLOW: a file swapped for a link after the walk is refused by the OS instead of read.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(os.path.join(index.root, *rel.split("/")), flags)
        with os.fdopen(descriptor, "rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        problems.append(Problem(Code.PATH_NOT_FOUND, Severity.ERROR, rel, f"cannot read: {exc.strerror}"))
        return None
    if len(raw) > MAX_FILE_BYTES:  # grew between the walk and the read
        problems.append(Problem(Code.FILE_TOO_LARGE, Severity.ERROR, rel, "the file grew past the cap while being read"))
        return None
    return raw.decode("utf-8-sig", errors="replace")
