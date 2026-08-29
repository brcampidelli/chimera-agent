"""The apparatus for the fusion experiment, checked before it is allowed to cost anything.

The experiment itself is registered in `bench/fusion_paired/PREREGISTRATION.md` and has not run. What
these tests hold is the machinery, because an experiment whose harness is wrong produces a number
that looks exactly like a result — and this one is meant to decide the project's central bet.

Everything here is deterministic and free: no model call, no network. What is asserted is the shape
of the comparison, not its outcome.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
PREREG = REPO / "bench/fusion_paired/PREREGISTRATION.md"


def _runner() -> Any:
    """Load the runner without executing the bench. It lives under `bench/`, not in the package."""
    sys.path.insert(0, str(REPO / "bench/local_lift"))
    spec = importlib.util.spec_from_file_location(
        "fusion_paired_runner", REPO / "bench/fusion_paired/run_paired.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- the design

def test_the_preregistration_exists_and_binds_before_the_run() -> None:
    """It is committed before the first model call or it is not a pre-registration — it is a summary
    written afterwards, which is the thing this practice exists to make impossible."""
    assert PREREG.is_file()
    text = PREREG.read_text(encoding="utf-8")

    assert "BEFORE a single model call" in text
    # The criterion must be absolute. A multiplicative one has already misfired in this project:
    # `pass@k > pass@1 × 1.5` printed "no useful tail" for 52.3% → 72.9%.
    assert "A − B ≥ +5.0 pp" in text
    assert "absolute floor, never a multiplicative one" in text
    # And what would refute the bet, written before the number exists.
    assert "What would refute the bet" in text
    assert "CANNOT show" in text


def test_the_design_has_the_control_that_makes_a_win_attributable() -> None:
    """A vs B alone cannot separate *more diversity* from *more computation*. C is what does, and it
    is the control the MoA paper omitted and Self-MoA had to add."""
    arms = _runner().ARMS

    assert set(arms) == {"A_fusion", "B_repeat", "C_single"}
    assert "--fuse" in arms["A_fusion"]["flags"]
    assert "--fuse" not in arms["B_repeat"]["flags"]
    assert arms["B_repeat"]["samples"] == 3, "equal sample count to the panel, or it is not a control"
    assert arms["C_single"]["samples"] == 1


def test_three_seeds_not_two() -> None:
    """Two alert, three decide. At two, a difference and a variance are indistinguishable."""
    assert len(_runner().SEEDS) == 3


# --------------------------------------------------------------------------- the accounting

def test_one_unpriced_row_makes_the_total_unknown_not_smaller(tmp_path: Path) -> None:
    """The direction that matters: a partial sum flatters whichever arm used the unpriced model,
    and here that would be the fusion panel."""
    run = _runner()
    (tmp_path / "usage.jsonl").write_text(
        '{"ts": "9", "prompt_tokens": 100, "completion_tokens": 10, "usd": 0.02}\n'
        '{"ts": "9", "prompt_tokens": 50, "completion_tokens": 5, "usd": null}\n',
        encoding="utf-8",
    )

    cost = run.read_cost(tmp_path, 0)

    assert cost["usd"] is None
    assert cost["prompt_tokens"] == 150, "the tokens are known even when the price is not"


def test_cost_outside_the_window_is_not_charged_to_this_solve(tmp_path: Path) -> None:
    run = _runner()
    (tmp_path / "usage.jsonl").write_text(
        '{"ts": "1", "prompt_tokens": 999, "completion_tokens": 0, "usd": 9.99}\n'
        '{"ts": "9", "prompt_tokens": 100, "completion_tokens": 10, "usd": 0.02}\n',
        encoding="utf-8",
    )

    assert run.read_cost(tmp_path, 5)["prompt_tokens"] == 100


def test_a_missing_usage_log_is_zero_and_unknown_not_a_crash(tmp_path: Path) -> None:
    assert _runner().read_cost(tmp_path, 0) == {
        "prompt_tokens": 0, "completion_tokens": 0, "usd": None
    }


# --------------------------------------------------------------------------- the report

def _rows(**passes: list[bool]) -> list[dict]:
    out = []
    for arm, results in passes.items():
        for index, ok in enumerate(results):
            out.append({
                "task": f"t{index}", "arm": arm, "seed": 42, "passed": ok,
                "prompt_tokens": 100, "completion_tokens": 10,
            })
    return out


def test_the_report_pairs_every_comparison_and_prints_the_price() -> None:
    """Criterion 3 is a cost ceiling, so a lift reported without its price cannot be checked
    against the design at all."""
    run = _runner()
    rows = _rows(
        A_fusion=[True, True, False], B_repeat=[True, False, False], C_single=[False, False, False]
    )

    text = run.report(rows)

    assert "A_fusion" in text and "B_repeat" in text and "C_single" in text
    assert text.count("ratio=") == 3


def test_an_incomplete_grid_says_so_instead_of_reporting_a_number() -> None:
    """A missing cell silently dropped is a comparison over a different task set than the one it
    names — and it reads exactly like a complete result."""
    run = _runner()
    rows = _rows(A_fusion=[True, True], B_repeat=[True])

    assert "incomplete" in run.report(rows)


def test_the_prereg_sha_is_stamped_so_a_result_cannot_be_read_against_another_design() -> None:
    run = _runner()

    assert run.prereg_sha() != "MISSING"
    assert len(run.prereg_sha()) == 12


# --------------------------------------------------------------------------- the stop

def test_the_runner_pilots_before_it_can_spend_the_full_budget() -> None:
    """The registered run is 900 solves. The first invocation pays for a handful, prints what they
    measured, and exits — because the alternative is an estimate, and there is nothing to estimate
    from: the prior bench's journal records outcomes and not cost."""
    source = (REPO / "bench/fusion_paired/run_paired.py").read_text(encoding="utf-8")

    assert '"--full", action="store_true"' in source
    assert "tasks = TASKS if args.full else TASKS[: args.pilot_tasks]" in source
    assert "Nothing here is a result. Re-run with --full to pay for one." in source


def test_the_verdict_is_the_test_and_never_the_arm_self_report() -> None:
    """The fusion arm contains a judge whose whole job is to say things went well. The verdict is
    an independent pytest run, and this is the line that keeps it that way."""
    source = (REPO / "bench/fusion_paired/run_paired.py").read_text(encoding="utf-8")

    assert "def independent_pytest" in source
    assert "ignore what solve claimed" in source
    assert "assert_discriminating" in source, "the apparatus must prove itself before it is paid for"


def test_each_repeat_sample_starts_from_a_fresh_fork() -> None:
    """Letting sample 2 inherit sample 1's edits would make arm B a three-attempt loop — a different
    experiment, and one this project has already run under the name `local_lift`."""
    source = (REPO / "bench/fusion_paired/run_paired.py").read_text(encoding="utf-8")

    assert "ws = setup_workspace(task, root / f\"{arm}_s{seed}_{index}\")" in source


@pytest.mark.parametrize("phrase", [
    "panel", "judge", "synthesiser", "Self-MoA", "Barrel of Monkeys", "review_judge",
])
def test_the_preregistration_states_the_evidence_it_is_testing_against(phrase: str) -> None:
    """Each arm of the split the literature found, named in the design — so the result is read
    against what was already known rather than against whatever is remembered afterwards."""
    assert phrase in PREREG.read_text(encoding="utf-8")
