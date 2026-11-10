"""A conversation leaves the list when its pull request is merged or closed (``CHIMERA_ARCHIVE_ON_PR_MERGE``).

ON by default. The sweep (`chimera/core/pr_auto_archive.py`) reads the latest PR of each conversation
whose PR is not yet settled, and archives the ones it SEES change from ``open`` to ``merged`` or
``closed``. The write is made under the marks lock after revalidating the PR (number and URL) and the
conversation's hold. Archiving touches nothing: transcript, folder, worktree and branch stay as they were.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.config import Settings
from chimera.core import pr_auto_archive
from chimera.core.code_session import CodeSessionStore
from chimera.core.code_session_marks import ARCHIVE_REASONS, CodeSessionMarks
from chimera.core.pr_auto_archive import (
    PrReading,
    SweepBlocked,
    Sweeper,
    pull_request_archive_reason,
    sweep,
)
from tests.conversation_fakes import TIMEOUT, Agent, App

PR_URL = "https://github.com/acme/app/pull/7"


@pytest.fixture
def agent() -> Any:
    Agent.hold.clear()
    Agent.held.clear()
    yield Agent
    Agent.hold.set()


# ----------------------------------------------------------------------------- the switch


def test_the_switch_is_on_by_default_and_reads_its_value() -> None:
    """ON by default (the owner's decision): archiving only moves a conversation out of the list."""
    assert Settings().archive_on_pr_merge is True
    assert Settings(CHIMERA_ARCHIVE_ON_PR_MERGE="true").archive_on_pr_merge is True
    assert Settings(CHIMERA_ARCHIVE_ON_PR_MERGE="false").archive_on_pr_merge is False


# ----------------------------------------------------------------------------- the rule and the record


@pytest.mark.parametrize(
    ("pr_state", "state", "reason"),
    [
        ("open", "merged", "pr_merged"),
        ("open", "closed", "pr_closed"),
        ("open", "open", ""),
        ("", "merged", ""),
        ("", "closed", ""),
        ("merged", "merged", ""),
        ("closed", "closed", ""),
    ],
)
def test_only_a_change_from_open_to_settled_archives(pr_state: str, state: str, reason: str) -> None:
    assert pull_request_archive_reason(pr_state=pr_state, state=state) == reason


def test_the_archive_reasons_are_the_ones_the_history_can_show() -> None:
    assert {"owner", "inactive", "pr_merged", "pr_closed", "worktree_pruned"} == set(ARCHIVE_REASONS)


def test_a_recorded_pull_request_is_open_and_recording_it_again_keeps_the_read_state(tmp_path: Path) -> None:
    marks = CodeSessionMarks(tmp_path / "m.json")
    marks.pull_request_opened("s1", number=7, url=PR_URL)
    assert marks.get("s1").pr_state == "open"
    marks.set_pr_state("s1", "merged")
    marks.pull_request_opened("s1", number=7, url=PR_URL)
    assert marks.get("s1").pr_state == "merged"


def test_an_unknown_reason_or_state_on_the_file_is_read_as_none(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(
        '{"sessions": {"s1": {"archived_at": 1.0, "archive_reason": "vanished", "pr_state": "weird"}}}',
        encoding="utf-8",
    )
    mark = CodeSessionMarks(path).get("s1")
    assert mark.archive_reason == "" and mark.pr_state == ""


def test_archiving_records_why_and_the_state_it_acted_on(tmp_path: Path) -> None:
    marks = CodeSessionMarks(tmp_path / "m.json")
    marks.pull_request_opened("s1", number=7, url=PR_URL)
    mark = marks.archive("s1", at=5.0, reason="pr_merged", pr_state="merged")
    assert (mark.archived_at, mark.archive_reason, mark.pr_state) == (5.0, "pr_merged", "merged")
    with pytest.raises(ValueError):
        marks.archive("s1", at=6.0, reason="because")


# ----------------------------------------------------------------------------- the write, under the lock


def _settle(marks: CodeSessionMarks, **overrides: Any) -> str:
    args: dict[str, Any] = {
        "number": 7, "url": PR_URL, "state": "merged", "reason": "pr_merged",
        "at": 9.0, "free": lambda sid: True,
    }
    args.update(overrides)
    return marks.settle_pull_request("s1", **args)


def test_settling_revalidates_that_the_pr_is_still_the_latest(tmp_path: Path) -> None:
    marks = CodeSessionMarks(tmp_path / "m.json")
    marks.pull_request_opened("s1", number=7, url=PR_URL)
    marks.pull_request_opened("s1", number=8, url="https://github.com/acme/app/pull/8")

    assert _settle(marks) == "moved", "PR 7 is no longer the latest: nothing may be written"
    assert _settle(marks, number=8, url="https://github.com/acme/app/pull/8") == "archived"


def test_settling_refuses_a_url_that_is_not_the_recorded_one(tmp_path: Path) -> None:
    marks = CodeSessionMarks(tmp_path / "m.json")
    marks.pull_request_opened("s1", number=7, url=PR_URL)
    assert _settle(marks, url="https://github.com/other/repo/pull/7") == "moved"
    assert marks.get("s1").archived_at is None


def test_settling_asks_the_hold_at_the_write_and_writes_nothing_while_held(tmp_path: Path) -> None:
    marks = CodeSessionMarks(tmp_path / "m.json")
    marks.pull_request_opened("s1", number=7, url=PR_URL)
    asked: list[str] = []

    def held_now(sid: str) -> bool:
        asked.append(sid)
        return True

    assert _settle(marks, free=lambda sid: not held_now(sid)) == "held"
    assert asked == ["s1"]
    assert marks.get("s1").archived_at is None and marks.get("s1").pr_state == "open"


# ----------------------------------------------------------------------------- the sweep


class Pr:
    """A stand-in for ``gh pr view``: one state and URL per conversation, and a record of every read."""

    def __init__(self, state: str = "open", url: str = PR_URL) -> None:
        self.state = state
        self.url = url
        self.reads: list[str] = []

    def __call__(self, folder: Path, number: int) -> PrReading:
        self.reads.append(folder.name)
        return PrReading(state=self.state, url=self.url)


def _marks_with_pr(tmp_path: Path, *sids: str) -> CodeSessionMarks:
    marks = CodeSessionMarks(tmp_path / "m.json")
    for sid in sids:
        marks.pull_request_opened(sid, number=7, url=PR_URL)
    return marks


def _run(
    marks: CodeSessionMarks, pr: Pr, *, held: frozenset[str] = frozenset(), now: float = 100.0
) -> list[str]:
    return sweep(
        marks,
        read=pr,
        folder_of=lambda sid: Path(sid),
        held=lambda sid: sid in held,
        now=now,
    )


def test_the_sweep_archives_a_conversation_whose_pr_it_saw_merge(tmp_path: Path) -> None:
    marks = _marks_with_pr(tmp_path, "s1")
    pr = Pr("open")
    assert _run(marks, pr) == []
    assert marks.get("s1").pr_state == "open"

    pr.state = "merged"
    assert _run(marks, pr) == ["s1"]
    mark = marks.get("s1")
    assert mark.archived_at == 100.0
    assert (mark.archive_reason, mark.pr_state) == ("pr_merged", "merged")


def test_a_pr_closed_without_merging_archives_for_that_reason(tmp_path: Path) -> None:
    marks = _marks_with_pr(tmp_path, "s1")
    pr = Pr("open")
    _run(marks, pr)
    pr.state = "closed"
    assert _run(marks, pr) == ["s1"]
    assert marks.get("s1").archive_reason == "pr_closed"


def test_a_read_whose_url_is_not_the_recorded_one_archives_nothing(tmp_path: Path) -> None:
    marks = _marks_with_pr(tmp_path, "s1")
    _run(marks, Pr("open"))
    assert _run(marks, Pr("merged", url="https://github.com/other/x/pull/7")) == []
    assert marks.get("s1").archived_at is None


def test_a_pr_already_merged_when_first_read_is_recorded_not_archived(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(
        '{"sessions": {"old": {"pull_requests": [{"number": 7, "url": "' + PR_URL + '"}]}}}',
        encoding="utf-8",
    )
    marks = CodeSessionMarks(path)
    assert _run(marks, Pr("merged")) == []
    assert marks.get("old").archived_at is None
    assert marks.get("old").pr_state == "merged"


def test_a_held_conversation_is_archived_by_a_later_pass_once_free(tmp_path: Path) -> None:
    marks = _marks_with_pr(tmp_path, "s1")
    pr = Pr("open")
    _run(marks, pr)
    pr.state = "merged"

    assert _run(marks, pr, held=frozenset({"s1"})) == []
    assert marks.get("s1").archived_at is None
    assert marks.get("s1").pr_state == "open", "a held change must not be recorded as seen"

    assert _run(marks, pr, now=200.0) == ["s1"]
    assert marks.get("s1").archived_at == 200.0


@pytest.mark.parametrize("reason_held", ["running", "waiting", "background", "shared"])
def test_each_hold_keeps_the_pass_off_the_conversation(tmp_path: Path, reason_held: str) -> None:
    from chimera.api.conversation_state import archive_refusal

    facts = {"running": False, "waiting": False, "background": False, "shared": False}
    facts[reason_held] = True
    assert archive_refusal(**facts)
    marks = _marks_with_pr(tmp_path, "s1")
    pr = Pr("open")
    _run(marks, pr)
    pr.state = "merged"
    assert _run(marks, pr, held=frozenset({"s1"})) == []
    assert marks.get("s1").archived_at is None


def test_a_brought_back_conversation_is_not_archived_again_for_the_same_pr(tmp_path: Path) -> None:
    marks = _marks_with_pr(tmp_path, "s1")
    pr = Pr("open")
    _run(marks, pr)
    pr.state = "merged"
    _run(marks, pr)
    marks.unarchive("s1", at=300.0)

    assert _run(marks, pr, now=400.0) == []
    assert marks.get("s1").archived_at is None


def test_a_conversation_archived_by_the_merge_cleanup_is_not_archived_again_after_unarchive(
    tmp_path: Path,
) -> None:
    """The cleanup archives with the PR's merged state (`code_api`), so a sweep that reads merged again
    has nothing to change from."""
    marks = _marks_with_pr(tmp_path, "s1")
    marks.archive("s1", at=50.0, reason="pr_merged", pr_state="merged")
    marks.unarchive("s1", at=60.0)
    pr = Pr("merged")
    assert _run(marks, pr, now=70.0) == []
    assert pr.reads == [], "a settled and recorded PR is not even read"
    assert marks.get("s1").archived_at is None


def test_an_unanswered_read_stops_the_pass_at_once(tmp_path: Path) -> None:
    marks = _marks_with_pr(tmp_path, "s1", "s2", "s3")

    def unanswered(folder: Path, number: int) -> PrReading:
        return PrReading(failed=True)

    with pytest.raises(SweepBlocked):
        sweep(marks, read=unanswered, folder_of=lambda sid: Path(sid), held=lambda sid: False, now=1.0)


def test_thirty_settled_conversations_do_not_hide_an_open_one_from_the_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pr_auto_archive, "MAX_PER_SWEEP", 25)
    marks = CodeSessionMarks(tmp_path / "m.json")
    for i in range(30):
        marks.pull_request_opened(f"legacy{i}", number=7, url=PR_URL)
        marks.set_pr_state(f"legacy{i}", "merged")
    marks.pull_request_opened("live", number=7, url=PR_URL)
    pr = Pr("open")

    assert _run(marks, pr) == []
    assert pr.reads == ["live"], "the open conversation must be read though 30 settled ones came first"


def test_the_bound_still_caps_how_many_open_prs_one_pass_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pr_auto_archive, "MAX_PER_SWEEP", 2)
    marks = _marks_with_pr(tmp_path, *[f"s{i}" for i in range(5)])
    pr = Pr("open")
    _run(marks, pr)
    assert len(pr.reads) == 2


# ----------------------------------------------------------------------------- the background sweeper


def test_the_sweeper_does_nothing_while_the_switch_is_off() -> None:
    clock = [1000.0]
    started: list[int] = []
    sweeper = Sweeper(interval=60.0, clock=lambda: clock[0])
    assert sweeper.kick(lambda: started.append(1), enabled=False) is False
    assert started == []
    # The clock did not move, so turning it on later sweeps at once.
    assert sweeper.kick(lambda: started.append(1), enabled=True) is True


def test_the_sweeper_is_throttled_and_never_runs_two_passes_at_once() -> None:
    clock = [1000.0]
    gate = threading.Event()
    runs: list[int] = []

    def slow() -> None:
        runs.append(1)
        gate.wait(TIMEOUT)

    sweeper = Sweeper(interval=60.0, clock=lambda: clock[0])
    assert sweeper.kick(slow, enabled=True) is True
    clock[0] = 1001.0
    assert sweeper.kick(slow, enabled=True) is False, "a pass is still running"
    gate.set()
    _wait_until(lambda: not sweeper.running)
    clock[0] = 1030.0
    assert sweeper.kick(slow, enabled=True) is False, "inside the interval"
    clock[0] = 1061.0
    assert sweeper.kick(slow, enabled=True) is True, "after it"
    _wait_until(lambda: not sweeper.running)


def test_an_unanswered_pass_backs_the_sweeper_off_for_the_backoff() -> None:
    clock = [1000.0]

    def blocked() -> None:
        raise SweepBlocked("gh could not answer")

    sweeper = Sweeper(interval=60.0, backoff=600.0, clock=lambda: clock[0])
    assert sweeper.kick(blocked, enabled=True) is True
    _wait_until(lambda: not sweeper.running)
    clock[0] = 1100.0
    assert sweeper.kick(lambda: None, enabled=True) is False, "inside the backoff"
    clock[0] = 1700.0
    assert sweeper.kick(lambda: None, enabled=True) is True, "after the backoff"
    _wait_until(lambda: not sweeper.running)


def test_a_thread_that_cannot_start_leaves_the_sweeper_free(monkeypatch: pytest.MonkeyPatch) -> None:
    class Refusing:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(pr_auto_archive.threading, "Thread", Refusing)
    sweeper = Sweeper(interval=60.0, clock=lambda: 1000.0)
    assert sweeper.kick(lambda: None, enabled=True) is False
    assert sweeper.running is False


def _wait_until(condition: Any) -> None:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline and not condition():
        time.sleep(0.01)
    assert condition()


# ----------------------------------------------------------------------------- through the list


def _record_pr(app: App, sid: str) -> None:
    store = CodeSessionStore(app.home / "code_sessions")
    assert store.record_pull_request(sid, number=7, url=PR_URL)


def _status(state: str, url: str = PR_URL, error: str = "") -> Any:
    return SimpleNamespace(state=state, url=url, error=error)


def _wait_archived(app: App, sid: str) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        row = app.rows(archived=1).get(sid)
        if row is not None:
            return row
        time.sleep(0.02)
    raise AssertionError("the sweep never archived the conversation")


def test_by_default_a_merged_pr_archives_its_conversation_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    """No env at all: the default is ON, so a merged PR archives its conversation."""
    app = App(tmp_path, monkeypatch, agent)
    sid = app.turn("explain it")
    _record_pr(app, sid)
    monkeypatch.setattr(
        "chimera.core.pull_request_status.pull_request_status",
        lambda folder, number, **_: _status("merged"),
    )

    assert sid in app.rows()
    row = _wait_archived(app, sid)
    assert row["archive_reason"] == "pr_merged"
    assert row["archived_at"] is not None


def test_with_the_switch_off_no_pull_request_is_read_and_nothing_archives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    """Turning the switch off does nothing: no GitHub read, no archive, the list unchanged."""
    app = App(tmp_path, monkeypatch, agent, CHIMERA_ARCHIVE_ON_PR_MERGE="false")
    sid = app.turn("explain it")
    _record_pr(app, sid)
    reads: list[int] = []

    def status(folder: Path, number: int, **_: Any) -> Any:
        reads.append(number)
        return _status("merged")

    monkeypatch.setattr("chimera.core.pull_request_status.pull_request_status", status)
    for _ in range(3):
        assert sid in app.rows()
    time.sleep(0.1)
    assert reads == []
    assert app.rows(archived=1) == {}


def test_the_merge_cleanup_then_unarchive_is_not_archived_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    """Through the real cleanup route: the PR is merged, the worktree removed, the conversation archived
    with its merged state. Bringing it back and looking again must not archive it a second time."""
    app = App(tmp_path, monkeypatch, agent)
    sid = app.turn("explain it")
    _record_pr(app, sid)
    CodeSessionStore(app.home / "code_sessions").set_pull_request_options(
        sid, {"cleanup_on_merge": True}
    )
    reads: list[str] = []

    def status(folder: Path, number: int, **_: Any) -> Any:
        reads.append("read")
        return _status("merged")

    from chimera.core import pull_request_cleanup

    monkeypatch.setattr("chimera.core.pull_request_status.pull_request_status", status)
    monkeypatch.setattr(
        pull_request_cleanup,
        "remove_merged_worktree",
        lambda folder: SimpleNamespace(removed=True, kept=""),
    )

    cleaned = app.client.post(f"/api/code/sessions/{sid}/pull-request/cleanup")
    assert cleaned.status_code == 200 and cleaned.json()["archived"] is True
    assert app.client.post(f"/api/code/sessions/{sid}/unarchive").status_code == 200

    reads.clear()
    assert sid in app.rows()
    time.sleep(0.2)
    assert reads == [], "a settled and recorded PR is not read again"
    assert sid in app.rows() and sid not in app.rows(archived=1)


# ----------------------------------------------------------------------------- per-PR errors and blocking errors


def test_an_error_about_one_pr_waits_only_that_conversation(tmp_path: Path) -> None:
    """A deleted PR or a folder that is gone is one conversation's error: the next one still archives."""
    marks = _marks_with_pr(tmp_path, "broken", "fine")
    _run(marks, Pr("open"))

    def read(folder: Path, number: int) -> PrReading:
        if folder.name == "broken":
            return PrReading()  # not blocking: an error about this PR, state unknown
        return PrReading(state="merged", url=PR_URL)

    archived = sweep(marks, read=read, folder_of=lambda sid: Path(sid), held=lambda sid: False, now=5.0)
    assert archived == ["fine"]
    assert marks.get("broken").archived_at is None


def test_a_blocking_error_stops_the_whole_pass(tmp_path: Path) -> None:
    marks = _marks_with_pr(tmp_path, "a", "b")
    seen: list[str] = []

    def read(folder: Path, number: int) -> PrReading:
        seen.append(folder.name)
        return PrReading(failed=True)

    with pytest.raises(SweepBlocked):
        sweep(marks, read=read, folder_of=lambda sid: Path(sid), held=lambda sid: False, now=5.0)
    assert seen == ["a"]


@pytest.mark.parametrize(
    ("stderr", "blocking"),
    [
        ("Could not resolve to a PullRequest with the number of 7.", False),
        ("Could not read pull request from GitHub", False),
        ("dial tcp: lookup api.github.com: no such host", True),
        ("Get https://api.github.com/graphql: i/o timeout", True),
        ("To get started with GitHub CLI, please run:  gh auth login", True),
    ],
)
def test_gh_errors_are_blocking_only_when_github_cannot_be_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stderr: str, blocking: bool
) -> None:
    from chimera.core import pull_request_status as prs

    class Done:
        returncode = 1
        stdout = ""

        def __init__(self, text: str) -> None:
            self.stderr = text

    monkeypatch.setattr(prs, "run_quiet", lambda command, cwd: Done(stderr))
    prs.clear_status_cache()
    status = prs.pull_request_status(tmp_path, 7, gh=["gh"])
    assert status.error and status.blocking is blocking


# ----------------------------------------------------------------------------- the URL


def test_a_record_with_no_url_gets_its_url_on_the_first_read_and_is_archived_only_later(tmp_path: Path) -> None:
    import json

    path = tmp_path / "m.json"
    path.write_text(
        json.dumps({"sessions": {"old": {"pr_state": "open", "pull_requests": [
            {"number": 7, "url": "", "fix_on_ci_failure": False, "cleanup_on_merge": False}
        ]}}}),
        encoding="utf-8",
    )
    marks = CodeSessionMarks(path)
    assert _run(marks, Pr("merged")) == [], "the first read writes the URL and archives nothing"
    assert marks.get("old").archived_at is None
    assert marks.get("old").pull_request is not None and marks.get("old").pull_request["url"] == PR_URL

    assert _run(marks, Pr("merged"), now=200.0) == ["old"], "the next pass compares the URL and archives"


# ----------------------------------------------------------------------------- reopening and starvation


def test_a_reopened_pr_is_archived_again_on_a_later_merge(tmp_path: Path) -> None:
    """A conversation first met closed (recorded, not archived) reopens, then merges: it archives then."""
    import json

    path = tmp_path / "m.json"
    path.write_text(
        json.dumps({"sessions": {"s1": {"pull_requests": [
            {"number": 7, "url": PR_URL, "fix_on_ci_failure": False, "cleanup_on_merge": False}
        ]}}}),
        encoding="utf-8",
    )
    marks = CodeSessionMarks(path)
    pr = Pr("closed")
    assert _run(marks, pr) == []
    assert marks.get("s1").pr_state == "closed"

    pr.state = "open"
    assert _run(marks, pr) == []
    assert marks.get("s1").pr_state == "open", "a reopened PR is recorded open"

    pr.state = "merged"
    assert _run(marks, pr, now=300.0) == ["s1"]


def test_closed_conversations_never_starve_an_open_one_of_the_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pr_auto_archive, "MAX_PER_SWEEP", 25)
    marks = CodeSessionMarks(tmp_path / "m.json")
    for i in range(30):
        marks.pull_request_opened(f"closed{i}", number=7, url=PR_URL)
        marks.set_pr_state(f"closed{i}", "closed")
    marks.pull_request_opened("live", number=7, url=PR_URL)
    pr = Pr("open")
    _run(marks, pr)
    assert pr.reads[0] == "live", "unsettled conversations are read before closed ones"
