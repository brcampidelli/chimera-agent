"""The bridge's job status and the guidance route agree on whether a turn is running.

Study 34, item 14: a job said ``turn_running: true`` while ``POST …/guidance`` answered "that turn is
not running any more". The status asked whether the turn was still REGISTERED, which stays true while
it saves after its loop returned; guidance asked whether the loop was still OPEN. One definition now.
"""

from __future__ import annotations

from chimera.api.live_turns import LiveTurns


def _started(turns: LiveTurns, turn_id: str = "t1") -> None:
    turns.start(turn_id=turn_id, session_id="s", workspace="w", message="m", live_since=0)


def test_a_turn_is_running_from_its_start_until_its_loop_returns() -> None:
    turns = LiveTurns()
    assert not turns.is_running("t1")
    _started(turns)
    assert turns.is_running("t1")
    turns.take_guidance("t1")()
    assert turns.guide("t1", "mind the tests") == "queued"
    turns.close_guidance("t1")
    # Still registered (the transcript is being saved) but no longer running: both questions say so.
    assert turns.get("t1") is not None
    assert not turns.is_running("t1")
    assert turns.guide("t1", "too late") == "not_running"
    turns.finish("t1")
    assert not turns.is_running("t1")


def test_status_and_guidance_never_disagree_in_any_state() -> None:
    turns = LiveTurns()
    for step in ("unknown", "started", "reading", "closed", "finished"):
        if step == "started":
            _started(turns)
        elif step == "reading":
            turns.take_guidance("t1")()
        elif step == "closed":
            turns.close_guidance("t1")
        elif step == "finished":
            turns.finish("t1")
        says_running = turns.is_running("t1")
        outcome = turns.guide("t1", "x")
        assert (outcome != "not_running") == says_running, step
