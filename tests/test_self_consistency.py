"""Tests for self-consistency / best-of-N (M13 B4) — cheap single-model fusion."""

from __future__ import annotations

from chimera.fusion.consistency import SelfConsistency, majority
from chimera.providers.gateway import CompletionResult


class _ScriptedBackend:
    """Returns queued answers in order; records how many completions it served."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.calls = 0

    def complete(self, messages: object, **kwargs: object) -> CompletionResult:
        self.calls += 1
        content = self.answers.pop(0) if self.answers else "(exhausted)"
        return CompletionResult(content=content, model="fake", prompt_tokens=2, completion_tokens=3)


# --- majority() -------------------------------------------------------------------------


def test_majority_picks_the_dominant_cluster() -> None:
    assert majority(["42", "42", "seven"]) == "42"


def test_majority_none_when_all_distinct() -> None:
    assert majority(["a", "b", "c"]) is None


def test_majority_none_on_tie() -> None:
    assert majority(["a", "a", "b", "b"]) is None


def test_majority_returns_longest_representative() -> None:
    # Similar answers cluster; the fullest phrasing represents the cluster.
    got = majority(["The answer is 42.", "The answer is 42", "nope"], threshold=0.8)
    assert got == "The answer is 42."


# --- SelfConsistency --------------------------------------------------------------------


def test_n1_is_passthrough() -> None:
    backend = _ScriptedBackend(["only answer"])
    result = SelfConsistency(backend, n=1).complete([])
    assert result.content == "only answer" and backend.calls == 1  # no extra sampling


def test_majority_wins_without_synthesis() -> None:
    backend = _ScriptedBackend(["yes", "yes", "no"])
    result = SelfConsistency(backend, n=3).complete([])
    assert result.content == "yes"
    assert backend.calls == 3  # 3 samples, no synth call
    assert result.model == "self-consistency"


def test_no_consensus_triggers_synthesis() -> None:
    # 3 distinct samples -> no majority -> a 4th (synthesis) call reconciles them.
    backend = _ScriptedBackend(["alpha", "beta", "gamma", "the synthesis"])
    result = SelfConsistency(backend, n=3).complete([])
    assert result.content == "the synthesis"
    assert backend.calls == 4  # 3 samples + 1 synthesis


def test_tokens_are_aggregated() -> None:
    backend = _ScriptedBackend(["x", "x", "y"])
    result = SelfConsistency(backend, n=3).complete([])
    assert result.prompt_tokens == 6 and result.completion_tokens == 9  # summed over 3 samples
