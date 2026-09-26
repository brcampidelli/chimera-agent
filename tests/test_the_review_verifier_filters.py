"""The second stage filters, one finding per call, and every drop is kept with its reason.

The verifier is `bench/review_judge`'s cautious stance: drop only when the diff does not show the
code, or contradicts the claim. Two properties follow and both are tested: what it drops is gone
from the findings but present in ``dropped`` with the reason, and anything short of a readable
"drop" keeps the finding, because an unread verdict is not a judgement.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.review import CautiousVerifier, KeepAll, ReviewerChoice, collect, review
from chimera.review.verifier import read_verdict
from tests.review_fakes import FakeBackend, finder_json, finding, repo_with_change

REVIEWER = ReviewerChoice(
    "openrouter/z-ai/glm-5.3", "openrouter/deepseek/deepseek-v4-flash", "flag"
)


def _by_title(user: str) -> str:
    if "false alarm" in user:
        return '{"reason": "line 11 already guards it", "verdict": "drop"}'
    if "mumble" in user:
        return "hard to say"
    return '{"reason": "the diff shows it", "verdict": "keep"}'


def _review(  # type: ignore[no-untyped-def]
    tmp_path: Path, findings: list[dict[str, Any]], verdict: Any = _by_title
):
    backend = FakeBackend(finder_json(findings), verdict)
    report = review(collect(repo_with_change(tmp_path)), backend, REVIEWER,
                    CautiousVerifier(backend, REVIEWER.model))
    return report, backend


def test_a_dropped_finding_leaves_the_findings_and_keeps_its_reason(tmp_path: Path) -> None:
    report, _ = _review(tmp_path, [finding("calc.py", 11, title="real bug"),
                                   finding("calc.py", 9, title="false alarm")])

    assert [f.title for f in report.findings] == ["real bug"]
    [dropped] = report.dropped
    assert dropped.title == "false alarm"
    assert dropped.verdict is not None
    assert (dropped.verdict.stage, dropped.verdict.state) == ("verifier", "dropped")
    assert dropped.verdict.reason == "line 11 already guards it"


def test_one_finding_per_verifier_call(tmp_path: Path) -> None:
    _, backend = _review(tmp_path, [finding("calc.py", 11, title="a"),
                                    finding("calc.py", 10, title="b")])

    checks = [user for role, user, _ in backend.calls if role == "verifier"]
    assert len(checks) == 2
    assert all(user.count("Finding (") == 1 for user in checks)
    assert all("<<external-data" in user for user in checks)


def test_an_unreadable_verdict_keeps_the_finding(tmp_path: Path) -> None:
    report, _ = _review(tmp_path, [finding("calc.py", 11, title="mumble")])

    [kept] = report.findings
    assert kept.verdict is not None and kept.verdict.state == "unverified"


def test_a_verifier_that_cannot_run_does_not_veto(tmp_path: Path) -> None:
    def down(_user: str) -> str:
        raise ConnectionError("gone")

    report, _ = _review(tmp_path, [finding("calc.py", 11)], down)

    assert len(report.findings) == 1
    assert report.findings[0].verdict is not None
    assert report.findings[0].verdict.label == "call failed"


def test_a_finding_outside_the_diff_is_dropped_before_any_model_sees_it(tmp_path: Path) -> None:
    report, backend = _review(tmp_path, [finding("calc.py", 400), finding("elsewhere.py", 3)])

    assert report.findings == []
    assert {d.verdict.stage for d in report.dropped if d.verdict} == {"anchor"}
    assert [role for role, _, _ in backend.calls] == ["finder"]


def test_no_verify_shows_every_located_finding(tmp_path: Path) -> None:
    backend = FakeBackend(finder_json([finding("calc.py", 11), finding("calc.py", 9)]))

    report = review(collect(repo_with_change(tmp_path)), backend, REVIEWER, KeepAll())

    assert len(report.findings) == 2
    assert [role for role, _, _ in backend.calls] == ["finder"]


def test_the_verdict_reader_takes_keep_or_drop_and_nothing_else() -> None:
    assert read_verdict('{"reason": "r", "verdict": "drop"}').state == "dropped"
    assert read_verdict('{"reason": "r", "verdict": "keep"}').state == "kept"
    assert read_verdict("drop").state == "dropped"
    assert read_verdict("keep or drop, unsure").state == "unverified"
    assert read_verdict("").state == "unverified"
