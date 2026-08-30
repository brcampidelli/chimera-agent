"""The apparatus for the cost-routing experiment, checked before it is allowed to cost anything.

The experiment is registered in `bench/cost_routing/PREREGISTRATION.md` and has not run. What these
tests hold is the machinery — and one thing more than usual, because this design is the kind most
easily got wrong: it is a **non-inferiority** test, and the failure mode is accepting the null.
"No significant difference" on a small sample means no difference *could* be found, and reading it
as "they are the same" is how a cheap model gets adopted on the strength of a wide interval.

Everything here is deterministic and free: no model call, no network.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
PREREG = REPO / "bench/cost_routing/PREREGISTRATION.md"
SOURCE = REPO / "bench/cost_routing/run_routing.py"


def _runner() -> Any:
    sys.path.insert(0, str(REPO / "bench/local_lift"))
    spec = importlib.util.spec_from_file_location("cost_routing_runner", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- the design


def test_it_is_registered_as_a_non_inferiority_test() -> None:
    """The distinction that decides what the result means. A superiority design asks "is cheap
    better?", gets "no", and teaches nothing; this one asks "is cheap not meaningfully worse?" —
    and "meaningfully" has to be a number fixed before the data exists."""
    text = PREREG.read_text(encoding="utf-8")

    assert "non-inferiority" in text.lower()
    # Sem `or`: uma asserção com alternativa e' satisfeita por qualquer metade, entao apagar a
    # sentenca que REGISTRA a margem passaria batido enquanto o criterio a repetisse mais abaixo.
    # As duas tem de existir — a que fixa o numero e a que o usa para decidir.
    assert "**Registered margin: 10 percentage points.**" in text, "a margem deixou de ser fixada"
    assert "−10.0 pp" in text, "o criterio deixou de usar a margem"
    # The margin binds through the interval, not the point estimate: a wide CI must FAIL.
    assert "95% CI lower bound" in text
    assert "Accepting the null" in text


def test_the_outcome_most_likely_to_be_misread_is_named_in_advance() -> None:
    """An underpowered run reported as "no difference found" is the way this experiment gets
    misused, so the pre-registration says so before the number exists rather than after."""
    text = PREREG.read_text(encoding="utf-8")

    assert "underpowered" in text
    assert 'NOT reported as "no' in text


def test_the_shipped_default_is_an_arm() -> None:
    """The decision on the table is "change the default". Measuring a candidate against a frontier
    model alone compares it to something nobody runs."""
    arms = _runner().ARMS

    assert set(arms) == {"A_flash", "B_frontier", "C_shipped"}
    assert "deepseek-chat-v3.1" in arms["C_shipped"], "arm C must be today's default, unchanged"


def test_three_seeds_not_two() -> None:
    assert len(_runner().SEEDS) == 3


def test_the_cost_criterion_is_absolute_and_has_a_floor() -> None:
    """A saving under 10x does not pay for the accuracy risk, and a multiplicative criterion on the
    ACCURACY side has misfired in this repo before."""
    text = PREREG.read_text(encoding="utf-8")

    assert "cost ratio B/A ≥ 10×" in text
    assert "Absolute, never multiplicative on the accuracy side" in text


# --------------------------------------------------------------------------- the ceiling gate


def test_the_band_stops_a_corpus_that_answers_before_the_models_do() -> None:
    """Four ceilings in this project's history: local_lift at 100% for three frontier models, GSM8K
    at 100% oracle, and three attempts at a 40-60% band that landed at 84-92%. A margin satisfied
    by a ceiling is satisfied by the ceiling."""
    run = _runner()

    assert run.BAND == (0.20, 0.90)
    source = SOURCE.read_text(encoding="utf-8")
    assert "if not BAND[0] <= rate <= BAND[1]:" in source, "the band is written down but not enforced"
    assert "--stage2" in source, "the paid stage must be opt-in"


def test_too_few_discriminating_tasks_stops_the_run() -> None:
    """A band satisfied by three tasks is a band satisfied by three tasks. Reporting the corpus as
    the limit IS the finding, and it costs nothing to say."""
    run = _runner()

    assert run.MIN_TASKS == 20
    assert "The corpus, not the models, is the limit" in SOURCE.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- the accounting


def _log(tmp_path: Path, *rows: dict) -> Path:
    import json

    (tmp_path / "runs.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    return tmp_path


def test_an_earlier_run_of_the_same_cell_is_not_charged_to_this_one(tmp_path: Path) -> None:
    """The workspace path repeats between runs. Caught in a pilot here: a cell read 86,983 prompt
    tokens where the same cell's own earlier run had measured 43,219 — the two added up."""
    run = _runner()
    home = _log(
        tmp_path,
        {"ts": "2026-08-01T00:00:00+00:00", "workspace": "/w/t__A_flash__s42",
         "attempts": [{"prompt_tokens": 900, "completion_tokens": 90, "usd": 0.9}]},
        {"ts": "2026-08-28T00:00:00+00:00", "workspace": "/w/t__A_flash__s42",
         "attempts": [{"prompt_tokens": 100, "completion_tokens": 10, "usd": 0.1}]},
    )

    scoped = run.cost(home, Path("/w/t__A_flash__s42"), "2026-08-27T00:00:00+00:00")

    assert scoped["prompt"] == 100, "an earlier run of the same cell was charged to this one"


def test_a_cell_that_joins_no_receipt_is_unknown_and_never_zero(tmp_path: Path) -> None:
    """A paid cell reading zero looks exactly like a cheap one, and the per-cell rate is what every
    projection is built on."""
    run = _runner()
    home = _log(tmp_path, {"ts": "2026-08-28T00:00:00+00:00", "workspace": "/w/other",
                           "attempts": [{"prompt_tokens": 1, "usd": 0.1}]})

    got = run.cost(home, Path("/w/t__A_flash__s42"), "")

    assert got["known"] is False
    assert got["usd"] is None


def test_one_unpriced_attempt_makes_the_total_unknown_not_smaller(tmp_path: Path) -> None:
    """The direction that matters: a partial sum flatters whichever arm used the unpriced model."""
    run = _runner()
    home = _log(tmp_path, {"ts": "2026-08-28T00:00:00+00:00", "workspace": "/w/c",
                           "attempts": [{"prompt_tokens": 100, "usd": 0.02},
                                        {"prompt_tokens": 50, "usd": None}]})

    got = run.cost(home, Path("/w/c"), "")

    assert got["usd"] is None
    assert got["prompt"] == 150, "the tokens are known even when the price is not"


# --------------------------------------------------------------------------- dead vs failed


def test_a_crashed_cell_leaves_the_denominator_and_says_so() -> None:
    """Measured during the four projects: one arm produced 0 tokens in 21 seconds and no file, and
    the same model worked when re-probed alone. A crash and an incapacity produce the same row and
    only one is evidence — so the exclusion is counted out loud rather than done in silence."""
    run = _runner()
    rows = [
        {"task": "t1", "arm": "A_flash", "seed": 42, "passed": False, "crashed": True,
         "prompt": 0, "completion": 0, "usd": None},
        {"task": "t1", "arm": "B_frontier", "seed": 42, "passed": True, "crashed": False,
         "prompt": 10, "completion": 1, "usd": 0.1},
        {"task": "t1", "arm": "C_shipped", "seed": 42, "passed": True, "crashed": False,
         "prompt": 10, "completion": 1, "usd": 0.01},
    ]

    text = run.report(rows)

    assert "excluded 1 crashed cell" in text


def test_every_cell_records_what_it_did() -> None:
    """Without the exit code and the tail, a cell that joined no receipt leaves no evidence of what
    happened — and the diagnosis has to be done by reading the workspace off disk, which is exactly
    how the last one had to be done."""
    source = SOURCE.read_text(encoding="utf-8")

    assert '"exit": ran["exit"]' in source
    assert '"tail": ran["tail"][-300:]' in source
    assert "124" in source, "a timeout must be distinguishable from a wrong answer"


def test_no_cell_can_teach_the_next_one() -> None:
    """The loop is task -> arm -> seed, so without hygiene an early arm writes a memory fact and a
    later arm solves THE SAME TASK with it in reach. The bias runs with arm order."""
    source = SOURCE.read_text(encoding="utf-8")

    for flag in ("--no-remember", "--no-collect", "--no-evolve-skills", "--no-skill-cards"):
        assert flag in source, f"a cell can carry state into the next: {flag} missing"


def test_the_verdict_is_the_gate_and_never_the_arm_self_report() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "ignore what solve claimed" in source
    assert "def gate(" in source


# --------------------------------------------------------------------------- the limits


@pytest.mark.parametrize("phrase", [
    "Only gated code tasks",
    "Three models, not a tier",
    "Nothing about latency",
])
def test_the_limits_are_registered_before_the_run(phrase: str) -> None:
    """Including the one that argues AGAINST the change even if it wins: the cheapest arm in the
    four projects was often the slowest — 1,496s against 174s — and a default that saves money and
    triples wall-clock is a different trade from the one being measured."""
    assert phrase in PREREG.read_text(encoding="utf-8")
