"""Tests for the statistically-honest degradation flag (SEA)."""

from __future__ import annotations

from chimera.eval.continuous import EvolutionReport, TaskOutcome


def _report(pattern: list[bool]) -> EvolutionReport:
    return EvolutionReport(
        outcomes=[TaskOutcome(id=str(i), passed=p) for i, p in enumerate(pattern)]
    )


def test_degraded_significantly_true_on_clear_drop() -> None:
    report = _report([True] * 8 + [False] * 8)  # first half perfect, second half all fail
    assert report.degraded_significantly() is True
    assert report.degradation_ci()[0] > 0.0  # lower bound above zero


def test_not_degraded_when_flat() -> None:
    report = _report([True] * 16)
    assert report.degraded_significantly() is False
    assert report.degradation_ci()[0] < 0.0  # CI straddles zero


def test_degraded_none_on_small_sample() -> None:
    report = _report([True] * 3 + [False] * 3)  # below min_n per half
    assert report.degraded_significantly() is None


def test_summary_exposes_ci_fields() -> None:
    summary = _report([True] * 8 + [False] * 8).summary()
    assert summary["degraded_significant"] == 1.0
    assert summary["degradation_ci_low"] > 0.0
    assert "degradation_ci_high" in summary
