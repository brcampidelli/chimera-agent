"""Whether a long run is still getting anywhere.

The tight-loop detector watches a 12-call window and trips on identical repeats. Drift is the slow
version and passes straight through that window: a run that revisits the same three files every
twenty turns never repeats back-to-back. What gives it away is the SHAPE of the whole trajectory —
which is why every assertion here is about early-versus-late, never about a total.

(Not to be confused with ``chimera.governance.drift``, which compares a spec against the code.)
"""

from __future__ import annotations

from chimera.core.context_drift import DriftReport, assess
from chimera.core.steplog import StepRecord, ToolRecord


def _step(index: int, tools: list[ToolRecord], *, compacted: bool = False) -> StepRecord:
    return StepRecord(
        index=index,
        prompt_tokens=1000 + index * 100,
        completion_tokens=50,
        model="m",
        tools=tools,
        compacted=compacted,
    )


def _tool(name: str, args: str, obs: str, ok: bool = True) -> ToolRecord:
    return ToolRecord(name=name, arguments=args, observation=obs, ok=ok)


def _run(pairs: list[list[ToolRecord]], *, compact_at: int | None = None) -> list[StepRecord]:
    return [_step(i + 1, t, compacted=(i == compact_at)) for i, t in enumerate(pairs)]


# --- refusing to answer without evidence -------------------------------------------------------

def test_a_short_run_is_not_assessed_rather_than_declared_clean() -> None:
    steps = _run([[_tool("read", f"{{f:{i}}}", f"body {i}")] for i in range(6)])
    report = assess(steps)

    assert report.assessed is False
    # The distinction that matters: "we could not tell" must never be readable as a clean bill of
    # health. `drifting` stays False, but only alongside `assessed` False saying why.
    assert report.drifting is False


def test_a_run_that_barely_used_tools_is_not_assessed() -> None:
    # Twelve steps, two tool calls: there is no rate to compare. Reporting "no drift" would be
    # manufacturing a finding out of an absence of evidence.
    steps = _run(
        [[_tool("read", "{f:1}", "body")], [], [], [_tool("read", "{f:2}", "b2")], *([[]] * 8)]
    )
    assert assess(steps).assessed is False


# --- the thing itself --------------------------------------------------------------------------

def test_a_run_that_keeps_learning_is_not_drifting() -> None:
    steps = _run([[_tool("read", f"{{f:{i}}}", f"body {i}")] for i in range(14)])
    report = assess(steps)

    assert report.assessed is True
    assert report.drifting is False
    assert report.signals == ()


def test_a_run_that_starts_re_deriving_what_it_knows_is_flagged() -> None:
    early = [[_tool("read", f"{{f:{i}}}", f"body {i}")] for i in range(7)]
    # The late half asks questions it already has the answers to — and never twice in a row, so the
    # sliding-window loop detector sees nothing wrong with any of it.
    late = [[_tool("read", f"{{f:{i % 3}}}", f"body {i % 3}")] for i in range(7)]
    report = assess(_run(early + late))

    assert report.drifting is True
    assert [s.name for s in report.signals] == ["redundancy"]
    assert report.signals[0].late > report.signals[0].early
    assert "re-derived" in report.summary


def test_high_but_flat_redundancy_is_not_degradation() -> None:
    # A run that polls the same thing from the very beginning. Its redundancy sits far above the
    # floor throughout — and it is not degrading, it is just what this run does. An absolute
    # threshold would flag it; the ratio test is what refuses to.
    steps = _run([[_tool("poll", "{}", "same")] for _ in range(16)])
    report = assess(steps)

    assert report.assessed is True
    assert report.drifting is False


def test_re_reading_a_file_after_editing_it_is_progress_not_repetition() -> None:
    # Same tool, same arguments, DIFFERENT answer — the edit changed the file. Keying redundancy on
    # (tool, args) alone, which is correct for a tight-loop detector, would call this a repeat and
    # punish the most ordinary shape there is in a coding run.
    steps = _run(
        [
            [_tool("read", "{f:main}", f"version {i}"), _tool("edit", f"{{v:{i}}}", "ok")]
            for i in range(14)
        ]
    )
    report = assess(steps)

    assert report.assessed is True
    assert report.drifting is False


def test_a_run_that_starts_failing_more_is_flagged() -> None:
    early = [[_tool("run", f"{{c:{i}}}", f"out {i}", ok=True)] for i in range(7)]
    late = [[_tool("run", f"{{c:{i + 100}}}", f"err {i}", ok=False)] for i in range(7)]
    report = assess(_run(early + late))

    assert "failure" in [s.name for s in report.signals]
    assert "failed" in report.summary


def test_a_run_that_was_always_failing_is_not_degrading() -> None:
    # Broken from step one is a different problem with a different fix. Drift is a run getting
    # worse, and this one never got worse.
    steps = _run([[_tool("run", f"{{c:{i}}}", f"err {i}", ok=False)] for i in range(16)])
    assert "failure" not in [s.name for s in assess(steps).signals]


# --- the failure that compaction itself can cause ----------------------------------------------

def test_redundancy_jumping_after_history_was_dropped_gets_its_own_signal() -> None:
    before = [[_tool("read", f"{{f:{i}}}", f"body {i}")] for i in range(8)]
    after = [[_tool("read", f"{{f:{i % 3}}}", f"body {i % 3}")] for i in range(8)]
    report = assess(_run(before + after, compact_at=7))

    # Its own signal because it names a MECHANISM: the fix is the restoration payload, not the task.
    # It is also invisible in the whole-run split when the compaction lands late.
    assert "post_compaction" in [s.name for s in report.signals]
    assert "after history was dropped" in report.summary


def test_a_compaction_the_run_recovered_from_is_not_flagged() -> None:
    before = [[_tool("read", f"{{f:{i}}}", f"body {i}")] for i in range(8)]
    after = [[_tool("read", f"{{g:{i}}}", f"other {i}")] for i in range(8)]
    report = assess(_run(before + after, compact_at=7))

    # Restoration did its job: the run took new ground instead of re-establishing old ground.
    assert "post_compaction" not in [s.name for s in report.signals]


def test_no_compaction_means_no_compaction_signal() -> None:
    steps = _run([[_tool("read", f"{{f:{i % 2}}}", f"body {i % 2}")] for i in range(16)])
    assert "post_compaction" not in [s.name for s in assess(steps).signals]


# --- the report ---------------------------------------------------------------------------------

def test_the_report_carries_the_numbers_that_raised_it() -> None:
    early = [[_tool("read", f"{{f:{i}}}", f"body {i}")] for i in range(7)]
    late = [[_tool("read", "{f:0}", "body 0")] for _ in range(7)]
    payload = assess(_run(early + late)).as_dict()

    # A verdict with no measurement behind it cannot be argued with, which means it cannot be
    # trusted either. Every signal ships the early and late rates that produced it.
    assert payload["drifting"] is True
    signal = payload["signals"][0]
    assert set(signal) == {"name", "early", "late", "detail"}
    assert isinstance(signal["late"], float)


def test_an_unassessed_report_is_empty_not_reassuring() -> None:
    report = DriftReport(assessed=False, steps=3)
    assert report.drifting is False and report.summary == ""
    assert report.as_dict()["assessed"] is False


# --- in the loop ---------------------------------------------------------------------------------

def test_the_trace_carries_the_assessment_so_a_run_is_self_describing() -> None:
    from chimera.core.steplog import StepLog

    log = StepLog()
    early = [_tool("read", f"{{f:{i}}}", f"body {i}") for i in range(7)]
    late = [_tool("read", f"{{f:{i % 2}}}", f"body {i % 2}") for i in range(7)]
    for i, call in enumerate([*early, *late]):
        log.add(_step(i + 1, [call]))

    payload = log.as_dict()
    # Whoever reads a trace back should not have to re-derive by hand whether the run was still
    # going somewhere. The verdict travels with the evidence.
    assert payload["drift"]["drifting"] is True
    assert payload["drift"]["signals"]


def test_a_real_loop_run_reports_drift_on_its_result() -> None:
    from typing import Any

    from chimera.core import Agent, AgentConfig
    from chimera.providers import CompletionResult, ToolCall
    from chimera.tools import ToolRegistry
    from chimera.tools.builtin import EchoTool

    class _CirclingBackend:
        """Asks for the same three echoes over and over — never twice in a row, so the tight-loop
        detector's window stays clean the whole way through."""

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any):
            self.calls += 1
            if self.calls > 20:
                return CompletionResult(content="done", model="fake", prompt_tokens=100)
            text = f"item {self.calls % 3}" if self.calls > 8 else f"item {self.calls}"
            return CompletionResult(
                content="", model="fake", prompt_tokens=100 * self.calls,
                tool_calls=[ToolCall(id=f"c{self.calls}", name="echo", arguments={"text": text})],
            )

    registry = ToolRegistry()
    registry.register(EchoTool())
    result = Agent(_CirclingBackend(), registry, AgentConfig(max_steps=24)).run("go")

    report = result.steplog.drift
    assert report.assessed is True
    assert report.drifting is True
    assert "redundancy" in [s.name for s in report.signals]


def test_an_ordinary_short_run_says_nothing_either_way() -> None:
    from typing import Any

    from chimera.core import Agent, AgentConfig
    from chimera.providers import CompletionResult
    from chimera.tools import ToolRegistry

    class _Backend:
        def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any):
            return CompletionResult(content="done", model="fake", prompt_tokens=100)

    result = Agent(_Backend(), ToolRegistry(), AgentConfig(max_steps=8)).run("go")
    # The default budget is eight steps. Every ordinary run is below the horizon where drift is
    # measurable, and the report says "not assessed" rather than pretending to a clean verdict.
    assert result.steplog.drift.assessed is False
