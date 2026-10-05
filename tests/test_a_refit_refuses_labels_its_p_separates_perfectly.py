"""A refit on labels that the raw ``p`` separates perfectly is refused (study 30, S30-38).

`fit_platt` carries a ridge of 1e-4, which is too small to stop the slope when no threshold on ``p``
misclassifies a single label: measured on 25 + 25 separated rows, ``a`` ran to 38 and the map sent
p = 0.50 to 0.00001 and p = 0.60 to 0.988 — a step function that the band would read as certainty.
The labels say only that the classes do not overlap *in this sample*; they cannot say how sure the
model should be. So ``chimera decisions refit`` gives that group no map, and says why, instead of
writing one that looks perfect in-sample.

Exact separation is not the only cliff: one label just across the boundary gives a = 19.2. The refit
also refuses when the plain fit and a fit on Platt's smoothed targets disagree by more than
``MAX_TARGET_SENSITIVITY`` — the fit's steepness then comes from treating the labels as certain.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.decisions import CalibrationMaps
from chimera.decisions.calibration import (
    MAX_TARGET_SENSITIVITY,
    fit_platt,
    perfectly_separated,
    target_sensitivity,
)
from chimera.decisions.governance import DECISION
from chimera.decisions.labels import refit
from chimera.decisions.log import DecisionLog, read
from chimera.decisions.maps import SHIPPED_MAPS

SHIPPED = SHIPPED_MAPS[0]


def _log(path: Path, pairs: list[tuple[float, int]]) -> None:
    log = DecisionLog(path)
    for raw_p, label in pairs:
        receipt = {"decision": DECISION, "backend": "local_logprob", "model": "qwen3:4b",
                   "prompt_hash": SHIPPED.prompt_hash, "resolved_model": SHIPPED.resolved_model,
                   "calibrated": True, "p": 0.5}
        log.outcome(log.answer(receipt, "s", raw_p=raw_p), bool(label), source="cli")


def _spread(lo: float, hi: float, n: int) -> list[float]:
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def test_a_refit_on_perfectly_separated_labels_gets_no_map(tmp_path: Path) -> None:
    pairs = [(p, 1) for p in _spread(0.60, 0.99, 25)] + [(p, 0) for p in _spread(0.05, 0.55, 25)]
    _log(tmp_path / "d.jsonl", pairs)
    (result,) = refit(read(tmp_path / "d.jsonl"), CalibrationMaps.shipped())
    assert result.map is None
    assert "separate" in result.reason


def test_separation_in_the_other_direction_is_refused_too(tmp_path: Path) -> None:
    pairs = [(p, 0) for p in _spread(0.60, 0.99, 25)] + [(p, 1) for p in _spread(0.05, 0.55, 25)]
    _log(tmp_path / "d.jsonl", pairs)
    (result,) = refit(read(tmp_path / "d.jsonl"), CalibrationMaps.shipped())
    assert result.map is None


def test_one_overlapping_label_deep_in_the_other_class_is_enough_to_fit(tmp_path: Path) -> None:
    pairs = [(p, 1) for p in _spread(0.60, 0.99, 25)] + [(p, 0) for p in _spread(0.05, 0.55, 24)] + [(0.95, 0)]
    _log(tmp_path / "d.jsonl", pairs)
    (result,) = refit(read(tmp_path / "d.jsonl"), CalibrationMaps.shipped())
    assert result.map is not None
    assert 0.02 < result.map.apply(0.5) < 0.98  # not a step


def test_one_overlapping_label_at_the_class_boundary_still_gets_no_map(tmp_path: Path) -> None:
    # Not separated: the negative at 0.61 sits above the lowest positive (0.60). The plain fit still
    # gives a = 19.2, sending p = 0.55 to 0.02 and p = 0.62 to 0.85 — the cliff, one label short of
    # the case the separation guard catches.
    pairs = [(p, 1) for p in _spread(0.60, 0.99, 25)] + [(p, 0) for p in _spread(0.05, 0.55, 24)] + [(0.61, 0)]
    assert not perfectly_separated(pairs)
    a, _ = fit_platt(pairs)
    assert a > 15  # the cliff is real on the plain fit
    assert target_sensitivity(pairs) > MAX_TARGET_SENSITIVITY
    _log(tmp_path / "d.jsonl", pairs)
    (result,) = refit(read(tmp_path / "d.jsonl"), CalibrationMaps.shipped())
    assert result.map is None
    assert "nearly separate" in result.reason


def test_the_shipped_rows_sit_well_inside_the_bound() -> None:
    # The gate the bound must not trip: a refit on the shipped map's own rows reproduces it.
    from chimera.decisions.maps import SHIPPED_ROWS

    assert target_sensitivity(list(SHIPPED_ROWS[SHIPPED.id])) < MAX_TARGET_SENSITIVITY / 2


def test_a_direct_fit_refuses_separated_labels_too() -> None:
    # Not only the refit: `fit_platt` and `PlattMap.fit` are public, and a caller other than refit
    # would otherwise get the a = 38 step without a word.
    pairs = [(p, 1) for p in _spread(0.60, 0.99, 25)] + [(p, 0) for p in _spread(0.05, 0.55, 25)]
    with pytest.raises(ValueError, match="separates"):
        fit_platt(pairs)


def test_quasi_separation_with_a_tie_at_the_boundary_counts_as_separated() -> None:
    # A tie at the boundary still lets the slope run away: the likelihood keeps rising with it.
    assert perfectly_separated([(0.2, 0), (0.5, 0), (0.5, 1), (0.9, 1)])
    assert perfectly_separated([(0.9, 0), (0.1, 1), (0.2, 1), (0.8, 0)])


def test_one_value_for_every_label_is_not_separation() -> None:
    # Every row at the same p: no threshold separates anything, and the fit is the base rate.
    assert not perfectly_separated([(0.5, 0), (0.5, 1), (0.5, 1), (0.5, 0)])
    assert not perfectly_separated([(0.2, 0), (0.9, 0), (0.5, 1), (0.6, 1)])
