"""When the agent is allowed to ask instead of guessing.

`DEFAULT_SYSTEM_PROMPT` says a final answer that only tells the user what they "can" or "should" do
is a failure — and that rule is right. It is what stops an agent from explaining a fix instead of
applying it, and the project has a nudge and a heuristic backing it up.

It also had no exception, and measured against a real request that cost something. Given *"faz um
site pra minha padaria"* — no technology named, no audience, nowhere for the result to live — the
agent wrote five files: a README, a config.json, a script.js, a stylesheet and an index.html, in a
stack nobody chose, for a bakery whose name it never learned. Paired run, same folder, same model,
same step ceiling; the only difference was this paragraph.

With the exception, the same request produced one step, no files, and three short questions.

**The control matters more than the effect.** An agent that asks every time is worse than one that
never asks: it turns each request into a questionnaire, and the rule above exists to prevent
exactly that failure in its other form. So the last sentence of the exception tells it to build when
the request says what to build, and the test for that case is the one worth keeping green.
"""

from __future__ import annotations

from chimera.core.agent import DEFAULT_SYSTEM_PROMPT


def test_the_rule_that_makes_it_act_is_still_there() -> None:
    """The exception is an exception. If the sentence it qualifies ever goes, the agent is free to
    describe work instead of doing it, which is the defect the whole prompt is built around."""
    assert "is a failure" in DEFAULT_SYSTEM_PROMPT
    assert "DO the task, not to describe how" in DEFAULT_SYSTEM_PROMPT


def test_asking_is_permitted_only_when_the_request_does_not_say_enough() -> None:
    """The three conditions are named, together, so the permission cannot be read as general."""
    for condicao in ("no technology", "no audience", "nowhere for the result to live"):
        assert condicao in DEFAULT_SYSTEM_PROMPT, f"the exception no longer names {condicao!r}"


def test_it_is_bounded_to_a_few_questions_and_no_writing() -> None:
    """Two bounds, and both are load-bearing: an unbounded ask is an interview, and an ask that
    writes files anyway is the worst of both — questions AND a guess."""
    assert "at most three" in DEFAULT_SYSTEM_PROMPT
    assert "stop without writing anything" in DEFAULT_SYSTEM_PROMPT


def test_a_specific_request_is_still_built_rather_than_questioned() -> None:
    """The anti-questionnaire clause. Without it the exception reads as blanket permission, and an
    agent that asks about everything is a worse product than one that asks about nothing."""
    assert "do not ask — build it" in DEFAULT_SYSTEM_PROMPT


def test_the_exception_costs_one_paragraph() -> None:
    """This text is prepended to every single turn on every surface, so its length is a per-turn
    cost paid by every user. Asserted rather than assumed: a prompt nobody measures is a prompt that
    grows a sentence at a time until somebody notices it in a token bill.
    """
    assert len(DEFAULT_SYSTEM_PROMPT) < 2200, (
        f"the system prompt is {len(DEFAULT_SYSTEM_PROMPT)} characters and is sent every turn"
    )
