"""Right-hand scenario suite — does the agent handle everyday assistant tasks?

A small, checkable set of the kind of micro-tasks a "right hand" gets all day:
extracting structured data, quick conversions, sentiment, action items. Each
scenario pairs a prompt with a deterministic check, so the suite runs live
against a real model *and* unit-tests without a network. Coding tasks (multi-file,
executable verification) go through ``chimera solve`` instead.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from chimera.eval.continuous import EvolutionReport, Solver, TaskOutcome


@dataclass
class Scenario:
    """One right-hand task: a prompt and a deterministic check on the answer."""

    id: str
    prompt: str
    check: Callable[[str], bool]


def run_scenarios(
    solver: Solver,
    scenarios: Iterable[Scenario],
    *,
    on_result: Callable[[TaskOutcome], None] | None = None,
) -> EvolutionReport:
    """Run each scenario through ``solver`` and check the answer."""
    report = EvolutionReport()
    for scenario in scenarios:
        try:
            output = solver.solve(scenario.prompt)
            passed = bool(scenario.check(output))
        except Exception as exc:  # a crashing scenario is a failure, never aborts
            output, passed = f"error: {exc}", False
        outcome = TaskOutcome(scenario.id, passed, output)
        report.outcomes.append(outcome)
        if on_result is not None:
            on_result(outcome)
    return report


def _has_all(*needles: str) -> Callable[[str], bool]:
    return lambda out: all(n in out.lower() for n in needles)


def _has_any(*needles: str) -> Callable[[str], bool]:
    return lambda out: any(n in out.lower() for n in needles)


def daily_scenarios() -> list[Scenario]:
    """The everyday right-hand task set, with deterministic checks."""
    return [
        Scenario(
            "iso_date",
            "Convert this date to ISO 8601 (YYYY-MM-DD). Reply with only the date. "
            "Date: March 5, 2026.",
            _has_all("2026-03-05"),
        ),
        Scenario(
            "sentiment",
            "In one word (positive, negative, or neutral), what is the sentiment of this "
            "review? 'I absolutely loved it — fantastic experience!'",
            _has_any("positive"),
        ),
        Scenario(
            "tip",
            "What is a 15% tip on an $80 bill? Reply with only the dollar amount.",
            _has_all("12"),
        ),
        Scenario(
            "extract_emails",
            "Extract all email addresses as a comma-separated list: "
            "'Reach Alice at alice@x.com or Bob at bob@y.org.'",
            _has_all("alice@x.com", "bob@y.org"),
        ),
        Scenario(
            "action_items",
            "List the action items as bullet points from these notes: 'Alice will email "
            "the Q3 report by Friday. Bob will book the venue. Carol will review the budget.'",
            _has_all("report", "venue", "budget"),
        ),
        Scenario(
            "minutes",
            "How many minutes are in 2.5 hours? Reply with only the number.",
            _has_all("150"),
        ),
        Scenario(
            "summarize",
            "Summarize in one sentence: 'The new release adds dark mode, fixes the login "
            "bug, and improves load time by 40%.'",
            _has_any("dark", "login", "load", "40"),
        ),
    ]
