"""Synthetic sensitivity checks for the lexical verifier-integrity detector."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from chimera.governance.verifier_integrity import (
    ASSERTION_WEAKENED,
    TESTS_REMOVED_OR_SKIPPED,
    flag_patch,
)

CORPUS = Path(__file__).resolve().parents[1] / "bench" / "verifier_integrity" / "weakening_corpus.json"


def _detected_kinds(before: str, after: str, language: str, *, path: str = "tests/test_case.py") -> set[str]:
    import difflib

    patch = "\n".join(
        difflib.unified_diff(
            before.splitlines(), after.splitlines(),
            fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
        )
    ) + "\n"
    inferred_path = path if language == "pytest" else "tests/case.test.ts"
    flags = flag_patch(inferred_path, patch)
    detected = {
        flag.detail for flag in flags if flag.kind == ASSERTION_WEAKENED
    }
    # Skips already have their own receipt kind; do not expect a duplicate weakening record.
    if any(
        flag.kind == TESTS_REMOVED_OR_SKIPPED and "skipped " in flag.detail
        for flag in flags
    ):
        detected.add("new_skip_or_xfail")
    return detected


def test_planted_weakening_corpus_reports_per_type_sensitivity(capsys: pytest.CaptureFixture[str]) -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    counts: Counter[tuple[str, str]] = Counter()
    totals: Counter[tuple[str, str]] = Counter()
    false_positives: Counter[str] = Counter()
    control_totals: Counter[str] = Counter()
    for case in corpus["cases"]:
        source = case.get("source", "synthetic")
        target = case["type"]
        totals[(source, target)] += 1
        detected = _detected_kinds(case["before"], case["after"], case["language"])
        if target == "expected_value_changed":
            counts[(source, target)] += (
                "expected_value_changed" in detected or "expected_value_weakened" in detected
            )
        else:
            counts[(source, target)] += (
                target in detected
                or target == "weak_matcher" and "expected_value_weakened" in detected
            )
    for case in corpus["negative_controls"]:
        source = case.get("source", "synthetic")
        control_totals[source] += 1
        false_positives[source] += bool(
            _detected_kinds(case["before"], case["after"], case["language"])
        )
    lines = ["source | type | detected | planted | sensitivity"]
    for source, kind in sorted(totals):
        count = counts[(source, kind)]
        total = totals[(source, kind)]
        lines.append(f"{source} | {kind} | {count} | {total} | {count / total:.0%}")
    for source in sorted(control_totals):
        fp = false_positives[source]
        total = control_totals[source]
        lines.append(f"{source} | negative controls (FP) | {fp} | {total} | {fp / total:.0%}")
    table = "\n".join(lines)
    print(table)
    assert capsys.readouterr().out == table + "\n"
    assert all(
        counts[("synthetic", kind)] == 10 and totals[("synthetic", kind)] == 10
        for kind in ("new_skip_or_xfail", "weak_matcher", "expected_value_changed", "fewer_assertions")
    )
    assert false_positives["synthetic"] == 0


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ("def test_x():\n    assert a\n    assert b\n", "def test_x():\n    assert a\n", "fewer_assertions"),
        ("def test_x():\n    assert x == 5\n", "def test_x():\n    assert x == 1\n", "expected_value_changed"),
        ("def test_x():\n    assert x == 1\n", "def test_x():\n    assert x\n", "expected_value_weakened"),
        ("def test_x():\n    assert len(x) == 3\n", "def test_x():\n    assert len(x) > 0\n", "expected_value_weakened"),
        ("it('x', () => { expect(x).toEqual(1); });", "it('x', () => { expect(x).toBeTruthy(); });", "weak_matcher"),
        ("it('x', () => { expect(x).toEqual(1); });", "it.skip('x', () => { expect(x).toEqual(1); });", "new_skip_or_xfail"),
        ("def test_x():\n    assert x\n", "def test_x():\n    pass\n", "fewer_assertions"),
        ("def test_x():\n    assert x == 1\n", "def test_x():\n    pass\n", "fewer_assertions"),
        ("class T(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(ok())\n", "class T(unittest.TestCase):\n    def test_x(self):\n        pass\n", "fewer_assertions"),
        ("def test_x():\n    with pytest.raises(ValueError):\n        operation()\n", "def test_x():\n    operation()\n", "fewer_assertions"),
        ("it('x', () => { expect(run()).toThrow(); });", "it('x', () => { run(); });", "fewer_assertions"),
    ],
)
def test_detector_unit_cases(before: str, after: str, expected: str) -> None:
    assert expected in _detected_kinds(before, after, "pytest") | _detected_kinds(before, after, "vitest")
