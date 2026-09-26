"""A review lists its findings first, P0 to P3, the more confident first within a priority.

Study 25 §7 S15: the reader of a review triages from the top, so the order is part of the output's
meaning. Everything the review could not settle comes after the findings, never before them.
"""

from __future__ import annotations

from pathlib import Path

from chimera.review import KeepAll, ReviewerChoice, collect, render_text, review
from tests.review_fakes import FakeBackend, finder_json, finding, repo_with_change

REVIEWER = ReviewerChoice(
    "openrouter/z-ai/glm-5.3", "openrouter/deepseek/deepseek-v4-flash", "flag"
)


def _report(tmp_path: Path, findings: list[dict[str, object]]):  # type: ignore[no-untyped-def]
    backend = FakeBackend(finder_json(findings, risks=["callers outside the diff"]))
    return review(collect(repo_with_change(tmp_path)), backend, REVIEWER, KeepAll())


def test_findings_are_ordered_by_priority_then_confidence(tmp_path: Path) -> None:
    report = _report(tmp_path, [
        finding("calc.py", 9, "P3", "small", 0.9),
        finding("calc.py", 11, "P0", "worst", 0.2),
        finding("calc.py", 10, "P2", "edge", 0.5),
        finding("calc.py", 8, "P1", "less sure", 0.4),
        finding("calc.py", 11, "P1", "surer", 0.9),
    ])

    assert [(f.id, f.priority, f.title) for f in report.findings] == [
        ("F1", "P0", "worst"),
        ("F2", "P1", "surer"),
        ("F3", "P1", "less sure"),
        ("F4", "P2", "edge"),
        ("F5", "P3", "small"),
    ]


def test_a_priority_written_as_a_word_is_read_onto_the_scale(tmp_path: Path) -> None:
    report = _report(tmp_path, [
        {**finding("calc.py", 11), "priority": "critical"},
        {**finding("calc.py", 10), "priority": "low"},
    ])

    assert [f.priority for f in report.findings] == ["P0", "P3"]


def test_the_findings_come_before_everything_else_in_the_human_form(tmp_path: Path) -> None:
    report = _report(tmp_path, [
        finding("calc.py", 10, "P2", "edge"),
        finding("calc.py", 11, "P0", "worst"),
    ])

    lines = render_text(report).splitlines()

    first_p0 = next(i for i, line in enumerate(lines) if line.startswith("P0  "))
    first_p2 = next(i for i, line in enumerate(lines) if line.startswith("P2  "))
    risks = lines.index("Residual risks")
    assert lines[0].startswith("Review (experimental)")
    assert 0 < first_p0 < first_p2 < risks
