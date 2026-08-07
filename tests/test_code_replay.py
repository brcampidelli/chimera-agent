"""Turning a stored transcript back into the conversation someone had.

Resuming used to show an empty screen while the agent silently carried the whole history — the
worst of both, because the follow-up worked for reasons the user could not see. These pin the fold
from message list to exchanges, and in particular the cases where the honest answer is "show it
anyway" rather than "drop it".
"""

from __future__ import annotations

from chimera.api.code_replay import exchanges_from_messages


def test_a_plain_question_and_answer() -> None:
    (one,) = exchanges_from_messages(
        [{"role": "user", "content": "what does this do?"}, {"role": "assistant", "content": "it parses"}]
    )
    assert one["you"] == "what does this do?"
    assert one["answer"] == "it parses"
    assert one["tools"] == []


def test_a_tool_call_is_paired_with_its_result() -> None:
    (one,) = exchanges_from_messages(
        [
            {"role": "user", "content": "read it"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "print(1)"},
            {"role": "assistant", "content": "it prints 1"},
        ]
    )
    assert one["answer"] == "it prints 1"
    (tool,) = one["tools"]
    assert tool["name"] == "read_file"
    assert tool["arguments"] == {"path": "a.py"}   # parsed from the JSON string it travels as
    assert tool["observation"] == "print(1)"
    assert tool["ok"] is True


def test_a_failed_tool_is_marked_failed() -> None:
    (one,) = exchanges_from_messages(
        [
            {"role": "user", "content": "read it"},
            {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "error: no such file"},
        ]
    )
    assert one["tools"][0]["ok"] is False


def test_two_questions_are_two_exchanges() -> None:
    got = exchanges_from_messages(
        [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "b"},
        ]
    )
    assert [e["you"] for e in got] == ["first", "second"]
    assert [e["answer"] for e in got] == ["a", "b"]


def test_reasoning_before_a_tool_call_is_kept_with_the_answer_after_it() -> None:
    """A turn that says why, calls something, then concludes. Keeping only the last part would drop
    the half that explains the call."""
    (one,) = exchanges_from_messages(
        [
            {"role": "user", "content": "fix it"},
            {"role": "assistant", "content": "let me look", "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
            {"role": "assistant", "content": "fixed"},
        ]
    )
    assert one["answer"] == "let me look\nfixed"


def test_work_before_the_first_question_is_shown_not_dropped() -> None:
    """Trimming cuts at a user boundary, but a stored file can still begin mid-turn. The work is
    real; a header it lacks is not a reason to hide it."""
    got = exchanges_from_messages([{"role": "assistant", "content": "carried over"}])
    assert got == [{"you": "", "answer": "carried over", "tools": [], "edits": []}]


def test_a_result_whose_call_was_trimmed_still_appears() -> None:
    got = exchanges_from_messages(
        [{"role": "user", "content": "go"}, {"role": "tool", "tool_call_id": "gone", "content": "output"}]
    )
    assert got[0]["tools"] == [{"name": "", "arguments": {}, "ok": True, "observation": "output"}]


def test_unparseable_arguments_are_kept_as_raw_rather_than_erased() -> None:
    """An argument we cannot read is still evidence of what was attempted; blanking it would make a
    malformed call look like a call with no arguments."""
    (one,) = exchanges_from_messages(
        [
            {"role": "user", "content": "go"},
            {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "x", "arguments": "{not json"}}]},
        ]
    )
    assert one["tools"][0]["arguments"] == {"raw": "{not json"}


def test_a_call_with_no_result_is_not_painted_as_a_failure() -> None:
    """Its result was trimmed away. Defaulting to failure would turn every long conversation red."""
    (one,) = exchanges_from_messages(
        [
            {"role": "user", "content": "go"},
            {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "x", "arguments": "{}"}}]},
        ]
    )
    assert one["tools"][0]["ok"] is True
    assert one["tools"][0]["observation"] == ""


def test_multimodal_content_parts_become_their_text() -> None:
    (one,) = exchanges_from_messages(
        [{"role": "user", "content": [{"type": "text", "text": "look"}, {"type": "image_url"}]}]
    )
    assert one["you"] == "look"


def test_an_empty_conversation_is_an_empty_list() -> None:
    assert exchanges_from_messages([]) == []
