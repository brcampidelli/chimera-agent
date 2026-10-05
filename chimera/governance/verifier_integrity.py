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
* ``tests_removed_or_skipped`` — a test function disappeared, or a test is under more
  skip/xfail/``.only`` markers than before, in a test file.

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
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
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
#: A JavaScript test that carries its own marker: ``it.skip('name'``, ``xit('name'``. The name is on
#: the marker's own line, so the marker is attributed to it directly.
_JS_MARKED_DEF = re.compile(
    r"^\s*(?:(?:it|test|describe)\.(?:skip|todo|only)|xit|xtest|xdescribe|fit|fdescribe)"
    r"\s*\(\s*(['\"`])(.+?)\1"
)
#: Any definition a decorator can sit on or a body-level ``pytest.skip()`` can sit in — a test or
#: not, so a decorator on a helper is attributed to the helper rather than to the next test down.
_ANY_DEF = re.compile(
    r"^(\s*)(?:(?:async\s+)?def\s+(\w+)|class\s+(\w+)|func\s+(?:\([^)]*\)\s*)?(\w+)"
    r"|(?:it|test|describe)\s*\(\s*(['\"`])(.+?)\5)"
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
        # `it.only('x'` is still test x: read as a definition, so marking a test is reported as a
        # skip and not as the test having been removed.
        marked = _JS_MARKED_DEF.match(line)
        if marked:
            names.add(marked.group(2))
            continue
        for pattern in _TEST_DEF:
            match = pattern.match(line)
            if match:
                names.add(match.group(match.lastindex or 1))
                break
    return names


def _definition(line: str) -> tuple[int, str] | None:
    """``(indent, name)`` when ``line`` opens a definition, else None."""
    marked = _JS_MARKED_DEF.match(line)
    if marked:
        return len(line) - len(line.lstrip()), marked.group(2)
    match = _ANY_DEF.match(line)
    if not match:
        return None
    name = match.group(2) or match.group(3) or match.group(4) or match.group(6) or ""
    return len(match.group(1)), name


def _skipped(lines: Sequence[str]) -> Counter[str]:
    """The skip markers in a run of lines, counted, keyed by the test each one turns off.

    Keyed by TEST and not by marker text because a bare ``@pytest.mark.skip`` is the same text on
    every test it is put on: compared as text, moving it from a passing test to the failing one is
    the same line removed and re-added, and the flag that exists for "skip the test that fails" was
    silent on exactly that move. A decorator belongs to the next definition below it; a marker with
    its own name (``it.skip('x'``) to that name; a call in a body (``pytest.skip()``, ``t.Skip()``)
    to the nearest definition above it with less indentation. A marker none of that places is
    counted by its text, so two bare markers where there was one still reads as one more.
    """
    out: Counter[str] = Counter()
    for i, line in enumerate(lines):
        if not _SKIP.search(line):
            continue
        stripped = line.strip()
        own = _JS_MARKED_DEF.match(line)
        name: str | None = own.group(2) if own else None
        if name is None and stripped.startswith("@"):
            for below in lines[i + 1:]:
                if not below.strip() or below.strip().startswith("@"):
                    continue
                found = _definition(below)
                name = found[1] if found else None
                break
        elif name is None:
            indent = len(line) - len(line.lstrip())
            for above in reversed(lines[:i]):
                found = _definition(above)
                if found and found[0] < indent:
                    name = found[1]
                    break
        out["test " + name if name else "marker " + stripped[:80]] += 1
    return out


def _hunk_sides(patch: str) -> list[tuple[list[str], list[str]]]:
    """Each hunk of a unified diff as ``(old side, new side)``, context lines on both.

    Context is what lets a stored patch attribute a marker to the test below it: a decorator added
    above an unchanged ``def`` has that ``def`` only as context. Hunks stay separate so a body-level
    skip is never attributed to a definition that sits in a different hunk.
    """
    sides: list[tuple[list[str], list[str]]] = []
    old: list[str] = []
    new: list[str] = []
    for raw in patch.splitlines():
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith("@@"):
            if old or new:
                sides.append((old, new))
            old, new = [], []
        elif raw.startswith("+"):
            new.append(raw[1:])
        elif raw.startswith("-"):
            old.append(raw[1:])
        elif raw.startswith(" "):
            old.append(raw[1:])
            new.append(raw[1:])
    if old or new:
        sides.append((old, new))
    return sides


def _removed_or_skipped(sides: list[tuple[list[str], list[str]]]) -> str:
    """Why this change turns tests off, or "" when it does not.

    ``sides`` is the file before and after — the whole texts for a snapshot, the hunks for a stored
    patch. Both questions are asked of the whole file at once, so a test that only moved from one
    hunk to another is neither gone nor newly skipped. Skips are a multiset: a test that is now
    under more markers than before counts, whatever happened to the same marker text elsewhere.
    """
    before = [line for old, _ in sides for line in old]
    after = [line for _, new in sides for line in new]
    gone = sorted(_test_names(before) - _test_names(after))
    was: Counter[str] = Counter()
    now: Counter[str] = Counter()
    for old, new in sides:
        was += _skipped(old)
        now += _skipped(new)
    newly = sorted((now - was).elements())
    reasons: list[str] = []
    if gone:
        reasons.append("removed " + ", ".join(gone[:5]) + (" …" if len(gone) > 5 else ""))
    if newly:
        reasons.append("skipped " + ", ".join(newly[:5]) + (" …" if len(newly) > 5 else ""))
    return "; ".join(reasons)


def flag_patch(path: str, patch: str, *, verify_command: str = "") -> list[IntegrityFlag]:
    """The three flags for one file's unified-diff body. ``patch`` empty = no change, no flags."""
    return _flag(path, patch, _hunk_sides(patch), verify_command=verify_command)


def _flag(
    path: str, patch: str, sides: list[tuple[list[str], list[str]]], *, verify_command: str
) -> list[IntegrityFlag]:
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
        reason = _removed_or_skipped(sides)
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
        # The whole texts decide the skip rule, not the n=0 patch: without context a decorator
        # added above an unchanged ``def`` could not be attributed to that test.
        sides = [(old.splitlines(), new.splitlines())]
        out.extend(_flag(path, patch, sides, verify_command=verify_command))
    return _ordered(out)


def _ordered(flags: list[IntegrityFlag]) -> list[IntegrityFlag]:
    return sorted(flags, key=lambda f: (KINDS.index(f.kind), f.path))
