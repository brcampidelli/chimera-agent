"""``RunState.tasks`` shipped with a docstring, a renderer, and no writer.

The field has existed since compaction was written. ``RunState.as_message`` renders it. Its comment
says what it is for — *"Task list with status, so finished work is not redone"* — and
``AutonomousAgent._seed_run_state`` says, in as many words, why that loop must not fill it: it
counts attempts, not steps, so it has no completion to report and inventing one would be *"a lie in
the field whose entire purpose is to be believed"*.

Both were right. What was missing is the only source that can truthfully say a step is finished,
which is the agent. These tests hold the tool that lets it say so, and the three properties that
make the answer worth having: the list reaches the field the restore path already reads, one run's
list cannot be written by another run's agent, and what the tool reports is labelled a claim
everywhere it travels.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from chimera.core import Agent, AgentConfig
from chimera.core.context_budget import RunState
from chimera.core.events import todo as todo_event
from chimera.providers import CompletionResult, ToolCall
from chimera.tools import ToolRegistry
from chimera.tools.builtin import EchoTool
from chimera.tools.todo import STATUSES, TodoItem, TodoWriteTool, render, schema_cost_chars


def _tool(**kw: Any) -> TodoWriteTool:
    return TodoWriteTool(RunState(), **kw)


def _three() -> list[dict[str, str]]:
    return [
        {"task": "read the schema", "status": "done"},
        {"task": "write the catalogue", "status": "doing"},
        {"task": "check the format", "status": "pending"},
    ]


# --- what the tool records -----------------------------------------------------------------------


def test_the_list_lands_in_the_field_the_restore_path_reads() -> None:
    """The whole point. `as_message` already renders `tasks`; nothing ever put anything there."""
    state = RunState()
    TodoWriteTool(state).run(items=_three())
    assert state.tasks == [
        "[done] read the schema",
        "[doing] write the catalogue",
        "[pending] check the format",
    ]


def test_a_compaction_restores_the_list_with_its_status() -> None:
    """Status is the reason the field exists: bare steps assert that none are done."""
    state = RunState()
    state.task = "build the catalogue"
    TodoWriteTool(state).run(items=_three())
    message = state.as_message()
    assert message is not None
    content = message["content"]
    assert "[done] read the schema" in content
    assert "[doing] write the catalogue" in content
    assert "[pending] check the format" in content


def test_the_observation_echoes_the_list_back() -> None:
    """The agent's only way to tell a call that landed from one the provider mangled."""
    out = _tool().run(items=_three())
    assert "1 done, 1 in progress, 1 pending" in out
    assert "[doing] write the catalogue" in out


def test_the_counts_in_the_observation_are_the_real_ones() -> None:
    out = _tool().run(items=[{"task": f"t{i}", "status": "done"} for i in range(4)])
    assert "4 done, 0 in progress, 0 pending" in out


def test_an_empty_list_is_recorded_rather_than_refused() -> None:
    """Clearing the list is a legitimate thing to say, and it must not read as an error."""
    tool = _tool()
    tool.run(items=_three())
    out = tool.run(items=[])
    assert not out.startswith("error:")
    assert tool.state.tasks == []


# --- the two rules that are enforced ---------------------------------------------------------------


def test_two_items_in_progress_is_refused() -> None:
    out = _tool().run(items=[{"task": "a", "status": "doing"}, {"task": "b", "status": "doing"}])
    assert out.startswith("error:")
    assert "a; b" in out, "the refusal has to name which ones, or the agent guesses"


def test_a_refused_call_leaves_the_previous_list_standing() -> None:
    """Refusing and half-applying are different. Half-applying is the failure mode this avoids."""
    tool = _tool()
    tool.run(items=_three())
    before = list(tool.state.tasks)
    tool.run(items=[{"task": "a", "status": "doing"}, {"task": "b", "status": "doing"}])
    assert tool.state.tasks == before


def test_one_item_in_progress_is_fine() -> None:
    assert not _tool().run(items=_three()).startswith("error:")


def test_items_that_left_the_list_are_named() -> None:
    """An agent that quietly drops a requirement renders identically to one that never had it."""
    tool = _tool()
    tool.run(items=_three())
    out = tool.run(items=[{"task": "read the schema", "status": "done"}])
    assert "No longer in the list" in out
    assert "write the catalogue" in out and "check the format" in out


def test_nothing_is_reported_as_dropped_when_nothing_dropped() -> None:
    tool = _tool()
    tool.run(items=_three())
    out = tool.run(items=_three())
    assert "No longer in the list" not in out


# --- input the model actually sends ----------------------------------------------------------------


@pytest.mark.parametrize(
    "items,fragment",
    [
        ("not a list", "must be a list"),
        (42, "must be a list"),
        ([{"task": "", "status": "done"}], "empty 'task'"),
        ([{"task": "a", "status": "finished"}], "status must be one of"),
        (["a string"], "must be an object"),
    ],
)
def test_malformed_input_is_refused_with_a_reason(items: Any, fragment: str) -> None:
    out = _tool().run(items=items)
    assert out.startswith("error:") and fragment in out


def test_a_json_string_of_items_is_parsed() -> None:
    """Some backends hand an array-valued argument back as its JSON text."""
    tool = _tool()
    assert not tool.run(items=json.dumps(_three())).startswith("error:")
    assert len(tool.state.tasks) == 3


def test_status_is_case_insensitive() -> None:
    tool = _tool()
    assert not tool.run(items=[{"task": "a", "status": "DONE"}]).startswith("error:")
    assert tool.state.tasks == ["[done] a"]


def test_every_declared_status_is_accepted() -> None:
    for status in STATUSES:
        assert not _tool().run(items=[{"task": "a", "status": status}]).startswith("error:")


# --- the sink ---------------------------------------------------------------------------------------


def test_the_sink_receives_the_structured_list() -> None:
    seen: list[list[TodoItem]] = []
    _tool(on_change=seen.append).run(items=_three())
    assert [i.status for i in seen[0]] == ["done", "doing", "pending"]


def test_a_raising_sink_does_not_lose_a_recorded_list() -> None:
    """The list is already committed by then; a broken renderer must not undo it."""

    def explode(_items: list[TodoItem]) -> None:
        raise RuntimeError("the UI fell over")

    tool = _tool(on_change=explode)
    assert not tool.run(items=_three()).startswith("error:")
    assert len(tool.state.tasks) == 3


def test_a_refused_call_does_not_reach_the_sink() -> None:
    seen: list[Any] = []
    _tool(on_change=seen.append).run(items=[{"task": "a", "status": "x"}])
    assert seen == []


# --- the event ---------------------------------------------------------------------------------------


def test_the_event_says_the_list_is_claimed_not_verified() -> None:
    """Every other structured frame reports an observation. This one reports what the model said."""
    event = todo_event(_three())
    assert event.kind == "todo"
    assert event.data["claimed"] is True


def test_the_event_carries_the_whole_list_and_the_counts() -> None:
    event = todo_event(_three())
    assert len(event.data["items"]) == 3
    assert (event.data["done"], event.data["doing"], event.data["pending"]) == (1, 1, 1)
    assert "1/3 done" in event.text


def test_the_event_copies_the_list_it_was_given() -> None:
    """Sent whole, so a consumer holding a frame must not watch it change under them."""
    items = _three()
    event = todo_event(items)
    items.clear()
    assert len(event.data["items"]) == 3


# --- wiring into the loop -------------------------------------------------------------------------


class ScriptedBackend:
    def __init__(self, responses: list[CompletionResult]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any) -> CompletionResult:
        self.calls.append({"tools": tools, "messages": list(messages)})
        if self._responses:
            return self._responses.pop(0)
        return CompletionResult(content="final answer", model="fake")


def _registry(*, todo: bool = True) -> ToolRegistry:
    """A registry shaped like the one the session hands the loop.

    `todo=False` stands for the case that matters: an operator who scoped the session to a tool set
    that does not name this one. `restrict_registry` produces exactly that, and the loop must run
    without rather than adding what it was not granted.
    """
    registry = ToolRegistry()
    registry.register(EchoTool())
    if todo:
        registry.register(TodoWriteTool())
    return registry


def _calling_backend() -> ScriptedBackend:
    return ScriptedBackend(
        [
            CompletionResult(
                content="",
                model="fake",
                tool_calls=[ToolCall(id="1", name="todo_write", arguments={"items": _three()})],
            )
        ]
    )


def test_the_real_default_registry_carries_it(tmp_path: Any) -> None:
    """The registry the surfaces actually build — not the hand-rolled one the rest of this file uses.

    Added because the sabotage matrix found nothing to fail when the registration in
    `builtin.default_registry` was removed: every other test here constructs its own registry, so
    the setting could have become a no-op and the suite would have stayed green.
    """
    from chimera.tools.builtin import default_registry

    assert "todo_write" in default_registry(tmp_path).names()


def test_the_default_registry_carries_it_so_the_allowlist_can_take_it_away() -> None:
    """Registered like every other tool, which is what puts it under the session's scoping."""
    from chimera.governance.allowlist import restrict_registry

    assert "todo_write" in _registry().names()
    scoped = restrict_registry(_registry(), allow=["echo"])
    assert "todo_write" not in scoped.names()


def test_a_session_that_was_not_granted_the_tool_runs_without_it() -> None:
    """Absent is an operator decision, not an error — and nothing must be bolted back on."""
    backend = ScriptedBackend([])
    agent = Agent(backend, _registry(todo=False), AgentConfig())
    agent.run("hello", on_todo=lambda _items: pytest.fail("nothing should announce"))
    names = [t["function"]["name"] for t in (backend.calls[0]["tools"] or [])]
    assert names == ["echo"]


def test_the_binding_reaches_through_the_governance_wrappers() -> None:
    """By the time the loop holds the registry, the tool is behind one or two wrappers."""

    class Wrapper:
        def __init__(self, inner: Any) -> None:
            self.inner = inner
            self.name = inner.name
            self.description = inner.description
            self.parameters = inner.parameters

        def run(self, **kwargs: Any) -> str:
            return self.inner.run(**kwargs)

        def to_openai_schema(self) -> dict[str, Any]:
            return self.inner.to_openai_schema()

    wrapped = ToolRegistry()
    wrapped.register(EchoTool())
    wrapped.register(Wrapper(Wrapper(TodoWriteTool())))  # type: ignore[arg-type]
    agent = Agent(_calling_backend(), wrapped, AgentConfig())
    agent.run("do the work")
    assert len(agent.run_state.tasks) == 3


def test_two_agents_on_one_registry_keep_separate_lists() -> None:
    """A crew runs several workers off ONE registry, concurrently, in separate threads."""
    import threading

    shared = _registry()
    first = Agent(_calling_backend(), shared, AgentConfig())
    second = Agent(_calling_backend(), shared, AgentConfig())
    barrier = threading.Barrier(2)

    def go(agent: Agent, label: str) -> None:
        barrier.wait()
        agent.run(label)

    threads = [
        threading.Thread(target=go, args=(first, "one")),
        threading.Thread(target=go, args=(second, "two")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(first.run_state.tasks) == 3
    assert len(second.run_state.tasks) == 3


def test_a_recorded_list_reaches_the_run_state_through_a_real_loop() -> None:
    agent = Agent(_calling_backend(), _registry(), AgentConfig())
    agent.run("do the work")
    assert agent.run_state.tasks[1] == "[doing] write the catalogue"


def test_the_sink_passed_to_run_receives_the_list() -> None:
    seen: list[list[dict[str, str]]] = []
    agent = Agent(_calling_backend(), _registry(), AgentConfig())
    agent.run("do the work", on_todo=seen.append)
    assert seen == [_three()]


def test_a_later_turn_without_a_sink_stops_announcing_into_the_old_one() -> None:
    """A queue belonging to turn 1 must not receive turn 2's frames.

    The regression this catches is binding only when a sink was passed: the tool then keeps the
    previous turn's `on_change`, and turn 2 announces into a queue whose reader has gone.
    """
    seen: list[Any] = []
    agent = Agent(_calling_backend(), _registry(), AgentConfig())
    agent.run("first", on_todo=seen.append)
    agent.backend = _calling_backend()  # type: ignore[assignment]
    agent.run("second")
    assert len(seen) == 1


# --- the price ---------------------------------------------------------------------------------------


def test_the_schema_cost_is_a_real_measurement() -> None:
    """Stated rather than assumed: this repo keeps `edit_batch` off over exactly this charge."""
    cost = schema_cost_chars()
    assert cost == len(
        json.dumps(
            {
                "name": TodoWriteTool.name,
                "description": TodoWriteTool.description,
                "parameters": TodoWriteTool.parameters,
            },
            separators=(",", ":"),
        )
    )
    assert 300 < cost < 1200, f"the schema moved to {cost} chars — reprice it, do not widen this"


def test_render_is_the_single_source_of_the_string_form() -> None:
    """`RunState.tasks` and the observation must not drift into two spellings of one list."""
    items = [TodoItem("a", "done"), TodoItem("b", "pending")]
    assert render(items) == ["[done] a", "[pending] b"]


# --- the sentence that makes the schema work ---------------------------------------------------


def test_the_prompt_mentions_the_tool_when_the_session_has_it() -> None:
    """Measured, not assumed: bare, two models called it zero times. One sentence changed that."""
    from chimera.core.agent import TODO_PROMPT

    backend = ScriptedBackend([])
    Agent(backend, _registry(), AgentConfig()).run("hello")
    assert TODO_PROMPT in backend.calls[0]["messages"][0]["content"]


def test_the_prompt_says_nothing_about_a_tool_the_session_withheld() -> None:
    """A sentence naming a tool the model was not given is an invitation to call nothing."""
    from chimera.core.agent import TODO_PROMPT

    backend = ScriptedBackend([])
    Agent(backend, _registry(todo=False), AgentConfig()).run("hello")
    assert TODO_PROMPT not in backend.calls[0]["messages"][0]["content"]
