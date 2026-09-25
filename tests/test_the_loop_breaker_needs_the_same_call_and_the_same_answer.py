"""A spin repeats the call AND the answer (study 24, 2026-09-24).

Two measured false alarms, one per half of that pair:

- five `scroll` calls, identical args, each returning a new viewport: every failure viewport-first
  added in `bench/browser_viewport_tasks` (20 of 20);
- four different successful edits answering the same `edited x: replaced 1 occurrence`: every breaker
  trip of the strong executor in `bench/tool_loop_fork` (20 of 20).

And the three things the breaker must still catch: the same call with the same answer (timing noise
included), a poll that never changes, and the same failure however the args vary.
"""

from __future__ import annotations

from chimera.core.tool_loop import ToolLoopDetector


def test_distinct_edits_with_the_same_confirmation_are_work_not_a_stall() -> None:
    det = ToolLoopDetector()
    for i in range(8):
        verdict = det.record(
            "edit_file", {"path": "db/migration.sql", "old": f"line {i}", "new": f"fixed {i}"},
            "edited db/migration.sql: replaced 1 occurrence", ok=True,
        )
        assert not verdict.tripped, (i, verdict.reason)


def test_a_scroll_that_shows_something_new_is_not_a_spin() -> None:
    det = ToolLoopDetector()
    for i in range(8):
        verdict = det.record(
            "browser", {"action": "scroll", "direction": "down"},
            f"[e{i}a] link: {chr(65 + i)}lpha story\n[e{i}b] button: {chr(66 + i)}uy now", ok=True,
        )
        assert not verdict.tripped, (i, verdict.reason)


def test_the_same_call_with_the_same_answer_still_breaks() -> None:
    det = ToolLoopDetector()
    verdicts = [det.record("grep", {"q": "x"}, "no matches", ok=True) for _ in range(5)]
    assert verdicts[-1].tripped


def test_a_test_run_that_differs_only_in_its_timing_is_still_the_same_answer() -> None:
    det = ToolLoopDetector()
    verdicts = [
        det.record("run_shell", {"command": "pytest -q"}, f"1 failed, 3 passed in 0.{50 + i}s", ok=True)
        for i in range(5)
    ]
    assert verdicts[-1].tripped


def test_a_poll_that_never_changes_still_breaks() -> None:
    det = ToolLoopDetector()
    verdicts = [det.record("job_status", {"id": 7}, "pending", ok=True) for _ in range(4)]
    assert verdicts[-1].tripped and "polled" in verdicts[-1].reason


def test_the_same_failure_with_different_args_is_still_a_wall() -> None:
    det = ToolLoopDetector()
    verdicts = [
        det.record("edit_file", {"old": f"anchor {i}"}, "error: old_string not found", ok=False)
        for i in range(4)
    ]
    assert verdicts[-1].tripped and "nothing ran" in verdicts[-1].reason
