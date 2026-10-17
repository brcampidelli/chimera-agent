"""One record holds every pull request of a conversation, whoever opened it.

Two PRs of the same round wrote two records. C1 (#932) put the PR the owner opened from the Git
panel into the conversation's transcript (`pull_request`), where the status card read it; C3 (#940)
put the PRs the agent's `open_pull_request` opened into the marks file beside the transcripts, where
the sidebar's count and the list's ``review`` state came from. So a PR opened from the panel never
counted in the sidebar or moved the state, and one the agent opened never reached the status card.

Both now write the marks file (`CodeSessionStore.record_pull_request`), and the card, the count and
the state all read it. The marks file because the list already reads it once per call: the
transcripts are read by the list only when their mtime changes (#923), and that has to stay true.

Held here:

* a PR opened from the panel counts in the sidebar and makes the conversation ``review``;
* a PR the agent opened shows on the status card, and its options can be switched there;
* the card shows the latest PR from either path, and the sidebar counts them all;
* a conversation written by C1 (record only in the transcript) and one written by C3 (record only in
  the marks, without options) both read right, and the first write moves the old record across;
* listing still opens no conversation file that did not change, legacy record or not.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from chimera.core.agent import AgentResult
from chimera.core.code_session import CodeSession, CodeSessionStore
from tests.conversation_fakes import Agent, App

HEAD = "a" * 40
OLD = "abc123"
OFF = {"fix_on_ci_failure": False, "cleanup_on_merge": False}


def _pr(number: int, **options: bool) -> dict[str, Any]:
    return {"number": number, "url": f"https://github.com/o/r/pull/{number}", **OFF, **options}


@pytest.fixture
def agent() -> Any:
    Agent.hold.clear()
    Agent.held.clear()
    yield Agent
    Agent.hold.set()


def _panel_opens(app: App, monkeypatch: pytest.MonkeyPatch, session_id: str, number: int) -> None:
    """The owner presses "open" on the Git panel of this conversation's folder."""
    import chimera.core.pull_request as core

    url = f"https://github.com/o/r/pull/{number}"
    monkeypatch.setattr(
        core, "open_pull_request", lambda *_a, **_k: {"ok": True, "url": url, "output": "", "error": ""}
    )
    answer = app.client.post(
        "/api/git/pull-request",
        json={"workspace": str(app.folder), "title": "t", "head": HEAD, "session_id": session_id},
    )
    assert answer.status_code == 200 and answer.json()["ok"] is True, answer.text


def _opener(agent: type, monkeypatch: pytest.MonkeyPatch, *urls: str) -> type:
    """An agent whose turn on "open the pr" reports each URL as its `open_pull_request` would."""
    from chimera.tools.pull_request import OpenPullRequestTool

    built: list[OpenPullRequestTool] = []
    original = OpenPullRequestTool.__init__

    def capture(self: OpenPullRequestTool, *a: Any, **kw: Any) -> None:
        original(self, *a, **kw)
        built.append(self)

    monkeypatch.setattr(OpenPullRequestTool, "__init__", capture)

    class Opener(agent):  # type: ignore[valid-type,misc]
        def run(self, task: str, **kw: Any) -> AgentResult:
            if "open the pr" in task:
                assert built and built[-1].on_opened is not None, "the turn's tool records nothing"
                for url in urls:
                    built[-1].on_opened(url)
            result: AgentResult = super().run(task, **kw)
            return result

    return Opener


def _card(app: App, session_id: str) -> dict[str, Any]:
    answer = app.client.get(f"/api/code/sessions/{session_id}/pull-request")
    assert answer.status_code == 200, answer.text
    return dict(answer.json())


# ---- the two paths, one record -------------------------------------------------------------------


def test_a_pr_opened_from_the_panel_counts_in_the_sidebar_and_makes_the_state_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent)
    sid = app.turn("explain it")
    assert app.state(sid) == "idle"

    _panel_opens(app, monkeypatch, sid, 42)

    row = app.rows()[sid]
    assert row["pull_request_count"] == 1
    assert row["pull_request"] == {"number": 42, "url": "https://github.com/o/r/pull/42"}
    assert row["state"] == "review"


def test_a_pr_the_agent_opened_shows_on_the_status_card_with_its_options_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    opener = _opener(agent, monkeypatch, "https://github.com/o/r/pull/43")
    app = App(tmp_path, monkeypatch, opener, CHIMERA_PULL_REQUESTS="true")
    sid = app.turn("open the pr")

    assert _card(app, sid) == _pr(43)
    switched = app.client.patch(
        f"/api/code/sessions/{sid}/pull-request", json={"cleanup_on_merge": True}
    )
    assert switched.status_code == 200 and switched.json() == _pr(43, cleanup_on_merge=True)
    assert _card(app, sid) == _pr(43, cleanup_on_merge=True)


def test_the_card_shows_the_latest_pr_from_either_path_and_the_sidebar_counts_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    opener = _opener(agent, monkeypatch, "https://github.com/o/r/pull/42")
    app = App(tmp_path, monkeypatch, opener, CHIMERA_PULL_REQUESTS="true")
    sid = app.turn("open the pr")
    app.client.patch(f"/api/code/sessions/{sid}/pull-request", json={"fix_on_ci_failure": True})

    _panel_opens(app, monkeypatch, sid, 44)
    assert _card(app, sid) == _pr(44)  # a new PR: the options given for #42 are not for it
    assert app.rows()[sid]["pull_request_count"] == 2

    _panel_opens(app, monkeypatch, sid, 44)  # the panel reporting the same PR again
    row = app.rows()[sid]
    assert row["pull_request_count"] == 2
    assert row["pull_request"] == {"number": 44, "url": "https://github.com/o/r/pull/44"}


# ---- conversations written before -------------------------------------------------------------


def _home_with(tmp_path: Path, document: dict[str, Any], marks: dict[str, Any] | None) -> Path:
    folder = tmp_path / "home" / "code_sessions"
    folder.mkdir(parents=True)
    (folder / f"{OLD}.json").write_text(json.dumps(document), encoding="utf-8")
    if marks is not None:
        (folder.parent / "code_sessions.marks.json").write_text(
            json.dumps({"sessions": {OLD: marks}}), encoding="utf-8"
        )
    return folder


def _transcript(tmp_path: Path, **extra: Any) -> dict[str, Any]:
    return {
        "session_id": OLD,
        "workspace": str(tmp_path / "proj"),
        "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "done"}],
        **extra,
    }


def test_a_conversation_whose_pr_is_only_in_its_transcript_reads_right_and_moves_on_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    # C1's format: the record, with an option the owner switched on, inside the transcript.
    folder = _home_with(
        tmp_path, _transcript(tmp_path, pull_request=_pr(7, cleanup_on_merge=True)), marks=None
    )
    app = App(tmp_path, monkeypatch, agent)

    assert _card(app, OLD) == _pr(7, cleanup_on_merge=True)
    row = app.rows()[OLD]
    assert (row["pull_request_count"], row["state"]) == (1, "review")

    app.turn("explain it", session_id=OLD)  # a save: the one write that would drop the field
    stored = json.loads((folder / f"{OLD}.json").read_text(encoding="utf-8"))
    assert "pull_request" not in stored
    store = CodeSessionStore(folder)
    assert store.marks.get(OLD).pull_requests == [_pr(7, cleanup_on_merge=True)]
    assert _card(app, OLD) == _pr(7, cleanup_on_merge=True)
    assert app.rows()[OLD]["pull_request_count"] == 1


def test_a_conversation_whose_pr_is_only_in_the_marks_reads_right_with_its_options_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    # C3's format: number and URL in the marks file, no options, nothing in the transcript.
    _home_with(
        tmp_path,
        _transcript(tmp_path),
        marks={"pull_requests": [{"number": 5, "url": "https://github.com/o/r/pull/5"}]},
    )
    app = App(tmp_path, monkeypatch, agent)

    assert _card(app, OLD) == _pr(5)
    row = app.rows()[OLD]
    assert (row["pull_request_count"], row["state"]) == (1, "review")
    switched = app.client.patch(f"/api/code/sessions/{OLD}/pull-request", json={"fix_on_ci_failure": True})
    assert switched.status_code == 200 and switched.json() == _pr(5, fix_on_ci_failure=True)


def test_both_old_records_at_once_merge_by_number_and_a_shared_pr_counts_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    folder = _home_with(
        tmp_path,
        _transcript(tmp_path, pull_request=_pr(7, fix_on_ci_failure=True)),
        marks={
            "pull_requests": [
                {"number": 5, "url": "https://github.com/o/r/pull/5"},
                {"number": 9, "url": "https://github.com/o/r/pull/9"},
            ]
        },
    )
    app = App(tmp_path, monkeypatch, agent)
    store = CodeSessionStore(folder)

    assert store.pull_requests(OLD) == [_pr(5), _pr(7, fix_on_ci_failure=True), _pr(9)]
    assert _card(app, OLD) == _pr(9)
    assert app.rows()[OLD]["pull_request_count"] == 3

    # The same PR in both files: one PR, with the marks file's copy (where options change now).
    _home_with(
        tmp_path / "again",
        _transcript(tmp_path, pull_request=_pr(5, cleanup_on_merge=True)),
        marks={"pull_requests": [_pr(5)]},
    )
    again = CodeSessionStore(tmp_path / "again" / "home" / "code_sessions")
    assert again.pull_requests(OLD) == [_pr(5)]


# ---- what the list costs ---------------------------------------------------------------------------


def test_listing_the_prs_opens_no_unchanged_conversation_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _home_with(tmp_path, _transcript(tmp_path, pull_request=_pr(7)), marks=None)
    store = CodeSessionStore(folder)
    store.save(CodeSession(Agent(), session_id="new", messages=[{"role": "user", "content": "q"}]))
    store.record_pull_request("new", number=3, url="https://github.com/o/r/pull/3")
    store.list_meta()

    reads: Counter[str] = Counter()
    original = Path.read_text

    def spy(path: Path, *args: Any, **kwargs: Any) -> str:
        reads[path.name] += 1
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)
    # As the list route does since titles live in the marks (study 03, A2): one read of the marks
    # file serves the rows' titles and the PRs, so a list still opens it exactly once.
    marks = store.marks.all()
    rows = store.list_meta(marks)
    listed = store.listed_pull_requests(marks)

    assert {r["id"] for r in rows} == {OLD, "new"}
    assert listed == {OLD: [_pr(7)], "new": [_pr(3)]}
    assert reads == Counter({"code_sessions.marks.json": 1}), reads
