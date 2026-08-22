"""A turn the spend cap stopped still has to say what the money went on.

The cap itself is honest — it reports the overshoot to the cent and the spend is recorded. What it
lost was the name: the budget branch reports ``self.config.model``, and a caller that did not name
one (the desktop does not — the gateway resolves the default) leaves that empty. So the run arrives
in the cost breakdown as a nameless row carrying real dollars, and "what did I spend this on" is
answered with a blank.

The comment defending it read: *the call that would have named one is the call that did not
happen*. True of the refused call, and beside the point — a run only reaches its ceiling by making
calls that DID happen, each answered by a model whose name the step log already has.
"""

from __future__ import annotations

from typing import Any

from chimera.core.agent import Agent, AgentConfig
from chimera.providers.gateway import ToolCall
from chimera.tools.registry import Tool, ToolRegistry


class _Result:
    """The shape ``Agent._step`` reads off a completion."""

    def __init__(self, content: str, model: str, prompt: int = 4000, completion: int = 400) -> None:
        self.content = content
        self.model = model
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.tool_calls: list[Any] = []
        self.finish_reason = "stop"
        self.route_meta: dict[str, Any] | None = None


class _Ping(Tool):
    """Keeps the loop going, so the run ends on the cap rather than on an answer."""

    name = "ping"
    description = "does nothing"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def run(self, **kwargs: Any) -> str:
        return "pong"


class _Backend:
    """Answers on a real, priced model — the one whose name must survive to the receipt."""

    ANSWERS_AS = "openrouter/deepseek/deepseek-chat"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Any], **kwargs: Any) -> _Result:
        self.calls += 1
        result = _Result(f"answer {self.calls}", self.ANSWERS_AS)
        result.tool_calls = [ToolCall(id=f"c{self.calls}", name="ping", arguments={})]
        return result


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_Ping())
    return registry


def _capped_run(model: str) -> Any:
    """One run against a ceiling too small to survive the first exchange."""
    agent = Agent(
        _Backend(),
        _registry(),
        AgentConfig(model=model, max_usd=0.002, max_steps=8),
    )
    return agent.run("do the thing")


def test_a_capped_run_names_the_model_that_answered_when_the_caller_named_none() -> None:
    # The desktop's own shape: `model` left empty so the gateway picks. This is the case that
    # produced a blank row with real dollars against it.
    result = _capped_run("")

    assert result.stopped_reason == "budget"
    assert result.usd and result.usd > 0, "a run that hit the ceiling must have spent something"
    assert result.model == _Backend.ANSWERS_AS


def test_it_still_prefers_the_model_the_caller_actually_asked_for() -> None:
    # When the caller DID name one, that name is the honest answer even if a failover answered:
    # this is the turn the user configured, and the per-call prices are already attributed
    # per-model in the tally. Without this case the fix could be "always use the last step" and
    # nothing would notice.
    result = _capped_run("openrouter/anthropic/claude-opus-5")

    assert result.stopped_reason == "budget"
    assert result.model == "openrouter/anthropic/claude-opus-5"
