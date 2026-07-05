"""Tests for the anti-stagnation signal (crowding-score analog, arXiv 2606.29717)."""

from __future__ import annotations

from chimera.evolution import (
    StagnationDetector,
    StagnationReport,
    mean_pairwise_correlation,
    pearson,
)

# --- pearson / mean_pairwise_correlation ---------------------------------------------


def test_pearson_identical_vectors_is_one() -> None:
    assert pearson([1.0, 0.0, 1.0, 0.0], [1.0, 0.0, 1.0, 0.0]) == 1.0


def test_pearson_anticorrelated_is_negative() -> None:
    assert pearson([0.0, 1.0, 0.0, 1.0], [1.0, 0.0, 1.0, 0.0]) < 0.0


def test_pearson_constant_equal_vectors_treated_as_fully_correlated() -> None:
    # Zero variance both sides + elementwise-equal → maximally redundant, not undefined.
    assert pearson([1.0, 1.0, 1.0], [1.0, 1.0, 1.0]) == 1.0


def test_pearson_constant_but_different_is_zero() -> None:
    assert pearson([1.0, 1.0, 1.0], [0.0, 0.0, 0.0]) == 0.0


def test_pearson_mismatched_length_is_zero() -> None:
    assert pearson([1.0, 0.0], [1.0]) == 0.0


def test_mean_pairwise_correlation_needs_two() -> None:
    assert mean_pairwise_correlation([[1.0, 0.0, 1.0, 0.0]]) == 0.0


# --- vector mode ---------------------------------------------------------------------


def _same_failures() -> StagnationDetector:
    det = StagnationDetector(window=3, corr_threshold=0.9, min_items=4)
    for _ in range(3):
        det.record_vector([1.0, 0.0, 1.0, 0.0])  # fails items 0 and 2 every round
    return det


def test_vector_mode_flags_repeated_failure_pattern() -> None:
    report = _same_failures().assess()
    assert report.stagnant
    assert report.signal >= 0.9
    assert report.persistent_failures == [0, 2]


def test_vector_mode_not_stagnant_when_improving() -> None:
    det = StagnationDetector(window=3, min_items=4)
    det.record_vector([1.0, 1.0, 1.0, 0.0])
    det.record_vector([1.0, 1.0, 0.0, 0.0])
    det.record_vector([1.0, 0.0, 0.0, 0.0])  # errors shrinking, pattern shifting
    assert not det.assess().stagnant


def test_vector_mode_waits_for_full_window() -> None:
    det = StagnationDetector(window=3, min_items=4)
    det.record_vector([1.0, 0.0, 1.0, 0.0])
    det.record_vector([1.0, 0.0, 1.0, 0.0])
    assert not det.assess().stagnant  # only 2 of 3 rounds
    det.record_vector([1.0, 0.0, 1.0, 0.0])
    assert det.assess().stagnant


def test_vector_mode_rejects_short_vectors() -> None:
    det = StagnationDetector(window=2, min_items=4)
    det.record_vector([1.0, 0.0])  # below min_items
    det.record_vector([1.0, 0.0])
    assert not det.assess().stagnant


def test_persistent_failures_require_failing_every_round() -> None:
    det = StagnationDetector(window=2, corr_threshold=0.0, min_items=3)
    det.record_vector([1.0, 1.0, 0.0])
    det.record_vector([1.0, 0.0, 0.0])  # only item 0 fails both rounds
    report = det.assess()
    assert report.persistent_failures == [0]


# --- signature mode ------------------------------------------------------------------


def test_signature_mode_flags_repeated_signature() -> None:
    det = StagnationDetector(window=2)
    det.record_signature("tool grep failed on attempt 1: no such file /tmp/run_17")
    det.record_signature("tool grep failed on attempt 2: no such file /tmp/run_42")  # only digits differ
    report = det.assess()
    assert report.stagnant and report.signal == 1.0  # volatile digits normalized → same fault


def test_signature_mode_not_stagnant_when_signatures_differ() -> None:
    det = StagnationDetector(window=2)
    det.record_signature("tool grep failed")
    det.record_signature("verification mismatch on output")
    assert not det.assess().stagnant


def test_signature_mode_ignores_empty_signatures() -> None:
    det = StagnationDetector(window=2)
    det.record_signature("")
    det.record_signature("")
    assert not det.assess().stagnant  # empty != a real repeated failure


def test_empty_detector_is_not_stagnant() -> None:
    assert StagnationDetector().assess() == StagnationReport(False)


def test_advice_is_nonempty_and_mentions_pivot() -> None:
    advice = _same_failures().advice()
    assert "pivot" in advice.lower()
    assert "[0, 2]" in advice  # persistent failures surfaced


# --- integration with the autonomous retry loop --------------------------------------


def test_autonomous_loop_injects_pivot_advice_on_repeated_failure() -> None:
    from chimera.core.agent import AgentResult
    from chimera.core.autonomous import AutonomousAgent, AutonomousConfig

    class StuckWorker:
        """Always returns the same wrong answer, and records the prompt it saw."""

        def __init__(self) -> None:
            self.prompts: list[str] = []

        def run(self, task: str) -> AgentResult:
            self.prompts.append(task)
            return AgentResult(answer="wrong", steps=0, transcript=[], stopped_reason="done")

    class AlwaysFailVerifier:
        def verify(self):
            from chimera.core.verify import VerificationResult

            return VerificationResult(passed=False, output="assertion failed: expected 42")

    worker = StuckWorker()
    agent = AutonomousAgent(
        worker,
        verifier=AlwaysFailVerifier(),
        stagnation=StagnationDetector(window=2),
        config=AutonomousConfig(max_attempts=3, use_planner=False, use_manager=False),
    )
    result = agent.run("compute the answer")
    assert not result.success
    # By the 3rd attempt the same failure has repeated → the prompt carries the pivot advice.
    assert any("pivot" in p.lower() for p in worker.prompts)
