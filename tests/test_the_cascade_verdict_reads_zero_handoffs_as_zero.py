"""The §8 verdict reads a hand-off rate of zero as zero.

The first real report of the verified-cascade bench (2026-09-27) printed `default: False` for an arm
with no hand-off on any answerable item. The rule read the rate as `(rate or 1.0) <= 0.05`, and `or`
takes 0.0 for a missing value, so the best possible rate failed the condition (Amendment 3).
"""

from __future__ import annotations

from typing import Any

from bench.verified_cascade.report import verdict


def _inputs(rate: float | None, ratio: float = 1.9) -> tuple[Any, ...]:
    prim = {"diff": -0.03, "p_holm": 0.002, "cluster_ci": [-0.045, -0.013], "cost_ratio": [ratio]}
    help_ = {"handoff_rate": rate, "correct_newcombe": [-0.028, 0.028]}
    gates = {"s1": {"instrument": {"jev": {"passed": True}, "local": {"passed": True}}}}
    mech = {"by_kind": {"draft_luna": {"ok": 100, "off_pin": 0}}}
    return prim, help_, gates, mech, 0.0


def test_zero_handoffs_on_answerable_items_can_make_the_default() -> None:
    out = verdict("D", *_inputs(0.0))
    assert out["opt_in"] and out["default"]


def test_a_missing_rate_still_cannot_make_the_default() -> None:
    out = verdict("D", *_inputs(None))
    assert not out["default"]


def test_the_cost_ceiling_still_holds_the_default_back() -> None:
    out = verdict("B", *_inputs(0.0, ratio=3.74))
    assert out["opt_in"] and not out["default"]
