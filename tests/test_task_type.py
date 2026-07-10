"""Tests for task-typed aggregation (MALLM, arXiv 2607.05477): classifier + fusion routing."""

from __future__ import annotations

from typing import Any

from chimera.fusion import FusionConfig, FusionEngine
from chimera.fusion.task_type import classify_task_type
from chimera.providers import CompletionResult

# --- classifier -------------------------------------------------------------------------


def _u(text: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": text}]


def test_logic_signals() -> None:
    assert classify_task_type(_u("How many primes are below 10?")) == "logic"
    assert classify_task_type(_u("What is 2 + 2?")) == "logic"
    assert classify_task_type(_u("True or false: the sky is blue.")) == "logic"
    assert classify_task_type(_u("Calculate the total.")) == "logic"
    assert classify_task_type(_u("Which of the following is prime?")) == "logic"
    assert classify_task_type(_u("Solve for x: 3x = 9")) == "logic"


def test_knowledge_default_and_veto() -> None:
    assert classify_task_type(_u("Explain quantum entanglement.")) == "knowledge"
    assert classify_task_type(_u("Describe the history of Rome.")) == "knowledge"
    # A code/write ask is knowledge even with a logic keyword — many valid phrasings, so voting is wrong.
    assert classify_task_type(_u("Write a function to compute how many primes are below n.")) == "knowledge"
    assert classify_task_type(_u("")) == "knowledge"


def test_uses_last_user_turn() -> None:
    msgs = [
        {"role": "user", "content": "Explain relativity."},
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": "How many moons does Mars have?"},
    ]
    assert classify_task_type(msgs) == "logic"


# --- engine routing ---------------------------------------------------------------------


class RoutingBackend:
    """Panel returns per-model content; tracks whether judge/synth were ever called."""

    def __init__(self, panel_answers: dict[str, str]) -> None:
        self.panel_answers = panel_answers
        self.judge_calls = 0
        self.synth_calls = 0

    def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
        if model == "judge":
            self.judge_calls += 1
            return CompletionResult(content="JUDGE", model="judge")
        if model == "synth":
            self.synth_calls += 1
            return CompletionResult(content="SYNTHESIZED", model="synth")
        return CompletionResult(content=self.panel_answers[str(model)], model=str(model))


def _cfg(task_typed: bool) -> FusionConfig:
    return FusionConfig(
        panel=["m1", "m2"], judge="judge", synthesizer="synth", mode="full", task_typed=task_typed
    )


def test_logic_majority_votes_skipping_judge_synth() -> None:
    backend = RoutingBackend({"m1": "42", "m2": "42"})
    trace = FusionEngine(backend, _cfg(True)).run(_u("How many apples are in the basket?"))
    assert trace.final == "42"
    assert trace.aggregation == "vote"
    assert backend.judge_calls == 0 and backend.synth_calls == 0
    assert trace.judge_analysis == ""


def test_logic_without_majority_falls_through_to_synth() -> None:
    backend = RoutingBackend({"m1": "42", "m2": "7"})
    trace = FusionEngine(backend, _cfg(True)).run(_u("How many apples are in the basket?"))
    assert trace.final == "SYNTHESIZED"
    assert trace.aggregation == "synth"
    assert backend.judge_calls == 1 and backend.synth_calls == 1


def test_knowledge_always_synthesizes_even_with_majority() -> None:
    backend = RoutingBackend({"m1": "same", "m2": "same"})
    trace = FusionEngine(backend, _cfg(True)).run(_u("Explain the causes of the French Revolution."))
    assert trace.final == "SYNTHESIZED"
    assert trace.aggregation == "synth"
    assert backend.synth_calls == 1


def test_task_typed_off_is_unchanged() -> None:
    backend = RoutingBackend({"m1": "42", "m2": "42"})
    trace = FusionEngine(backend, _cfg(False)).run(_u("How many apples are in the basket?"))
    assert trace.final == "SYNTHESIZED"  # default synth path, vote never considered
    assert trace.aggregation == "synth"
    assert backend.judge_calls == 1 and backend.synth_calls == 1
