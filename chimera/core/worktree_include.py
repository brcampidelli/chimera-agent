"""Copy the ignored files a new conversation worktree needs, named by ``.worktreeinclude``.

A worktree starts from the remote base, so the files git ignores — ``.env`` and ``.env.local`` above
all — are not in it. The project lists the ones to carry in a ``.worktreeinclude`` file at its root
(or ``.chimera/worktreeinclude``), in ``.gitignore`` syntax, and a new conversation worktree gets a
copy of each file that matches AND that git ignores. A tracked file is never copied: it is already
in the worktree from the base, and a copy would overwrite the base's version with the owner's.

Safety, each a refusal rather than a warning:

* only files git reports as ignored and untracked (``git ls-files --others --ignored``), so a
  pattern that matches a tracked file does nothing to it;
* a symbolic link is never followed, in the source or as a parent of it, and a file whose resolved
  path leaves the project is skipped; a parent of the destination that is a symlink is refused too
  (a tracked symlink in the base must not make the copy land outside the worktree);
* at most :data:`MAX_BYTES` in total and :data:`MAX_FILES` files; a file that would pass a cap is
  skipped and the rest still go, and every skip is named in the report;
* a malformed pattern is reported by line and ignored, and a pattern that climbs with ``..`` is
  malformed. A rules file over :data:`MAX_RULES_BYTES` is not read.
* the copy waits for the owner's agreement (`worktree_include_consent.py`): the file is the
  repository's text, so it is asked about once per project and again when it changes;
* every copy is checked with `git check-ignore` IN the worktree and deleted again if git there does
  not ignore it, so a `git add -A` cannot stage a copied secret; the copied files are recorded (with
  their hashes) so the unattended worktree cleanup can tell them from files a conversation made.

Nothing here fails the worktree: the caller gets the report, and a copy that failed is a line in it.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.core.worktree import _admin_dir, _git, is_inside
from chimera.telemetry import get_logger

_log = get_logger("core.worktree_include")

#: The two places the rules file may be. Both are read when both exist, the root's first.
RULE_FILES = (".worktreeinclude", ".chimera/worktreeinclude")

#: Copied bytes per worktree. A ``.env`` is kilobytes; this stops a pattern that matches a model
#: cache from filling the disk of a new worktree.
MAX_BYTES = 50 * 1024 * 1024

#: Copied files per worktree.
MAX_FILES = 200

#: Files read from one ``git ls-files`` and the time it may take. A project with a ``node_modules`` is
#: one line per ignored folder, but the cut is here for the case that is not.
MAX_LISTED = 20_000
LIST_TIMEOUT = 20.0

#: The whole listing, all its git calls together. A per-call timeout alone lets a project with many
#: ignored folders take that long once per folder.
LIST_BUDGET = 20.0

#: Folders a bare-name rule does not reach into, so ``.env`` does not mean walking ``node_modules``.
HEAVY_DIRS = frozenset(
    {"node_modules", ".git", ".venv", "venv", "__pycache__", ".next", ".cache", ".tox", "target"}
)

#: How many names the payload and the consent screen carry. The rest is a count.
PREVIEW_FILES = 20

#: Where the copied files are recorded: the worktree's git admin directory (never in the checkout).
MANIFEST_FILE = "chimera-worktreeinclude.json"

#: A rules file bigger than this is not read: it is a mistake or an attack, not a list of files.
MAX_RULES_BYTES = 64 * 1024


@dataclass(frozen=True)
class Rule:
    """One line of a rules file: a glob, whether it negates, and where it is anchored."""

    text: str
    negated: bool
    anchored: bool
    dir_only: bool
    regex: re.Pattern[str]


@dataclass(frozen=True)
class InvalidLine:
    source: str
    line: int
    text: str
    reason: str


@dataclass
class IncludeReport:
    """What one worktree got. ``copied`` are project-relative POSIX paths; ``skipped`` pairs a path
    with the reason it was not copied; ``invalid`` names the rules that were ignored."""

    copied: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    invalid: list[InvalidLine] = field(default_factory=list)
    bytes_copied: int = 0
    #: Set when nothing was copied because the owner has not agreed to this rules file yet: the
    #: fingerprint, the first names and how many more (:func:`_preview`).
    consent: dict[str, Any] | None = None

    @property
    def empty(self) -> bool:
        return not (self.copied or self.skipped or self.invalid or self.consent)

    def as_payload(self) -> dict[str, Any]:
        """The shape the turn frame carries and the card reads. Lists are cut at
        :data:`PREVIEW_FILES` and carry their full count, so a hundred skips are a line, not a page."""
        out: dict[str, Any] = {
            "copied": self.copied[:PREVIEW_FILES],
            "copied_total": len(self.copied),
            "skipped": [{"path": p, "reason": r} for p, r in self.skipped[:PREVIEW_FILES]],
            "skipped_total": len(self.skipped),
            "invalid": [
                {"source": i.source, "line": i.line, "text": i.text, "reason": i.reason}
                for i in self.invalid[:PREVIEW_FILES]
            ],
            "bytes": self.bytes_copied,
        }
        if self.consent is not None:
            names = list(self.consent.get("files", []))
            out["consent_required"] = {
                **self.consent,
                "files": names[:PREVIEW_FILES],
                "more": max(0, len(names) - PREVIEW_FILES),
            }
        return out


# -- the pattern language ---------------------------------------------------------------------

def _glob_to_regex(glob: str) -> str:
    """Translate one gitignore glob (no leading ``!``, no trailing ``/``) to a regex over a path.

    ``*`` and ``?`` stay inside one segment; ``**`` as a whole segment crosses them. A character
    class must close, or the line is malformed — it is never guessed at.
    """
    out: list[str] = []
    i = 0
    n = len(glob)
    while i < n:
        ch = glob[i]
        if glob.startswith("**", i):
            at_start = i == 0 or glob[i - 1] == "/"
            at_end = i + 2 == n
            if at_start and at_end:
                out.append(".*")
                i += 2
                continue
            if at_start and glob[i + 2 : i + 3] == "/":
                out.append("(?:.*/)?")
                i += 3
                continue
            # `**` next to other characters behaves as two stars in git.
            out.append("[^/]*")
            i += 2
            continue
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "[":
            close = glob.find("]", i + 2 if glob[i + 1 : i + 2] in ("!", "^") else i + 1)
            if close == -1:
                raise ValueError("unclosed character class")
            body = glob[i + 1 : close]
            if body[:1] in ("!", "^"):
                body = "^" + body[1:]
            if "/" in body or "\\" in body:
                raise ValueError("a character class may not hold a slash or a backslash")
            out.append("[" + body + "]")
            i = close + 1
            continue
        elif ch == "\\":
            if i + 1 >= n:
                raise ValueError("a trailing backslash escapes nothing")
            out.append(re.escape(glob[i + 1]))
            i += 2
            continue
        else:
            out.append(re.escape(ch))
        i += 1
    return "".join(out)


def parse_rules(text: str, *, source: str = "") -> tuple[list[Rule], list[InvalidLine]]:
    """The rules of one file, in order, and the lines that were ignored, each with its reason."""
    rules: list[Rule] = []
    invalid: list[InvalidLine] = []
    for number, raw in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        line = raw.rstrip(" ") if not raw.endswith("\\ ") else raw
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "\x00" in line:
            invalid.append(InvalidLine(source, number, line[:80], "NUL byte"))
            continue
        negated = line.startswith("!")
        body = line[1:] if negated else line
        dir_only = body.endswith("/")
        if dir_only:
            body = body[:-1]
        anchored = "/" in body
        body = body.lstrip("/")
        if not body:
            invalid.append(InvalidLine(source, number, line[:80], "empty pattern"))
            continue
        if any(segment == ".." for segment in body.split("/")):
            invalid.append(InvalidLine(source, number, line[:80], "'..' climbs out of the project"))
            continue
        try:
            translated = _glob_to_regex(body)
            regex = re.compile(translated + r"\Z")
        except (ValueError, re.error) as exc:
            invalid.append(InvalidLine(source, number, line[:80], str(exc)))
            continue
        rules.append(Rule(body, negated, anchored, dir_only, regex))
    return rules, invalid


def _rule_hits(rule: Rule, prefix: str, *, is_dir: bool) -> bool:
    if rule.dir_only and not is_dir:
        return False
    subject = prefix if rule.anchored else prefix.rsplit("/", 1)[-1]
    return rule.regex.match(subject) is not None


def is_included(rules: Sequence[Rule], relpath: str) -> bool:
    """Whether ``relpath`` (a file, POSIX separators) is selected by ``rules``.

    Git's rule for ignores: the last rule that matches decides, and a directory's match covers the
    files under it. Here the chain is walked from the top directory down, each level takes the
    state of its last matching rule, and the deepest level decides. So ``config/`` selects
    everything under it, and ``!config/secret.json`` after it takes that one file back out.
    """
    parts = relpath.split("/")
    state = False
    prefix = ""
    for depth, part in enumerate(parts):
        prefix = part if depth == 0 else f"{prefix}/{part}"
        is_dir = depth < len(parts) - 1
        decided: bool | None = None
        for rule in rules:
            if _rule_hits(rule, prefix, is_dir=is_dir):
                decided = not rule.negated
        if decided is not None:
            state = decided
    return state


# -- reading the files ------------------------------------------------------------------------


def read_rules(project: Path) -> tuple[list[Rule], list[InvalidLine], list[str]]:
    """The rules of the project's rules files, in the order they are read, and the file names read.

    A file that is a symbolic link, is not a file, or is over :data:`MAX_RULES_BYTES` is not read.
    """
    rules: list[Rule] = []
    invalid: list[InvalidLine] = []
    read_from: list[str] = []
    root = Path(project)
    for name in RULE_FILES:
        path = root / name
        if path.is_symlink() or not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_RULES_BYTES:
                invalid.append(InvalidLine(name, 0, "", "file is larger than 64 KB; not read"))
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            invalid.append(InvalidLine(name, 0, "", f"could not be read: {exc.strerror or exc}"))
            continue
        found, bad = parse_rules(text, source=name)
        rules.extend(found)
        invalid.extend(bad)
        read_from.append(name)
    return rules, invalid, read_from


def _list_ignored(
    project: Path, extra: Sequence[str] = (), *, timeout: float = LIST_TIMEOUT
) -> list[str] | None:
    """One ``git ls-files`` over the ignored, untracked files; None when git failed or took too long."""
    try:
        result = _git(
            ["ls-files", "--others", "--ignored", "--exclude-standard", "-z", *extra],
            project,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return sorted(p for p in (result.stdout or "").split("\x00") if p)


def _rule_may_reach(rule: Rule, folder: str) -> bool:
    """Whether ``rule`` could select a file inside the ignored ``folder`` (POSIX, no trailing slash).
    A rule that names a heavy folder's contents does; a rule that matches a bare name at any depth
    does not reach into ``node_modules`` and the like, which is what keeps the listing short."""
    if rule.negated:
        return False
    heavy = any(part in HEAVY_DIRS for part in folder.split("/"))
    if not rule.anchored or rule.text.startswith("**"):
        return not heavy
    return rule.text.startswith(folder + "/")


def _may_hold(rules: Sequence[Rule], folder: str) -> bool:
    return is_included(rules, folder + "/\x00probe") or any(_rule_may_reach(r, folder) for r in rules)


def ignored_untracked(
    project: Path,
    rules: Sequence[Rule] | None = None,
    *,
    budget: float = LIST_BUDGET,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[str], bool]:
    """The ignored, untracked files of ``project`` (POSIX relative paths, sorted) and whether the
    listing was cut at :data:`MAX_LISTED` or failed.

    An ignored folder comes back as one entry (``--directory``) and is opened only when a rule could
    select something inside it, so a ``node_modules`` with a hundred thousand files is one line, not a
    hundred thousand. Without ``rules`` every ignored folder is opened.

    ``budget`` is the total time of every git call here (``clock`` is injectable): once it is spent
    the listing stops, what was listed so far stands, and the second value says it was cut."""
    started = clock()

    def left() -> float:
        return budget - (clock() - started)

    top = _list_ignored(project, ["--directory"], timeout=max(0.1, min(LIST_TIMEOUT, left())))
    if top is None:
        return [], True
    out: list[str] = []
    truncated = False
    for entry in top:
        if len(out) >= MAX_LISTED or left() <= 0:
            truncated = True
            break
        if not entry.endswith("/"):
            out.append(entry)
            continue
        folder = entry[:-1]
        if rules is not None and not _may_hold(rules, folder):
            continue
        inner = _list_ignored(project, ["--", entry], timeout=max(0.1, min(LIST_TIMEOUT, left())))
        if inner is None:
            truncated = True
            continue
        room = MAX_LISTED - len(out)
        out.extend(inner[:room])
        truncated = truncated or len(inner) > room
    return sorted(set(out)), truncated


def _is_link(path: Path) -> bool:
    """A symbolic link or, on Windows, a junction: neither is followed."""
    try:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def _has_symlink_parent(root: Path, relpath: str) -> bool:
    cur = root
    for part in relpath.split("/")[:-1]:
        cur = cur / part
        if _is_link(cur):
            return True
    return False


def rules_digest(project: Path) -> str:
    """A fingerprint of the rules files as they are now: what the owner is asked to agree to, and
    what changes when the file does."""
    h = hashlib.sha256()
    for name in RULE_FILES:
        path = Path(project) / name
        if _is_link(path) or not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_RULES_BYTES:
                continue
            body = path.read_bytes().replace(b"\r\n", b"\n")
        except OSError:
            continue
        h.update(name.encode() + b"\x00" + body + b"\x00")
    return h.hexdigest()


@dataclass
class IncludePlan:
    """What a copy would do, decided by reading only."""

    digest: str = ""
    files: list[tuple[str, int]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    invalid: list[InvalidLine] = field(default_factory=list)
    has_rules: bool = False


def plan_included(project: Path) -> IncludePlan:
    """The files a copy from ``project`` would carry, the ones it would refuse, and the rules that
    were ignored. Reads the project; writes nothing."""
    plan = IncludePlan()
    root = Path(project)
    rules, invalid, _read_from = read_rules(root)
    plan.invalid.extend(invalid)
    if not rules:
        return plan
    plan.has_rules = True
    plan.digest = rules_digest(root)
    root_real = root.resolve()
    listed, truncated = ignored_untracked(root, rules)
    if truncated:
        plan.skipped.append(
            ("(listing)", f"limited: stopped after {MAX_LISTED} files or {LIST_BUDGET:.0f} s, the rest was not copied")
        )
    total = 0
    for rel in listed:
        if not is_included(rules, rel):
            continue
        if len(plan.files) >= MAX_FILES:
            plan.skipped.append((rel, f"more than {MAX_FILES} files"))
            continue
        reason = _refusal(root, root_real, rel)
        if reason:
            plan.skipped.append((rel, reason))
            continue
        size = (root / rel).stat().st_size
        if total + size > MAX_BYTES:
            plan.skipped.append((rel, "over the 50 MB total"))
            continue
        total += size
        plan.files.append((rel, size))
    # What the owner agrees to is the rules AND the files they select now: a new ignored file that
    # matches the pattern is a different list, and asks again.
    h = hashlib.sha256(plan.digest.encode())
    for rel, _size in plan.files:
        h.update(b"\x00" + rel.encode("utf-8", "surrogatepass"))
    plan.digest = h.hexdigest()
    return plan


#: What the owner is asked about: ``(digest, preview) -> allowed``. Required: a copy with no way to
#: ask copies nothing.
Consent = Callable[[str, dict[str, Any]], bool]


def _preview(plan: IncludePlan) -> dict[str, Any]:
    names = [rel for rel, _size in plan.files]
    # The whole list (at most MAX_FILES names): the screen can show every file the owner agrees to.
    return {
        "digest": plan.digest,
        "files": names,
        "more": 0,
        "bytes": sum(size for _rel, size in plan.files),
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _ignored_in_worktree(worktree: Path, rel: str) -> bool:
    """Whether git, run IN the worktree, ignores ``rel``. The worktree starts from the base, so a
    ``.gitignore`` that exists only in the project's working copy does not protect the copy there."""
    try:
        done = _git(["check-ignore", "-q", "--", rel], worktree)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return done.returncode == 0


def _drop_copy(worktree: Path, rel: str) -> None:
    target = worktree / rel
    try:
        target.unlink()
    except OSError:
        return
    parent = target.parent
    while parent != worktree and parent.is_relative_to(worktree):
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def manifest_path(worktree: Path) -> Path | None:
    admin = _admin_dir(Path(worktree))
    return None if admin is None else admin / MANIFEST_FILE


def read_manifest(worktree: Path) -> dict[str, str]:
    """The files `.worktreeinclude` copied into ``worktree`` and their SHA-256 as copied. Empty when
    there is no record — which reads, to the cleanup, as "no ignored file here is the project's"."""
    path = manifest_path(worktree)
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = raw.get("files") if isinstance(raw, dict) else None
    if not isinstance(files, dict):
        return {}
    return {str(k): str(v) for k, v in files.items()}


def _write_manifest(worktree: Path, files: dict[str, str]) -> None:
    path = manifest_path(worktree)
    if path is None or not files:
        return
    try:
        path.write_text(json.dumps({"files": files}), encoding="utf-8")
    except OSError as exc:
        _log.warning("could not record the copied files of %s: %s", worktree, exc)


def copy_included(project: Path, worktree: Path, *, consent: Consent) -> IncludeReport:
    """Copy the selected ignored files of ``project`` into ``worktree``, once the owner has agreed to
    this rules file (``consent``). Never raises for a file.

    Each copy is then checked with git IN the worktree: a file it does not ignore there is deleted
    again and reported, so a copied secret can never be staged by a ``git add -A``."""
    report = IncludeReport()
    plan = plan_included(project)
    report.invalid.extend(plan.invalid)
    if not plan.has_rules:
        return report
    report.skipped.extend(plan.skipped)
    if not plan.files:
        return report
    preview = _preview(plan)
    if not consent(plan.digest, preview):
        report.consent = preview
        report.skipped = []
        return report
    root = Path(project)
    target_root = Path(worktree)
    manifest: dict[str, str] = {}
    for rel, size in plan.files:
        target = target_root / rel
        if target.exists() or _is_link(target):
            report.skipped.append((rel, "already present in the worktree"))
            continue
        if _has_symlink_parent(target_root, rel):
            report.skipped.append((rel, "a folder on its way is a symbolic link"))
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # The last look, right before the bytes move: the plan was made a moment ago, and a link
            # swapped in since (source, destination or a folder on either way) must not be followed.
            if (
                _is_link(root / rel) or _has_symlink_parent(root, rel)
                or _is_link(target) or _has_symlink_parent(target_root, rel)
            ):
                report.skipped.append((rel, "symbolic link: not followed (changed since the plan)"))
                continue
            shutil.copy2(root / rel, target, follow_symlinks=False)
        except OSError as exc:
            report.skipped.append((rel, f"copy failed: {exc.strerror or exc}"))
            continue
        if not _ignored_in_worktree(target_root, rel):
            _drop_copy(target_root, rel)
            report.skipped.append((rel, "not ignored in the worktree: the copy was removed"))
            continue
        try:
            manifest[rel] = _sha256(target)
        except OSError:
            _drop_copy(target_root, rel)
            report.skipped.append((rel, "could not be read back: the copy was removed"))
            continue
        report.copied.append(rel)
        report.bytes_copied += size
    _write_manifest(target_root, manifest)
    if report.copied or report.skipped:
        _log.info(
            "worktreeinclude: copied %d, skipped %d into %s",
            len(report.copied), len(report.skipped), worktree,
        )
    return report


def _refusal(root: Path, root_real: Path, rel: str) -> str:
    """Why ``rel`` must not be copied, or "" when it may."""
    source = root / rel
    if _has_symlink_parent(root, rel) or _is_link(source):
        return "symbolic link: not followed"
    if not source.is_file():
        return "not a regular file"
    if not is_inside(source.resolve(), root_real):
        return "resolves outside the project"
    return ""


__all__ = [
    "MANIFEST_FILE",
    "MAX_BYTES",
    "MAX_FILES",
    "RULE_FILES",
    "Consent",
    "IncludePlan",
    "IncludeReport",
    "InvalidLine",
    "Rule",
    "copy_included",
    "is_included",
    "parse_rules",
    "plan_included",
    "read_manifest",
    "read_rules",
    "rules_digest",
]
