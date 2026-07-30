"""Per-step accounting: context size, and what each tool was asked and answered.

The loop reported a run-level token sum and, downstream, only `{tool, ok}` pairs. That could not
answer "how much context was this run carrying?" or "what did the tool actually return?" — the two
questions that matter when a run goes wrong. These tests pin both.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.core import Agent, AgentConfig
from chimera.core.steplog import StepLog, StepRecord, clip, tool_record
from chimera.providers import CompletionResult, ToolCall
from chimera.tools import ToolRegistry
from chimera.tools.builtin import EchoTool


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry


class _Backend:
    """Answers with a scripted queue, reporting a growing prompt size like a real provider does."""

    def __init__(self, responses: list[CompletionResult]) -> None:
        self._responses = list(responses)

    def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any) -> CompletionResult:
        if self._responses:
            return self._responses.pop(0)
        return CompletionResult(content="done", model="fake", prompt_tokens=900)


def _tool_step(prompt_tokens: int, text: str = "hi") -> CompletionResult:
    return CompletionResult(
        content="",
        model="fake",
        prompt_tokens=prompt_tokens,
        completion_tokens=10,
        tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": text})],
    )


def test_clip_keeps_head_and_tail() -> None:
    # Errors live at the END of an output; a head-only truncation throws away the useful half.
    clipped = clip("A" * 100 + "TRACEBACK_AT_THE_END", 60)
    assert clipped.startswith("AAA")
    assert clipped.endswith("END")
    assert "elided" in clipped


def test_clip_leaves_short_text_alone() -> None:
    assert clip("short", 60) == "short"


def test_tool_record_follows_the_loops_error_convention() -> None:
    # `ok` must agree with what every other consumer in the codebase already checks.
    ok = tool_record("read_file", {"path": "a.py"}, "contents")
    bad = tool_record("read_file", {"path": "nope"}, "error: file not found")
    assert ok.ok is True
    assert bad.ok is False
    assert '"path"' in ok.arguments  # the arguments survive, not just the tool name


def test_tool_record_survives_unserialisable_arguments() -> None:
    # A tool called with something json can't encode must not take the whole run down.
    rec = tool_record("weird", {"fn": object()}, "ok")
    assert rec.name == "weird"
    assert rec.arguments  # some representation, not an exception


def test_context_peak_is_the_largest_prompt_not_the_sum() -> None:
    log = StepLog()
    log.add(StepRecord(index=1, prompt_tokens=1000, completion_tokens=10, model="m"))
    log.add(StepRecord(index=2, prompt_tokens=4000, completion_tokens=10, model="m"))
    log.add(StepRecord(index=3, prompt_tokens=2500, completion_tokens=10, model="m"))
    # The sum (7500) is a cost figure. The peak (4000) is what the context budget is spent against —
    # confusing the two is how you conclude a run is fine right until it dies.
    assert log.context_peak_tokens == 4000


def test_growth_per_step_answers_how_many_more_steps_fit() -> None:
    log = StepLog()
    for i, tokens in enumerate([1000, 3000, 5000], start=1):
        log.add(StepRecord(index=i, prompt_tokens=tokens, completion_tokens=0, model="m"))
    assert log.context_growth_per_step == 2000.0


def test_growth_ignores_steps_the_provider_did_not_measure() -> None:
    # Some providers omit usage on streamed calls; a 0 must not be read as "the context shrank".
    log = StepLog()
    log.add(StepRecord(index=1, prompt_tokens=1000, completion_tokens=0, model="m"))
    log.add(StepRecord(index=2, prompt_tokens=0, completion_tokens=0, model="m"))
    log.add(StepRecord(index=3, prompt_tokens=3000, completion_tokens=0, model="m"))
    assert log.context_growth_per_step == 2000.0


def test_growth_is_zero_with_too_little_data() -> None:
    log = StepLog()
    log.add(StepRecord(index=1, prompt_tokens=1000, completion_tokens=0, model="m"))
    assert log.context_growth_per_step == 0.0


def test_run_records_context_size_at_every_step() -> None:
    backend = _Backend([_tool_step(1000), _tool_step(2600)])
    agent = Agent(backend, _registry(), AgentConfig(max_steps=4))

    result = agent.run("do the thing")

    # Three model calls: two tool steps plus the final answer.
    assert [s.prompt_tokens for s in result.steplog.steps] == [1000, 2600, 900]
    assert result.steplog.context_peak_tokens == 2600


def test_run_records_what_each_tool_was_asked_and_answered() -> None:
    backend = _Backend([_tool_step(1000, text="hello world")])
    agent = Agent(backend, _registry(), AgentConfig(max_steps=3))

    result = agent.run("echo something")

    tools = [t for step in result.steplog.steps for t in step.tools]
    assert len(tools) == 1
    # This is the whole point: previously only ("echo", True) survived. "echo ✓" without the
    # arguments or the result is nearly zero information when a run goes wrong.
    assert tools[0].name == "echo"
    assert "hello world" in tools[0].arguments
    assert "hello world" in tools[0].observation
    assert tools[0].ok is True


def test_trace_writes_one_line_per_run(tmp_path: Path) -> None:
    log = StepLog()
    log.add(StepRecord(index=1, prompt_tokens=1200, completion_tokens=30, model="m"))
    log.steps[0].tools.append(tool_record("echo", {"text": "x"}, "x"))
    path = tmp_path / "traces" / "steps.jsonl"

    log.write(path, task="a task", stopped_reason="final")
    log.write(path, task="another", stopped_reason="max_steps")

    lines = path.read_text(encoding="utf-8").strip().split("\n")
    # One line per RUN, not per step: a run is the unit anyone reads back, and interleaving two
    # partial runs is worse than having no trace.
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["context_peak_tokens"] == 1200
    assert first["stopped_reason"] == "final"
    assert first["steps"][0]["tools"][0]["name"] == "echo"


# --- cache accounting -------------------------------------------------------------------------

def test_cache_hit_rate_is_a_share_of_the_prompt_not_of_the_steps() -> None:
    log = StepLog()
    log.add(StepRecord(index=1, prompt_tokens=1000, completion_tokens=10, cached_tokens=900, model="m"))
    log.add(StepRecord(index=2, prompt_tokens=1000, completion_tokens=10, cached_tokens=100, model="m"))
    # Averaging per-step rates would say 50%. What is billed is tokens, and half of these 2000 were
    # served from cache — which is the number that predicts the invoice.
    assert log.cache_hit_rate == 0.5


def test_a_silent_provider_is_not_a_cache_miss() -> None:
    log = StepLog()
    log.add(StepRecord(index=1, prompt_tokens=1000, completion_tokens=10, cached_tokens=None, model="m"))
    # Scoring silence as 0% would read as a broken prompt prefix and send someone off to fix a
    # cache that was never reported on in the first place.
    assert log.cache_hit_rate is None
    assert log.as_dict()["cache_hit_rate"] is None


def test_steps_without_a_cache_report_are_excluded_rather_than_counted_as_misses() -> None:
    log = StepLog()
    log.add(StepRecord(index=1, prompt_tokens=1000, completion_tokens=10, cached_tokens=800, model="m"))
    log.add(StepRecord(index=2, prompt_tokens=1000, completion_tokens=10, cached_tokens=None, model="m"))
    # Only the step that reported counts: 800/1000. Folding the silent step in would report 40% and
    # understate a cache that is working.
    assert log.cache_hit_rate == 0.8


# --- the trace actually reaching disk -----------------------------------------------------------

def test_the_loop_writes_its_trace_when_a_path_is_configured(tmp_path: Path) -> None:
    """A step log nothing ever persists is a measurement with no consumer.

    The writer existed from the start and had no caller: every number the loop took died with the
    process. `trace_path` is what connects them.
    """

    from chimera.core import Agent, AgentConfig
    from chimera.providers import CompletionResult
    from chimera.tools import ToolRegistry

    class _Backend:
        def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any) -> Any:
            return CompletionResult(
                content="done", model="fake", prompt_tokens=120, completion_tokens=8
            )

    trace = tmp_path / "traces.jsonl"
    agent = Agent(_Backend(), ToolRegistry(), AgentConfig(max_steps=3, trace_path=trace))
    agent.run("the task")

    line = json.loads(trace.read_text(encoding="utf-8").strip())
    assert line["task"] == "the task"
    assert line["stopped_reason"] == "final"
    assert line["context_peak_tokens"] == 120
    assert line["drift"]["assessed"] is False  # too short to say, and it says so


def test_no_trace_path_writes_nothing(tmp_path: Path) -> None:

    from chimera.core import Agent, AgentConfig
    from chimera.providers import CompletionResult
    from chimera.tools import ToolRegistry

    class _Backend:
        def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any) -> Any:
            return CompletionResult(content="done", model="fake", prompt_tokens=100)

    Agent(_Backend(), ToolRegistry(), AgentConfig(max_steps=2)).run("t")
    # Disk the caller did not ask for is not a default anyone should inherit.
    assert list(tmp_path.iterdir()) == []


def test_an_unwritable_trace_does_not_take_the_run_down(tmp_path: Path) -> None:

    from chimera.core import Agent, AgentConfig
    from chimera.providers import CompletionResult
    from chimera.tools import ToolRegistry

    class _Backend:
        def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any) -> Any:
            return CompletionResult(content="done", model="fake", prompt_tokens=100)

    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    agent = Agent(
        _Backend(), ToolRegistry(), AgentConfig(max_steps=2, trace_path=blocker / "traces.jsonl")
    )
    # The answer is the product; the trace is evidence about how it was reached. Losing the evidence
    # must never cost the work.
    assert agent.run("t").answer == "done"
