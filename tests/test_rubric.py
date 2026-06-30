"""Tests for cascade rubric evaluation (DailyReport)."""

from __future__ import annotations

from chimera.eval import Dimension, cascade_dimensions, evaluate_cascade


def _dim(name: str, weight: float, score: float, gate: float = 0.5) -> Dimension:
    return Dimension(name, weight, lambda a, t: score, gate=gate)


def test_all_dimensions_pass_weighted_overall() -> None:
    dims = [_dim("a", 0.5, 1.0), _dim("b", 0.5, 0.6)]
    result = evaluate_cascade("answer", "task", dims)
    assert result.stopped_at is None
    assert result.scores == {"a": 1.0, "b": 0.6}
    assert abs(result.overall - 0.8) < 1e-9  # 0.5*1.0 + 0.5*0.6


def test_cascade_stops_at_first_failed_gate() -> None:
    evaluated: list[str] = []

    def make(name: str, score: float) -> Dimension:
        def check(a: str, t: str) -> float:
            evaluated.append(name)
            return score

        return Dimension(name, 1 / 3, check, gate=0.5)

    dims = [make("instruction", 0.2), make("factuality", 1.0), make("rationality", 1.0)]
    result = evaluate_cascade("answer", "task", dims)

    assert evaluated == ["instruction"]  # downstream dims never run
    assert result.stopped_at == "instruction"
    assert "factuality" not in result.scores
    assert result.overall < 0.1  # only instruction (0.2) counted; rest contribute 0


def test_cascade_dimensions_default_order_and_weights() -> None:
    dims = cascade_dimensions(lambda a, t, c: 1.0)
    assert [d.name for d in dims] == ["instruction_following", "factuality", "rationality"]
    assert [d.weight for d in dims] == [0.4, 0.4, 0.2]
    assert evaluate_cascade("a", "t", dims).overall == 1.0
