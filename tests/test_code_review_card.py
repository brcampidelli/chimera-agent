"""The Code screen's ``/code-review`` card: the review it shows is the terminal's review, shaped.

What is pinned: a reply that is not a readable review makes the card ``incomplete`` (never a clean
card with no findings); a finding about a line the diff does not show is dropped, not shown; the
person's limit is counted, not silently cut; the route refuses a bad limit, and it spends nothing on
a folder that has no change or is not a repository.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.providers.gateway import CompletionResult
from chimera.review import KeepAll, review
from chimera.review.code_card import SEVERITY, build_card, parse_limit
from chimera.review.diff import ReviewDiff, parse
from chimera.review.family import choose_reviewer
from chimera.review.report import Finding, NotReviewed, Reviewer, ReviewReport

DIFF = """diff --git a/app/money.py b/app/money.py
--- a/app/money.py
+++ b/app/money.py
@@ -1,3 +1,4 @@
 def total(items):
+    total = 0
     return sum(items)

"""


class _Fake:
    """A backend that answers with one fixed text and counts its calls."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def complete(self, messages: Any, **_: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(content=self.text, model="fake/reviewer")


def _run(text: str) -> ReviewReport:
    reviewer = choose_reviewer("author/model", explicit="other/reviewer")
    diff = ReviewDiff(parse(DIFF), base="abc", target="working tree", base_label="merge base with main")
    return review(diff, _Fake(text), reviewer, KeepAll(), effort="low")


def _report(findings: list[Finding], *, status: str = "findings") -> ReviewReport:
    return ReviewReport(
        status=status,  # type: ignore[arg-type]
        base="abc",
        base_label="merge base with main",
        target="working tree",
        files_changed=1,
        reviewer=Reviewer(
            model="other/reviewer", family="other", author_model="author/model",
            author_family="author", source="flag", same_family=False, verifier="keep-all",
        ),
        findings=findings,
    )


def _finding(i: int, file: str, line: int, priority: str) -> Finding:
    return Finding(
        id=f"F{i}", priority=priority, file=file, line=line, title=f"t{i}",  # type: ignore[arg-type]
    )


# --- the reply, read by the terminal's own reader ------------------------------------------------


def test_a_readable_reply_with_an_anchored_finding_is_shown() -> None:
    text = (
        '{"findings": [{"file": "app/money.py", "line": 2, "priority": "P1", "title": "shadowed",'
        ' "evidence": "total = 0", "consequence": "wrong sum", "confidence": 0.9}]}'
    )
    card = build_card(_run(text), limit=None, patch_of=lambda p: f"PATCH {p}", tainted=False)
    assert card["status"] == "findings"
    assert card["shown"] == 1
    (group,) = card["files"]
    assert group["file"] == "app/money.py"
    assert group["patch"] == "PATCH app/money.py"
    assert group["findings"][0]["severity"] == "high"


def test_a_finding_about_a_line_the_diff_does_not_show_is_dropped_not_shown() -> None:
    text = '{"findings": [{"file": "app/money.py", "line": 400, "priority": "P0", "title": "far"}]}'
    card = build_card(_run(text), limit=None, patch_of=lambda p: "", tainted=False)
    assert card["shown"] == 0
    assert card["dropped"] == 1
    assert card["files"] == []


@pytest.mark.parametrize("text", ["", "I could not review this change.", "[1, 2, 3"])
def test_an_unreadable_reply_is_an_incomplete_review_never_a_clean_one(text: str) -> None:
    card = build_card(_run(text), limit=None, patch_of=lambda p: "", tainted=False)
    assert card["status"] == "incomplete"
    assert card["files"] == []
    assert any("not reviewed" in note for note in card["notes"])


def test_a_clean_reply_says_no_findings_and_reads_no_patch() -> None:
    seen: list[str] = []
    card = build_card(
        _run('{"findings": []}'), limit=None, patch_of=lambda p: seen.append(p) or "", tainted=False
    )
    assert card["status"] == "no_findings"
    assert seen == []


# --- the limit, the grouping and the severity ----------------------------------------------------


def test_the_limit_keeps_the_first_findings_and_counts_the_rest() -> None:
    findings = [_finding(i, "a.py", i, "P2") for i in range(1, 6)]
    card = build_card(_report(findings), limit=2, patch_of=lambda p: "", tainted=False)
    assert card["shown"] == 2
    assert card["omitted"] == 3
    assert [f["id"] for g in card["files"] for f in g["findings"]] == ["F1", "F2"]


def test_findings_are_grouped_by_file_and_patch_is_read_only_for_shown_files() -> None:
    findings = [
        _finding(1, "a.py", 1, "P0"),
        _finding(2, "b.py", 3, "P3"),
        _finding(3, "a.py", 9, "P2"),
    ]
    read: list[str] = []
    card = build_card(
        _report(findings), limit=None, patch_of=lambda p: read.append(p) or "", tainted=False
    )
    assert [g["file"] for g in card["files"]] == ["a.py", "b.py"]
    assert [f["id"] for f in card["files"][0]["findings"]] == ["F1", "F3"]
    assert sorted(read) == ["a.py", "b.py"]


def test_the_priority_reads_as_a_severity_on_the_card() -> None:
    assert SEVERITY == {"P0": "high", "P1": "high", "P2": "medium", "P3": "low"}


def test_a_taint_and_an_unreviewed_file_are_carried_to_the_card() -> None:
    report = _report([], status="incomplete")
    report.not_reviewed = [NotReviewed(file="x.py", reason="the reviewer's reply could not be used")]
    card = build_card(report, limit=None, patch_of=lambda p: "", tainted=True)
    assert card["tainted"] is True
    assert card["status"] == "incomplete"
    assert "not reviewed: x.py" in card["notes"][0]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 10), ("", 10), ("all", None), ("ALL", None), ("3", 3), (7, 7), ("200", 200)],
)
def test_parse_limit_reads_a_count_or_all(value: Any, expected: int | None) -> None:
    assert parse_limit(value) == expected


@pytest.mark.parametrize("value", ["0", "201", "-1", "abc", "3.5"])
def test_parse_limit_refuses_anything_else_rather_than_guessing(value: str) -> None:
    with pytest.raises(ValueError):
        parse_limit(value)


# --- the route -----------------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo, check=True, capture_output=True,
    )


def _repo_with_change(root: Path) -> Path:
    repo = root / "ws"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "money.py").write_text("def total(items):\n    return sum(items)\n", encoding="utf-8")
    _git(repo, "add", "money.py")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "money.py").write_text(
        "def total(items):\n    total = 0\n    return sum(items)\n", encoding="utf-8"
    )
    return repo


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: str, ws: Path) -> tuple[TestClient, _Fake]:
    import chimera.api.commands_api as commands_api
    from chimera.api import build_api_app
    from chimera.interface import ChatSession

    fake = _Fake(reply)
    monkeypatch.setattr(Settings, "can_answer", lambda self: True)
    monkeypatch.setattr(commands_api, "LLMGateway", lambda *_a, **_k: fake)
    monkeypatch.setattr(
        commands_api, "reviewer_for",
        lambda author, explicit="": choose_reviewer(author, explicit="other/reviewer"),
    )
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))

    class _Idle:
        def run(self, task: str, **_: Any) -> Any:  # pragma: no cover - the route never runs a turn
            raise AssertionError("/code-review must not start a turn")

    app = build_api_app(lambda: ChatSession(_Idle()), workspace=ws, settings=settings)  # type: ignore[arg-type]
    return TestClient(app), fake


def test_the_route_reviews_the_working_tree_and_returns_the_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_change(tmp_path)
    reply = (
        '{"findings": [{"file": "money.py", "line": 2, "priority": "P2", "title": "shadowed",'
        ' "evidence": "total = 0", "consequence": "wrong", "confidence": 0.9}]}'
    )
    client, fake = _client(tmp_path, monkeypatch, reply, repo)
    response = client.post("/api/code/review", json={"workspace": str(repo), "max_findings": "all"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "findings"
    assert body["files"][0]["file"] == "money.py"
    assert "+    total = 0" in body["files"][0]["patch"]
    assert body["files"][0]["findings"][0]["severity"] == "medium"
    assert fake.calls >= 1


def test_the_route_refuses_a_limit_it_cannot_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_change(tmp_path)
    client, fake = _client(tmp_path, monkeypatch, '{"findings": []}', repo)
    response = client.post("/api/code/review", json={"workspace": str(repo), "max_findings": "0"})
    assert response.status_code == 400
    assert fake.calls == 0


def test_a_repository_with_no_change_spends_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_change(tmp_path)
    _git(repo, "checkout", "-q", "--", "money.py")
    client, fake = _client(tmp_path, monkeypatch, '{"findings": []}', repo)
    body = client.post("/api/code/review", json={"workspace": str(repo)}).json()
    assert body["status"] == "empty"
    assert fake.calls == 0


def test_a_folder_that_is_not_a_repository_says_so_and_spends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    client, fake = _client(tmp_path, monkeypatch, '{"findings": []}', plain)
    body = client.post("/api/code/review", json={"workspace": str(plain)}).json()
    assert body["status"] == "no_diff"
    assert fake.calls == 0
