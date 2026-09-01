"""The trace called a refused tool a successful one.

`agent.py` learned this distinction the hard way, and its comment records the measurement: a gate
declined to run a shell command, the screen drew a tick, the receipt counted a completed call, and
the model answered "Done. I force-pushed the branch to origin as requested" for a command that never
ran. The fix there was to stop treating "starts with error:" as the whole definition of failure.

`registry.py` carries the same rule. `steplog.tool_record` — the one that writes `traces.jsonl`, the
artefact someone reads *after* a run went wrong — was left behind, so the file that exists to answer
"what actually happened" was the one place still answering it wrong.

The `[idempotent: …]` case gets its own test because `is_refusal` goes out of its way to exclude it:
that marker says the effect ALREADY HAPPENED and is not being repeated. Calling it a refusal would
claim the action did not occur, when it did — the opposite error, and just as expensive.
"""

from __future__ import annotations

from chimera.core.steplog import tool_record
from chimera.tools.base import refusal


def test_a_refused_tool_is_not_recorded_as_ok() -> None:
    """The whole point: a gate said no, so nothing ran, so the record must not say it did."""
    assert tool_record("run_shell", {"cmd": "git push"}, refusal("blocked by policy")).ok is False


def test_the_reason_survives_into_the_trace() -> None:
    """A flag that says "no" without saying why sends the reader back to the server log."""
    record = tool_record("run_shell", {"cmd": "git push"}, refusal("blocked by policy"))

    assert "blocked by policy" in record.observation


def test_an_error_is_still_a_failure() -> None:
    """The rule this one already had, pinned so widening it cannot quietly drop the original half."""
    assert tool_record("read_file", {"path": "x"}, "error: file not found").ok is False


def test_an_ordinary_observation_is_still_a_success() -> None:
    """The failure mode of a greedy fix: a stricter rule that starts calling real work a failure."""
    assert tool_record("read_file", {"path": "x"}, "def main():\n    pass\n").ok is True


def test_an_already_done_action_is_not_a_refusal() -> None:
    """`is_refusal` excludes this on purpose, and the distinction is load-bearing.

    `[idempotent: …]` means the side effect HAPPENED on an earlier attempt and is not being repeated.
    Recording it as a refusal would tell whoever reads the receipt that the action never occurred —
    which is exactly the wrong thing to believe about a push that already landed.
    """
    assert tool_record("run_shell", {"cmd": "git push"}, "[idempotent: already pushed]").ok is True


def test_a_refusal_mentioning_an_error_is_still_a_refusal() -> None:
    """Order of the two checks must not matter: the mark leads, so the text after it is free."""
    assert tool_record("http_get", {"url": "u"}, refusal("error budget policy denied this")).ok is False
