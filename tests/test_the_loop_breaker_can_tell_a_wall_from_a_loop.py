"""The circuit breaker could not tell a model repeating itself from a model being stonewalled.

`agent.py` computes, one line above the call into the detector, whether the tool actually ran —
`not observation.startswith("error:") and not is_refusal(observation)`. That value went to the
screen and was thrown away before `loop_detector.record`, which therefore counted every repetition
the same way.

Both halves of that are wrong in a different direction. A tool refused five times by a governance
gate ends the run with `stopped_reason="tool_loop"` and the words "called with identical args 5×",
which reads as the model looping — when the model asked, was told no, and asked again. Whoever
reads that receipt is told the wrong thing about whose behaviour ended the run. It is the same
attribution defect `agent.py:638` and `registry.py:73` were both changed to fix, and `steplog.py`
after them; this is the fourth site of one rule.

The breaker still fires — a model that keeps hitting a wall should stop, and burning the remaining
steps against a gate helps nobody. What changes is what it says happened.
"""

from __future__ import annotations

from chimera.core.tool_loop import ToolLoopDetector
from chimera.tools.base import refusal


def _repeat(times: int, *, ok: bool | None, observation: str = "conteúdo do arquivo") -> str:
    """Record the same call `times` times and return the final verdict's reason."""
    detector = ToolLoopDetector()
    verdict = None
    for _ in range(times):
        verdict = detector.record("read_file", {"path": "x.py"}, observation, ok=ok)
    assert verdict is not None
    return verdict.reason


# ------------------------------------------------------------------ the wall


def test_a_wall_still_stops_the_run() -> None:
    """Not the fix: a gate refusing five times must still end the turn, not be waved through."""
    detector = ToolLoopDetector()
    verdict = None
    for _ in range(5):
        verdict = detector.record("run_shell", {"cmd": "git push"}, refusal("blocked"), ok=False)

    assert verdict is not None and verdict.tripped


def test_a_wall_does_not_get_called_a_loop() -> None:
    """The fix. The words are the deliverable: this receipt is read by someone asking whose
    behaviour ended the run, and 'called with identical args' answers that question wrongly."""
    reason = _repeat(5, ok=False, observation=refusal("blocked by policy"))

    assert "identical args" not in reason
    assert "refused" in reason or "failed" in reason


def test_the_tool_is_still_named() -> None:
    """Whatever the wording, a reason that does not say WHICH tool sends the reader to the log."""
    detector = ToolLoopDetector()
    verdict = None
    for _ in range(5):
        verdict = detector.record("run_shell", {"cmd": "git push"}, refusal("no"), ok=False)

    assert verdict is not None
    assert "run_shell" in verdict.reason


# ------------------------------------------------------------------ the loop


def test_a_real_loop_is_still_called_a_loop() -> None:
    """The guard against fixing this by simply removing the honest case."""
    reason = _repeat(5, ok=True)

    assert "identical args" in reason


def test_an_unreported_outcome_reads_as_before() -> None:
    """`ok` is optional, and a caller that does not pass it must get exactly the old wording.

    Every other consumer of this detector — and every test written before the parameter existed —
    depends on that. A default that changed the words would make this a breaking change wearing the
    costume of a bug fix.
    """
    assert _repeat(5, ok=None) == _repeat(5, ok=True)


def test_a_mixed_run_is_a_loop_not_a_wall() -> None:
    """One success among the repeats means something DID happen, so the wall reading is false.

    This is the case that decides the implementation: counting a contiguous tail would call this a
    wall, because the failures are adjacent. The question is not "were the last few blocked" but
    "did this call ever get through".
    """
    detector = ToolLoopDetector()
    detector.record("read_file", {"path": "x.py"}, "conteúdo", ok=True)
    verdict = None
    for _ in range(4):
        verdict = detector.record("read_file", {"path": "x.py"}, "error: permission denied", ok=False)

    assert verdict is not None and verdict.tripped
    assert "identical args" in verdict.reason


def test_a_stalled_poll_that_never_ran_says_so() -> None:
    """The other counter. `_no_progress` fires on four identical observations of the same tool,
    which is exactly the shape of a write refused four times — and it had the same wrong words."""
    detector = ToolLoopDetector()
    verdict = None
    for _ in range(4):
        verdict = detector.record("write_file", {"path": "x.py"}, refusal("read-only"), ok=False)

    assert verdict is not None and verdict.tripped
    assert "polled" not in verdict.reason


def test_a_stalled_poll_that_did_run_still_says_polled() -> None:
    """Same guard as above, on the second counter: the honest case keeps its honest words."""
    detector = ToolLoopDetector()
    verdict = None
    for _ in range(4):
        verdict = detector.record("read_file", {"path": "x.py"}, "mesma saída", ok=True)

    assert verdict is not None and verdict.tripped
    assert "polled" in verdict.reason
