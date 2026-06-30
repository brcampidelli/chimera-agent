"""Tests for the right-hand scenario suite (no network)."""

from __future__ import annotations

from chimera.eval import daily_scenarios, run_scenarios


class CannedSolver:
    def __init__(self, answer: str) -> None:
        self.answer = answer

    def solve(self, prompt: str) -> str:
        return self.answer


_MATCHES_EVERYTHING = (
    "2026-03-05, positive, tip $12, alice@x.com and bob@y.org, "
    "report venue budget, 150 minutes, dark mode login load 40%"
)


def test_all_scenarios_pass_when_output_matches() -> None:
    report = run_scenarios(CannedSolver(_MATCHES_EVERYTHING), daily_scenarios())
    assert report.pass_rate == 1.0
    assert report.total == len(daily_scenarios())


def test_scenarios_fail_on_empty_output() -> None:
    report = run_scenarios(CannedSolver(""), daily_scenarios())
    assert report.pass_rate == 0.0


def test_on_result_callback_fires_per_scenario() -> None:
    seen: list[str] = []
    run_scenarios(CannedSolver("nope"), daily_scenarios(), on_result=lambda o: seen.append(o.id))
    assert seen == [s.id for s in daily_scenarios()]


def test_suite_is_non_trivial() -> None:
    assert len(daily_scenarios()) >= 5
