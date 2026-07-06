"""Tests for the honest A/B engine + terminal-bench adapter (M14 A1)."""

from __future__ import annotations

from chimera.eval.bench_ab import Arm, format_report
from chimera.eval.bench_ab import compare as compare_ab
from chimera.eval.terminal_bench import build_solve_command, command_string, make_chimera_tb_agent

# --- Arm stats --------------------------------------------------------------------------


def test_arm_rate_and_wilson() -> None:
    arm = Arm("x", [True, True, True, False])
    assert arm.n == 4 and arm.successes == 3 and arm.rate == 0.75
    low, high = arm.wilson
    assert 0.0 <= low <= 0.75 <= high <= 1.0  # Wilson brackets the point estimate


def test_empty_arm_is_safe() -> None:
    arm = Arm("empty", [])
    assert arm.n == 0 and arm.rate == 0.0 and arm.wilson == (0.0, 1.0)


# --- A/B comparison ---------------------------------------------------------------------


def test_clear_win_is_significant() -> None:
    # Treatment passes almost everything, baseline almost nothing, over a decent N.
    result = compare_ab([False] * 18 + [True] * 2, [True] * 18 + [False] * 2)
    assert result.delta > 0.7
    assert result.diff_ci[0] > 0.0 and result.significant is True


def test_tiny_or_equal_samples_not_significant() -> None:
    # Same rate -> delta 0, CI straddles 0.
    equal = compare_ab([True, False, True, False], [True, False, True, False])
    assert equal.delta == 0.0 and equal.significant is False
    # A 1-of-3 vs 2-of-3 edge is far too small to be significant.
    tiny = compare_ab([True, False, False], [True, True, False])
    assert tiny.significant is False


def test_summary_and_report_shape() -> None:
    result = compare_ab([False, False, True], [True, True, True], treatment_name="chimera")
    s = result.summary()
    for key in ("baseline_rate", "treatment_rate", "delta", "ci_low", "ci_high", "significant"):
        assert key in s
    report = format_report(result)
    assert "delta" in report and "chimera" in report and "verdict" in report


# --- terminal-bench adapter (pure parts) ------------------------------------------------


def test_build_solve_command_is_deterministic() -> None:
    argv = build_solve_command("fix the failing test", model="openrouter/free/x")
    assert argv[:3] == ["chimera", "solve", "fix the failing test"]
    assert "--model" in argv and "openrouter/free/x" in argv
    # the scaffolding-under-test flags are present (this is Chimera's contribution over raw model)
    assert "--repo-map" in argv and "--progress-ledger" in argv and "--replan" in argv


def test_command_string_quotes_the_instruction() -> None:
    argv = build_solve_command("do a thing; rm -rf /", model="m")
    s = command_string(argv)
    assert "'do a thing; rm -rf /'" in s  # instruction is a single quoted arg, never interpolated


def test_baseline_flags_can_be_stripped() -> None:
    argv = build_solve_command("t", model="m", flags=())
    assert "--repo-map" not in argv  # an ablation arm can run Chimera with no scaffolding flags


def test_adapter_needs_the_extra() -> None:
    # terminal_bench isn't installed in dev -> friendly ImportError, not a bare one.
    import pytest

    with pytest.raises(ImportError, match=r"chimera-agent\[bench\]"):
        make_chimera_tb_agent("some/model")
