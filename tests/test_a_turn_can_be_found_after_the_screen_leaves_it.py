"""A coding turn that is still running can be found, and followed, from outside it.

Measured live on 2026-09-29, with two tasks started in two conversations. Both kept running on the
server after the caller stopped waiting, and their frames were kept and replayable. But the
conversation is stored when the agent finishes, not while it works, so for as long as a turn ran
its session read back as empty (`exchanges: []`, no workspace) and did not appear in the list. A
screen that came back to it saw a conversation that looked idle and had no way to learn that a turn
was working, or which one to follow.

So the server now says so: `GET /api/code/turns/running`, a `running` flag on each row of the
session list (with the not-yet-stored conversation listed too), and `running_turn` on the session.
The registry is in memory, because a turn is a thread of this process and a record on disk would
outlive it.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.live_turns import LiveTurns
from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult
from chimera.interface import ChatSession

TIMEOUT = 10.0


def _agent_class(entered: threading.Event, release: threading.Event) -> type:
    class _Blocking:
        def __init__(self, *_a: Any, **kwargs: Any) -> None:
            self.config = kwargs.get("config") or (_a[2] if len(_a) > 2 else None)

        def run(self, task: str, **_kw: Any) -> AgentResult:
            entered.set()
            assert release.wait(TIMEOUT), "the test never released the turn"
            return AgentResult(
                answer="done", steps=1, stopped_reason="final",
                transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "done"}],
            )

    return _Blocking


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", agent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    return TestClient(build_api_app(lambda: ChatSession(agent()), workspace=ws, settings=settings))


def _wait(predicate: Any) -> bool:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class _Running:
    """A turn held open mid-run, so the app can be asked about it from outside."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.entered, self.release = threading.Event(), threading.Event()
        self.client = _client(tmp_path, monkeypatch, _agent_class(self.entered, self.release))
        self.thread = threading.Thread(
            target=lambda: self.client.post("/api/code/turn", json={"message": "audit the gateway"}),
            daemon=True,
        )

    def __enter__(self) -> _Running:
        self.thread.start()
        assert self.entered.wait(TIMEOUT), "the turn never reached the agent"
        return self

    def finish(self) -> None:
        self.release.set()
        self.thread.join(TIMEOUT)
        assert not self.thread.is_alive()

    def __exit__(self, *_exc: object) -> None:
        self.release.set()


def test_a_running_turn_is_listed_with_what_it_was_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _Running(tmp_path, monkeypatch) as run:
        response = run.client.get("/api/code/turns/running")

        assert response.status_code == 200, "the fixed path was read as a turn id"
        [turn] = response.json()
        assert turn["message"] == "audit the gateway"
        assert turn["turn_id"] and turn["session_id"]
        assert turn["transcript_saved"] is False
        # The sequence BEFORE the opening frame: replaying the conversation's live stream from it
        # starts at that frame, and this is the first turn of a new conversation.
        assert turn["live_since"] == 0
        run.finish()


def test_nothing_is_listed_once_the_turn_has_ended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _Running(tmp_path, monkeypatch) as run:
        assert len(run.client.get("/api/code/turns/running").json()) == 1
        run.finish()

        assert run.client.get("/api/code/turns/running").json() == []


def test_a_conversation_whose_first_turn_is_running_is_in_the_list_though_its_file_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _Running(tmp_path, monkeypatch) as run:
        rows = run.client.get("/api/code/sessions").json()

        [row] = [r for r in rows if r["running"]]
        assert row["title"] == "audit the gateway" and row["turns"] == 0
        run.finish()

        after = run.client.get("/api/code/sessions").json()
        assert [r["id"] for r in after] == [row["id"]] and not after[0]["running"]
        assert after[0]["turns"] == 1


def test_the_session_read_names_the_turn_to_follow_instead_of_looking_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _Running(tmp_path, monkeypatch) as run:
        [turn] = run.client.get("/api/code/turns/running").json()

        read = run.client.get(f"/api/code/sessions/{turn['session_id']}").json()

        assert read["exchanges"] == []  # the file has nothing yet: this is the gap being closed
        assert read["running_turn"]["turn_id"] == turn["turn_id"]
        assert read["running_turn"]["transcript_saved"] is False
        assert read["workspace"] == turn["workspace"] != ""
        run.finish()

        done = run.client.get(f"/api/code/sessions/{turn['session_id']}").json()
        assert done["running_turn"] is None and len(done["exchanges"]) == 1


def test_a_stored_conversation_nobody_is_working_in_says_nothing_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _Running(tmp_path, monkeypatch) as run:
        run.finish()
        [row] = run.client.get("/api/code/sessions").json()

        assert row["running"] is False
        assert run.client.get(f"/api/code/sessions/{row['id']}").json()["running_turn"] is None


# ---------------------------------------------------------------- the window after the agent finishes


def test_the_turns_own_saves_are_the_writes_the_registry_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The counter is only as good as its callers: the transcript save and the receipt save."""
    seen: list[str] = []
    original = LiveTurns.writing

    def spy(self: LiveTurns, turn_id: str) -> Any:
        seen.append(turn_id)
        return original(self, turn_id)

    monkeypatch.setattr(LiveTurns, "writing", spy)
    with _Running(tmp_path, monkeypatch) as run:
        [turn] = run.client.get("/api/code/turns/running").json()
        run.finish()

    assert len(seen) >= 2 and set(seen) == {turn["turn_id"]}


def test_a_turn_is_saved_only_after_a_complete_transcript_write() -> None:
    turns = LiveTurns()
    turns.start(turn_id="t", session_id="s", workspace="w", message="m", live_since=0)
    assert turns.of_session("s") is not None and not turns.of_session("s").transcript_saved  # type: ignore[union-attr]

    with turns.writing("t"):
        # Mid-write the count is odd: the file is being replaced, and is not yet trustworthy.
        assert not turns.of_session("s").transcript_saved  # type: ignore[union-attr]
        assert turns.of_session("s").writes == 1  # type: ignore[union-attr]

    assert turns.of_session("s").transcript_saved  # type: ignore[union-attr]


def test_a_turn_that_ends_leaves_no_trace_and_a_write_for_an_unknown_turn_is_harmless() -> None:
    turns = LiveTurns()
    turns.start(turn_id="t", session_id="s", workspace="w", message="m", live_since=0)

    turns.finish("t")
    with turns.writing("a-background-work"):
        pass

    assert turns.running() == [] and turns.of_session("s") is None


def test_a_read_that_a_write_overlaps_is_retried_and_reports_the_settled_state() -> None:
    """The agent saves while the screen reads: the answer must not pair the old file with the new flag."""
    turns = LiveTurns()
    turns.start(turn_id="t", session_id="s", workspace="w", message="m", live_since=0)
    reads: list[str] = []

    def read() -> str:
        reads.append("file")
        if len(reads) == 1:
            # A write lands in the middle of the first read, the way the agent's save does.
            with turns.writing("t"):
                pass
        return f"read {len(reads)}"

    value, turn = turns.read_consistently("s", read)

    assert len(reads) == 2 and value == "read 2"
    assert turn is not None and turn.transcript_saved


def test_a_read_that_no_write_touches_is_not_repeated() -> None:
    turns = LiveTurns()
    turns.start(turn_id="t", session_id="s", workspace="w", message="m", live_since=0)
    reads: list[int] = []

    value, turn = turns.read_consistently("s", lambda: reads.append(1) or "file")

    assert value == "file" and len(reads) == 1
    assert turn is not None and not turn.transcript_saved


def test_a_read_that_never_settles_gives_up_after_a_bounded_number_of_tries() -> None:
    turns = LiveTurns()
    turns.start(turn_id="t", session_id="s", workspace="w", message="m", live_since=0)
    reads: list[int] = []

    def read() -> str:
        reads.append(1)
        with turns.writing("t"):
            pass
        return "file"

    turns.read_consistently("s", read)

    assert len(reads) == 3
