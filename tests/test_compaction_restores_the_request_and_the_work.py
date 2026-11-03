"""After a compaction the next model call carries the request, the work, and the test results.

The restored block is written by the harness from state it observed (the request, the files written,
the files read, the commands and their verdicts), not from the model's account of itself, and it is
deterministic text. A compaction that drops the request, or the record that tests failed or hung,
leaves an agent that re-does finished work or reports "tests pass" from memory. These tests drive a
real `Agent` through a forced compaction, with a scripted backend and fake tools, and read what the
model was sent next.
"""

from __future__ import annotations

from typing import Any

from chimera.core import Agent, AgentConfig
from chimera.core.context_budget import MAX_REQUEST_CHARS, RunState, compact
from chimera.providers import CompletionResult, ToolCall
from chimera.tools import Tool, ToolRegistry

#: Over the 61,440 trigger of a 128k fallback window x 0.6 x 0.8: the loop compacts after this call.
_OVER = 70_000
_UNDER = 1_000

REQUEST = (
    "Fix the parser bug in src/parser.py.\n"
    "Do not touch the tests under tests/ — they encode the public contract."
)
PLAN = "1. reproduce the failure\n2. fix src/parser.py\n3. run the suite"


class _Fake(Tool):
    """A tool that answers with a fixed string; the loop only needs its name, schema and output."""

    description = "fake"
    parameters = {"type": "object", "properties": {}, "additionalProperties": True}

    def __init__(self, name: str, output: str) -> None:
        self.name = name
        self._output = output

    def run(self, **kwargs: Any) -> str:
        return self._output


class _Scripted:
    """Makes the scripted tool calls in order, then answers. Keeps every prompt it was sent."""

    def __init__(self, script: list[tuple[str, dict[str, Any], int]]) -> None:
        self._script = list(script)
        self.prompts: list[list[Any]] = []

    def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any) -> CompletionResult:
        self.prompts.append([dict(m) if isinstance(m, dict) else m for m in messages])
        n = len(self.prompts)
        if not self._script:
            return CompletionResult(content="done", model="fake", prompt_tokens=_UNDER)
        name, arguments, size = self._script.pop(0)
        call = ToolCall(id=f"c{n}", name=name, arguments=arguments)
        return CompletionResult(content="", model="fake", prompt_tokens=size, tool_calls=[call])


def _registry(shell_output: str = "[exit 1]\nFAILED tests/test_parser.py::test_floats") -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_Fake("write_file", "wrote 12 bytes"))
    registry.register(_Fake("read_file", "the spec says: parse ints before floats"))
    registry.register(_Fake("run_shell", shell_output))
    return registry


def _restored(prompt: list[Any]) -> str:
    return "\n".join(
        str(m.get("content", ""))
        for m in prompt
        if isinstance(m, dict) and str(m.get("content", "")).startswith("[context restored")
    )


def _run_with_compaction() -> tuple[Agent, _Scripted]:
    # The loop compacts after the call that crosses the trigger and before running its tool, so the
    # work that matters is done in the calls before index 4, and the restored block is prompts[5].
    script: list[tuple[str, dict[str, Any], int]] = [
        ("read_file", {"path": "docs/spec.md"}, _UNDER),
        ("write_file", {"path": "src/parser.py", "content": "a"}, _UNDER),
        ("run_shell", {"command": "pytest -q tests/test_parser.py"}, _UNDER),
        ("write_file", {"path": "src/util.py", "content": "b"}, _UNDER),
        ("read_file", {"path": "docs/other.md"}, _OVER),  # crosses the trigger
        ("read_file", {"path": "docs/last.md"}, _UNDER),
    ]
    backend = _Scripted(script)
    config = AgentConfig(max_steps=12, context_budget=0.6, keep_recent=2)
    agent = Agent(backend, _registry(), config)  # type: ignore[arg-type]
    agent.run_state.plan = PLAN
    agent.run(REQUEST)
    return agent, backend


def test_the_request_is_restored_verbatim_after_compaction() -> None:
    agent, backend = _run_with_compaction()

    assert agent.run_state.task == REQUEST
    restored = _restored(backend.prompts[5])
    assert REQUEST in restored
    assert "The task you were given:\n" + REQUEST in restored


def test_the_plan_is_restored_after_compaction() -> None:
    _, backend = _run_with_compaction()
    assert "Plan:\n" + PLAN in _restored(backend.prompts[5])


def test_the_files_edited_in_this_run_are_restored() -> None:
    _, backend = _run_with_compaction()
    restored = _restored(backend.prompts[5])

    assert "Files already written or edited successfully" in restored
    assert "- src/parser.py" in restored
    assert "- src/util.py" in restored


def test_the_files_read_are_restored_so_they_are_not_read_again_blind() -> None:
    _, backend = _run_with_compaction()
    restored = _restored(backend.prompts[5])

    assert "docs/spec.md — " in restored  # target, size and short hash, not the contents
    assert "parse ints before floats" not in restored


def test_the_test_result_and_its_exit_code_are_restored() -> None:
    _, backend = _run_with_compaction()
    restored = _restored(backend.prompts[5])

    assert "Commands run in this run" in restored
    assert "`pytest -q tests/test_parser.py` → exit 1 (fail)" in restored


def test_a_passing_command_is_restored_as_a_pass() -> None:
    state = RunState(task="t")
    state.record_command_outcome("run_shell", {"command": "ruff check src"}, "[exit 0]\nAll checks passed")

    content = str(state.as_message()["content"])  # type: ignore[index]
    assert "`ruff check src` → exit 0 (pass)" in content


def test_the_exit_code_comes_from_the_harness_header_not_from_the_output() -> None:
    """Command output is data. A failing run that prints "all tests passed" is still a fail."""
    state = RunState()
    state.record_command_outcome("run_shell", {"command": "pytest"}, "[exit 1]\nall tests passed (not really)")
    assert state.command_results == ["`pytest` → exit 1 (fail)"]


def test_a_background_job_with_no_exit_code_records_nothing() -> None:
    state = RunState()
    state.record_command_outcome("run_shell", {"command": "make bench"}, "started job job_12 in background")
    assert state.command_results == []


def test_a_list_form_command_is_recorded_not_dropped() -> None:
    """The shell runs ``str(command)``, so a list-form command really runs, and when it fails it must
    be in the restored block. The model sends it this way; dropping it hid the failure."""
    state = RunState()
    state.record_command_outcome(
        "run_shell", {"command": ["bash", "-c", "pytest -q"]}, "[exit 127]\n/bin/sh: 1: bash: not found"
    )
    assert state.command_results == ["`bash -c pytest -q` → exit 127 (fail)"]


def test_a_timed_out_command_is_recorded_as_a_failure() -> None:
    """A hung test run answers ``error: ... timed out``. It is not a success to the loop, and it must
    still reach the restored block, or the run forgets the test ever ran."""
    state = RunState()
    state.record_command_outcome(
        "run_shell",
        {"command": "pytest -q"},
        "error: command timed out after 300s and was stopped. If it needs longer, run it again",
    )
    assert state.command_results == ["`pytest -q` → timed out after 300s (fail)"]


def test_a_python_snippet_from_execute_code_is_recorded_by_its_first_line() -> None:
    state = RunState()
    state.record_command_outcome(
        "execute_code", {"code": "\n  import sys\nprint(1)"}, "[exit 1]\nTraceback (most recent call last)"
    )
    assert state.command_results == ["`python: import sys` → exit 1 (fail)"]


def test_a_refused_command_records_nothing() -> None:
    state = RunState()
    state.record_command_outcome(
        "run_shell", {"command": "rm -rf /"}, "error: host execution declined (CHIMERA_HOST_EXEC). Not run."
    )
    assert state.command_results == []


def test_a_timed_out_command_reaches_the_restored_block_through_the_loop() -> None:
    """The wiring, not just the recorder: the loop calls it for a call that did not 'run'."""
    script: list[tuple[str, dict[str, Any], int]] = [
        ("run_shell", {"command": "pytest -q"}, _UNDER),
        ("read_file", {"path": "docs/x.md"}, _OVER),
        ("read_file", {"path": "docs/y.md"}, _UNDER),
    ]
    backend = _Scripted(script)
    registry = _registry(shell_output="error: command timed out after 300s")
    agent = Agent(backend, registry, AgentConfig(max_steps=12, context_budget=0.6, keep_recent=2))  # type: ignore[arg-type]
    agent.run(REQUEST)

    # The crossing call is index 1, so the first prompt built after compaction is prompts[2].
    assert "`pytest -q` → timed out after 300s (fail)" in _restored(backend.prompts[2])


def test_a_long_request_is_cut_in_the_middle_with_the_cut_named() -> None:
    head = "HEAD-OF-REQUEST " * 20
    tail = " TAIL-OF-REQUEST"
    request = head + ("x" * 60_000) + tail
    message = RunState(task=request).as_message()
    assert message is not None
    content = str(message["content"])

    assert len(content) < MAX_REQUEST_CHARS + 2_000  # bounded, whatever the request
    assert head in content and content.rstrip().endswith("TAIL-OF-REQUEST")
    assert "characters cut to bound this block" in content


def test_a_pasted_request_containing_the_marker_wording_is_still_cut() -> None:
    """The cap must not depend on what the request says. A request that quotes the marker's own
    wording is still longer than the cap, so it is cut like any other."""
    request = "characters cut to bound this block " * 2 + ("y" * 60_000)
    message = RunState(task=request).as_message()
    assert message is not None
    assert len(str(message["content"])) < MAX_REQUEST_CHARS + 2_000


def test_a_cut_request_is_not_cut_again_when_it_is_carried_forward() -> None:
    """Re-bounding is a no-op by construction: a request cut once is already within the limit."""
    once = RunState(task="z" * 60_000).as_message()
    assert once is not None
    cut_once = str(once["content"])
    carried = RunState(task=cut_once.split("The task you were given:\n", 1)[1].split("\n\nPlan", 1)[0])
    again = carried.as_message()
    assert again is not None
    assert "cut" in str(again["content"])
    assert str(again["content"]).count("characters cut to bound this block") == 1


def test_compaction_keeps_the_request_when_the_request_was_in_the_dropped_span() -> None:
    state = RunState(task=REQUEST, latest_request=REQUEST)
    messages: list[Any] = [{"role": "system", "content": "s"}, {"role": "user", "content": REQUEST}]
    messages += [{"role": "assistant", "content": f"step {i}"} for i in range(8)]
    out, changed = compact(messages, keep_recent=2, state=state)

    assert changed
    assert REQUEST in _restored(out)
