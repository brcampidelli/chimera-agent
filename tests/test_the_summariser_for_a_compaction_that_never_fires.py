"""A summariser for the span a compaction drops — and the census that says it has never run.

`compact()` has taken a `summarise` callable since it was written and no production caller has ever
passed one. This is that callable, built on the split the two papers `context_budget` cites already
describe: compactors retain 17% of injected session constraints, and rule-form items survive far
better than facts. So it asks for standing instructions, not for a narration — the structural note
already says what happened, cheaper and without a model.

It ships **off**, and not only because the paired arms in `bench/compaction/PREREGISTRATION.md` were
not run. Measured over the 137 runs this install has traced: **zero compactions**, peak context a
median 6,826 tokens against a trigger of 786,000. The behaviour under test is not reachable, and a
default that turns on a model call for a path nothing takes is a cost with no effect to weigh it
against.

What is tested here is therefore the contract, not the benefit: the thing degrades to the note on
every failure, it never returns an empty span, and it carries the note alongside the summary rather
than instead of it.
"""

from __future__ import annotations

from typing import Any

from chimera.core.context_budget import _structural_note, compact
from chimera.core.summarise import MAX_INPUT_CHARS, SYSTEM, rule_summariser


class _Backend:
    """A scripted `complete`, or an explosion. Nothing here needs a model."""

    def __init__(self, answer: str = "Always start a file with '# (c) Bruno'.", boom: bool = False):
        self.answer = answer
        self.boom = boom
        self.calls: list[list[Any]] = []

    def complete(self, messages: list[Any], **kwargs: Any) -> Any:
        self.calls.append(list(messages))
        if self.boom:
            raise RuntimeError("the provider fell over")

        class _R:
            content = self.answer

        return _R()


SPAN: list[Any] = [
    {"role": "user", "content": "Every file in this project starts with '# (c) Bruno'."},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "1", "function": {"name": "write_file", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "1", "content": "wrote a.py"},
    {"role": "user", "content": "now do the next one"},
]


# --- what it produces -------------------------------------------------------------------------


def test_the_note_travels_with_the_summary_never_instead_of_it() -> None:
    """They answer different questions: how much went, and what still binds."""
    out = rule_summariser(_Backend())(SPAN)

    assert "earlier messages were removed" in out, "the count is what says how much is missing"
    assert "# (c) Bruno" in out


def test_the_rules_are_labelled_as_coming_from_the_dropped_span() -> None:
    out = rule_summariser(_Backend())(SPAN)
    assert "Standing from that span:" in out


def test_tool_results_are_clipped_before_they_are_sent() -> None:
    """A span is mostly tool output, and tool output is the part the agent can just ask for again."""
    backend = _Backend()
    span = [*SPAN, {"role": "tool", "tool_call_id": "2", "content": "x" * 5_000}]
    rule_summariser(backend)(span)

    sent = str(backend.calls[0][1].content)
    assert "x" * 200 in sent
    assert "x" * 400 not in sent


def test_a_very_long_span_is_clipped_at_both_ends() -> None:
    """Head and tail: the beginning carries what was established, the end where the work had got to."""
    backend = _Backend()
    span = [{"role": "user", "content": "FIRST"}]
    span += [{"role": "user", "content": "filler " * 400} for _ in range(20)]
    span += [{"role": "user", "content": "LAST"}]
    rule_summariser(backend)(span)

    sent = str(backend.calls[0][1].content)
    assert len(sent) <= MAX_INPUT_CHARS + 8
    assert "FIRST" in sent and "LAST" in sent


def test_the_prompt_forbids_inventing() -> None:
    """The risk this whole design is exposed to, held in the one place that can prevent it."""
    assert "not actually said" in SYSTEM
    assert "NONE" in SYSTEM


# --- every way it can fail --------------------------------------------------------------------


def test_a_backend_that_raises_degrades_to_the_note() -> None:
    """A compaction must still compact. The alternative is a run that dies to free memory."""
    out = rule_summariser(_Backend(boom=True))(SPAN)
    assert out == _structural_note(SPAN)


def test_an_empty_answer_degrades_to_the_note() -> None:
    assert rule_summariser(_Backend(answer="   "))(SPAN) == _structural_note(SPAN)


def test_the_word_none_degrades_to_the_note() -> None:
    """"Nothing standing was said" is a real answer, and a common one."""
    assert rule_summariser(_Backend(answer="NONE"))(SPAN) == _structural_note(SPAN)


def test_a_span_with_no_text_never_reaches_the_model() -> None:
    backend = _Backend()
    out = rule_summariser(backend)([{"role": "tool", "tool_call_id": "1", "content": ""}])
    assert backend.calls == [], "an empty span is not worth a model call"
    assert "earlier messages were removed" in out


def test_it_never_returns_an_empty_span() -> None:
    """The one outcome strictly worse than either arm: a span replaced by nothing at all."""
    for backend in (_Backend(), _Backend(boom=True), _Backend(answer=""), _Backend(answer="NONE")):
        assert rule_summariser(backend)(SPAN).strip()


# --- through the real compaction ----------------------------------------------------------------


def test_compact_uses_the_summariser_when_one_is_given() -> None:
    messages: list[Any] = [{"role": "system", "content": "sys"}]
    messages += [{"role": "user", "content": f"turn {i}"} for i in range(12)]

    out, changed = compact(messages, keep_recent=4, summarise=rule_summariser(_Backend()))

    assert changed
    assert "# (c) Bruno" in str(out[1]["content"])


def test_compact_without_one_is_byte_identical_to_before() -> None:
    """The default path is untouched: `summarise=None` is what every caller passes today."""
    messages: list[Any] = [{"role": "system", "content": "sys"}]
    messages += [{"role": "user", "content": f"turn {i}"} for i in range(12)]

    with_none, _ = compact(messages, keep_recent=4, summarise=None)
    plain, _ = compact(messages, keep_recent=4)

    assert with_none == plain


def test_the_agent_builds_no_summariser_unless_asked(monkeypatch: Any) -> None:
    from chimera.core import Agent, AgentConfig
    from chimera.tools import ToolRegistry

    assert Agent(_Backend(), ToolRegistry(), AgentConfig())._summarise is None
    assert (
        Agent(_Backend(), ToolRegistry(), AgentConfig(summarise_compaction=True))._summarise
        is not None
    )
