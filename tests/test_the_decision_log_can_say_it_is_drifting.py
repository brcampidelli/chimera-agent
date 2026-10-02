"""Drift alarms computed from the log alone (study 27, phase 3): they annotate and gate nothing.

The statistics are checked against published table values, not against themselves, because a chi-square
survival function that is wrong by a factor shows up nowhere else: an alarm that never fires and an
alarm that always fires both print a plausible report.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from chimera.decisions import drift
from chimera.decisions.log import Row


def row(p: float, *, at: float, build: str = "m-2026-01", decision: str = "governance.danger",
        calibrated: bool = True, halt: str = "") -> Row:
    answer: dict[str, Any] = {
        "decision": decision, "backend": "local_logprob", "model": "qwen3:4b", "prompt_hash": "abc",
        "resolved_model": build, "p": p, "raw_p": p, "calibrated": calibrated, "at": at,
    }
    if halt:
        answer["halt"] = halt
    return Row(answer=answer, label=None, source=None)


def series(values: list[float], *, start: float = 0.0, **kw: Any) -> list[Row]:
    return [row(v, at=start + i, **kw) for i, v in enumerate(values)]


# --- the statistics, against table values -----------------------------------------------------


@pytest.mark.parametrize(
    ("statistic", "df", "expected"),
    [
        (3.841458820694124, 1, 0.05),
        (5.991464547107979, 2, 0.05),
        (16.918977604620448, 9, 0.05),
        (21.665994333461924, 9, 0.01),
        (0.0, 4, 1.0),
    ],
)
def test_the_chi_square_survival_function_matches_the_tables(statistic: float, df: int, expected: float) -> None:
    assert drift.chi2_sf(statistic, df) == pytest.approx(expected, abs=1e-4)


def test_the_survival_function_is_monotone_and_bounded() -> None:
    values = [drift.chi2_sf(x, 9) for x in (0.5, 2, 5, 10, 20, 40, 80)]
    assert values == sorted(values, reverse=True)
    assert all(0.0 <= v <= 1.0 for v in values)


def test_psi_is_zero_for_identical_histograms_and_large_for_disjoint_ones() -> None:
    same = [5, 5, 5, 5, 5, 5, 5, 5, 5, 5]
    assert drift.psi(same, same) == pytest.approx(0.0)
    low = [50, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    high = [0, 0, 0, 0, 0, 0, 0, 0, 0, 50]
    assert drift.psi(low, high) > 5.0


def test_homogeneity_has_nothing_to_say_about_a_single_occupied_bin() -> None:
    assert drift.homogeneity([10, 0, 0], [5, 0, 0]) is None


# --- the build changed ------------------------------------------------------------------------


def test_a_second_serving_build_is_an_alarm_with_when_each_was_first_seen() -> None:
    rows = series([0.3] * 5, build="m-2026-01") + series([0.3] * 5, start=100, build="m-2026-02")

    found = drift.model_changed(rows)

    assert [a.kind for a in found] == ["model_changed"]
    assert [b["build"] for b in found[0].detail["builds"]] == ["m-2026-01", "m-2026-02"]
    assert found[0].detail["builds"][1]["first_seen"] == 100


def test_one_build_or_an_unnamed_build_is_not_an_alarm() -> None:
    assert drift.model_changed(series([0.3] * 5)) == []
    assert drift.model_changed(series([0.3] * 5, build="")) == []


def test_a_build_change_in_one_decision_is_not_charged_to_another() -> None:
    rows = series([0.3] * 5, build="a") + series([0.3] * 5, build="b", decision="other.spec")
    assert drift.model_changed(rows) == []


# --- the distribution moved -------------------------------------------------------------------


def test_a_window_that_moved_far_from_the_rest_is_an_alarm() -> None:
    rng = random.Random(7)
    before = [min(0.99, max(0.01, rng.gauss(0.25, 0.08))) for _ in range(120)]
    after = [min(0.99, max(0.01, rng.gauss(0.75, 0.08))) for _ in range(50)]

    found = drift.answer_drift(series(before + after))

    assert [a.kind for a in found] == ["answer_drift"]
    assert found[0].detail["psi"] > drift.PSI_ALARM and found[0].detail["p_value"] < drift.ALPHA


def test_a_stable_stream_is_quiet_however_long() -> None:
    rng = random.Random(11)
    values = [min(0.99, max(0.01, rng.gauss(0.4, 0.15))) for _ in range(400)]

    assert drift.answer_drift(series(values)) == []


def test_too_few_answers_to_test_is_silence_not_an_alarm() -> None:
    assert drift.answer_drift(series([0.1] * 20 + [0.9] * 20)) == []


def test_halts_and_uncalibrated_numbers_do_not_feed_the_distribution() -> None:
    rng = random.Random(3)
    calm = [min(0.99, max(0.01, rng.gauss(0.4, 0.1))) for _ in range(140)]
    halts = [row(0.99, at=1000 + i, halt="ConnectError") for i in range(60)]

    assert drift.answer_drift(series(calm) + halts) == []


# --- answers on a threshold -------------------------------------------------------------------


def test_a_window_crowding_a_cut_is_an_alarm_naming_the_cut() -> None:
    values = [0.5 + 0.01 * (i % 3) for i in range(30)] + [0.05] * 10

    found = drift.near_threshold(series(values), review_at=0.5, allow_below=0.2)

    assert [a.detail["cut"] for a in found] == ["review_at"]
    assert found[0].detail["near"] == 30 and found[0].detail["of"] == 40


def test_numbers_far_from_both_cuts_are_quiet() -> None:
    assert drift.near_threshold(series([0.9] * 30 + [0.05] * 30), review_at=0.5, allow_below=0.2) == []


def test_an_uncalibrated_number_is_not_placed_against_the_band() -> None:
    rows = series([0.5] * 40, calibrated=False)
    assert drift.near_threshold(rows, review_at=0.5, allow_below=0.2) == []


# --- together ---------------------------------------------------------------------------------


def test_alerts_are_stable_ordered_and_serialisable() -> None:
    rows = series([0.5] * 30, build="a") + series([0.5] * 30, start=100, build="b")

    found = drift.alerts(rows, review_at=0.5, allow_below=0.2)

    assert [a.kind for a in found][0] == "model_changed"
    assert all(set(a.as_dict()) == {"kind", "decision", "backend", "model", "prompt_hash", "detail"} for a in found)
