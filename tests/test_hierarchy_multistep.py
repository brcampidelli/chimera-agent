"""Offline tests for the multi-step hierarchy companion bench.

The key property under test is the token-accounting crossover: with a fake backend
whose token cost is proportional to the characters it is sent, the scoped arm must
cost materially fewer tokens than the single-context baseline — because the baseline
re-sends every large document on every turn.
"""

from __future__ import annotations

from chimera.eval.hierarchy_multistep import (
    multistep_tasks,
    run_baseline,
    run_scoped,
)


def _char_backend(answer_for):  # type: ignore[no-untyped-def]
    """Fake complete(): tokens ~= chars sent (chars/4), answer chosen by the caller."""

    def complete(messages):  # type: ignore[no-untyped-def]
        sent = sum(len(m["content"]) for m in messages)
        return answer_for(messages), sent // 4

    return complete


def test_suite_is_well_formed() -> None:
    tasks = multistep_tasks()
    assert len(tasks) == 6
    for task in tasks:
        assert len(task.docs) >= 3
        assert len(task.steps) == len(task.docs)
        # Every step's document is large enough that re-sending it matters.
        for step in task.steps:
            assert len(task.docs[step.doc]) > 3000
        assert task.check([""]) is False
        full = [s.needle for s in task.steps]
        assert task.check(full) is True


def test_scoped_uses_far_fewer_tokens_than_baseline() -> None:
    """The crossover, proven deterministically: baseline re-sends all docs every turn."""
    task = multistep_tasks()[0]

    # Perfect answers both arms — isolate the TOKEN axis from the quality axis.
    def answer_for(messages):  # type: ignore[no-untyped-def]
        # Echo the needle for whichever doc is present in the last user turn.
        text = messages[-1]["content"].lower()
        for step in task.steps:
            if step.doc.lower() in text and step.question.lower() in text:
                return step.needle
        # baseline: the question arrives without the doc name in the SAME turn, so map
        # by question text.
        for step in task.steps:
            if step.question.lower() in text:
                return step.needle
        return ""

    complete = _char_backend(answer_for)
    base = run_baseline(task, complete)
    scoped = run_scoped(task, complete)

    assert base.passed and scoped.passed
    # Baseline pays ~Q * sum(docs); scoped pays ~sum(docs). With Q=3 docs, scoped must
    # be well under half the baseline's tokens.
    assert scoped.tokens < base.tokens * 0.6, (scoped.tokens, base.tokens)


def test_grading_requires_all_needles() -> None:
    task = multistep_tasks()[1]

    # A backend that answers only the first step correctly must fail the task.
    def answer_for(messages):  # type: ignore[no-untyped-def]
        text = messages[-1]["content"].lower()
        first = task.steps[0]
        return first.needle if first.question.lower() in text else "no idea"

    run = run_baseline(task, _char_backend(answer_for))
    assert run.passed is False
