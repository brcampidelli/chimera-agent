"""`python -m pytest` on a machine without pytest is not a failing test.

Found by driving the shipped rc39 build. Asked for a file with a function in it, with
``gen_tests`` on, the run wrote the file, generated a test for it, and came back:

    verified: false · reverted: true · evidence: "verifier"
    diff: +1 new (soma.py)
    verify_output: spec-grounded tests (test_chimera_spec.py):
                   C:\\...\\python.exe: No module named pytest

The work was correct. The generator worked. Nothing ran the tests, and the attempt was reverted
for it — with `evidence: "verifier"` in the receipt, naming a verdict that was never reached.

Two independent defects, and either one alone still loses the work:

1. `python -m pytest` with no pytest installed exits **1** — the same code every runner uses for
   "your tests failed" — so `_NO_VERDICT` (5, 127) never saw it and `program_missing` answered
   "the program is there", which is true and beside the point. It is the same class of bug the 127
   handling exists to prevent, arriving through the `-m` door.
2. `SpecTestVerifier.verify` wrapped the runner's result and **dropped `abstained`**. Every other
   exit in that method chooses carefully between passing and abstaining; the one path where a real
   command ran threw the distinction away. Fixing only (1) would have turned a wrong failure into a
   confident wrong pass, which is worse.

And the audience is the point. `gen_tests` exists for the person who is not a Python developer —
the same person the app invites to describe a project instead of writing its YAML — so "pytest is
not installed" is the *expected* state of their machine, not an edge case.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from chimera.core.checklist import Requirement
from chimera.core.spec_test import SpecTestVerifier
from chimera.core.verify import CommandVerifier, module_missing

#: A module name no environment will ever have installed, so the interpreter really does print the
#: message rather than us pretending it did.
ABSENT = "chimera_module_that_does_not_exist"

#: The interpreter, quoted, because every command built from it here goes through a shell.
#: Unquoted, a checkout under a path with a space — this project's own lives under
#: "Desenvolvendo Projetos" — makes cmd.exe read the first word as the program and answer
#: "'C:\\Users\\...\\Desenvolvendo' is not recognized as an internal or external command".
#:
#: The damage is worse than a red test, and worse in the direction that hides itself: that message
#: is what `module_missing` and the exit code turn into an ABSTENTION, so the abstention cases
#: below pass for entirely the wrong reason while their controls — the ones asserting a real
#: failure still fails — are what goes red. Measured 2026-09-09: four failures across this file and
#: `test_the_kernel_holds_the_boundary.py` on such a path, none on a path without the space.
PYTHON = f'"{sys.executable}"'


class _Generator:
    """Stands in for the LLM: returns whatever test source the case needs."""

    def __init__(self, code: str) -> None:
        self.code = code

    def generate(self, task: str, requirements: list[Requirement], code_context: str = "") -> str:
        return self.code


# --------------------------------------------------------------------------- the predicate


def test_the_interpreter_saying_the_module_is_absent_is_recognised() -> None:
    out = f"C:\\Python\\python.exe: No module named {ABSENT}\n"
    assert module_missing(f"python -m {ABSENT} -q file.py", out) is True


def test_a_traceback_from_the_code_under_test_is_not() -> None:
    """The discrimination that keeps a real failure a failure. Python's own message is unquoted;
    an `import pytest` failing inside the tests raises `ModuleNotFoundError: No module named
    'pytest'`, with quotes. Reading the second as "the tool is absent" would abstain on a genuine
    error, and abstention KEEPS the work — the dangerous direction of this bug."""
    quoted = f"ModuleNotFoundError: No module named '{ABSENT}'\n"
    assert module_missing(f"python -m {ABSENT} -q file.py", quoted) is False


def test_a_different_module_does_not_count() -> None:
    out = "python: No module named something_else\n"
    assert module_missing(f"python -m {ABSENT}", out) is False


def test_a_command_without_dash_m_never_matches() -> None:
    out = f"No module named {ABSENT}\n"
    assert module_missing("pytest -q", out) is False
    assert module_missing("", out) is False


def test_a_prefix_of_the_module_name_does_not_count() -> None:
    """`-m pytest` must not be satisfied by `No module named pytest_asyncio`."""
    assert module_missing("python -m pytest", "No module named pytest_asyncio\n") is False


# --------------------------------------------------------------------------- the verifier


def test_a_missing_module_abstains_instead_of_failing(tmp_path: Path) -> None:
    """Run for real, no mocking: the interpreter is asked for a module that does not exist, and its
    actual exit code and actual message are what the verifier sees."""
    result = CommandVerifier(f"{PYTHON} -m {ABSENT}", tmp_path, source="user").verify()
    assert result.abstained is True
    assert result.passed is True  # abstention keeps the work; the other gates decide


def test_a_command_that_really_failed_still_fails(tmp_path: Path) -> None:
    """The control. A verifier that abstained on everything would keep every change ever made."""
    command = f'{PYTHON} -c "import sys; sys.exit(1)"'
    result = CommandVerifier(command, tmp_path, source="user").verify()
    assert result.abstained is False
    assert result.passed is False


def test_a_command_that_passed_still_passes(tmp_path: Path) -> None:
    result = CommandVerifier(f'{PYTHON} -c "pass"', tmp_path, source="user").verify()
    assert result.passed is True
    assert result.abstained is False


# --------------------------------------------------------------------------- the wrapper


def test_the_spec_verifier_carries_the_abstention_out(tmp_path: Path) -> None:
    """The second defect, and the one that would have made fixing the first actively harmful: an
    abstention arrives with `passed=True`, so dropping the flag reports a confident pass and writes
    `evidence="verifier"` about a test that never reached a verdict."""
    verifier = SpecTestVerifier(
        _Generator("def test_ok() -> None:\n    assert True\n"),  # type: ignore[arg-type]
        "build soma.py",
        [Requirement(text="soma(a, b) returns a + b")],
        tmp_path,
        command=f"{PYTHON} -m {ABSENT} " + "{file}",
    )
    result = verifier.verify()
    assert result.abstained is True, "the abstention was swallowed on the way out again"


def test_the_spec_verifier_still_reports_a_real_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control for the wrapper: a generated test that genuinely fails is a true negative the
    gate must heed, and carrying `abstained` must not turn every result into an abstention."""
    # The spec runner executes model-written tests, so it runs where the shell runs: behind the
    # host-exec gate on a machine with no isolated sandbox. This test is about the wrapper, not the
    # gate — say `allow`, as the operator of such a machine would, so the command really runs.
    monkeypatch.setenv("CHIMERA_HOST_EXEC", "allow")
    verifier = SpecTestVerifier(
        _Generator("def test_no() -> None:\n    assert False\n"),  # type: ignore[arg-type]
        "build soma.py",
        [Requirement(text="soma(a, b) returns a + b")],
        tmp_path,
        command=f"{PYTHON} -m pytest -q " + "{file}",
    )
    result = verifier.verify()
    assert result.abstained is False
    assert result.passed is False
