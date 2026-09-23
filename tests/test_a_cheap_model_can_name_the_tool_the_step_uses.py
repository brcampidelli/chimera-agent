"""The System One router: a cheap model names the tool, the executor is given that one tool.

Study 20 §3 B4, the experiment registered in `bench/tool_router/PREREGISTRATION.md`. What this file
holds is the part a bench cannot: that the router NARROWS rather than decides, that an undecided
router leaves the step exactly as it would have been, that its spend is charged to the run like any
other call, and that it reads a shallow context on purpose — which is the design choice the whole
measurement is about, and would otherwise be a comment nobody checks.
"""

from __future__ import annotations

from typing import Any

from chimera.core.tool_router import ANSWER, ToolRouter, narrow

SCHEMAS = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file."}},
    {"type": "function", "function": {"name": "write_file", "description": "Write a file."}},
    {"type": "function", "function": {"name": "run_shell", "description": "Run a shell command."}},
]


PRICED_MODEL = "openrouter/deepseek/deepseek-v4-flash-0731"
"""A model the catalogue prices, so the router's own cost is a number and not a zero."""


def _Result(content: str) -> Any:
    """The gateway's own result type, not a stand-in: the loop's usage tally reads fields a
    hand-rolled fake does not have (`cache_read_tokens` — found by this test), and a router whose
    result cannot be tallied would fail only in a paid run."""
    from chimera.providers.gateway import CompletionResult

    return CompletionResult(
        content=content, model=PRICED_MODEL, prompt_tokens=100_000, completion_tokens=10
    )


class _Backend:
    """Records what it was asked, and answers with a fixed script."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.seen: list[list[dict[str, Any]]] = []
        self.models: list[str] = []

    def complete(self, messages: list[dict[str, Any]], **kw: Any) -> _Result:
        self.seen.append(messages)
        self.models.append(str(kw.get("model")))
        return _Result(self.answers.pop(0) if self.answers else "read_file")


class _Raises:
    def complete(self, *a: Any, **k: Any) -> _Result:
        raise RuntimeError("the router's provider is down")


def test_a_named_tool_narrows_the_step_to_that_tool() -> None:
    router = ToolRouter(_Backend("write_file"), "cheap/model")
    pick = router.pick("write the file", [], SCHEMAS)
    assert pick == "write_file"
    narrowed = narrow(SCHEMAS, pick)
    assert narrowed is not None
    assert [s["function"]["name"] for s in narrowed] == ["write_file"]
    assert router.stats.narrowed == 1 and router.stats.fallbacks == 0


def test_answer_narrows_to_no_tools_at_all() -> None:
    # The router's other decision: the task is done, so the step is a reply, not a call. `None`
    # here is "no tools on the wire", which is what makes the executor answer instead of reaching.
    router = ToolRouter(_Backend(ANSWER), "cheap/model")
    pick = router.pick("done?", [], SCHEMAS)
    assert pick == ANSWER
    assert narrow(SCHEMAS, pick) is None
    assert router.stats.answered == 1


def test_an_undecided_router_leaves_the_step_exactly_as_it_was() -> None:
    """The safety property the whole design rests on. A router that names nothing recognisable must
    not narrow: the step then runs with every tool, which is the run WITHOUT a router — so a bad
    router costs money and makes no other difference. Counted, not hidden (§2r)."""
    router = ToolRouter(_Backend("I think we should consider the situation"), "cheap/model")
    assert router.pick("do something", [], SCHEMAS) is None
    assert router.stats.fallbacks == 1 and router.stats.narrowed == 0


def test_a_provider_failure_is_a_fallback_and_never_an_exception() -> None:
    router = ToolRouter(_Raises(), "cheap/model")
    assert router.pick("do something", [], SCHEMAS) is None
    assert router.stats.fallbacks == 1


def test_a_name_inside_a_sentence_is_still_a_decision_and_the_longest_name_wins() -> None:
    schemas = SCHEMAS + [
        {"type": "function", "function": {"name": "read_file_lines", "description": "A window."}}
    ]
    router = ToolRouter(_Backend("Use `read_file_lines` to see the region."), "cheap/model")
    assert router.pick("look", [], schemas) == "read_file_lines"


def test_the_router_reads_a_shallow_context_not_the_conversation() -> None:
    """The design choice the number is about. A router that re-reads the transcript costs what the
    call it precedes costs, and the hypothesis is about a fast, shallow decision — so the prompt
    carries the task, the LAST output and the tool menu, and nothing else."""
    backend = _Backend("run_shell")
    router = ToolRouter(backend, "cheap/model")
    messages = [
        {"role": "user", "content": "SECRET-EARLY-MESSAGE"},
        {"role": "assistant", "content": "middle of the conversation"},
        {"role": "tool", "content": "LAST-OUTPUT-HERE"},
    ]
    router.pick("the task", messages, SCHEMAS)
    sent = "\n".join(m["content"] for m in backend.seen[0])
    assert "LAST-OUTPUT-HERE" in sent
    assert "SECRET-EARLY-MESSAGE" not in sent
    assert "read_file" in sent and "run_shell" in sent  # the menu is there
    assert len(sent) < 4000  # shallow by construction, whatever the conversation grew to


def test_the_routers_spend_is_charged_to_the_run() -> None:
    """Pricing one arm's calls and not the other's would make the cost comparison meaningless —
    the router is a call, and a call is money."""
    charged: list[Any] = []

    class _Usage:
        def add(self, result: Any) -> None:
            charged.append(result)

    class _Spend:
        def record_result(self, result: Any) -> None:
            charged.append(result)

    router = ToolRouter(_Backend("read_file"), PRICED_MODEL)
    router.pick("read it", [], SCHEMAS, usage=_Usage(), spend=_Spend())
    assert len(charged) == 2
    # Priced through the repository's own pricer. `CompletionResult` carries tokens, not money:
    # reading a `usd` attribute (there is none) made this read 0.00 while the call really spent,
    # which would have made the router's arm cheap by construction. The test that caught it is
    # this one, and it is here so the defect cannot come back.
    assert router.stats.usd > 0.0


def test_the_loop_narrows_the_step_when_a_router_is_configured() -> None:
    """End to end through `Agent.run`: with a router that says `read_file`, the executor's call
    carries exactly one tool. Sabotage-verified by the next test, which removes the wiring."""
    from chimera.core import Agent, AgentConfig
    from chimera.tools import ToolRegistry
    from chimera.tools.base import Tool

    class _Echo(Tool):
        name = "read_file"
        description = "Read a UTF-8 text file."
        parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

        def run(self, **kwargs: Any) -> str:
            return "contents"

    class _Other(Tool):
        name = "run_shell"
        description = "Run a shell command."
        parameters = {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}

        def run(self, **kwargs: Any) -> str:
            return "ok"

    seen_tools: list[Any] = []

    class _Executor:
        def complete(self, messages: list[dict[str, Any]], **kw: Any) -> Any:
            from chimera.providers.gateway import CompletionResult

            seen_tools.append(kw.get("tools"))
            return CompletionResult(content="done", model="exec", prompt_tokens=1, completion_tokens=1)

    registry = ToolRegistry()
    registry.register(_Echo())
    registry.register(_Other())
    router = ToolRouter(_Backend("read_file"), "cheap/model")
    agent = Agent(_Executor(), registry, AgentConfig(model="exec", max_steps=1, tool_router=router))
    agent.run("read the file")
    assert seen_tools, "the executor was never called"
    names = [s["function"]["name"] for s in (seen_tools[0] or [])]
    assert names == ["read_file"], f"the step was not narrowed: {names}"
