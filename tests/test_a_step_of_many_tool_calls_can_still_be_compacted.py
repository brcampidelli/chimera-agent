"""A step that made many tool calls at once can still be compacted, and a stuck turn says why.

Measured 2026-10-06 driving the desktop: a turn whose ninth step made eight parallel reads stopped
as `context_stuck` with an EMPTY answer. Two defects: `compact` walked the tail forward past every
leading tool result, so a tail made only of tool results came out empty and nothing compacted —
with eight earlier steps available to summarise; and the stuck answer was the last call's content,
which is empty when that call asked for tools.
"""

from __future__ import annotations

from typing import Any

from chimera.core.context_budget import compact


def _step(i: int, calls: int) -> list[dict[str, Any]]:
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": f"c{i}-{k}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
            for k in range(calls)
        ],
    }
    results = [
        {"role": "tool", "tool_call_id": f"c{i}-{k}", "content": "x" * 50} for k in range(calls)
    ]
    return [assistant, *results]


def test_a_tail_of_tool_results_keeps_its_step_and_compacts_what_is_older() -> None:
    messages: list[Any] = [{"role": "system", "content": "s"}, {"role": "user", "content": "go"}]
    for i in range(8):
        messages += _step(i, 2)
    messages += _step(8, 8)  # the last step: eight calls, more than keep_recent=6

    out, changed = compact(messages, keep_recent=6)

    assert changed is True  # the old code returned (messages, False) here
    # The last step survives whole: its assistant message and all eight results, in order.
    tail = out[-9:]
    assert tail[0]["role"] == "assistant" and len(tail[0]["tool_calls"]) == 8
    assert [m["tool_call_id"] for m in tail[1:]] == [f"c8-{k}" for k in range(8)]
    # And the prompt is legal: no tool result whose assistant call is gone.
    ids = {c["id"] for m in out if m.get("role") == "assistant" for c in m.get("tool_calls") or []}
    assert all(m["tool_call_id"] in ids for m in out if m.get("role") == "tool")
    assert len(out) < len(messages)


def test_a_stuck_turn_never_answers_with_nothing() -> None:
    from chimera.core import agent

    assert agent._CONTEXT_STUCK_ANSWER.startswith("Stopped:")
    assert "new thread" in agent._CONTEXT_STUCK_ANSWER
