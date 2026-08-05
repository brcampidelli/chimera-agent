"""Was it worth it? — and the four refusals that are the whole feature.

A panel like this is easy to build and easy to make lie. Every test below pins a place where the
obvious implementation would produce a number that reads well and is wrong: a partial cost sum, a
ranking, old runs credited to a profile they never used, a 100% pass rate over two runs.
"""

from __future__ import annotations

from chimera.api.runs import AttemptReceipt, RunReceipt, total_usd
from chimera.api.worth import READABLE_N, summarize_worth


def _attempt(**over: object) -> AttemptReceipt:
    base: dict[str, object] = {
        "index": 1,
        "success": True,
        "verified": True,
        "reverted": False,
        "diff_productive": True,
        "usd": 0.01,
    }
    return AttemptReceipt(**{**base, **over})  # type: ignore[arg-type]


def _run(profile: str | None = "balanced", **over: object) -> RunReceipt:
    attempts = over.pop("attempts", [_attempt()])
    base: dict[str, object] = {"task": "t", "success": True, "profile": profile}
    receipt = RunReceipt(**{**base, **over}, attempts=attempts)  # type: ignore[arg-type]
    receipt.usd = total_usd(receipt.attempts)
    return receipt


# --- the cost refusal ------------------------------------------------------------------------


def test_one_unpriced_attempt_makes_the_whole_run_unpriced() -> None:
    """A partial sum is not a conservative estimate. It is confidently wrong in one direction, and
    the direction is "the run that used a free or unpriced model looks cheaper than it was"."""
    assert total_usd([_attempt(usd=0.01), _attempt(usd=None)]) is None
    assert total_usd([_attempt(usd=0.01), _attempt(usd=0.02)]) == 0.03


def test_one_unpriced_run_makes_the_whole_group_unpriced_and_says_how_many_were_known() -> None:
    report = summarize_worth(
        [_run(), _run(), _run(attempts=[_attempt(usd=None)])]
    )
    group = report.profiles[0]
    assert group.usd_total is None
    # Without this the null is indistinguishable from "no data at all".
    assert group.usd_known_runs == 2 and group.runs == 3


def test_a_fully_priced_group_reports_its_real_total() -> None:
    group = summarize_worth([_run(), _run()]).profiles[0]
    assert group.usd_total == 0.02 and group.usd_known_runs == 2


# --- the attribution refusal -----------------------------------------------------------------


def test_runs_with_no_profile_stay_in_their_own_group() -> None:
    """Every receipt written before the field existed has profile=null. Folding those into the
    default would put fabricated evidence into the one view built to judge profiles."""
    report = summarize_worth([_run("balanced"), _run(None), _run(None)])
    by_profile = {p.profile: p.runs for p in report.profiles}
    assert by_profile == {"balanced": 1, None: 2}


# --- the ranking refusal ---------------------------------------------------------------------


def test_groups_are_ordered_by_name_never_by_outcome() -> None:
    """These are observational groups from whatever the user happened to run — different tasks,
    different days, no randomisation. Sorting by pass rate would look like the pre-registered A/B
    and would be a much weaker claim wearing its clothes."""
    report = summarize_worth(
        [_run("max", success=False), _run("economy"), _run("balanced"), _run(None)]
    )
    assert [p.profile for p in report.profiles] == ["balanced", "economy", "max", None]


def test_the_report_carries_no_winner_field() -> None:
    """Asserted structurally: there is nowhere for a "best profile" to be put, which is the point."""
    report = summarize_worth([_run()])
    fields = set(type(report).model_fields)
    assert not {"best", "winner", "recommended", "ranking"} & fields


# --- the small-n refusal ---------------------------------------------------------------------


def test_a_handful_of_runs_is_marked_unreadable() -> None:
    """Three runs is an anecdote. A 100% pass rate over two must not read as a finding."""
    report = summarize_worth([_run() for _ in range(3)])
    assert not report.profiles[0].readable and not report.any_readable


def test_enough_runs_becomes_readable() -> None:
    report = summarize_worth([_run() for _ in range(READABLE_N)])
    assert report.profiles[0].readable and report.any_readable


# --- what the counts actually mean -------------------------------------------------------------


def test_a_pass_that_changed_no_file_is_counted_apart_from_a_pass() -> None:
    """The empty-patch failure this project measured and fixed. Folding it into `passed` would let
    a configuration look good at precisely the thing it is bad at."""
    hollow = _run(attempts=[_attempt(diff_productive=False)])
    report = summarize_worth([hollow, _run()])
    group = report.profiles[0]
    assert group.passed == 2 and group.unproductive == 1


def test_an_unproductive_FAILURE_is_only_a_failure() -> None:
    """Counting it in both columns would punish the same run twice for one outcome."""
    failed = _run(success=False, attempts=[_attempt(success=False, diff_productive=False)])
    assert summarize_worth([failed]).profiles[0].unproductive == 0


def test_reverted_work_is_counted_because_it_was_paid_for() -> None:
    report = summarize_worth([_run(attempts=[_attempt(reverted=True), _attempt(index=2)])])
    assert report.profiles[0].reverted == 1
    assert report.profiles[0].attempts_total == 2


def test_a_paused_run_is_not_counted_as_a_failure() -> None:
    """It has not reached a verdict. Counting it would blame a configuration for a decision the
    human has not made yet."""
    report = summarize_worth([_run(paused=True, success=False), _run()])
    assert report.total_runs == 1 and report.profiles[0].passed == 1


def test_an_empty_log_is_an_empty_report_not_an_error() -> None:
    report = summarize_worth([])
    assert report.profiles == [] and report.total_runs == 0 and not report.any_readable
