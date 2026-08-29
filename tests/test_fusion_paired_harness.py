"""The apparatus for the fusion experiment, checked before it is allowed to cost anything.

The experiment itself is registered in `bench/fusion_paired/PREREGISTRATION.md` and has not run. What
these tests hold is the machinery, because an experiment whose harness is wrong produces a number
that looks exactly like a result — and this one is meant to decide the project's central bet.

Everything here is deterministic and free: no model call, no network. What is asserted is the shape
of the comparison, not its outcome.
"""

from __future__ import annotations

import importlib.util
import json
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


def test_no_arm_can_carry_anything_in_from_the_cell_before_it() -> None:
    """The loop is `for task -> for seed -> for arm`, so without this every cell teaches the next.

    Arm A solves a task, writes a long-term memory fact and may propose a learned skill; arms B and C
    then solve THE SAME TASK with it in reach. The contamination runs WITH the arm order, so it
    credits whichever arm ran last, and no recorded field would have shown it. Every other runner in
    `bench/local_lift` has carried these four flags for exactly this reason; this one did not until
    ADDENDUM-01.
    """
    arms = _runner().ARMS
    required = {"--no-remember", "--no-collect", "--no-evolve-skills", "--no-skill-cards"}

    for name, spec in arms.items():
        missing = required - set(spec["flags"])
        assert not missing, f"{name} can carry state between cells: missing {sorted(missing)}"


def test_the_single_model_arms_are_pinned_to_a_panel_grade_model() -> None:
    """Unpinned, arms B and C inherited `settings.default_model` — a cheap model — against a frontier
    panel. That would have made criterion 1 a measurement of model tier and criterion 3 a comparison
    between price classes, which is the failure `§2g` names: two things measured different ways.

    Arm A stays unpinned on purpose: `--fuse` leaves non-deep turns on the default model, and that
    mixed route IS the shipped product. Pinning it would measure something no user gets.
    """
    run = _runner()

    for arm in ("B_repeat", "C_single"):
        flags = run.ARMS[arm]["flags"]
        assert "--model" in flags, f"{arm} would inherit the cheap default model"
        assert flags[flags.index("--model") + 1] == run.SINGLE_MODEL
    assert "--model" not in run.ARMS["A_fusion"]["flags"], "arm A must be the shipped route"
    assert run.SINGLE_MODEL in run.SCREEN_MODELS, "the declared single model is not a panel member"


def test_the_screen_can_exclude_a_model_and_can_never_promote_one() -> None:
    """Five tasks at one seed cannot resolve a real difference between frontier models. The screen
    exists to catch a BROKEN one — zero solved is a ruler that stopped reporting, `§2e` — and letting
    a good showing pick the model instead would turn a declaration into a forking path.

    Asserted on the source because the promotion that must not exist is an absence, and an absence
    has no behaviour to call.
    """
    source = (REPO / "bench/fusion_paired/run_paired.py").read_text(encoding="utf-8")

    assert "hits == 0" in source, "nothing excludes a model that solved nothing"
    assert "SINGLE_MODEL =" in source and source.count("SINGLE_MODEL =") == 1, (
        "SINGLE_MODEL is assigned more than once: the screen may be promoting"
    )
    assert "raise SystemExit(2)" in source, "a dead single model must stop the run, not run it"


def test_the_addendum_is_stamped_beside_the_preregistration() -> None:
    """It changed the apparatus rather than the design, and a row that does not say which apparatus
    produced it cannot be compared with one that does."""
    run = _runner()
    addendum = REPO / "bench/fusion_paired/ADDENDUM-01.md"

    assert addendum.is_file()
    assert run.addendum_sha() != "MISSING"
    text = addendum.read_text(encoding="utf-8")
    # The direction of the residual bias, written before the number exists rather than after.
    assert "favour of fusion" in text
    assert "declaration" in text and "not a selection" in text


# --------------------------------------------------------------------------- the accounting

def _runs_log(tmp_path: Path, *attempts: dict, workspace: str = "w/A_fusion_s42_0/t1") -> Path:
    """A `runs.jsonl` shaped like the one a CLI solve writes: the receipt is on the ATTEMPT."""
    (tmp_path / "runs.jsonl").write_text(
        json.dumps({"workspace": f"/tmp/workspaces/{workspace}", "attempts": list(attempts)}) + "\n",
        encoding="utf-8",
    )
    return tmp_path


def test_the_cost_comes_from_the_run_receipt_and_not_from_the_api_usage_log(tmp_path: Path) -> None:
    """The reader looked in `usage.jsonl`, which only the API layer writes, and in `~/.chimera`, when
    `settings.home` is the RELATIVE `.chimera`. It returned `0 tokens, $0.00` for six paid solves and
    the token ratio — criterion 3 — printed `inf`. A confident zero is worse than a missing number.
    """
    run = _runner()
    home = _runs_log(
        tmp_path, {"prompt_tokens": 100, "completion_tokens": 10, "usd": 0.02, "model": "m"}
    )
    # An `usage.jsonl` beside it, holding the wrong answer, so the test fails if the old path returns.
    (tmp_path / "usage.jsonl").write_text(
        '{"ts": "9", "prompt_tokens": 999, "completion_tokens": 999, "usd": 9.99}\n', encoding="utf-8"
    )

    cost = run.read_cost(home, Path("/tmp/workspaces/w/A_fusion_s42_0/t1"))

    assert cost["prompt_tokens"] == 100 and cost["completion_tokens"] == 10
    assert cost["usd"] == 0.02
    assert cost["tokens_known"] is True


def test_a_cell_that_joins_no_receipt_is_unknown_and_never_zero(tmp_path: Path) -> None:
    """The failure that actually happened, kept as a test: a paid run whose cost reads zero looks
    exactly like a cheap run, and the full-run estimate is built on that rate."""
    run = _runner()
    home = _runs_log(tmp_path, {"prompt_tokens": 100, "completion_tokens": 10, "usd": 0.02})

    cost = run.read_cost(home, Path("/tmp/workspaces/w/SOMETHING_ELSE/t1"))

    assert cost["tokens_known"] is False
    assert cost["usd"] is None, "an unjoined cell must not report a price"


def test_one_unpriced_attempt_makes_the_total_unknown_not_smaller(tmp_path: Path) -> None:
    """The direction that matters: a partial sum flatters whichever arm used the unpriced model, and
    the frontier models are exactly the ones the local catalogue does not price."""
    run = _runner()
    home = _runs_log(
        tmp_path,
        {"prompt_tokens": 100, "completion_tokens": 10, "usd": 0.02},
        {"prompt_tokens": 50, "completion_tokens": 5, "usd": None},
    )

    cost = run.read_cost(home, Path("/tmp/workspaces/w/A_fusion_s42_0/t1"))

    assert cost["usd"] is None
    assert cost["prompt_tokens"] == 150, "the tokens are known even when the price is not"


def test_a_missing_run_log_is_unknown_not_a_crash(tmp_path: Path) -> None:
    cost = _runner().read_cost(tmp_path, Path("/tmp/workspaces/w/x/t1"))
    assert cost["tokens_known"] is False and cost["usd"] is None


def test_arm_b_pays_for_every_sample_it_took(tmp_path: Path) -> None:
    """Arm B is three solves in three forks. Charging it for one would halve the denominator of the
    token ratio and hand fusion a ceiling it never had to clear."""
    run = _runner()
    log = tmp_path / "runs.jsonl"
    log.write_text("".join(
        json.dumps({
            "workspace": f"/tmp/workspaces/B_repeat_s42_{i}/t1",
            "attempts": [{"prompt_tokens": 100, "completion_tokens": 10, "usd": 0.01}],
        }) + "\n" for i in range(3)
    ), encoding="utf-8")

    cost = run._sum_costs(tmp_path, [Path(f"/tmp/workspaces/B_repeat_s42_{i}/t1") for i in range(3)])

    assert cost["prompt_tokens"] == 300 and cost["completion_tokens"] == 30
    assert cost["usd"] == pytest.approx(0.03)


def test_a_fusion_that_spent_what_the_control_spent_is_void_not_null() -> None:
    """ADDENDUM-02. Arm A adds a fused planning turn over arm C and nothing else, so an A/C ratio of
    1.0 says the intervention never fired. Without this line an inert run reads as a clean null —
    the strongest conclusion the experiment could produce, drawn from nothing happening.
    """
    run = _runner()
    rows = _rows(A_fusion=[True, False], B_repeat=[True, False], C_single=[True, False])

    text = run.report(rows)

    assert "ACTIVATION" in text and "void, not null" in text


def test_an_arm_that_read_no_receipt_blocks_the_full_run_estimate() -> None:
    """The confident zero, one level up: a rate built on cells that joined nothing understates the
    registered run by however much those cells actually cost."""
    source = (REPO / "bench/fusion_paired/run_paired.py").read_text(encoding="utf-8")

    assert "NO ESTIMATE:" in source
    assert 'r.get("tokens_known", True)' in source


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
