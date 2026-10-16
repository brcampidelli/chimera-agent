"""The live context frame: sent when the measure moves a point, and never as a guess.

The coding screen draws "Context 30%" while a turn runs. The agent loop already reads the
provider's prompt count every step (it is what compaction decides on), so the frame costs nothing
to measure — the risk is in how often and as what it is sent:

- one frame per step would redraw the same percentage a hundred times in a long turn, so the loop
  sends one only when the number moved at least one percentage point (or, with no window, one in a
  hundred of the last count shown);
- the budget's window falls back to a flat 128,000 for a model nobody knows, and a percentage of
  that fallback reads exactly like one of a measured window — so the frame carries the window only
  when something KNOWS it (`known_window_tokens`), and the screen shows plain tokens otherwise;
- it travels on its own channel, not as a `notice`: every chat bot appends each notice to its reply,
  and "context usage" under every Discord answer would be the price of a progress bar.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.core import Agent, AgentConfig, context_budget
from chimera.core.agent import context_report_due
from chimera.providers import CompletionResult, ToolCall
from chimera.tools import ToolRegistry
from chimera.tools.builtin import EchoTool


class _SizedBackend:
    """Reports the given prompt sizes, one per step, calling a tool until the last one."""

    def __init__(self, sizes: list[int]) -> None:
        self._sizes = list(sizes)
        self._step = 0

    def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any) -> CompletionResult:
        size = self._sizes.pop(0) if self._sizes else 0
        self._step += 1
        if self._sizes:
            # A different argument each step: the same call repeated is what the loop breaker
            # stops, and this test is about the frame, not about loops.
            return CompletionResult(
                content="", model="fake", prompt_tokens=size,
                tool_calls=[ToolCall(id=f"c{self._step}", name="echo", arguments={"text": f"x{self._step}"})],
            )
        return CompletionResult(content="done", model="fake", prompt_tokens=size)


def _agent(sizes: list[int], model: str = "") -> Agent:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return Agent(_SizedBackend(sizes), registry, AgentConfig(max_steps=20, model=model))


def _run(agent: Agent, **kwargs: Any) -> list[tuple[int, int | None]]:
    seen: list[tuple[int, int | None]] = []
    agent.run("task", on_context=lambda used, window: seen.append((used, window)), **kwargs)
    return seen


def test_with_a_known_window_a_frame_goes_only_when_the_percentage_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(context_budget, "known_window_tokens", lambda _model: 10_000)
    seen = _run(_agent([3_000, 3_010, 3_050, 3_100, 3_150, 3_200]))
    # 3,010 and 3,050 are under a point from 3,000; 3,150 is under a point from 3,100.
    assert seen == [(3_000, 10_000), (3_100, 10_000), (3_200, 10_000)]


def test_six_steps_at_the_same_size_send_one_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(context_budget, "known_window_tokens", lambda _model: 10_000)
    assert _run(_agent([4_000] * 6)) == [(4_000, 10_000)]


def test_a_compacted_prompt_reports_its_drop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(context_budget, "known_window_tokens", lambda _model: 10_000)
    assert _run(_agent([8_000, 2_000])) == [(8_000, 10_000), (2_000, 10_000)]


def test_without_a_known_window_the_frame_carries_tokens_and_no_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(context_budget, "known_window_tokens", lambda _model: None)
    # 3,020 is under one in a hundred of 3,000; 3,030 is not.
    assert _run(_agent([3_000, 3_020, 3_030])) == [(3_000, None), (3_030, None)]


def test_the_fallback_window_is_not_reported_as_a_known_one(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.providers import listing

    monkeypatch.setattr(listing, "known_window", lambda _slug: None)
    slug = "nobody/has-measured-this-model-9"
    # The budget still needs a number and gets the fallback; the screen gets "unknown".
    assert context_budget.window_tokens(slug) == context_budget.FALLBACK_CONTEXT_TOKENS
    assert context_budget.known_window_tokens(slug) is None
    assert _run(_agent([5_000], model=slug)) == [(5_000, None)]


def test_the_context_never_rides_the_notice_channel_that_bots_print(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(context_budget, "known_window_tokens", lambda _model: 10_000)
    notices: list[str] = []
    seen = _run(
        _agent([3_000, 6_000]),
        on_notice=lambda code, _text, _data: notices.append(code),
    )
    assert seen == [(3_000, 10_000), (6_000, 10_000)]
    assert "context" not in notices


def test_a_broken_context_listener_cannot_fail_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(context_budget, "known_window_tokens", lambda _model: 10_000)

    def explode(_used: int, _window: int | None) -> None:
        raise RuntimeError("screen gone")

    result = _agent([3_000, 6_000]).run("task", on_context=explode)
    assert result.answer == "done"


def test_a_step_with_no_count_sends_nothing() -> None:
    assert context_report_due(None, 0, 10_000) is False
    assert context_report_due(None, 1, None) is True


def test_a_window_that_changes_always_reports() -> None:
    assert context_report_due((3_000, 10_000), 3_000, 20_000) is True
    assert context_report_due((3_000, None), 3_000, 10_000) is True
