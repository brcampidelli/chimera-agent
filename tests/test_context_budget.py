"""Context as a budget, and compaction that keeps the run knowing what it was doing.

Before this the message list only grew and an overflow was terminal (CONTEXT_OVERFLOW -> ABORT),
which made the window — not the difficulty of the task — the real ceiling on the agent.
"""

from __future__ import annotations

from typing import Any

from chimera.core import Agent, AgentConfig
from chimera.core.context_budget import (
    ContextBudget,
    RunState,
    compact,
    estimate_tokens,
    window_tokens,
)
from chimera.providers import CompletionResult, ToolCall
from chimera.tools import ToolRegistry
from chimera.tools.builtin import EchoTool


def _msgs(n: int, role: str = "user") -> list[Any]:
    return [{"role": role, "content": f"message {i}"} for i in range(n)]


# --- the budget ------------------------------------------------------------------------------

def test_budget_is_spent_below_the_advertised_window() -> None:
    budget = ContextBudget(window=200_000, fraction=0.6)
    # The remainder is not waste: it covers the completion plus the gap between our estimate and
    # the provider's real count. A budget equal to the window has no room to be wrong in.
    assert budget.budget == 120_000


def test_trigger_fires_on_the_budget_not_the_window() -> None:
    budget = ContextBudget(window=200_000, fraction=0.6, trigger=0.8)
    assert budget.threshold == 96_000
    # Firing at the window would be firing too late — compaction itself needs room to work.
    assert budget.threshold < budget.window
    assert budget.should_compact(96_000) is True
    assert budget.should_compact(95_999) is False


def test_headroom_goes_negative_after_the_trigger() -> None:
    budget = ContextBudget(window=100_000, fraction=0.5, trigger=0.8)  # threshold 40_000
    assert budget.headroom(30_000) == 10_000
    assert budget.headroom(45_000) == -5_000


def test_unknown_model_falls_back_rather_than_guessing_big() -> None:
    # Under-estimating costs one unnecessary compaction; over-estimating costs the whole run.
    assert window_tokens("some/model-nobody-has-heard-of") == 128_000
    assert window_tokens("") == 128_000


def test_estimate_counts_tool_call_payloads_too() -> None:
    # Tool-call arguments ride on the assistant message and are NOT in `content`; ignoring them
    # under-counts exactly the messages that grow a coding run.
    plain = [{"role": "assistant", "content": "x" * 400}]
    with_calls = [{"role": "assistant", "content": "x" * 400, "tool_calls": [{"a": "y" * 400}]}]
    assert estimate_tokens(with_calls) > estimate_tokens(plain)


# --- compaction ------------------------------------------------------------------------------

def test_compaction_never_touches_the_system_message() -> None:
    messages = [{"role": "system", "content": "the rules"}, *_msgs(20)]
    out, changed = compact(messages, keep_recent=4)
    assert changed is True
    # The system message is the stable prefix the whole prompt cache is keyed on. Rewriting it
    # invalidates every cached turn behind it — a 10x price difference on the next call.
    assert out[0] == {"role": "system", "content": "the rules"}


def test_compaction_keeps_the_recent_tail_verbatim() -> None:
    messages = [{"role": "system", "content": "sys"}, *_msgs(20)]
    out, _ = compact(messages, keep_recent=4)
    assert out[-4:] == messages[-4:]


def test_compaction_is_a_noop_when_there_is_nothing_to_drop() -> None:
    messages = [{"role": "system", "content": "sys"}, *_msgs(3)]
    out, changed = compact(messages, keep_recent=6)
    # A no-op must report itself as one, so a caller does not retry into the same wall believing
    # it just freed space.
    assert changed is False
    assert out == messages


def test_tail_never_starts_on_an_orphaned_tool_result() -> None:
    # The failure this prevents is a hard provider error, not a quality issue: a `tool` message
    # whose matching assistant tool_call was compacted away is a malformed prompt.
    messages = [
        {"role": "system", "content": "sys"},
        *_msgs(10),
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
        {"role": "user", "content": "next"},
    ]
    out, changed = compact(messages, keep_recent=2)
    assert changed is True
    body = [m for m in out if m.get("role") != "system"]
    assert body[0]["role"] != "tool"
    for i, message in enumerate(body):
        if message.get("role") == "tool":
            assert body[i - 1].get("tool_calls"), "a tool result must follow its assistant call"


def test_the_default_note_describes_what_was_dropped_without_inventing_it() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "read_file"}}, {"function": {"name": "grep"}},
        ]},
        *_msgs(10),
    ]
    out, _ = compact(messages, keep_recent=3)
    note = out[1]["content"]
    # Claiming to preserve content nobody read would be worse than saying plainly what happened.
    # The agent can re-read a file; it cannot un-believe a fabricated summary.
    assert "read_file" in note and "grep" in note
    assert "Re-read" in note


def test_a_supplied_summariser_replaces_the_structural_note() -> None:
    messages = [{"role": "system", "content": "sys"}, *_msgs(20)]
    out, _ = compact(messages, keep_recent=4, summarise=lambda older: f"summary of {len(older)}")
    assert "summary of" in out[1]["content"]


# --- active restoration ----------------------------------------------------------------------

def test_restoration_reinjects_what_the_run_needs_to_still_be_itself() -> None:
    state = RunState(
        open_file=("src/auth.py", "def login(): ..."),
        plan="1. fix login\n2. add test",
        tasks=["[x] read the file", "[ ] write the fix"],
        current_state="Found the bug in login(); about to patch it.",
    )
    messages = [{"role": "system", "content": "sys"}, *_msgs(20)]
    out, _ = compact(messages, keep_recent=4, state=state)

    restored = out[2]["content"]
    # Compressing is the easy half. An agent that compacted and no longer knows which file it was
    # editing keeps working — confidently — on the wrong thing.
    assert "src/auth.py" in restored
    assert "def login()" in restored
    assert "fix login" in restored
    assert "[ ] write the fix" in restored
    assert "about to patch it" in restored


def test_empty_state_adds_no_message() -> None:
    messages = [{"role": "system", "content": "sys"}, *_msgs(20)]
    out, _ = compact(messages, keep_recent=4, state=RunState())
    # No plan, no open file, nothing to restore — do not spend tokens on an empty header.
    assert "context restored" not in out[1]["content"]
    assert len(out) == 1 + 1 + 4


# --- the loop --------------------------------------------------------------------------------

class _GrowingBackend:
    """Reports a prompt that grows past the trigger, like a real run accumulating tool output."""

    def __init__(self, sizes: list[int]) -> None:
        self._sizes = list(sizes)

    def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any) -> CompletionResult:
        size = self._sizes.pop(0) if self._sizes else 100
        if self._sizes:
            return CompletionResult(
                content="", model="fake", prompt_tokens=size,
                tool_calls=[ToolCall(id="c", name="echo", arguments={"text": "x"})],
            )
        return CompletionResult(content="done", model="fake", prompt_tokens=size)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry


def test_loop_does_not_compact_when_no_budget_is_configured() -> None:
    # The historical behaviour is the default: compaction discards messages, and a caller that has
    # not asked for that must not silently get it.
    backend = _GrowingBackend([500_000, 500_000, 500_000])
    agent = Agent(backend, _registry(), AgentConfig(max_steps=4))
    result = agent.run("task")
    assert result.steplog.compactions == 0


def test_loop_compacts_once_the_prompt_crosses_the_trigger() -> None:
    # 128k fallback window * 0.6 budget * 0.8 trigger = 61_440.
    backend = _GrowingBackend([10_000, 70_000, 70_000, 500])
    agent = Agent(
        backend, _registry(),
        AgentConfig(max_steps=6, context_budget=0.6, keep_recent=2),
    )
    result = agent.run("a long task")

    assert result.steplog.compactions >= 1
    # And the step where history was dropped is marked — the first thing to check when an agent
    # starts contradicting its earlier self.
    assert any(s.compacted for s in result.steplog.steps)
    assert result.answer == "done"
