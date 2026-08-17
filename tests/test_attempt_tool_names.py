"""An attempt records what it DID, not only what it cost.

`AgentResult.tool_names` has existed all along and died at the loop boundary: `Attempt` kept the
tokens, the price and the model, and dropped the tools. That made a whole class of question
unanswerable from a finished run — how many edits a task took, whether a tool is ever reached at all
— and it is why `bench/edit_tools/PREREGISTRATION.md` had to be written before the harness: the
bench's own primary metric could not be read from the loop it needs to measure.

These tests pin the wire. Deleting the propagation line makes the first one fail.
"""

from __future__ import annotations

from dataclasses import asdict

from chimera.core.agent import AgentResult
from chimera.core.autonomous import Attempt, AutonomousAgent, AutonomousConfig


def test_an_attempt_can_carry_the_tools_it_called() -> None:
    attempt = Attempt(0, "done", True, True, False)
    attempt.tool_names = ["read_file", "edit_file", "edit_file", "run_command"]
    assert attempt.tool_names.count("edit_file") == 2, (
        "the count of one tool family is the whole point — a bench that cannot ask 'how many edits' "
        "cannot compare two edit tools"
    )


def test_the_default_is_empty_not_missing() -> None:
    """A resumed run rebuilds attempts with `Attempt(**saved)`; an old checkpoint has no such key."""
    assert Attempt(0, "a", True, True, False).tool_names == []
    revived = Attempt(**{"index": 0, "answer": "a", "approved": True, "verified": True,
                         "reverted": False})
    assert revived.tool_names == []


def test_it_survives_the_round_trip_a_checkpoint_does() -> None:
    attempt = Attempt(1, "done", True, True, False)
    attempt.tool_names = ["edit_file"]
    assert Attempt(**asdict(attempt)).tool_names == ["edit_file"]


def test_the_sequence_is_kept_not_a_tally() -> None:
    """Order answers questions a counter cannot — which tool a failure follows, for instance."""
    attempt = Attempt(0, "x", True, True, False)
    attempt.tool_names = ["edit_file", "run_command", "edit_file"]
    assert attempt.tool_names == ["edit_file", "run_command", "edit_file"]


class _WorkerThatUsedTools:
    def run(self, task: str) -> AgentResult:
        return AgentResult(
            answer="done", steps=3, stopped_reason="final",
            tool_names=["read_file", "edit_file", "edit_file"],
        )


def test_the_loop_actually_carries_them_up() -> None:
    """The wiring test, not the class test.

    The dataclass having a field proves nothing about production: the field could sit empty forever
    and every test above would still pass. This drives the real loop and asserts the names arrived —
    delete the propagation line in `autonomous.py` and only this one goes red.
    """
    auto = AutonomousAgent(
        _WorkerThatUsedTools(),  # type: ignore[arg-type]
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )
    result = auto.run("do the task")
    assert result.attempts, "no attempt recorded — the test proves nothing"
    assert result.attempts[0].tool_names == ["read_file", "edit_file", "edit_file"]


class _WorkerWithoutTheField:
    """A worker whose result predates the field — the loop must not crash on it."""

    def run(self, task: str) -> object:
        class _Bare:
            answer, steps, stopped_reason = "done", 1, "final"

        return _Bare()


def test_a_result_without_the_field_is_not_a_crash() -> None:
    auto = AutonomousAgent(
        _WorkerWithoutTheField(),  # type: ignore[arg-type]
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )
    assert auto.run("t").attempts[0].tool_names == []
