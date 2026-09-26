"""A review that finds nothing says "no findings" in words, and one that could not finish does not.

An empty list of findings means two opposite things: the reviewer read the change and found
nothing, or the reviewer never produced a readable answer. The second used to be the more common
reason for an empty list in this project's judges (`bench/review_judge` kept ``unparsed`` apart
from ``call_failed`` for this reason), so the report keeps three states apart and the text says
which one it is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.review import KeepAll, ReviewerChoice, collect, render_text, review
from chimera.review.diff import ReviewDiff
from tests.review_fakes import FakeBackend, finder_json, finding, repo_with_change

REVIEWER = ReviewerChoice(
    "openrouter/z-ai/glm-5.3", "openrouter/deepseek/deepseek-v4-flash", "flag"
)


def test_no_findings_is_said_with_the_residual_risks_and_untested_paths(tmp_path: Path) -> None:
    backend = FakeBackend(finder_json([], risks=["mean is not called anywhere shown"],
                                      untested=["mean with one element"]))

    report = review(collect(repo_with_change(tmp_path)), backend, REVIEWER, KeepAll())
    text = render_text(report)

    assert report.status == "no_findings"
    assert "No findings: nothing the reviewer reported survived the checks" in text
    assert "  - mean is not called anywhere shown" in text
    assert "  - mean with one element" in text
    # Named by the pipeline itself, not by the model: the change touches code and no test.
    assert any("no test file changed in this diff" in p for p in report.untested_paths)


def test_an_unreadable_reply_is_an_incomplete_review_not_a_clean_one(tmp_path: Path) -> None:
    backend = FakeBackend("I looked at it and it seems fine overall.")

    report = review(collect(repo_with_change(tmp_path)), backend, REVIEWER, KeepAll())
    text = render_text(report)

    assert report.status == "incomplete"
    assert "No findings" not in text
    assert "Incomplete: part of the change was not reviewed" in text
    assert [n.file for n in report.not_reviewed] == ["calc.py"]
    assert report.usage.failed_calls == 1


def test_a_regex_quoted_in_a_finding_does_not_void_the_review(tmp_path: Path) -> None:
    """`\\Z` is not a JSON escape, and a reviewer quoting a regex writes it anyway. Measured in
    `bench/review_seeded` run 1: one such backslash made a reply with two correct findings unreadable,
    and the whole review came back incomplete."""
    reply = (
        '{"findings": [{"file": "calc.py", "line": 11, "priority": "P1", "title": "divides by n - 1",'
        ' "evidence": "pattern needs \\Z, and \\d+ never matches", "confidence": 0.9}],'
        ' "residual_risks": [], "untested_paths": []}'
    )

    report = review(collect(repo_with_change(tmp_path)), FakeBackend(reply), REVIEWER, KeepAll())

    assert report.status == "findings"
    assert report.findings[0].evidence == "pattern needs \\Z, and \\d+ never matches"


def test_an_escaped_backslash_beside_a_stray_one_is_read_as_written(tmp_path: Path) -> None:
    """A quoted regex holds both kinds: ``\\\\[`` is a valid escape (a backslash, then ``[``) and
    ``\\]`` is not. The repair read backslashes one at a time, so it took the second half of the
    valid pair for a stray one and broke it. Measured in `bench/review_reviewer`'s pilot: a
    mistral-small reply quoting `(?:\\[[^\\]]*\\])?` came back unreadable and the review
    incomplete."""
    reply = (
        '{"findings": [{"file": "calc.py", "line": 11, "priority": "P1", "title": "divides by n - 1",'
        ' "evidence": "(?:\\\\[[^\\\\]]*\\])?", "confidence": 0.9}],'
        ' "residual_risks": [], "untested_paths": []}'
    )

    report = review(collect(repo_with_change(tmp_path)), FakeBackend(reply), REVIEWER, KeepAll())

    assert report.status == "findings"
    assert report.findings[0].evidence == "(?:\\[[^\\]]*\\])?"


def test_a_failed_call_is_an_incomplete_review(tmp_path: Path) -> None:
    class Down(FakeBackend):
        def complete(self, messages: list[Any], **kw: Any) -> Any:
            raise TimeoutError("provider did not answer")

    report = review(collect(repo_with_change(tmp_path)), Down(""), REVIEWER, KeepAll())

    assert report.status == "incomplete"
    assert "TimeoutError" in report.not_reviewed[0].reason


def test_findings_all_dropped_still_read_as_no_findings_and_say_how_many(tmp_path: Path) -> None:
    backend = FakeBackend(finder_json([finding("calc.py", 400), finding("other.py", 1)]))

    report = review(collect(repo_with_change(tmp_path)), backend, REVIEWER, KeepAll())

    assert report.status == "no_findings"
    assert len(report.dropped) == 2
    assert "2 finding(s) dropped by the checks" in render_text(report)


def test_no_change_is_nothing_to_review(tmp_path: Path) -> None:
    backend = FakeBackend(finder_json([]))

    report = review(ReviewDiff([], base="abc", base_label="merge base with main"), backend,
                    REVIEWER, KeepAll())

    assert report.status == "empty"
    assert "nothing to review" in render_text(report)
    assert backend.calls == []
