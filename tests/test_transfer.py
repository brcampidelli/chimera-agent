"""Transfer-gated promotion (from EvoAgentBench 2607.05202): a change must help its tuned
slice AND not regress a disjoint same-capability holdout."""

from __future__ import annotations

from chimera.eval.transfer import transfer_gated_promotion


def _pat(n_pass: int, n_fail: int) -> list[bool]:
    return [True] * n_pass + [False] * n_fail


def test_promotes_when_tuned_helps_and_holdout_holds() -> None:
    # tuned: candidate wins several discordant pairs; holdout: parity (no regression).
    d = transfer_gated_promotion(
        tuned_baseline=[False, False, False, True, True],
        tuned_treatment=[True, True, True, True, True],
        holdout_baseline=[True, False, True, False],
        holdout_treatment=[True, False, True, False],
    )
    assert d.promote is True
    assert d.transfer_measured is True
    assert "generalizes" in d.reason


def test_blocks_negative_transfer_even_when_tuned_improves() -> None:
    # The EvoAgentBench failure: helps the tuned slice, REGRESSES the same-capability holdout.
    d = transfer_gated_promotion(
        tuned_baseline=[False, False, False, False],
        tuned_treatment=[True, True, True, True],
        holdout_baseline=[True, True, True, True],
        holdout_treatment=[False, False, True, True],  # 100% -> 50% on the holdout
    )
    assert d.promote is False
    assert d.transfer_measured is True
    assert "NEGATIVE TRANSFER" in d.reason


def test_blocks_when_tuned_does_not_improve() -> None:
    d = transfer_gated_promotion(
        tuned_baseline=_pat(3, 1),
        tuned_treatment=_pat(2, 2),  # regressed on its own slice
    )
    assert d.promote is False
    assert "did not improve" in d.reason


def test_falls_back_to_tuned_when_no_holdout_but_flags_untested() -> None:
    d = transfer_gated_promotion(
        tuned_baseline=[False, False, True],
        tuned_treatment=[True, True, True],
    )
    assert d.promote is True
    assert d.transfer_measured is False
    assert "TRANSFER NOT MEASURED" in d.reason


def test_regression_tolerance_allows_small_dips() -> None:
    # A tiny holdout dip within tolerance should NOT block.
    d = transfer_gated_promotion(
        tuned_baseline=[False, False],
        tuned_treatment=[True, True],
        holdout_baseline=[True] * 10,
        holdout_treatment=[True] * 9 + [False],  # 100% -> 90%, a 10% dip
        holdout_regression_tol=0.15,
    )
    assert d.promote is True
