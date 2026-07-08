"""Tests for delegation receipts + profitability gate + counterfactual (M16-A3)."""

from __future__ import annotations

from pathlib import Path

from chimera.fusion.receipts import resolve_price
from chimera.orchestration.receipts import (
    DelegationReceipt,
    append_delegation,
    estimate_profitability,
    estimate_tokens,
    format_delegation_summary,
    load_delegations,
    make_receipt,
    price_delegation,
    summarize_delegations,
)
from chimera.orchestration.spec import TaskSpec


def _spec(objective: str = "summarize the report", context: str = "") -> TaskSpec:
    return TaskSpec(task_id="t1", objective=objective, context=context)


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


def test_free_tier_prices_zero_not_none() -> None:
    price = resolve_price("openrouter/meta-llama/llama-3.3-70b-instruct:free")
    assert price is not None
    assert price.input_per_m == 0.0 and price.output_per_m == 0.0
    assert price_delegation("openrouter/qwen/qwen3-next-80b-a3b-instruct:free", 1000, 500) == 0.0


def test_free_marker_beats_paid_family_substring() -> None:
    free = resolve_price("openrouter/meta-llama/llama-3.3-70b-instruct:free")
    paid = resolve_price("openrouter/meta-llama/llama-3.3-70b-instruct")
    assert free is not None and free.input_per_m == 0.0
    assert paid is not None and paid.input_per_m > 0.0


def test_reasoner_is_priced() -> None:
    assert resolve_price("openrouter/deepseek/deepseek-r1") is not None


def test_unknown_model_prices_none_never_guessed() -> None:
    assert price_delegation("some/mystery-model-x", 1000, 1000) is None


# ---------------------------------------------------------------------------
# Profitability gate (deterministic)
# ---------------------------------------------------------------------------


def test_delegation_profitable_when_orchestrator_context_is_huge() -> None:
    est = estimate_profitability(_spec(), orchestrator_context_chars=400_000)
    assert est.profitable is True
    assert est.margin > 0


def test_delegation_unprofitable_when_context_is_tiny() -> None:
    est = estimate_profitability(_spec(), orchestrator_context_chars=1_000)
    assert est.profitable is False


def test_profitability_is_monotone_in_context_size() -> None:
    margins = [
        estimate_profitability(_spec(), orchestrator_context_chars=chars).margin
        for chars in (1_000, 50_000, 200_000, 800_000)
    ]
    assert margins == sorted(margins), "bigger orchestrator context must favor delegation"


def test_estimate_tokens_floor() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcd" * 100) == 100


# ---------------------------------------------------------------------------
# Receipts: assembly, JSONL round-trip, summary math
# ---------------------------------------------------------------------------


def test_make_receipt_prices_measured_and_counterfactual() -> None:
    receipt = make_receipt(
        _spec(),
        tier="mid",
        model="openrouter/deepseek/deepseek-chat-v3.1",
        prompt_tokens=10_000,
        completion_tokens=2_000,
        counterfactual_tokens=50_000,
        counterfactual_model="openrouter/deepseek/deepseek-r1",
        profitable_estimate=True,
    )
    assert receipt.usd is not None and receipt.usd > 0
    assert receipt.counterfactual_usd is not None
    assert receipt.counterfactual_usd > receipt.usd  # the whole point
    assert receipt.profitable_estimate is True
    assert receipt.total_tokens == 12_000


def test_receipt_jsonl_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "delegations.jsonl"
    r1 = make_receipt(
        _spec(), tier="weak", model="openrouter/qwen/qwen3-next-80b-a3b-instruct:free",
        prompt_tokens=500, completion_tokens=300,
    )
    r2 = make_receipt(
        _spec(), tier="top", model="openrouter/deepseek/deepseek-r1",
        prompt_tokens=None, completion_tokens=None, tokens_estimated=True,
    )
    append_delegation(path, r1)
    append_delegation(path, r2)
    loaded = load_delegations(path)
    assert loaded == [r1, r2]
    assert load_delegations(tmp_path / "missing.jsonl") == []


def test_summary_pairs_counterfactual_with_same_rows() -> None:
    with_cf = make_receipt(
        _spec(), tier="mid", model="openrouter/deepseek/deepseek-chat-v3.1",
        prompt_tokens=8_000, completion_tokens=2_000,
        counterfactual_tokens=40_000, counterfactual_model="openrouter/deepseek/deepseek-r1",
    )
    without_cf = make_receipt(
        _spec(), tier="weak", model="openrouter/qwen/qwen3-next-80b-a3b-instruct:free",
        prompt_tokens=100_000, completion_tokens=1_000,
    )
    summary = summarize_delegations([with_cf, without_cf])
    assert summary["n"] == 2
    assert summary["counterfactual_n"] == 1
    # The saving must compare the counterfactual against ONLY its paired rows,
    # never against the whole set (the free row's 101k tokens must not pollute it).
    assert summary["paired_measured_tokens"] == 10_000
    assert summary["token_saving"] == 30_000
    assert summary["by_tier"] == {"mid": 1, "weak": 1}


def test_summary_discloses_estimated_rows() -> None:
    estimated = DelegationReceipt(
        task_id="t1", tier="mid", model="m", prompt_tokens=100, completion_tokens=100,
        tokens_estimated=True,
    )
    summary = summarize_delegations([estimated])
    assert summary["estimated_n"] == 1
    text = format_delegation_summary(summary)
    assert "chars/4 estimator" in text


def test_format_summary_reports_overspend_honestly() -> None:
    # Delegation that cost MORE than the counterfactual: the summary must say so.
    bad = make_receipt(
        _spec(), tier="mid", model="openrouter/deepseek/deepseek-chat-v3.1",
        prompt_tokens=90_000, completion_tokens=10_000,
        counterfactual_tokens=20_000, counterfactual_model="openrouter/deepseek/deepseek-r1",
    )
    text = format_delegation_summary(summarize_delegations([bad]))
    assert "OVERSPENT" in text


def test_empty_summary() -> None:
    assert summarize_delegations([]) == {"n": 0}
    assert format_delegation_summary({"n": 0}) == "no delegation receipts yet"
