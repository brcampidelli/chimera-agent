"""What a delegated sub-agent spends reaches the bill of the run that delegated to it.

`SubAgentTool` (`chimera solve --subagents`) and `ResearchWebTool` (`CHIMERA_RESEARCH_AGENT`) each
run a whole `Agent` of their own inside one tool call, and returned only its answer: the turn was
billed for its own steps and not for the sub-agent's, and its dollar ceiling never saw them. The
explorer had the same hole and closed it with `enclosing_run()` and `add_nested()`; these two use
the same pair. Standing alone, outside any run, each keeps what its last delegation spent on
`last_spend`, so a caller can read it: there is no run to put it on.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

import chimera.fusion.receipts as receipts
from chimera.core import Agent, AgentConfig, SubAgentTool
from chimera.core.research import RESEARCH_SYSTEM, ResearchWebTool
from chimera.core.subagent import SUBAGENT_SYSTEM
from chimera.fusion.receipts import ModelPrice
from chimera.providers.gateway import CompletionResult, ToolCall
from chimera.tools import ToolRegistry
from chimera.tools.base import Tool
from chimera.tools.builtin import EchoTool

OUTER_MODEL = "test/outer-free"  # priced at a known zero, so a total is the sub-agent's alone
SUB_MODEL = "test/sub-priced"  # $3/M in, $4/M out
#: 2,000 prompt tokens at $3/M plus 200 completion tokens at $4/M: one answering sub-agent call.
SUB_COST = 0.0068
#: 1,000 prompt tokens at $3/M plus 100 completion tokens at $4/M: one searching call.
SEARCH_COST = 0.0034
PAGE = "https://example.org/mercury"


@pytest.fixture(autouse=True)
def _priced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exact prices for the fake models, restored afterwards. The shipped catalogue is folded in
    first, so the restore cannot leave the table without it."""
    receipts._ensure_catalog_registered()
    monkeypatch.setattr(receipts, "_PRICES", [
        (OUTER_MODEL, ModelPrice(0.0, 0.0)), (SUB_MODEL, ModelPrice(3.0, 4.0)),
        *receipts._PRICES,
    ])


class _Page(Tool):
    """`http_get` over one page, so the research sub-agent has a web to read."""

    name = "http_get"
    description = "fetch a page"
    parameters: dict[str, Any] = {"type": "object", "properties": {"url": {"type": "string"}}}

    def run(self, **kwargs: Any) -> str:
        return f"[200] {kwargs.get('url')}\nMercury is the smallest planet."


#: The two tools under test: the system prompt that marks their sub-agent's calls, the call the
#: outer loop makes to them, the call their sub-agent makes to search, and how each is built.
KINDS: dict[str, dict[str, Any]] = {
    "subagent": {
        "system": SUBAGENT_SYSTEM,
        "call": ToolCall(id="d1", name="spawn_subagent", arguments={"task": "sum the numbers"}),
        "search": ToolCall(id="s1", name="echo", arguments={"text": "look"}),
        "source": EchoTool,
        "tool": lambda backend, source: SubAgentTool(backend, source, max_turns=3),
        "standalone": {"task": "sum the numbers"},
    },
    "research": {
        "system": RESEARCH_SYSTEM,
        "call": ToolCall(id="d1", name="research_web", arguments={"question": "smallest planet?"}),
        "search": ToolCall(id="s1", name="http_get", arguments={"url": PAGE}),
        "source": _Page,
        "tool": lambda backend, source: ResearchWebTool(backend, source, max_turns=3),
        "standalone": {"question": "smallest planet?"},
    },
}


class _Model:
    """The outer loop delegates once, then answers; the sub-agent follows ``script``.

    Each entry of ``script`` is one sub-agent call: ``"answer"`` ends it, ``"search"`` is a paid
    tool call, ``"boom"`` raises the way a provider does.
    """

    def __init__(self, kind: str, script: list[str], *, sub_model: str = SUB_MODEL,
                 outer_tokens: int = 10) -> None:
        self.kind = KINDS[kind]
        self.script = list(script)
        self.sub_model = sub_model
        self.outer_tokens = outer_tokens
        self.sub_calls = 0
        self.outer_calls = 0

    def complete(self, messages: list[Any], **_k: Any) -> CompletionResult:
        first = messages[0]
        system = str(first.get("content") if isinstance(first, dict) else first.content)
        if self.kind["system"][:60] in system:
            self.sub_calls += 1
            step = self.script.pop(0) if self.script else "answer"
            if step == "boom":
                raise RuntimeError("the provider fell over")
            if step == "search":
                return CompletionResult(content="", model=self.sub_model, prompt_tokens=1000,
                                        completion_tokens=100, tool_calls=[self.kind["search"]])
            return CompletionResult(content=f"Mercury. Source: {PAGE}", model=self.sub_model,
                                    prompt_tokens=2000, completion_tokens=200)
        self.outer_calls += 1
        if self.outer_calls == 1:
            return CompletionResult(content="", model=OUTER_MODEL, prompt_tokens=self.outer_tokens,
                                    tool_calls=[self.kind["call"]])
        return CompletionResult(content="done", model=OUTER_MODEL, prompt_tokens=self.outer_tokens)


def _registry(kind: str, backend: _Model) -> tuple[ToolRegistry, Tool]:
    registry = ToolRegistry()
    registry.register(KINDS[kind]["source"]())
    tool: Tool = KINDS[kind]["tool"](backend, lambda: registry)
    registry.register(tool)
    return registry, tool


def _turn(kind: str, backend: _Model, **config: Any) -> Any:
    registry, _ = _registry(kind, backend)
    return Agent(backend, registry, AgentConfig(max_steps=4, **config)).run("do the task")


both: Callable[..., Any] = pytest.mark.parametrize("kind", list(KINDS))


@both
def test_inside_a_turn_the_sub_agent_is_in_the_turns_tokens_and_price(kind: str) -> None:
    backend = _Model(kind, ["answer"])

    result = _turn(kind, backend)

    assert backend.sub_calls == 1 and result.stopped_reason == "final"
    assert result.prompt_tokens == 20 + 2000
    assert result.completion_tokens == 200
    assert result.usd == pytest.approx(SUB_COST)


@both
def test_the_sub_agents_spend_counts_against_the_turns_ceiling(kind: str) -> None:
    """$0.0068 of a $0.005 ceiling: the step after the delegation is refused, as after any call."""
    backend = _Model(kind, ["answer"])

    result = _turn(kind, backend, max_usd=0.005)

    assert backend.sub_calls == 1
    assert result.stopped_reason == "spend"


@both
def test_a_sub_agent_under_a_ceiling_already_reached_makes_no_call(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checked before each of its calls too, so no money is spent to discover the turn was over."""
    monkeypatch.setattr(receipts, "_PRICES", [(OUTER_MODEL, ModelPrice(1.0, 1.0)),
                                              *receipts._PRICES])
    backend = _Model(kind, ["answer"], outer_tokens=10_000)

    result = _turn(kind, backend, max_usd=0.005)

    assert backend.sub_calls == 0
    assert result.stopped_reason == "spend"


@both
def test_a_sub_agent_that_died_after_paying_still_bills_what_it_paid(kind: str) -> None:
    backend = _Model(kind, ["search", "boom"])

    result = _turn(kind, backend)

    assert backend.sub_calls == 2
    assert result.prompt_tokens == 20 + 1000
    assert result.usd == pytest.approx(SEARCH_COST)


@both
def test_an_unpriced_sub_agent_makes_the_turns_price_unknown(kind: str) -> None:
    backend = _Model(kind, ["answer"], sub_model="test/no-price-anywhere")

    result = _turn(kind, backend)

    assert result.usd is None, "an unpriced call must never read as a low total"


@both
def test_standing_alone_the_tool_keeps_what_it_spent_and_answers_as_before(kind: str) -> None:
    backend = _Model(kind, ["answer"])
    _, tool = _registry(kind, backend)

    out = tool.run(**KINDS[kind]["standalone"])

    assert out.startswith(f"Mercury. Source: {PAGE}")
    spent = tool.last_spend  # type: ignore[attr-defined]
    assert (spent.prompt_tokens, spent.completion_tokens) == (2000, 200)
    assert spent.usd == pytest.approx(SUB_COST)


def test_a_research_with_no_web_tool_costs_a_known_nothing() -> None:
    """Refused before any call: nothing was spent, and nothing may turn the turn's price unknown."""
    backend = _Model("research", ["answer"])
    registry = ToolRegistry()
    registry.register(ResearchWebTool(backend, lambda: ToolRegistry(), max_turns=3))

    result = Agent(backend, registry, AgentConfig(max_steps=4)).run("do the task")

    assert backend.sub_calls == 0
    assert result.usd == 0.0
