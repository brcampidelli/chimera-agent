"""A generated spec test is evidence only if it fails before the change and passes after.

`SpecTestVerifier` kept whatever the generator wrote as long as it said `def test`, ran it on the
workspace the attempt left, and reported the exit code as `evidence="verifier"`. A test that would
have passed on the workspace BEFORE the attempt measured nothing about the attempt — and
`bench/spec_test_vacuity` (2026-09-11) counted how much of the shipped gate rested on exactly that.

ExecCritic (arXiv 2609.09133): keep the tests that fail on the base and pass on the candidate.
With the attempt's snapshot handed in, a test that passes on both is excluded and counted; if none
remain the verifier ABSTAINS (the caller falls back to its other gates, as it already does when
nothing runnable was generated); a test that passes before and fails after is a regression; a test
that fails on both is unchanged — the candidate did not satisfy the spec. Without a snapshot the
behaviour is byte-identical to before.

Real pytest, real subprocesses: the classification is read off `-rA` output, and a fake would be
asserting the shape of a string this module itself invented.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.core.checklist import Requirement
from chimera.core.checkpoint import FileSnapshot, WorkspaceGuard
from chimera.core.spec_test import SpecTestVerifier, parse_outcomes


class _Gen:
    def __init__(self, code: str) -> None:
        self.code = code

    def generate(self, task: str, reqs: Any, *, code_context: str = "") -> str:
        return self.code


REQS = [Requirement(text="add works", kind="do")]


@pytest.fixture(autouse=True)
def _host_exec_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The generated tests run behind the host-exec gate (no isolated sandbox here); this file is
    about the classification, not the gate — say so, so the commands really run."""
    monkeypatch.setenv("CHIMERA_HOST_EXEC", "allow")

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"

TESTS = (
    "from calc import add\n\n"
    "def test_add_two_and_three():\n    assert add(2, 3) == 5\n\n"        # fails on buggy, passes on fixed
    "def test_add_is_callable():\n    assert callable(add)\n\n"            # passes on both: vacuous
)


def _verifier(workspace: Path, code: str = TESTS) -> SpecTestVerifier:
    # `sys.executable` keeps the run on the interpreter that has pytest; the default `python`
    # resolves to whatever is first on PATH, which on this machine has no pytest.
    import sys

    return SpecTestVerifier(
        _Gen(code), "add", REQS, workspace, command=f'"{sys.executable}" -m pytest -q -rA -p no:cacheprovider {{file}}'
    )


def test_parse_outcomes_reads_the_short_summary() -> None:
    out = (
        "FAILED test_chimera_spec.py::test_a - assert 0 == 4\n"
        "PASSED test_chimera_spec.py::test_b\n"
        "ERROR test_chimera_spec.py::test_c\n"
        "PASSED other_test.py::test_not_ours\n"
    )
    assert parse_outcomes(out, "test_chimera_spec.py") == {"test_a": "FAILED", "test_b": "PASSED", "test_c": "ERROR"}
    assert parse_outcomes("collected 0 items / 1 error", "test_chimera_spec.py") == {}


def test_without_a_snapshot_the_exit_code_decides_as_before(tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text(FIXED, encoding="utf-8")
    result = _verifier(tmp_path).verify()
    assert result.passed and not result.abstained
    assert "vacuous" not in result.output


def test_a_fix_is_verified_by_the_test_that_failed_on_the_bug_and_the_vacuous_one_is_named(tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text(BUGGY, encoding="utf-8")
    before = WorkspaceGuard(tmp_path).snapshot()
    (tmp_path / "calc.py").write_text(FIXED, encoding="utf-8")
    verifier = _verifier(tmp_path)
    verifier.base_snapshot = before
    result = verifier.verify()
    assert result.passed and not result.abstained
    assert "1 discriminating, 1 vacuous" in result.output
    assert "vacuous: test_add_is_callable" in result.output


def test_when_every_test_passes_on_the_bug_too_the_verifier_abstains(tmp_path: Path) -> None:
    """The shipped verifier's false positive: a green verdict on a workspace that still holds the bug."""
    (tmp_path / "calc.py").write_text(BUGGY, encoding="utf-8")
    before = WorkspaceGuard(tmp_path).snapshot()
    # The worker changed nothing that matters; the generator wrote only tests that cannot fail.
    verifier = _verifier(tmp_path, "from calc import add\n\ndef test_add_is_callable():\n    assert callable(add)\n")
    verifier.base_snapshot = before
    result = verifier.verify()
    assert result.passed and result.abstained, "a test that passes on the bug is not evidence"
    assert "no test could have failed before the change" in result.output


def test_a_regression_fails_the_attempt(tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text(FIXED, encoding="utf-8")
    before = WorkspaceGuard(tmp_path).snapshot()
    (tmp_path / "calc.py").write_text("def add(a, b):\n    raise RuntimeError('broken')\n", encoding="utf-8")
    verifier = _verifier(tmp_path)
    verifier.base_snapshot = before
    result = verifier.verify()
    assert not result.passed
    assert "1 regression(s)" in result.output


def test_a_candidate_that_still_fails_the_spec_still_fails(tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text(BUGGY, encoding="utf-8")
    before = WorkspaceGuard(tmp_path).snapshot()
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a * b\n", encoding="utf-8")
    verifier = _verifier(tmp_path)
    verifier.base_snapshot = before
    result = verifier.verify()
    assert not result.passed and "1 failing" in result.output


def test_a_module_created_by_the_attempt_fails_on_the_empty_base_by_construction(tmp_path: Path) -> None:
    """A `create X` task: the base has nothing to import, so every test failed before the change."""
    before = FileSnapshot()  # an empty workspace
    (tmp_path / "calc.py").write_text(FIXED, encoding="utf-8")
    verifier = _verifier(tmp_path)
    verifier.base_snapshot = before
    result = verifier.verify()
    assert result.passed and not result.abstained
    assert "2 discriminating, 0 vacuous" in result.output


def test_the_attempt_loop_hands_the_snapshot_to_a_verifier_that_can_use_it(tmp_path: Path) -> None:
    from chimera.core.autonomous import AutonomousAgent as AutonomousRunner

    class _Verifier:
        base_snapshot: Any = None

        def verify(self) -> Any:
            from chimera.core.verify import VerificationResult

            return VerificationResult(True, "ok")

    runner = AutonomousRunner.__new__(AutonomousRunner)
    runner.verifier = _Verifier()
    snap = FileSnapshot(files={"a.py": "x = 1"}, present={"a.py"})
    assert runner._verify(snap) == (True, "ok", False)
    assert runner.verifier.base_snapshot is snap
