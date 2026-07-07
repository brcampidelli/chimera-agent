"""Tests for fusion receipts — per-advisor cost attribution + cost×quality (M15-B3)."""

from __future__ import annotations

from pathlib import Path

from chimera.fusion.engine import FusionTrace, PanelResponse, StageUsage
from chimera.fusion.receipts import (
    ModelPrice,
    append_receipt,
    format_summary,
    load_receipts,
    price_stage,
    receipt_from_trace,
    resolve_price,
    set_price,
    summarize,
)


def _trace(*, early_stopped: bool = False) -> FusionTrace:
    """A trace with two priced panel advisors + a judge + a synth stage."""
    return FusionTrace(
        panel=[PanelResponse(model="deepseek/deepseek-chat"), PanelResponse(model="meta-llama/llama-3.1-8b")],
        judge_analysis="j",
        final="answer",
        usage=[
            StageUsage("panel", "deepseek/deepseek-chat", 1_000_000, 1_000_000),  # $0.14 + $0.28
            StageUsage("panel", "meta-llama/llama-3.1-8b", 1_000_000, 1_000_000),  # $0.05 + $0.05
            StageUsage("judge", "deepseek/deepseek-chat", 1_000_000, 0),  # $0.14
            StageUsage("synth", "deepseek/deepseek-chat", 0, 1_000_000),  # $0.28
        ],
        early_stopped=early_stopped,
    )


# --- pricing -----------------------------------------------------------------------------


def test_resolve_price_by_family_substring() -> None:
    assert resolve_price("openrouter/deepseek/deepseek-chat-v3.1") == ModelPrice(0.14, 0.28)
    assert resolve_price("meta-llama/llama-3.1-8b-instruct") == ModelPrice(0.05, 0.05)


def test_unknown_model_prices_to_none_not_a_guess() -> None:
    assert resolve_price("some-obscure/model-x") is None


def test_price_stage_computes_usd_at_own_rate() -> None:
    cost = price_stage(StageUsage("panel", "deepseek/deepseek-chat", 1_000_000, 1_000_000))
    assert cost.usd == round(0.14 + 0.28, 6)


def test_price_stage_unknown_model_is_none() -> None:
    assert price_stage(StageUsage("panel", "mystery/model", 1000, 1000)).usd is None


# --- per-advisor attribution + totals ----------------------------------------------------


def test_receipt_attributes_cost_per_advisor() -> None:
    rcpt = receipt_from_trace(_trace())
    advisors = rcpt.advisor_costs
    assert advisors["deepseek/deepseek-chat"] == round(0.14 + 0.28, 6)
    assert advisors["meta-llama/llama-3.1-8b"] == round(0.05 + 0.05, 6)
    # total = both panels + judge + synth
    assert rcpt.total_usd == round(0.42 + 0.10 + 0.14 + 0.28, 6)
    assert rcpt.total_tokens == 6_000_000


def test_total_is_none_if_any_stage_unpriced() -> None:
    trace = _trace()
    trace.usage.append(StageUsage("panel", "unknown/model", 1000, 1000))
    # An unknown price must poison the total to None — never masquerade as free.
    assert receipt_from_trace(trace).total_usd is None


def test_set_price_override() -> None:
    set_price("myco/custom", ModelPrice(1.0, 2.0))
    assert resolve_price("myco/custom-large") == ModelPrice(1.0, 2.0)


# --- persistence + summary ---------------------------------------------------------------


def test_persist_and_summarize_cost_quality(tmp_path: Path) -> None:
    path = tmp_path / "receipts.jsonl"
    # Two full-fusion runs (one passed, one not) + one selective short-circuit.
    append_receipt(path, receipt_from_trace(_trace(), passed=True))
    append_receipt(path, receipt_from_trace(_trace(), passed=False))
    append_receipt(path, receipt_from_trace(_trace(early_stopped=True), passed=True))

    loaded = load_receipts(path)
    assert len(loaded) == 3

    summary = summarize(loaded)
    assert summary["n"] == 3
    assert summary["fusion_rate"] == round(2 / 3, 4)  # one of three short-circuited
    assert summary["pass_rate"] == round(2 / 3, 4)
    assert summary["mean_usd"] is not None
    assert "usd_per_pass" in summary


def test_summarize_empty_is_safe() -> None:
    assert summarize([]) == {"n": 0}


def test_format_summary_renders() -> None:
    out = format_summary(summarize([receipt_from_trace(_trace(), passed=True).to_json()]))
    assert "receipts:" in out and "cost/pass" in out


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_receipts(tmp_path / "nope.jsonl") == []
