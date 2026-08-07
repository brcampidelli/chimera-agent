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


# --- verdict quality: which "passed" is which ---------------------------------------------------


def test_a_pass_judged_by_a_command_is_counted_apart_from_a_pass_judged_by_a_model() -> None:
    """The column that stops two different claims from wearing one number.

    A run whose winning attempt has `evidence == "verifier"` was approved by an exit code. Every
    other passing run was approved by a Manager LLM reading the answer text — it never sees the diff,
    the transcript, or a file (`chimera/core/autonomous.py:684-690`). Both really did pass; only one
    was verified, and a view whose whole job is to say whether a configuration earned its cost cannot
    count them as the same evidence.
    """
    report = summarize_worth([
        _run(attempts=[_attempt(evidence="verifier")]),
        _run(attempts=[_attempt(evidence="diff+manager")]),
    ])

    (group,) = report.profiles
    assert group.passed == 2
    assert group.passed_by_verifier == 1


def test_it_reads_the_WINNING_attempt_not_any_attempt() -> None:
    """A run that failed under a verifier and then passed on a manager-only retry was not verified.
    Crediting the earlier attempt would count evidence that judged different, discarded work."""
    report = summarize_worth([
        _run(attempts=[
            _attempt(index=1, success=False, evidence="verifier"),
            _attempt(index=2, success=True, evidence="manager"),
        ])
    ])

    (group,) = report.profiles
    assert group.passed == 1
    assert group.passed_by_verifier == 0


def test_a_failed_run_counts_in_neither() -> None:
    report = summarize_worth([
        _run(success=False, attempts=[_attempt(success=False, evidence="none")])
    ])

    (group,) = report.profiles
    assert group.passed == 0 and group.passed_by_verifier == 0


def test_a_default_nobody_picked_is_its_own_group() -> None:
    """The screen stopped asking, so most new runs carry a profile the app chose.

    Counting those beside deliberate picks would answer "was this configuration worth it?" with runs
    that were never a configuration decision — and the merge would be permanent, which is the same
    reason this module refuses to back-attribute receipts written before the field existed.
    """
    report = summarize_worth([
        _run("balanced"),
        _run("balanced", profile_source="system"),
    ])

    by_key = {(g.profile, g.profile_source): g.runs for g in report.profiles}
    assert by_key == {("balanced", "user"): 1, ("balanced", "system"): 1}


def test_old_receipts_count_as_deliberate_because_they_were() -> None:
    """Every receipt written before the field existed came from a screen that asked."""
    from chimera.api.runs import RunReceipt

    assert RunReceipt(task="t").profile_source == "user"
