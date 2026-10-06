"""Study 30, S30-23, after review: the canonical pytest and unittest spellings of "skip".

The skip rule matched only the literal prefixes ``@pytest.mark.`` and ``@unittest.``. Probed on
its own entry point, three of the most common ways to turn a Python test off raised
``tests_touched`` and nothing else: ``pytestmark = pytest.mark.skip`` (one line, the whole file),
``@mark.skip`` after ``from pytest import mark``, and ``@skip('x')`` after
``from unittest import skip``. None is a helper the regexes could not know; they are the APIs'
own spellings, and ``tests_removed_or_skipped`` is the flag RESULTS calls eligible for a later
opt-in REVIEW.
"""

from __future__ import annotations

from chimera.governance.verifier_integrity import TESTS_REMOVED_OR_SKIPPED, flag_snapshots

_PATH = "tests/test_x.py"
_BEFORE = (
    "import pytest\n\n\n"
    "def test_ok():\n    assert True\n\n\n"
    "def test_bug():\n    assert 1 + 1 == 3\n"
)


def _skipped(after: str, before: str = _BEFORE) -> list[str]:
    flags = flag_snapshots({_PATH: before}, {_PATH: after}, verify_command="pytest")
    return [f.detail for f in flags if f.kind == TESTS_REMOVED_OR_SKIPPED]


def test_a_module_level_pytestmark_skip_turns_the_whole_file_off() -> None:
    after = _BEFORE.replace("import pytest\n", "import pytest\n\npytestmark = pytest.mark.skip\n")
    (detail,) = _skipped(after)
    assert "whole module" in detail


def test_a_module_level_pytestmark_list_with_an_xfail_is_seen() -> None:
    after = _BEFORE.replace(
        "import pytest\n", "import pytest\n\npytestmark = [pytest.mark.xfail(reason='x')]\n"
    )
    assert _skipped(after)


def test_mark_skip_imported_from_pytest_is_a_skip_on_its_test() -> None:
    before = _BEFORE.replace("import pytest", "from pytest import mark")
    after = before.replace("def test_bug", "@mark.skip\ndef test_bug")
    (detail,) = _skipped(after, before)
    assert "test_bug" in detail


def test_skip_imported_from_unittest_is_a_skip_on_its_test() -> None:
    before = _BEFORE.replace("import pytest", "from unittest import skip, skipIf, expectedFailure")
    for marker in ("@skip('flaky')", "@skipIf(True, 'x')", "@expectedFailure"):
        after = before.replace("def test_bug", f"{marker}\ndef test_bug")
        (detail,) = _skipped(after, before)
        assert "test_bug" in detail, marker


def test_a_skipped_parametrize_case_belongs_to_the_test_below_it() -> None:
    before = (
        "import pytest\n\n\n"
        "def test_ok():\n    assert True\n\n\n"
        "@pytest.mark.parametrize('n', [\n    1,\n    2,\n])\n"
        "def test_bug(n):\n    assert n == 1\n"
    )
    after = before.replace("    2,\n", "    pytest.param(2, marks=pytest.mark.skip),\n")
    (detail,) = _skipped(after, before)
    # Attributed downward, to the test the case belongs to — not to `test_ok` above it.
    assert "test_bug" in detail and "test_ok" not in detail


def test_an_unchanged_pytestmark_is_not_a_new_skip() -> None:
    before = _BEFORE.replace("import pytest\n", "import pytest\n\npytestmark = pytest.mark.skip\n")
    after = before.replace("assert True", "assert 1")
    assert _skipped(after, before) == []
