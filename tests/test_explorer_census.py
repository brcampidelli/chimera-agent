"""The explorer census — its arithmetic, pinned on traces small enough to compute by hand.

The census reads stored traces and estimates what an explorer sub-agent would have saved. Every
number it publishes comes from three decisions: where the opening read-only phase ends, which traces
it may use at all, and the counterfactual's call-by-call sum. Each is pinned here before it runs on
the data, so a later edit that moves a number has to move a test first.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

BENCH = Path(__file__).resolve().parents[1] / "bench" / "explorer_census"
sys.path.insert(0, str(BENCH))

census = pytest.importorskip("census", reason="bench/explorer_census not on the path")

Call = census.Call
Trace = census.Trace


def _calls(*spec: tuple[int, tuple[str, ...]]) -> tuple[Any, ...]:
    return tuple(Call(p, 0, tools) for p, tools in spec)


#: Three single reads of ~2000 tokens, then four solving calls. Small enough to sum by hand.
_HAND = _calls(
    (1000, ("read_file",)),
    (3000, ("read_file",)),
    (5000, ("read_file",)),
    (7000, ("apply_patch",)),
    (7500, ("run_shell",)),
    (8000, ("run_shell",)),
    (8500, ()),
)


def test_the_opening_phase_ends_at_the_first_call_that_changes_something() -> None:
    calls = _calls(
        (1, ("grep", "glob")),
        (2, ("todo_write",)),
        (3, ("read_file", "read_file")),
        (4, ("read_file", "edit_file")),
        (5, ("read_file",)),
        (6, ()),
    )
    phase = census.exploration_phase(calls)
    assert phase.e == 3
    assert phase.reads == 2  # the read in the editing call is solving, not locating
    assert phase.searches == 2
    assert phase.post_calls == 3


def test_a_silent_call_between_reads_does_not_end_the_phase_but_the_answer_does() -> None:
    between = census.exploration_phase(
        _calls((1, ("read_file",)), (2, ()), (3, ("read_file",)), (4, ()))
    )
    assert between.e == 3 and between.reads == 2 and between.post_calls == 1
    only_reads = census.exploration_phase(_calls((1, ("read_file",)), (2, ())))
    assert only_reads.e == 1 and only_reads.post_calls == 1


def test_a_trace_whose_tool_counts_do_not_sum_to_its_tool_list_is_left_out() -> None:
    calls = [{"prompt": 10, "tool_calls": 2}, {"prompt": 20, "tool_calls": 1}]
    assert census.split_calls(calls, ["grep", "read_file"]) is None
    joined = census.split_calls(calls, ["grep", "read_file", "apply_patch"])
    assert joined is not None
    assert [c.tools for c in joined] == [("grep", "read_file"), ("apply_patch",)]


def test_a_trace_whose_prompt_shrank_is_not_append_only() -> None:
    assert census.append_only(_HAND)
    assert not census.append_only(_calls((10, ()), (9, ())))


def test_the_counterfactual_matches_the_sum_done_by_hand() -> None:
    trace = Trace("A", "x", _HAND, patch_files=1)
    phase = census.exploration_phase(_HAND)
    assert (phase.e, phase.reads, phase.post_calls) == (3, 3, 4)
    plain = census.counterfactual(trace, phase, block=1000, reread=False, read_size=2000)
    # base 40000; R = 6000; removed = 6000 - 60 - 1000 = 4940 from each of the 4 later calls;
    # explorer arm = 1000 (ask) + 9000 (replayed reads) + 7000 (closing call) + 31000 - 4*4940.
    assert plain.base_tokens == 40000
    assert plain.cf_tokens == 28240
    assert plain.saving == pytest.approx(0.294)
    reread = census.counterfactual(trace, phase, block=1000, reread=True, read_size=2000)
    # One file patched is opened again: removed = 2940, plus one extra call at 7000 - 2940.
    assert reread.cf_tokens == 1000 + 9000 + 7000 + (31000 - 4 * 2940) + 4060
    assert reread.saving < 0  # a short solving phase does not pay for the hand-off


def test_a_trace_with_nothing_to_delegate_costs_the_same_in_both_arms() -> None:
    calls = _calls((1000, ("apply_patch",)), (1500, ()))
    trace = Trace("A", "x", calls, patch_files=1)
    cf = census.counterfactual(
        trace, census.exploration_phase(calls), block=300, reread=True, read_size=500
    )
    assert cf.cf_tokens == cf.base_tokens and cf.saving == 0


def test_the_removed_tokens_leave_the_cached_part_of_the_prompt() -> None:
    calls = (
        Call(1000, 0, ("read_file",)),
        Call(9000, 8000, ("apply_patch",)),
        Call(9500, 9000, ()),
    )
    trace = Trace("A", "x", calls, patch_files=1)
    cf = census.counterfactual(
        trace, census.exploration_phase(calls), block=300, reread=False, read_size=0
    )
    # 27% fewer tokens, and MORE dollars: what leaves was cached at a quarter of the price, while
    # the explorer's replay of the reading and its closing call are priced at the main loop's cache
    # pattern, which here is cold on the replay (cache 0) and warm on the closing call (8000).
    assert cf.saving == pytest.approx(1 - 14220 / 19500)
    assert cf.usd_saving < 0 < cf.saving


def test_billing_the_explorer_uncached_moves_dollars_and_not_tokens() -> None:
    calls = (
        Call(1000, 0, ("read_file",)),
        Call(9000, 8000, ("apply_patch",)),
        Call(9500, 9000, ()),
    )
    trace = Trace("A", "x", calls, patch_files=1)
    phase = census.exploration_phase(calls)
    warm = census.counterfactual(trace, phase, block=300, reread=False, read_size=0)
    cold = census.counterfactual(
        trace, phase, block=300, reread=False, read_size=0, explorer_cached=False
    )
    assert cold.cf_tokens == warm.cf_tokens and cold.base_usd == warm.base_usd
    # Only the explorer's closing call (9000 tokens, 8000 of them cached) changes price: the 8000
    # go from the cache-read rate to the input rate. The main loop's calls keep their cache.
    in_price, cache_price = census.PRICES["A"]
    assert cold.cf_usd - warm.cf_usd == pytest.approx(8000 * (in_price - cache_price) / 1e6)


def test_gated_delegation_charges_traces_out_of_the_regime_as_they_ran() -> None:
    key = "B1000_reread"
    long_tail = _HAND + _calls((9000, ()), (9500, ()))
    short = _calls((1000, ("read_file",)), (3000, ("apply_patch",)), (3500, ()))
    per = []
    for i, calls in enumerate((long_tail, short)):
        trace = Trace("A", str(i), calls, patch_files=1)
        phase = census.exploration_phase(calls)
        cf = census.counterfactual(trace, phase, block=1000, reread=True, read_size=2000)
        per.append({"trace": trace, "phase": phase, "cf": {key: cf}})
    assert census.in_regime(per[0]["phase"]) and not census.in_regime(per[1]["phase"])
    out = census.gated_delegation(per, key)
    base = sum(p["cf"][key].base_tokens for p in per)
    expected = 1 - (per[0]["cf"][key].cf_tokens + per[1]["cf"][key].base_tokens) / base
    assert out["all"]["pooled_token_saving"] == pytest.approx(expected, abs=1e-4)
    assert out["regime_share_of_prompt_tokens"] == pytest.approx(
        per[0]["cf"][key].base_tokens / base, abs=1e-4
    )
    assert out["read_by_no_rule"] is True


def test_the_regime_needs_three_reads_and_three_calls_after_them() -> None:
    assert census.in_regime(census.Phase(e=4, reads=3, searches=1, post_calls=3))
    assert not census.in_regime(census.Phase(e=4, reads=2, searches=2, post_calls=9))
    assert not census.in_regime(census.Phase(e=4, reads=5, searches=0, post_calls=2))


def test_a_patch_counts_each_file_once() -> None:
    patch = "diff --git a/x.py b/x.py\n@@\ndiff --git a/y.py b/y.py\n@@\ndiff --git a/x.py b/x.py\n"
    assert census.patch_file_count(patch) == 2
    assert census.patch_file_count("") == 0


def test_reads_before_the_first_action_ignore_reads_after_it() -> None:
    assert census.sequence_reads(["grep", "read_file", "read_file", "run_shell", "read_file"]) == 2


def _report(**over: Any) -> dict[str, Any]:
    key = "B1000_reread"
    base: dict[str, Any] = {
        "decision_key": key,
        "usable": 500,
        "excluded": {"misaligned": 0, "not_append_only": 0},
        "regime_share": 0.5,
        "regime": {key: {"median_token_saving": 0.3}},
        "all": {key: {"pooled_token_saving": 0.2, "pooled_usd_saving": 0.1}},
    }
    base.update(over)
    return base


def test_the_rule_recommends_only_when_all_three_conditions_hold() -> None:
    key = "B1000_reread"
    assert census.verdict(_report())["verdict"] == "RECOMMEND"
    assert census.verdict(_report(regime_share=0.24))["verdict"] == "DO NOT RECOMMEND"
    assert (
        census.verdict(_report(regime={key: {"median_token_saving": 0.19}}))["verdict"]
        == "DO NOT RECOMMEND"
    )
    low_pool = {key: {"pooled_token_saving": 0.09, "pooled_usd_saving": 0.1}}
    assert census.verdict(_report(all=low_pool))["verdict"] == "DO NOT RECOMMEND"


def test_the_rule_is_void_on_too_few_traces_or_too_many_failed_checks() -> None:
    assert census.verdict(_report(usable=99))["verdict"] == "VOID"
    failed = {"misaligned": 100, "not_append_only": 100}
    assert census.verdict(_report(usable=500, excluded=failed))["verdict"] == "VOID"


def test_a_recommendation_says_when_its_saving_is_tokens_and_not_money() -> None:
    key = "B1000_reread"
    cheap = {key: {"pooled_token_saving": 0.2, "pooled_usd_saving": 0.02}}
    labels = census.verdict(_report(all=cheap))["labels"]
    assert any(label.startswith("token-only") for label in labels)
    assert any(label.startswith("proxy population") for label in labels)


# --- Amendment 1: the closing calls at max_steps carry no tool schema ------------------------------

_CLOSING = [
    {"prompt": 1000, "tool_calls": 1, "tools_offered": True},
    {"prompt": 3000, "tool_calls": 1, "tools_offered": True},
    # The step budget is spent: the loop asks for a summary WITHOUT the schema, so the prompt is
    # smaller by the schema, and the tool call the model emits anyway is never run or named.
    {"prompt": 1800, "tool_calls": 1, "tools_offered": False},
    {"prompt": 1900, "tool_calls": 0, "tools_offered": False},
]


def test_a_tool_call_on_a_closing_call_without_a_schema_does_not_misalign_the_trace() -> None:
    names = ["read_file", "apply_patch"]
    assert census.split_calls(_CLOSING, names, amended=False) is None  # the registered join
    calls = census.split_calls(_CLOSING, names)
    assert calls is not None
    assert [c.tools for c in calls] == [("read_file",), ("apply_patch",), (), ()]
    assert [c.offered for c in calls] == [True, True, False, False]


def test_a_closing_call_smaller_by_its_schema_is_not_lost_context() -> None:
    calls = census.split_calls(
        _CLOSING[:2] + [{**_CLOSING[2], "tool_calls": 0}], ["read_file", "x"]
    )
    assert calls is not None
    assert census.append_only(calls)
    assert not census.append_only(calls, amended=False)
    # A real drop between two calls that both carried the schema still fails the check.
    shrank = (Call(3000, 0, ("read_file",)), Call(2000, 0, ("grep",)))
    assert not census.append_only(shrank)


def test_a_read_followed_by_a_closing_call_is_not_measured_as_a_read() -> None:
    calls = (Call(1000, 0, ("read_file",)), Call(3000, 0, ("read_file",)), Call(1500, 0, (), False))
    assert census.single_read_sizes(calls) == [2000]
