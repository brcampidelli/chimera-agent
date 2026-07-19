"""The standard-benchmark harness — graded honestly, or not at all.

These run without a provider key: they exercise grading, extraction and the budget guard, which is
where a benchmark silently goes wrong. The arms themselves need a key and are exercised by the run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1] / "bench" / "llm_benchmarks"
sys.path.insert(0, str(BENCH))

pytest.importorskip("arms", reason="bench/llm_benchmarks not on the path")

import gsm8k  # noqa: E402
import humaneval  # noqa: E402
from arms import Spend, cost_usd, extract_code  # noqa: E402
from datasets import gsm8k_reference  # noqa: E402

_PROBLEM = {
    "task_id": "HumanEval/0",
    "prompt": 'def add(a, b):\n    """Add two numbers.\n\n    >>> add(1, 2)\n    3\n    """\n',
    "entry_point": "add",
    "test": "def check(candidate):\n    assert candidate(1, 2) == 3\n    assert candidate(-1, 1) == 0\n",
}


# --- HumanEval grading ------------------------------------------------------------------------


def test_correct_solution_passes() -> None:
    assert humaneval.grade("def add(a, b):\n    return a + b\n", _PROBLEM) is True


def test_wrong_solution_fails() -> None:
    assert humaneval.grade("def add(a, b):\n    return a * b\n", _PROBLEM) is False


def test_grader_is_not_vacuous_a_stub_must_fail() -> None:
    # The vacuity check every benchmark needs: a do-nothing implementation must NOT score.
    assert humaneval.grade("def add(a, b):\n    pass\n", _PROBLEM) is False


def test_empty_and_unparseable_solutions_fail() -> None:
    assert humaneval.grade("", _PROBLEM) is False
    assert humaneval.grade("this is not python", _PROBLEM) is False


def test_infinite_loop_is_a_fail_not_a_hang() -> None:
    # A timeout is an honest FAIL. Excluding it would flatter whichever arm is slower.
    assert humaneval.grade("def add(a, b):\n    while True:\n        pass\n", _PROBLEM) is False


def test_solution_cannot_pass_by_reading_the_test_file() -> None:
    # THE integrity property. Grading happens in a throwaway dir containing only the generated
    # runner, so a solution that tries to find and read the hidden tests finds nothing to cheat with.
    sneaky = (
        "import os\n"
        "def add(a, b):\n"
        "    found = [f for f in os.listdir('.') if 'test' in f.lower()]\n"
        "    assert not found, f'the grading tests were reachable: {found}'\n"
        "    return a + b\n"
    )
    assert humaneval.grade(sneaky, _PROBLEM) is True  # passes *because* it found no test files


def test_solve_task_prompt_never_contains_the_hidden_test() -> None:
    # The prompt handed to the agent must not leak the grader, even by accident.
    assert "check(" not in humaneval._SOLVE_TASK
    assert "assert" not in humaneval._SOLVE_TASK


# --- code extraction --------------------------------------------------------------------------


def test_extract_code_prefers_the_fenced_block() -> None:
    text = "Here you go:\n```python\ndef add(a, b):\n    return a + b\n```\nHope that helps!"
    assert extract_code(text) == "def add(a, b):\n    return a + b"


def test_extract_code_picks_the_longest_block_not_the_first() -> None:
    # Models often emit a tiny illustrative snippet before the real answer.
    text = "```python\n# sketch\n```\n```python\ndef add(a, b):\n    return a + b\n```"
    assert "return a + b" in extract_code(text)


def test_extract_code_falls_back_to_raw_text() -> None:
    assert extract_code("def add(a, b):\n    return a + b") == "def add(a, b):\n    return a + b"


# --- GSM8K ------------------------------------------------------------------------------------


def test_gsm8k_reference_takes_the_number_after_the_marker() -> None:
    assert gsm8k_reference("Some working out.\n#### 1,234") == "1234"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("blah blah\nANSWER: 42", "42"),
        ("ANSWER: 1,250", "1250"),
        ("the total is 18 dollars", "18"),  # no marker: fall back to the last number
        ("ANSWER: 7.0", "7"),  # 7.0 and 7 are the same answer
        ("", ""),
    ],
)
def test_gsm8k_answer_extraction(text: str, expected: str) -> None:
    assert gsm8k.extract_answer(text) == expected


def test_gsm8k_grade_matches_on_value_not_formatting() -> None:
    problem = {"answer": "working\n#### 1000"}
    assert gsm8k.grade("1000", problem) is True
    assert gsm8k.grade(gsm8k.normalise("1,000"), problem) is True  # formatting is normalised away
    assert gsm8k.grade("999", problem) is False
    assert gsm8k.grade("", problem) is False  # no answer is a FAIL, never a pass


def test_gsm8k_solve_task_does_not_leak_the_gold_answer() -> None:
    assert "####" not in gsm8k._SOLVE_TASK


# --- budget accounting ------------------------------------------------------------------------


def test_spend_tracks_totals_and_flags_estimates() -> None:
    spend = Spend()
    spend.add("baseline", 1000, 100, 0.001, estimated=False)
    spend.add("chimera", 14000, 2500, 0.002, estimated=True)
    assert spend.prompt_tokens == 15000
    assert round(spend.usd, 4) == 0.003
    assert spend.estimated_calls == 1  # so the report can say the total is a floor, not a fact
    assert set(spend.by_arm) == {"baseline", "chimera"}


def test_cost_uses_the_catalog_price() -> None:
    # mistral-small is 0.10/0.30 per million in the catalog.
    cost = cost_usd("openrouter/mistralai/mistral-small-3.2-24b-instruct", 1_000_000, 1_000_000)
    assert round(cost, 4) == 0.40


def test_unknown_model_costs_zero_for_the_guard_but_is_countable() -> None:
    # An unknown price must not crash the guard; the caller flags it via estimated_calls.
    assert cost_usd("nonexistent/model", 1_000_000, 1_000_000) == 0.0
