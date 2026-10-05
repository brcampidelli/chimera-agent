"""Record-only flags for an attempt that changed what judges it: the tests, or the verifier.

The autonomous loop runs the verify command in the very workspace the agent just edited. A pass
there is evidence about the code only while the instrument is the one the user set up. An attempt
that rewrote a test, deleted one, marked one ``skip``/``xfail``, or edited the file the verify
command runs, can turn the verifier green without touching the bug — and the receipt used to say
``verified: True`` either way. ImpossibleBench (arXiv 2510.20270) measured 49–76% of frontier-model
solves on impossible tasks passing by exactly these moves; 2609.28614 found 30.5% on ordinary ones.

Three flags, each one rendered line per file:

* ``tests_touched`` — a test file was added, changed or deleted;
* ``verifier_modified`` — a file the verify command names (``pytest tests/test_a.py``,
  ``bash check.sh``) or a test-runner configuration (``conftest.py``, the pytest section of
  ``pyproject.toml``/``setup.cfg``/``tox.ini``, a ``jest``/``vitest`` config) changed;
* ``tests_removed_or_skipped`` — a test function disappeared, or a skip/xfail/``.only`` marker
  appeared, in a test file.

They are RECORD-ONLY. Legitimate work edits tests all the time — a bug fix ships with its
regression test — so none of the three blocks, and none pauses: a REVIEW on any of them waits for
its false-positive rate, measured on stored solves in `bench/verifier_integrity/`. What they buy is
that a reader of the receipt can tell "verified against the user's tests" from "verified against
tests this same attempt rewrote" without re-reading the diff.

Lexical by design and declared as such: no model, no network, no ``ast`` (test files are Python,
JavaScript, Go…). A test renamed in the same patch reads as removed; a skip added through a helper
the regexes do not know is missed. Both limits are the price of a rule cheap enough to run on every
attempt, and the reason it records instead of acting.
"""

from __future__ import annotations

import difflib
import posixpath
import re
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

TESTS_TOUCHED = "tests_touched"
VERIFIER_MODIFIED = "verifier_modified"
TESTS_REMOVED_OR_SKIPPED = "tests_removed_or_skipped"

#: The three kinds, in the order a receipt lists them.
KINDS: tuple[str, ...] = (TESTS_TOUCHED, VERIFIER_MODIFIED, TESTS_REMOVED_OR_SKIPPED)

_TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec", "specs", "testing"})
_TEST_NAME = re.compile(
    r"^(?:test_.+\.py|.+_test\.py|.+_test\.go|.+\.(?:test|spec)\.(?:[cm]?[jt]sx?)|.+Test\.java"
    r"|.+_spec\.rb)$"
)

#: Files that configure the test runner itself. ``conftest.py`` can skip a whole directory with
#: one line, so it is the verifier even when no test changed.
_RUNNER_FILES = frozenset({"conftest.py", "pytest.ini", "noxfile.py"})
_RUNNER_CONFIG = re.compile(r"^(?:jest|vitest|karma|mocha|playwright)\.config\.[cm]?[jt]s$|^\.mocharc\.")
#: Shared config files that are a verifier only in their test section: ``pyproject.toml`` is edited
#: for dependencies far more often than for pytest, and counting every such edit would bury the
#: flag. A changed line has to mention the runner.
_SHARED_CONFIG = frozenset({"pyproject.toml", "setup.cfg", "tox.ini", "package.json"})
_RUNNER_LINE = re.compile(
    r"pytest|addopts|testpaths|python_files|python_functions|norecursedirs|\[testenv"
    r"|\"test\"\s*:|jest|vitest|mocha"
)

#: A test definition, per language, capturing the name so a rename-in-place is not "removed".
_TEST_DEF = (
    re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\("),
    re.compile(r"^\s*(?:it|test)\s*\(\s*(['\"`])(.+?)\1"),
    re.compile(r"^\s*func\s+(Test\w+)\s*\("),
)
#: A marker that turns a test off (or, with ``.only``/``fit``, turns every OTHER test off).
_SKIP = re.compile(
    r"@pytest\.mark\.(?:skip|skipif|xfail)\b|\bpytest\.(?:skip|xfail|importorskip)\s*\("
    r"|@unittest\.(?:skip|skipIf|skipUnless|expectedFailure)\b|\bself\.skipTest\s*\("
    r"|\braise\s+(?:unittest\.)?SkipTest\b"
    r"|\b(?:it|test|describe)\.(?:skip|todo|only)\s*\(|(?<![\w.])(?:xit|xtest|xdescribe|fit|fdescribe)\s*\("
    r"|\bt\.Skip(?:Now|f)?\s*\(|@(?:Disabled|Ignore)\b"
)


@dataclass(frozen=True)
class IntegrityFlag:
    kind: str
    path: str
    detail: str = ""

    def render(self) -> str:
        return f"{self.kind}: {self.path}" + (f" — {self.detail}" if self.detail else "")


def _norm(path: str) -> str:
    path = path.replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    return path.strip("/")


def is_test_path(path: str) -> bool:
    """A test file by name or by directory. ``conftest.py`` is the runner, not a test."""
    path = _norm(path)
    base = posixpath.basename(path)
    if base == "conftest.py":
        return False
    if _TEST_NAME.match(base):
        return True
    return any(part in _TEST_DIRS for part in path.split("/")[:-1])


def _command_paths(verify_command: str) -> set[str]:
    """The tokens of the verify command that could name a file or directory in the workspace."""
    if not verify_command:
        return set()
    try:
        tokens = shlex.split(verify_command, posix=True)
    except ValueError:
        tokens = verify_command.split()
    out: set[str] = set()
    for raw in tokens:
        if raw.startswith("-") or any(ch in raw for ch in "*?$|;&<>()"):
            continue
        token = _norm(raw.split("::", 1)[0])
        if token and token not in (".", ".."):
            out.add(token)
    return out


def _named_by_command(path: str, command_paths: set[str]) -> bool:
    return any(path == t or path.startswith(t + "/") for t in command_paths)


def _changed_lines(patch: str) -> tuple[list[str], list[str]]:
    added: list[str] = []
    removed: list[str] = []
    for raw in patch.splitlines():
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith("+"):
            added.append(raw[1:])
        elif raw.startswith("-"):
            removed.append(raw[1:])
    return added, removed


def _test_names(lines: Iterable[str]) -> set[str]:
    names: set[str] = set()
    for line in lines:
        for pattern in _TEST_DEF:
            match = pattern.match(line)
            if match:
                names.add(match.group(match.lastindex or 1))
                break
    return names


def _removed_or_skipped(added: list[str], removed: list[str]) -> str:
    """Why this patch turns tests off, or "" when it does not."""
    gone = sorted(_test_names(removed) - _test_names(added))
    # A marker that was only MOVED (the same line removed and re-added) is not a new skip.
    moved = {line.strip() for line in removed}
    skips = [line.strip() for line in added if _SKIP.search(line) and line.strip() not in moved]
    reasons: list[str] = []
    if gone:
        reasons.append("removed " + ", ".join(gone[:5]) + (" …" if len(gone) > 5 else ""))
    if skips:
        reasons.append("added " + skips[0][:80])
    return "; ".join(reasons)


def flag_patch(path: str, patch: str, *, verify_command: str = "") -> list[IntegrityFlag]:
    """The three flags for one file's unified-diff body. ``patch`` empty = no change, no flags."""
    path = _norm(path)
    if not path or not patch:
        return []
    added, removed = _changed_lines(patch)
    if not added and not removed:
        return []
    base = posixpath.basename(path)
    out: list[IntegrityFlag] = []
    test = is_test_path(path)
    if test:
        out.append(IntegrityFlag(TESTS_TOUCHED, path))
    if _named_by_command(path, _command_paths(verify_command)):
        out.append(IntegrityFlag(VERIFIER_MODIFIED, path, "named by the verify command"))
    elif base in _RUNNER_FILES or _RUNNER_CONFIG.match(base):
        out.append(IntegrityFlag(VERIFIER_MODIFIED, path, "test-runner configuration"))
    elif base in _SHARED_CONFIG and any(_RUNNER_LINE.search(line) for line in added + removed):
        out.append(IntegrityFlag(VERIFIER_MODIFIED, path, "test-runner section"))
    if test or base == "conftest.py":
        reason = _removed_or_skipped(added, removed)
        if reason:
            out.append(IntegrityFlag(TESTS_REMOVED_OR_SKIPPED, path, reason))
    return out


def flag_patches(
    diffs: Iterable[tuple[str, str]], *, verify_command: str = ""
) -> list[IntegrityFlag]:
    """Every flag over ``(path, patch)`` pairs — the shape a stored receipt keeps its diffs in."""
    out: list[IntegrityFlag] = []
    for path, patch in diffs:
        out.extend(flag_patch(path, patch, verify_command=verify_command))
    return _ordered(out)


def flag_snapshots(
    before: Mapping[str, str], after: Mapping[str, str], *, verify_command: str = ""
) -> list[IntegrityFlag]:
    """Every flag over a workspace change: {relative path: text} before and after.

    The union of both sides, because a DELETED test file is the strongest case of all and exists
    only in ``before``. The diff is computed here in full, so a receipt's clipped patch never
    decides what counts as removed.
    """
    out: list[IntegrityFlag] = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path, ""), after.get(path, "")
        if old == new:
            continue
        patch = "\n".join(
            difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
        )
        out.extend(flag_patch(path, patch, verify_command=verify_command))
    return _ordered(out)


def _ordered(flags: list[IntegrityFlag]) -> list[IntegrityFlag]:
    return sorted(flags, key=lambda f: (KINDS.index(f.kind), f.path))
