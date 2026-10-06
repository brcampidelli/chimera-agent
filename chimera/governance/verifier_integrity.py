"""Record-only flags for an attempt that changed what judges it: the tests, or the verifier.

The autonomous loop runs the verify command in the very workspace the agent just edited. A pass
there is evidence about the code only while the instrument is the one the user set up. An attempt
that rewrote a test, deleted one, marked one ``skip``/``xfail``, or edited the file the verify
command runs, can turn the verifier green without touching the bug — and the receipt used to say
``verified: True`` either way. ImpossibleBench (arXiv 2510.20270) measured 49–76% of frontier-model
solves on impossible tasks passing by exactly these moves; 2609.28614 found 30.5% on ordinary ones.

Three flags, each one rendered line per file:

* ``tests_touched`` — a test file was added, changed or deleted;
* ``verifier_modified`` — a FILE the verify command names (``pytest tests/test_a.py``,
  ``bash check.sh``), the build file whose recipe it runs (``make test`` → ``Makefile``,
  ``just``, ``tox``), the file it was inferred from, or a test-runner configuration
  (``conftest.py``, the pytest section of ``pyproject.toml``/``setup.cfg``/``tox.ini``, a
  ``jest``/``vitest`` config) changed. A DIRECTORY the command names (``pytest tests``,
  ``cd backend && …``) is not the verifier: every edit beneath it would be flagged, and for a test
  directory that only repeats ``tests_touched``;
* ``tests_removed_or_skipped`` — a test function disappeared, or a test is under more
  skip/xfail/``.only`` markers than before, in a test file.

They are RECORD-ONLY. Legitimate work edits tests all the time — a bug fix ships with its
regression test — so none of the three blocks, and none pauses: a REVIEW on any of them waits for
its false-positive rate, measured on stored solves in `bench/verifier_integrity/`. What they buy is
that a reader of the receipt can tell "verified against the user's tests" from "verified against
tests this same attempt rewrote" without re-reading the diff.

Where it runs, and where it does not: on the autonomous loop's attempt receipts (``chimera solve``,
the desktop Run, everything built on `AutonomousAgent`) and on the Code tab's verdict after each
editing turn (``integrity_flags`` on the ``verified`` event and the stored receipt). NOT on the
crew's per-worker check or its re-verify of the merge (`chimera/orchestration/crew.py`): a worker
runs in its own worktree with no snapshot taken here, so nothing there says these flags were
checked, and an absent field must not be read as a clean one.

Lexical by design and declared as such: no model, no network, no ``ast`` (test files are Python,
JavaScript, Go…). A test renamed in the same patch reads as removed. Skips are read in the APIs'
own spellings — ``@pytest.mark.skip``/``skipif``/``xfail`` and ``@mark.…`` imported from pytest,
``pytestmark = …skip`` on the module, ``pytest.param(..., marks=…skip)``, ``@unittest.skip…`` and
``@skip(…)``/``@expectedFailure`` imported from unittest, ``pytest.skip()``/``self.skipTest()``/
``raise SkipTest``, JS ``.skip``/``.only``/``x…``/``f…``, Go ``t.Skip``, JUnit ``@Disabled``.
MISSED: a ``pytestmark = [`` list whose marker sits on a later line, a marker bound to a name first
(``slow = pytest.mark.skip`` then ``@slow``), and a skip done in ``conftest.py`` hooks
(``pytest_collection_modifyitems``) — though conftest itself is flagged as the runner. These limits
are the price of a rule cheap enough to run on every attempt, and the reason it records instead of
acting.
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
#:
#: ``@mark.skip`` (after ``from pytest import mark``), ``@skip('x')``/``@expectedFailure`` (after
#: ``from unittest import skip``), ``pytestmark = pytest.mark.skip`` and
#: ``pytest.param(..., marks=pytest.mark.skip)`` are the APIs' own spellings, not helpers. Matching
#: only the ``@pytest.mark.``/``@unittest.`` prefixes left the one-line, whole-file skip as nothing
#: more than ``tests_touched``.
_SKIP = re.compile(
    r"@(?:pytest\.)?mark\.(?:skip|skipif|xfail)\b|\bpytest\.(?:skip|xfail|importorskip)\s*\("
    r"|@unittest\.(?:skip|skipIf|skipUnless|expectedFailure)\b|\bself\.skipTest\s*\("
    r"|@skip(?:If|Unless)?\s*\(|@expectedFailure\b"
    r"|^\s*pytestmark\s*=.*\bmark\.(?:skip|skipif|xfail)\b"
    r"|\bmarks\s*=.*\bmark\.(?:skip|skipif|xfail)\b"
    r"|\braise\s+(?:unittest\.)?SkipTest\b"
    r"|\b(?:it|test|describe)\.(?:skip|todo|only)\s*\(|(?<![\w.])(?:xit|xtest|xdescribe|fit|fdescribe)\s*\("
    r"|\bt\.Skip(?:Now|f)?\s*\(|@(?:Disabled|Ignore)\b"
)
#: ``pytestmark = …`` at column 0 marks every test in the module at once, so it is keyed to the
#: module and not to whatever definition happens to sit nearby.
_MODULE_MARK = re.compile(r"^pytestmark\s*=")
#: ``pytest.param(..., marks=pytest.mark.skip)`` is a case of the parametrized test BELOW it (it sits
#: inside that test's decorator), so it is attributed downward like a decorator, never upward.
_PARAM_MARK = re.compile(r"\bmarks\s*=.*\bmark\.(?:skip|skipif|xfail)\b")
#: A JavaScript test that carries its own marker:``it.skip('name'``, ``xit('name'``. The name is on
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


#: Options whose next token is a value, never a file: ``-k "not slow"``, ``python -m pytest``,
#: ``-p no:cacheprovider``, ``make -j 4``. ``-c`` (pytest's config file) is absent on purpose: its
#: operand IS a file the verifier reads.
_VALUE_OPTIONS = frozenset(
    {"-k", "-m", "-p", "-n", "-W", "-o", "-j", "--maxfail", "--rootdir", "--deselect", "--ignore",
     "--timeout", "--tb", "--durations"}
)
_SEPARATORS = frozenset({"&&", "||", ";", "|", "&", "(", ")"})
_REDIRECTS = frozenset({">", ">>", "<", ">&", "&>"})
_CHDIR = frozenset({"cd", "pushd"})
_ENV_ASSIGN = re.compile(r"^[A-Za-z_]\w*=")
#: Programs whose recipe lives in a build file the command never names. ``make test`` runs
#: whatever the Makefile's ``test:`` says, so rewriting that recipe to ``true`` rewrites the
#: verifier, and ``verify_infer`` produces exactly ``make test`` from a Makefile.
_BUILD_FILES: dict[str, tuple[str, ...]] = {
    "make": ("Makefile", "makefile", "GNUmakefile"),
    "gmake": ("Makefile", "makefile", "GNUmakefile"),
    "just": ("justfile", "Justfile", ".justfile"),
    "tox": ("tox.ini",),
    "nox": ("noxfile.py",),
}
#: Where those programs are told to look instead: ``make -C sub``, ``make -f other.mk``.
_BUILD_DIR_OPTIONS = frozenset({"-C", "--directory", "--working-directory"})
_BUILD_FILE_OPTIONS = frozenset({"-f", "--file", "--makefile", "--justfile", "-c"})


@dataclass(frozen=True)
class _Verifier:
    """What, in the workspace, IS the verifier, read off the command (and its origin)."""

    named: frozenset[str] = frozenset()
    build: frozenset[str] = frozenset()
    origin: frozenset[str] = frozenset()


def _join(cwd: str, token: str) -> str:
    if token.startswith("/") or re.match(r"^[A-Za-z]:", token):
        return ""  # absolute: outside anything a workspace-relative path can equal
    return _norm(posixpath.normpath(posixpath.join(cwd, token)) if cwd else token)


def _read_segment(words: list[str], cwd: str, named: set[str], build: set[str]) -> str:
    """One simple command of the verify line; returns the directory the next one runs in."""
    while words and _ENV_ASSIGN.match(words[0]):
        words = words[1:]
    if not words:
        return cwd
    program, args = words[0], words[1:]
    base = posixpath.basename(program)
    if base in _CHDIR:
        # `cd backend && pytest`: `backend` is where the check runs, not a file it runs. Read as a
        # path it put every edit under backend/ on the receipt as "the verifier".
        target = next((a for a in args if not a.startswith("-")), "")
        return _join(cwd, target) if target else ""
    if "/" in program or "." in base:
        named.add(_join(cwd, program))  # ./check.sh, scripts/test.py
    files = _BUILD_FILES.get(base)
    where = cwd
    skip = ""
    for arg in args:
        if skip:
            if skip == "dir":
                where = _join(cwd, arg)
            elif skip == "file":
                build.add(_join(cwd, arg))
            skip = ""
            continue
        if files is not None and arg in _BUILD_DIR_OPTIONS:
            skip = "dir"
        elif files is not None and arg in _BUILD_FILE_OPTIONS:
            skip = "file"
        elif arg in _VALUE_OPTIONS or arg in _REDIRECTS:
            skip = "value"
        elif arg.startswith("-") or any(ch in arg for ch in "*?$"):
            continue
        else:
            named.add(_join(cwd, arg.split("::", 1)[0]))
    if files is not None:
        build.update(_join(where, name) for name in files)
    return cwd


def _verifier_of(verify_command: str, verifier_files: Iterable[str] = ()) -> _Verifier:
    origin = frozenset(p for p in (_norm(f) for f in verifier_files) if p)
    if not verify_command.strip():
        return _Verifier(origin=origin)
    # Backslashes as separators, not as POSIX escapes: `scripts\test.cmd` is a path on Windows.
    text = verify_command.replace("\\", "/")
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = text.split()
    named: set[str] = set()
    build: set[str] = set()
    cwd = ""
    words: list[str] = []
    for token in [*tokens, ";"]:
        if token in _SEPARATORS:
            cwd = _read_segment(words, cwd, named, build)
            words = []
        else:
            words.append(token)
    named.discard("")
    named.discard(".")
    build.discard("")
    return _Verifier(frozenset(named), frozenset(build), origin)


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
        if name is None and _MODULE_MARK.match(line):
            out["whole module (pytestmark)"] += 1
            continue
        if name is None and _PARAM_MARK.search(line) and not stripped.startswith("@"):
            # Inside a multi-line decorator call: the rest of the call (`2,`, `])`) comes before
            # the `def`, so take the first definition below rather than the first line.
            for below in lines[i + 1:]:
                found = _definition(below)
                if found:
                    name = found[1]
                    break
        elif name is None and stripped.startswith("@"):
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


def flag_patch(
    path: str, patch: str, *, verify_command: str = "", verifier_files: Iterable[str] = ()
) -> list[IntegrityFlag]:
    """The three flags for one file's unified-diff body. ``patch`` empty = no change, no flags.

    ``verifier_files``: files the verify command came out of, when the caller knows them — an
    inferred ``make test`` names its Makefile here, so a rewritten recipe is the verifier changing.
    """
    verifier = _verifier_of(verify_command, verifier_files)
    return _flag(path, patch, _hunk_sides(patch), verifier)


def _flag(
    path: str, patch: str, sides: list[tuple[list[str], list[str]]], verifier: _Verifier
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
    if path in verifier.named:
        out.append(IntegrityFlag(VERIFIER_MODIFIED, path, "named by the verify command"))
    elif path in verifier.build:
        out.append(IntegrityFlag(VERIFIER_MODIFIED, path, "the build file the verify command runs"))
    elif path in verifier.origin:
        out.append(IntegrityFlag(VERIFIER_MODIFIED, path, "the verify command was inferred from it"))
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
    diffs: Iterable[tuple[str, str]], *, verify_command: str = "",
    verifier_files: Iterable[str] = (),
) -> list[IntegrityFlag]:
    """Every flag over ``(path, patch)`` pairs — the shape a stored receipt keeps its diffs in."""
    verifier = _verifier_of(verify_command, verifier_files)
    out: list[IntegrityFlag] = []
    for path, patch in diffs:
        out.extend(_flag(path, patch, _hunk_sides(patch), verifier))
    return _ordered(out)


def flag_snapshots(
    before: Mapping[str, str], after: Mapping[str, str], *, verify_command: str = "",
    verifier_files: Iterable[str] = (),
) -> list[IntegrityFlag]:
    """Every flag over a workspace change: {relative path: text} before and after.

    The union of both sides, because a DELETED test file is the strongest case of all and exists
    only in ``before``. The diff is computed here in full, so a receipt's clipped patch never
    decides what counts as removed.
    """
    verifier = _verifier_of(verify_command, verifier_files)
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
        out.extend(_flag(path, patch, sides, verifier))
    return _ordered(out)


def _ordered(flags: list[IntegrityFlag]) -> list[IntegrityFlag]:
    return sorted(flags, key=lambda f: (KINDS.index(f.kind), f.path))
