"""A coding turn checks its own diff before its answer is final. Deterministic: no model, no git.

A turn that edits a workspace can leave four kinds of residue that nothing else on the screen
names, and each one is cheap to see in the diff the turn itself produced:

* ``unnamed_file`` — a file the turn changed that neither the request nor a test of a named file
  mentions. Only checked when the request names a real path (see `named_paths_of`): with no names
  there is nothing to compare against, and guessing "outside the plan" would be a finding made up
  from nothing. A rename is one change, so its old path follows its new one.
* ``tests_touched`` / ``tests_removed_or_skipped`` / ``assertion_weakened`` / ``verifier_modified``
  — the record-only rules of `chimera.governance.verifier_integrity`, reused verbatim: this module
  does not carry a second copy of them, it re-labels nothing and decides nothing about them.
* ``scratch_file`` — a log, a temp file or a captured output the turn CREATED at the repository root.
  A tracked file of the same name that the turn edited on purpose is not scratch.
* ``generated_without_source`` — a generated file (the desktop's OpenAPI schema, its TypeScript
  types, the CLI snapshot) changed while nothing in its generator's inputs changed in the same turn.
  That is the signature of a hand edit: the generator would have moved both. Lockfiles are NOT in
  this rule: a lock moves on its own under `uv lock --upgrade-package` or `npm audit fix`.
* ``crlf_introduced`` — a text file that had no CRLF line endings and has some now.

Pure on purpose: it takes the changed paths with their before/after texts and returns findings,
so every case is testable without a repository. `changed_files_from` and `incomplete_reason_of` are
the only pieces that know the workspace guard's record, and they read it without calling it.
`locate_in` is the one disk read, and it asks only whether a path exists.

Record-only, like the integrity flags: a finding is shown to the person and to the agent's next
turn, it does not block the answer, revert anything or count as a failure. What it must never do
is call a turn clean that it did not measure: a snapshot that was truncated says so in the result.
"""

from __future__ import annotations

import fnmatch
import posixpath
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.core.checkpoint import TurnChange
from chimera.governance.verifier_integrity import flag_snapshots, is_test_path

UNNAMED_FILE = "unnamed_file"
SCRATCH_FILE = "scratch_file"
GENERATED_WITHOUT_SOURCE = "generated_without_source"
CRLF_INTRODUCED = "crlf_introduced"

#: Kinds in the order a reader sees them: the request first, then the tests, then the residue.
_ORDER: tuple[str, ...] = (
    UNNAMED_FILE, "tests_touched", "tests_removed_or_skipped", "assertion_weakened",
    "verifier_modified", SCRATCH_FILE, GENERATED_WITHOUT_SOURCE, CRLF_INTRODUCED,
)
_RANK: Mapping[str, int] = {kind: rank for rank, kind in enumerate(_ORDER)}

#: Names a turn leaves at the repository ROOT when it ran something and kept its output. Matched
#: against the basename of a root-level path only: a ``speech.wav`` under ``assets/`` is an asset.
SCRATCH_PATTERNS: tuple[str, ...] = ("*-output.txt", "*.log", "*.tmp", "speech.wav", "nohup.out")

#: Generated file → the inputs that regenerate it. A value ending in ``/`` is a directory prefix (any
#: file beneath it); any other value is one exact path. ``chimera.api.schema_dump`` builds the schema
#: from the whole app, so any change under ``chimera/`` is a legitimate source of it.
GENERATED_FROM: Mapping[str, tuple[str, ...]] = {
    "apps/desktop/openapi.json": ("chimera/",),
    "apps/desktop/src/lib/api-schema.ts": ("apps/desktop/openapi.json",),
    "chimera/_cli_snapshot.json": ("chimera/cli/",),
}

#: Lockfile basename → its manifest, in the same folder. Used for ONE purpose: a lock that moved
#: with its manifest is part of the request that moved the manifest, so it is not unnamed.
LOCKFILE_MANIFEST: Mapping[str, str] = {"uv.lock": "pyproject.toml", "package-lock.json": "package.json"}

_TOKEN = re.compile(r"[\w.\-/\\]+")


#: Tools that can write into the workspace, or run a command that can. A turn that called none of them
#: and edited nothing cannot have changed the folder, so an unmeasurable folder says nothing about it.
CHANGING_TOOLS: frozenset[str] = frozenset({
    "write_file", "edit_file", "apply_patch", "edit_batch", "run_shell", "execute_code",
    "code_interpreter", "download_media", "create_document", "render_chart", "design", "browser",
    "generate_image", "generate_audio", "generate_video", "text_to_speech", "open_pull_request",
})


def could_have_changed(tool_names: Iterable[str], edited: bool) -> bool:
    """Whether this turn could have changed the workspace: it edited, or called a tool that writes or runs
    a command. A plain chat turn could not have."""
    return edited or any(name in CHANGING_TOOLS for name in tool_names)


@dataclass(frozen=True)
class ChangedFile:
    """One path the turn changed. ``before``/``after`` are the texts, None when absent or not text."""

    path: str
    existed_before: bool
    exists_after: bool
    before: str | None
    after: str | None


@dataclass(frozen=True)
class SelfCheckFinding:
    kind: str
    path: str
    detail: str = ""

    def render(self) -> str:
        return f"{self.kind}: {self.path}" + (f" — {self.detail}" if self.detail else "")


@dataclass(frozen=True)
class SelfCheck:
    findings: tuple[SelfCheckFinding, ...] = ()
    #: False when the turn's changes could not be read in full. Then `clean` is False whatever the
    #: findings say: an unmeasured turn is not a clean turn.
    measured: bool = True
    measured_reason: str = ""

    @property
    def clean(self) -> bool:
        return not self.findings and self.measured

    def to_payload(self) -> dict[str, Any]:
        """The ``self_check`` field of the done frame."""
        return {
            "findings": [
                {"kind": f.kind, "path": f.path, "detail": f.detail} for f in self.findings
            ],
            "clean": self.clean,
            "measured": self.measured,
            "measured_reason": self.measured_reason,
        }

    def note(self) -> str | None:
        """A short paragraph for the agent (and the person) to act on. None only when the check
        measured the turn and found nothing: a clean turn says nothing, rather than a line that the
        check ran."""
        if self.clean:
            return None
        lines = [
            "Self-check of this turn's own changes (a deterministic rule, not a judgement). Fix what "
            "is yours to fix, and say plainly what you left as it is:"
        ]
        if not self.measured:
            lines.append(
                f"- not measured: {self.measured_reason}. Nothing here says the changes are clean."
            )
        shown = self.findings[:_NOTE_LIMIT]
        lines += [f"- {f.render()}" for f in shown]
        if len(self.findings) > len(shown):
            lines.append(f"- and {len(self.findings) - len(shown)} more")
        return "\n".join(lines)


_NOTE_LIMIT = 8


def changed_files_from(change: TurnChange) -> list[ChangedFile]:
    """The turn's record in this module's shape. Reads the guard's record; never touches the disk."""
    out: list[ChangedFile] = []
    for path in change.paths:
        exists_after, after = change.after[path]
        existed = path in change.before.present
        before = change.before.files.get(path) if existed else None
        out.append(ChangedFile(path, existed, bool(exists_after), before, after))
    return out


def incomplete_reason_of(change: TurnChange) -> str:
    """Why this turn's record is not a full measurement, or "" when it is. A snapshot that hit its
    file cap cannot tell "created by the turn" from "existed but was not captured"."""
    if change.incomplete_reason:
        return change.incomplete_reason
    if change.truncated:
        return "the folder was too large to snapshot before the turn"
    return ""


def locate_in(root: Path, rel: str) -> str | None:
    """"dir" or "file" for a path that exists under ``root``, None otherwise. Reads no file."""
    target = root / rel
    if target.is_dir():
        return "dir"
    if target.is_file():
        return "file"
    return None


def _nowhere(_: str) -> str | None:
    return None


#: The most path-shaped tokens of one request that are looked up as names. A pasted log or a long
#: list is not a plan: past this many, the rest of the message is ignored, not scanned.
MAX_NAME_TOKENS = 2000


@dataclass(frozen=True)
class _Keys:
    """Every way a path can be named: its suffixes at a ``/`` (``api/x.py`` names ``a/api/x.py``) and
    its contiguous folder runs (``api`` names ``a/api/x.py``). Built once per path, so a lookup is a set
    membership test instead of a scan of every changed path."""

    files: frozenset[str]
    folders: frozenset[str]


def _keys_of(path: str) -> _Keys:
    parts = _clean(path).split("/")
    files = {"/".join(parts[i:]) for i in range(len(parts))}
    folders = {"/".join(parts[i:j]) for i in range(len(parts)) for j in range(i + 1, len(parts))}
    return _Keys(frozenset(files), frozenset(folders))


def _keys_of_all(paths: Iterable[str]) -> _Keys:
    files: set[str] = set()
    folders: set[str] = set()
    for path in paths:
        keys = _keys_of(path)
        files |= keys.files
        folders |= keys.folders
    return _Keys(frozenset(files), frozenset(folders))


class _Names:
    """A named set (``a/b.py`` for a file, ``a/b/`` for a folder) with constant-time membership."""

    def __init__(self, named: Iterable[str]) -> None:
        names = list(named)
        self.files = frozenset(n for n in names if not n.endswith("/"))
        self.folders = frozenset(n.rstrip("/") for n in names if n.endswith("/"))

    def covers(self, path: str) -> bool:
        keys = _keys_of(path)
        return bool(keys.files & self.files) or bool(keys.folders & self.folders)


def named_paths_of(
    message: str,
    *,
    changed: Iterable[str] = (),
    locate: Callable[[str], str | None] = _nowhere,
) -> frozenset[str]:
    """The paths a request names, and only those that are real here.

    A token counts when it resolves to a path in this turn's change set (exactly, as a trailing part of
    one, or as a folder of one) or to an existing file or folder in the workspace (``locate``). Shape
    alone is not enough: ``cliente/servidor``, ``UI/UX``, ``Next.js``, ``e.g`` and ``v1.2`` are words,
    not names, and naming them would flag every file the turn touched. A bare folder (``chimera/api``)
    names its children; a file names itself. At most `MAX_NAME_TOKENS` path-shaped tokens are looked up.
    """
    keys = _keys_of_all(changed)
    named: set[str] = set()
    considered = 0
    for token in _TOKEN.findall(message or ""):
        cleaned = _clean(token.rstrip(".,;:"))
        if not _plausible(cleaned) or ("/" not in cleaned and "." not in cleaned):
            continue  # a plain word ("api", "widget") is never a name, even when a folder has it
        considered += 1
        if considered > MAX_NAME_TOKENS:
            break
        is_folder = cleaned.endswith("/")
        candidate = cleaned.rstrip("/")
        if not candidate:
            continue
        kind = locate(candidate)
        if kind == "file" and not is_folder:
            named.add(candidate)
        elif kind == "dir" or (is_folder and candidate in keys.folders):
            named.add(candidate + "/")
        elif candidate in keys.files:
            named.add(candidate)
        elif candidate in keys.folders:
            named.add(candidate + "/")
    return frozenset(named)


def _plausible(token: str) -> bool:
    """A token that could be a repository-relative path: no absolute path, no parent escape, no
    drive letter, no URL, no bare dot-run."""
    if not token or token.startswith(("/", "~")) or ".." in token or ":" in token:
        return False
    return token.strip("./") != ""


def check_turn_diff(
    changes: Sequence[ChangedFile],
    *,
    named_paths: Iterable[str] | None = None,
    verify_command: str = "",
    incomplete_reason: str = "",
) -> SelfCheck:
    """Every finding over one turn's changes. ``changes`` are the paths the turn changed; an entry
    that is in fact unchanged is dropped here. An incomplete record is reported as unmeasured."""
    changed = [c for c in changes if _changed(c)]
    if not changed:
        return SelfCheck(measured=not incomplete_reason, measured_reason=incomplete_reason)
    named = frozenset(_clean(n) for n in (named_paths or ()) if _clean(n))
    changed_paths = frozenset(_clean(c.path) for c in changed)
    findings: list[SelfCheckFinding] = []
    if named:
        findings += _unnamed(changed, named, changed_paths)
    findings += _integrity(changed, verify_command)
    findings += _scratch(changed)
    findings += _generated(changed, changed_paths)
    findings += _crlf(changed)
    return SelfCheck(
        tuple(sorted(findings, key=_sort_key)),
        measured=not incomplete_reason,
        measured_reason=incomplete_reason,
    )


def _changed(c: ChangedFile) -> bool:
    """Whether the path differs. Present on both sides with NEITHER text readable (a binary) counts
    as changed: the bytes may well have moved, and that must not read as "untouched"."""
    if c.existed_before != c.exists_after:
        return True
    if not c.exists_after:
        return False
    return c.before != c.after or (c.before is None and c.after is None)


def _sort_key(finding: SelfCheckFinding) -> tuple[int, str]:
    return (_RANK.get(finding.kind, len(_RANK)), finding.path)


def _clean(path: str) -> str:
    path = path.replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    return path


def _module_of(path: str) -> str:
    """``tests/test_widget.py`` and ``src/widget.test.ts`` both name the module ``widget``."""
    stem = posixpath.basename(_clean(path)).split(".", 1)[0]
    if stem.startswith("test_"):
        stem = stem[len("test_"):]
    if stem.endswith("_test"):
        stem = stem[: -len("_test")]
    return stem


def _lock_manifest(path: str) -> str | None:
    base = posixpath.basename(path)
    if base not in LOCKFILE_MANIFEST:
        return None
    folder = posixpath.dirname(path)
    return posixpath.join(folder, LOCKFILE_MANIFEST[base]) if folder else LOCKFILE_MANIFEST[base]


def _unnamed_sources(path: str) -> tuple[str, ...]:
    lock = _lock_manifest(path)
    return (*GENERATED_FROM.get(_clean(path), ()), *((lock,) if lock else ()))


def _is_added(c: ChangedFile) -> bool:
    return not c.existed_before and c.exists_after


def _is_removed(c: ChangedFile) -> bool:
    return c.existed_before and not c.exists_after


def _unnamed(
    changed: Sequence[ChangedFile], named: frozenset[str], changed_paths: frozenset[str],
) -> list[SelfCheckFinding]:
    names = _Names(named)
    modules = {_module_of(n) for n in named if not n.endswith("/")}
    source_pool = changed_paths | named
    # A rename is a deletion plus an addition with the same text. The half at the old path is the same
    # change as the new path, so it is not unrequested when the new path is the one the request names.
    named_added = {
        c.after for c in changed if _is_added(c) and c.after is not None and names.covers(_clean(c.path))
    }
    out: list[SelfCheckFinding] = []
    for c in changed:
        path = _clean(c.path)
        if names.covers(path):
            continue
        if is_test_path(path) and _module_of(path) in modules:
            continue  # the test of a file the request names is part of that request
        if _is_scratch(c):
            continue  # reported once, as the scratch file it is, not again as an unrequested one
        if _is_removed(c) and c.before in named_added:
            continue
        if any(_source_changed(s, source_pool) for s in _unnamed_sources(path)):
            continue  # regenerated or re-locked because its source was named or changed
        out.append(SelfCheckFinding(UNNAMED_FILE, path, "neither the request nor a test of it names this file"))
    return out


def _integrity(changed: Sequence[ChangedFile], verify_command: str) -> list[SelfCheckFinding]:
    before = {_clean(c.path): c.before for c in changed if c.before is not None}
    after = {_clean(c.path): c.after for c in changed if c.after is not None}
    return [
        SelfCheckFinding(f.kind, f.path, f.detail)
        for f in flag_snapshots(before, after, verify_command=verify_command)
    ]


def _is_scratch(c: ChangedFile) -> bool:
    """A root-level file the turn CREATED under a scratch name. A tracked file the turn edited is
    the repository's own, whatever its name."""
    path = _clean(c.path)
    if c.existed_before or not c.exists_after or "/" in path:
        return False
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in SCRATCH_PATTERNS)


def _scratch(changed: Sequence[ChangedFile]) -> list[SelfCheckFinding]:
    return [
        SelfCheckFinding(SCRATCH_FILE, _clean(c.path), "left at the repository root; delete it or move it")
        for c in changed
        if _is_scratch(c)
    ]


def _source_changed(source: str, changed_paths: Iterable[str]) -> bool:
    if source.endswith("/"):
        return any(_clean(p).startswith(source) for p in changed_paths)
    return source in changed_paths


def _generated(changed: Sequence[ChangedFile], changed_paths: frozenset[str]) -> list[SelfCheckFinding]:
    out: list[SelfCheckFinding] = []
    for c in changed:
        path = _clean(c.path)
        sources = GENERATED_FROM.get(path)
        if not sources or any(_source_changed(s, changed_paths) for s in sources):
            continue
        out.append(SelfCheckFinding(
            GENERATED_WITHOUT_SOURCE, path,
            f"changed, but nothing under {' or '.join(sources)} changed in this turn: a hand edit, "
            "unless a generator ran and wrote nothing new",
        ))
    return out


def _crlf(changed: Sequence[ChangedFile]) -> list[SelfCheckFinding]:
    out: list[SelfCheckFinding] = []
    for c in changed:
        if c.after is None or "\r\n" not in c.after:
            continue
        if "\r\n" in (c.before or ""):
            continue  # already CRLF: not introduced by this turn
        out.append(SelfCheckFinding(CRLF_INTRODUCED, _clean(c.path), "CRLF line endings the file did not have"))
    return out
