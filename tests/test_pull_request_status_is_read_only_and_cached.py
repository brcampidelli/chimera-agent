"""PR status is read-only, parsed without turning unknown checks green, and cached briefly."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.core import pull_request_status as status


def test_status_parser_counts_mixed_checks_and_names_failures() -> None:
    parsed = status.parse_status(
        {
            "state": "OPEN",
            "mergeable": "CONFLICTING",
            "statusCheckRollup": [
                {"name": "lint", "conclusion": "SUCCESS", "status": "COMPLETED"},
                {"name": "tests", "conclusion": "FAILURE", "status": "COMPLETED"},
                {"context": "legacy", "state": "ERROR"},
                {"name": "build", "status": "IN_PROGRESS"},
            ],
        }
    )
    assert parsed.as_dict() == {
        "state": "open",
        "mergeable": "conflicting",
        # A legacy status context in ERROR is a failure (GitHub's StatusContext has no other way to
        # say "this broke"); the first draft of this test expected it pending, and the parser was right.
        "passed": 1,
        "failed": 2,
        "pending": 1,
        "failing_names": ["tests", "legacy"],
        "error": "",
    }
    assert status.parse_status({"state": "CLOSED", "statusCheckRollup": []}).state == "closed"
    assert status.parse_status({"state": "MERGED", "statusCheckRollup": []}).state == "merged"
    assert status._check_status({"status": "COMPLETED"}) == "pending"
    assert status._check_status({"state": "SUCCESS"}) == "passed"


def _result(returncode: int, stdout: str = "", stderr: str = "") -> Any:
    return type("Result", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr})()


OPEN = json.dumps({"state": "OPEN", "mergeable": "MERGEABLE", "statusCheckRollup": []})


def test_status_errors_are_clear_for_absent_and_signed_out_gh(tmp_path: Path, monkeypatch: Any) -> None:
    status.clear_status_cache()
    monkeypatch.setattr(status, "gh_command", lambda: None)
    missing = status.pull_request_status(tmp_path, 9)
    assert "not installed" in missing.error

    monkeypatch.setattr(status, "gh_command", lambda: ["gh"])
    monkeypatch.setattr(
        status,
        "run_quiet",
        lambda argv, cwd: _result(4, stderr="To get started with GitHub CLI, please run:  gh auth login"),
    )
    signed_out = status.pull_request_status(tmp_path, 10)
    assert "not authenticated" in signed_out.error

    monkeypatch.setattr(status, "run_quiet", lambda argv, cwd: _result(1, stderr="no pull requests found"))
    other = status.pull_request_status(tmp_path, 11)
    assert other.error == "Could not read pull request from GitHub"


def test_one_gh_call_per_read_and_no_auth_status(tmp_path: Path, monkeypatch: Any) -> None:
    """Auth is read from `pr view`'s own stderr; a separate `gh auth status` doubled every read."""
    calls: list[list[str]] = []

    def run(argv: list[str], cwd: Path) -> Any:
        calls.append(argv)
        return _result(0, OPEN)

    status.clear_status_cache()
    monkeypatch.setattr(status, "run_quiet", run)
    monkeypatch.setattr(status, "gh_command", lambda: ["gh"])
    status.pull_request_status(tmp_path, 12, now=100.0)
    assert len(calls) == 1
    assert calls[0][1:3] == ["pr", "view"]
    assert not any("auth" in call for call in calls)


def test_status_cache_expires_and_calls_gh_with_read_only_argv(tmp_path: Path, monkeypatch: Any) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], cwd: Path) -> Any:
        calls.append(argv)
        return _result(0, OPEN)

    status.clear_status_cache()
    monkeypatch.setattr(status, "run_quiet", run)
    monkeypatch.setattr(status, "gh_command", lambda: ["gh"])
    first = status.pull_request_status(tmp_path, 12, now=100.0)
    cached = status.pull_request_status(tmp_path, 12, now=159.0)
    assert first == cached
    assert len(calls) == 1
    refreshed = status.pull_request_status(tmp_path, 12, now=160.0)
    assert refreshed.state == "open"
    assert len(calls) == 2
    assert all(call[1:3] == ["pr", "view"] for call in calls)
    assert not any(any(action in call for action in ("merge", "comment", "rerun", "edit")) for call in calls)


def test_an_error_is_never_cached(tmp_path: Path, monkeypatch: Any) -> None:
    """A dropped connection must not hold the card on "could not read" after the network is back."""
    answers = [_result(1, stderr="connection reset"), _result(0, OPEN)]
    status.clear_status_cache()
    monkeypatch.setattr(status, "gh_command", lambda: ["gh"])
    monkeypatch.setattr(status, "run_quiet", lambda argv, cwd: answers.pop(0))
    assert status.pull_request_status(tmp_path, 5, now=100.0).error
    assert status.pull_request_status(tmp_path, 5, now=101.0).state == "open"


def test_the_cache_is_bounded(tmp_path: Path, monkeypatch: Any) -> None:
    status.clear_status_cache()
    monkeypatch.setattr(status, "gh_command", lambda: ["gh"])
    monkeypatch.setattr(status, "run_quiet", lambda argv, cwd: _result(0, OPEN))
    for number in range(1, status._CACHE_MAX + 20):
        status.pull_request_status(tmp_path, number, now=100.0)
    assert len(status._CACHE) == status._CACHE_MAX
    assert (str(tmp_path.resolve()), 1) not in status._CACHE  # the oldest went first


def test_a_check_name_cannot_carry_a_terminal_escape() -> None:
    parsed = status.parse_status(
        {"state": "OPEN", "statusCheckRollup": [{"name": "ci\x1b]52;c;evil\x07", "conclusion": "FAILURE"}]}
    )
    assert "\x1b" not in parsed.failing_names[0]
    assert "\\x1b" in parsed.failing_names[0]
