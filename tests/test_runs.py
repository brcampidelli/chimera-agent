"""Tests for run receipts (append-only per-run proof log) and their capture by the AutonomousAgent."""

from __future__ import annotations

import os
from pathlib import Path

from chimera.api.runs import (
    AcceptedChangeCost,
    AttemptReceipt,
    RunReceipt,
    _in_project,
    append_run,
    build_receipt,
    cost_per_accepted_change,
    load_runs,
    same_folder,
    total_usd,
)
from chimera.core import AutonomousAgent, AutonomousConfig
from chimera.core.agent import AgentResult
from chimera.core.autonomous import Attempt, AutonomousResult
from chimera.core.verify import VerificationResult
from chimera.evolution.diff_gate import FileDiff


class _FakeWorker:
    """A worker that optionally writes a file each run, then returns a fixed answer."""

    def __init__(
        self, answer: str = "done", *, workspace: Path | None = None, filename: str | None = None
    ) -> None:
        self.answer = answer
        self.workspace = workspace
        self.filename = filename
        self.runs = 0

    def run(self, task: str) -> AgentResult:
        self.runs += 1
        if self.workspace and self.filename:
            (self.workspace / self.filename).write_text("content", encoding="utf-8")
        return AgentResult(answer=self.answer, steps=1, stopped_reason="final")


class _FlakyVerifier:
    """Fails the first ``fail_times`` calls, then passes — mirrors tests/test_autonomous.py."""

    command = "pytest -q"

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def verify(self) -> VerificationResult:
        self.calls += 1
        passed = self.calls > self.fail_times
        return VerificationResult(passed=passed, output="" if passed else "tests failed")


class _FailVerifier:
    command = "make check"

    def verify(self) -> VerificationResult:
        return VerificationResult(False, "always fails")


def test_append_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    r1 = RunReceipt(ts="2026-07-13T00:00:00+00:00", task="a", success=True, verify_command="pytest")
    r2 = RunReceipt(
        ts="2026-07-13T01:00:00+00:00",
        task="b",
        success=False,
        attempts=[AttemptReceipt(index=1, verified=False, reverted=True, verify_output="boom")],
    )
    append_run(path, r1)
    append_run(path, r2)

    loaded = load_runs(path)
    assert [r.task for r in loaded] == ["a", "b"]  # append order preserved
    assert loaded[0].success is True and loaded[0].verify_command == "pytest"
    assert loaded[1].attempts[0].reverted is True and loaded[1].attempts[0].verify_output == "boom"
    assert loaded[0].chimera_version == r1.chimera_version
    assert loaded[0].chimera_git_sha == r1.chimera_git_sha


def test_old_run_receipt_defaults_build_identity_to_empty() -> None:
    receipt = RunReceipt.model_validate_json('{"ts":"old","task":"before identity"}')
    assert receipt.chimera_version == "" and receipt.chimera_git_sha == ""


def test_git_sha_lookup_is_empty_outside_a_checkout(tmp_path: Path) -> None:
    from chimera.build_info import _checkout_sha

    assert _checkout_sha(tmp_path) == ""


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_runs(tmp_path / "nope.jsonl") == []


def test_load_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    append_run(path, RunReceipt(ts="t", task="ok"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
    loaded = load_runs(path)
    assert len(loaded) == 1 and loaded[0].task == "ok"  # the bad line was skipped, the good one kept


def test_build_receipt_maps_attempts_and_truncates_bounded_fields() -> None:
    result = AutonomousResult(
        answer="X" * 5000,
        success=True,
        attempts=[
            Attempt(
                index=1,
                answer="a1",
                approved=False,
                verified=False,
                reverted=True,
                success=False,
                feedback="F" * 3000,
                verify_output="V" * 9000,
                diff_summary="modified: foo.py",
            ),
            Attempt(
                index=2,
                answer="a2",
                approved=True,
                verified=True,
                reverted=False,
                success=True,
                feedback="",
                verify_output="ok",
                diff_summary="added: bar.py",
            ),
        ],
    )
    receipt = build_receipt(result, "T" * 4000, "pytest -q", "2026-07-13T00:00:00+00:00")

    assert receipt.success is True and receipt.verify_command == "pytest -q"
    assert receipt.ts == "2026-07-13T00:00:00+00:00"
    from chimera.build_info import CHIMERA_GIT_SHA, CHIMERA_VERSION

    assert receipt.chimera_version == CHIMERA_VERSION
    assert receipt.chimera_git_sha == CHIMERA_GIT_SHA
    assert len(receipt.task) == 2000  # task truncated to 2000
    assert len(receipt.answer) == 2000  # answer truncated to 2000
    assert len(receipt.attempts) == 2
    first = receipt.attempts[0]
    assert first.index == 1 and first.reverted is True and first.verified is False
    assert first.diff_summary == "modified: foo.py"
    assert len(first.verify_output) == 4000  # verify_output truncated to 4000
    assert len(first.feedback) == 1000  # feedback truncated to 1000
    second = receipt.attempts[1]
    assert second.success is True and second.diff_summary == "added: bar.py"


def test_build_receipt_maps_the_per_file_diffs_and_bounds_them() -> None:
    """The per-file diffs ARE the machine truth of what an attempt changed, so the receipt has to
    carry them — and bound them, so one big run can't bloat runs.jsonl."""
    result = AutonomousResult(
        answer="a",
        success=True,
        attempts=[
            Attempt(
                index=1,
                answer="x",
                approved=True,
                verified=True,
                reverted=False,
                success=True,
                diffs=[
                    FileDiff(path="over.py", patch="P" * 4001, truncated=False),
                    FileDiff(path="flagged.py", patch="short", truncated=True),
                    FileDiff(path="exact.py", patch="C" * 4000, truncated=False),
                    FileDiff(path="empty.py", patch="", truncated=False),
                ],
            )
        ],
    )
    diffs = build_receipt(result, "t", None, "ts").attempts[0].diffs

    assert [d.path for d in diffs] == ["over.py", "flagged.py", "exact.py", "empty.py"]
    # An over-long patch is clipped to the bound and marked truncated even though its source said False.
    assert len(diffs[0].patch) == 4000
    assert diffs[0].truncated is True
    # A patch the source ALREADY marked truncated keeps that flag even though it is short.
    assert diffs[1].patch == "short"
    assert diffs[1].truncated is True
    # Exactly at the bound is a complete patch, not a truncated one (the boundary is `>`, not `>=`).
    assert len(diffs[2].patch) == 4000
    assert diffs[2].truncated is False
    # An empty patch stays empty — never backfilled with invented text.
    assert diffs[3].patch == ""
    assert diffs[3].truncated is False


def test_build_receipt_caps_the_per_file_diffs_at_twenty() -> None:
    result = AutonomousResult(
        answer="a",
        success=True,
        attempts=[
            Attempt(
                index=1,
                answer="x",
                approved=True,
                verified=True,
                reverted=False,
                success=True,
                diffs=[FileDiff(path=f"f{i:02d}.py", patch="x") for i in range(25)],
            )
        ],
    )
    diffs = build_receipt(result, "t", None, "ts").attempts[0].diffs
    assert len(diffs) == 20
    assert [d.path for d in diffs] == [f"f{i:02d}.py" for i in range(20)]


def test_build_receipt_tolerates_an_attempt_with_no_diffs_attribute() -> None:
    """build_receipt duck-types its result (it cannot import `autonomous` without a cycle), so an
    attempt object with no `diffs` at all must yield an empty list rather than raising."""

    class _BareAttempt:
        index = 1
        verified = True
        reverted = False
        success = True
        verify_output = "ok"
        diff_summary = ""
        feedback = ""

    class _BareResult:
        answer = "a"
        success = True
        paused = False
        attempts = [_BareAttempt()]

    receipt = build_receipt(_BareResult(), "t", None, "ts")  # type: ignore[arg-type]
    assert receipt.attempts[0].diffs == []
    assert receipt.attempts[0].verify_output == "ok"


def test_build_receipt_coerces_absent_text_to_empty_strings() -> None:
    # Empty in → empty out. The receipt shows the empty string, never invented detail.
    result = AutonomousResult(
        answer="",
        success=False,
        attempts=[
            Attempt(
                index=1,
                answer="",
                approved=False,
                verified=False,
                reverted=True,
                success=False,
                feedback="",
                verify_output="",
                diff_summary="",
            )
        ],
    )
    receipt = build_receipt(result, "", None, "ts")
    assert receipt.task == "" and receipt.answer == ""
    attempt = receipt.attempts[0]
    assert attempt.verify_output == ""
    assert attempt.diff_summary == ""
    assert attempt.feedback == ""


def test_build_receipt_carries_the_paused_flag() -> None:
    # A run paused for human approval is not a finished run — the receipt must not report it as one.
    result = AutonomousResult(answer="a", success=False, attempts=[], paused=True)
    assert build_receipt(result, "t", None, "ts").paused is True


def test_append_run_creates_missing_parent_directories(tmp_path: Path) -> None:
    # Two missing levels: this only works if the parent mkdir is recursive.
    path = tmp_path / "deep" / "nested" / "runs.jsonl"
    append_run(path, RunReceipt(ts="t", task="a"))
    assert [r.task for r in load_runs(path)] == ["a"]


def test_load_skips_blank_lines_without_dropping_later_receipts(tmp_path: Path) -> None:
    # A blank line must be SKIPPED, not read as end-of-file: stopping there would silently truncate
    # the run history and quietly under-report what the agent did.
    path = tmp_path / "runs.jsonl"
    append_run(path, RunReceipt(ts="t1", task="first"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    append_run(path, RunReceipt(ts="t2", task="second"))
    assert [r.task for r in load_runs(path)] == ["first", "second"]


def test_agent_writes_a_receipt_on_success(tmp_path: Path) -> None:
    run_log = tmp_path / "runs.jsonl"
    worker = _FakeWorker("done")
    auto = AutonomousAgent(
        worker,
        verifier=_FlakyVerifier(fail_times=1),  # attempt 1 fails, attempt 2 passes
        run_log=run_log,
        config=AutonomousConfig(max_attempts=2, use_planner=False, use_manager=False),
    )
    result = auto.run("do the task")
    assert result.success is True

    receipts = load_runs(run_log)
    assert len(receipts) == 1  # exactly one receipt for the finished run
    rec = receipts[0]
    assert rec.success is True and rec.task == "do the task"
    assert rec.verify_command == "pytest -q"  # captured from the verifier
    assert [a.index for a in rec.attempts] == [1, 2]
    assert rec.attempts[0].success is False and rec.attempts[0].verify_output == "tests failed"
    assert rec.attempts[1].success is True and rec.attempts[1].verified is True  # passed & verified


def test_agent_writes_a_receipt_on_budget_exhausted_failure(tmp_path: Path) -> None:
    run_log = tmp_path / "runs.jsonl"
    worker = _FakeWorker("nope")
    auto = AutonomousAgent(
        worker,
        verifier=_FailVerifier(),  # never passes → budget exhausts, terminal failure
        run_log=run_log,
        config=AutonomousConfig(max_attempts=2, use_planner=False, use_manager=False),
    )
    result = auto.run("hard task")
    assert result.success is False

    receipts = load_runs(run_log)
    assert len(receipts) == 1
    rec = receipts[0]
    assert rec.success is False and rec.task == "hard task"
    assert rec.verify_command == "make check"
    assert len(rec.attempts) == 2 and all(not a.success for a in rec.attempts)


def test_receipt_captures_revert_and_diff_when_guarded(tmp_path: Path) -> None:
    # With a WorkspaceGuard, a failed attempt is reverted and its workspace diff is audited — both
    # must land on the receipt's attempt row (the machine truth of what the attempt changed).
    from chimera.core import WorkspaceGuard

    ws = tmp_path / "ws"
    ws.mkdir()
    run_log = tmp_path / "runs.jsonl"
    worker = _FakeWorker("done", workspace=ws, filename="new.txt")
    auto = AutonomousAgent(
        worker,
        verifier=_FailVerifier(),  # every attempt fails → each is reverted
        guard=WorkspaceGuard(ws),
        run_log=run_log,
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )
    assert auto.run("write a file").success is False

    rec = load_runs(run_log)[0]
    assert rec.attempts[0].reverted is True  # the failed attempt was rolled back
    assert rec.attempts[0].diff_summary  # and the diff it made (adding new.txt) was captured


def test_no_run_log_writes_nothing(tmp_path: Path) -> None:
    # Without a run_log the agent must not create any file — persistence is strictly opt-in.
    worker = _FakeWorker("done")
    auto = AutonomousAgent(
        worker,
        verifier=_FlakyVerifier(fail_times=0),
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )
    assert auto.run("t").success is True
    assert not (tmp_path / "runs.jsonl").exists()


# --- provenance survives persistence ------------------------------------------------------------
# The evidence label and the unknown diff state existed on ``Attempt`` for a release while the
# receipt carried only booleans. Nobody decided to drop them; they simply never crossed the
# serialization boundary — which is how a three-way verdict quietly becomes a two-way one. These
# tests exist so that erosion has to be an explicit, failing choice rather than an omission.


def test_receipt_carries_the_evidence_label(tmp_path: Path) -> None:
    result = AutonomousResult(
        answer="a",
        success=True,
        attempts=[Attempt(0, "a", True, False, False, True, evidence="diff+manager")],
    )
    receipt = build_receipt(result, "t", None, "2026-07-30T00:00:00+00:00")
    assert receipt.attempts[0].evidence == "diff+manager"
    # verified=False AND success=True is exactly the row a reader would otherwise misread as an
    # unexplained pass: the label is the only thing naming who approved it.
    assert receipt.attempts[0].verified is False


def test_unknown_diff_state_survives_the_round_trip(tmp_path: Path) -> None:
    # None means "could not be measured" and must stay distinguishable from False ("measured,
    # nothing changed") after a write-and-load — including as an explicit JSON null, since a field
    # that vanishes when unset is indistinguishable from one nobody wrote.
    path = tmp_path / "runs.jsonl"
    unknown = Attempt(0, "a", True, False, False, True)  # no guard ran → diff_productive is None
    measured_empty = Attempt(1, "a", True, False, False, False)
    measured_empty.diff_productive = False
    append_run(
        path,
        build_receipt(
            AutonomousResult(answer="a", success=True, attempts=[unknown, measured_empty]),
            "t", None, "2026-07-30T00:00:00+00:00",
        ),
    )
    assert '"diff_productive":null' in path.read_text(encoding="utf-8").replace(" ", "")

    loaded = load_runs(path)[0]
    assert loaded.attempts[0].diff_productive is None
    assert loaded.attempts[1].diff_productive is False


def test_receipt_records_out_of_checkout_side_effects(tmp_path: Path) -> None:
    # An empty diff means something different once a run has already sent mail. The receipt records
    # that rather than making a reader infer "nothing happened" from "no file changed".
    attempt = Attempt(0, "a", True, False, False, True)
    attempt.side_effects = ["send_email"]
    receipt = build_receipt(
        AutonomousResult(answer="a", success=True, attempts=[attempt]),
        "t", None, "2026-07-30T00:00:00+00:00",
    )
    assert receipt.attempts[0].side_effects == ["send_email"]


def test_receipt_defaults_are_honest_for_an_attempt_predating_the_fields(tmp_path: Path) -> None:
    # build_receipt duck-types its input by design. An object without the new fields must read as
    # "unknown / nothing recorded" rather than raising or inventing a value.
    class _Old:
        index, verified, reverted, success = 0, True, False, True
        verify_output = diff_summary = feedback = ""
        diffs: list[FileDiff] = []

    receipt = build_receipt(
        AutonomousResult(answer="a", success=True, attempts=[_Old()]),  # type: ignore[list-item]
        "t", None, "2026-07-30T00:00:00+00:00",
    )
    assert receipt.attempts[0].evidence == "none"
    assert receipt.attempts[0].diff_productive is None
    assert receipt.attempts[0].side_effects == []


def test_side_effects_are_read_off_the_step_log(tmp_path: Path) -> None:
    from chimera.core.autonomous import _side_effects
    from chimera.core.steplog import StepLog, StepRecord, ToolRecord

    log = StepLog()
    log.steps.extend([
        StepRecord(index=0, prompt_tokens=0, completion_tokens=0, model="m", tools=[
            ToolRecord(name="read_file", arguments="{}", observation="x", ok=True),
            ToolRecord(name="send_email", arguments='{"to":"a@b"}', observation="sent", ok=True),
        ]),
        StepRecord(index=1, prompt_tokens=0, completion_tokens=0, model="m", tools=[
            # ok=False: the ledger blocked it, or it failed before reaching the network. No effect
            # happened, so warning about one would be its own kind of dishonesty.
            ToolRecord(name="http_post", arguments="{}", observation="[taint: needs review]", ok=False),
            ToolRecord(name="send_email", arguments='{"to":"c@d"}', observation="sent", ok=True),
        ]),
    ])
    # De-duplicated by name, in first-call order; the failed http_post is absent.
    assert _side_effects(log) == ["send_email"]
    assert _side_effects(None) == []


def test_total_usd_sums_known_prices_and_refuses_a_partial_sum() -> None:
    # All-or-nothing on purpose: a partial sum is not a conservative estimate, it is a number that
    # is confidently wrong in the direction that flatters the unpriced leg. None is the only answer
    # that cannot mislead a cost comparison. The digits are chosen so the 6-decimal rounding is
    # observable: a sum rounded to 7 places (or multiplied instead of added) returns a different
    # number, and the assertion is exact.
    priced = [AttemptReceipt(usd=0.123456789), AttemptReceipt(usd=0.111111111)]
    assert total_usd(priced) == 0.234568
    assert total_usd([AttemptReceipt(usd=0.0)]) == 0.0  # a real zero is a real zero
    assert total_usd([AttemptReceipt(usd=0.5), AttemptReceipt(usd=None)]) is None
    assert total_usd([]) is None  # no attempts: nothing was measured, not "free"


def test_cost_per_accepted_change_counts_only_delivered_attempts() -> None:
    # Accepted = verified AND not reverted AND measured as having changed the tree. Each condition
    # alone admits something that is not a delivered change: verified without a measured diff is the
    # hollow success the diff gate exists to catch; verified-then-reverted delivered nothing.
    # diff_productive is None ("could not be measured") does not count — the same refusal
    # total_usd makes, because guessing in the flattering direction is how a loop that changed
    # nothing comes to look cheap per change.
    attempts = [
        AttemptReceipt(verified=True, reverted=False, diff_productive=True, usd=0.4),
        AttemptReceipt(verified=True, reverted=False, diff_productive=True, usd=0.35),
        AttemptReceipt(verified=True, reverted=False, diff_productive=True, usd=0.25),
        AttemptReceipt(verified=True, reverted=False, diff_productive=False, usd=0.25),
        AttemptReceipt(verified=True, reverted=True, diff_productive=True, usd=0.25),
        AttemptReceipt(verified=False, reverted=False, diff_productive=True, usd=0.25),
        AttemptReceipt(verified=True, reverted=False, diff_productive=None, usd=0.25),
    ]
    cost = cost_per_accepted_change(attempts)
    assert isinstance(cost, AcceptedChangeCost)
    assert cost.accepted == 3
    assert cost.usd == 2.0
    # 2.0 / 3 rounded to 6 places — the digits make the rounding (and the division itself)
    # observable: a product or a 7th decimal returns a different number.
    assert cost.per_change == 0.666667


def test_cost_per_accepted_change_separates_nothing_accepted_from_unknown_cost() -> None:
    # Two unrelated reasons for per_change to be None, which must not collapse into each other:
    # nothing was accepted (the denominator does not exist) vs. a leg was unpriced (the run's cost
    # is unknown). Reporting both alongside is what lets a reader tell "this run bought nothing"
    # from "we cannot say what this run cost" — opposite verdicts.
    nothing_accepted = cost_per_accepted_change(
        [AttemptReceipt(verified=True, reverted=False, diff_productive=False, usd=0.5)]
    )
    assert nothing_accepted.accepted == 0 and nothing_accepted.usd == 0.5
    assert nothing_accepted.per_change is None

    unpriced = cost_per_accepted_change(
        [AttemptReceipt(verified=True, reverted=False, diff_productive=True, usd=None)]
    )
    assert unpriced.accepted == 1 and unpriced.usd is None
    assert unpriced.per_change is None

    # Exactly one accepted change: the denominator exists and the ratio is the run's whole cost.
    single = cost_per_accepted_change(
        [AttemptReceipt(verified=True, reverted=False, diff_productive=True, usd=0.5)]
    )
    assert single.accepted == 1 and single.per_change == 0.5


def test_same_folder_matches_spellings_without_touching_the_disk() -> None:
    # One folder, several spellings: the filter that answered "nothing" when the list asked for the
    # same folder spelled with the OS separator made correctly-recorded runs vanish from their own
    # project. normpath settles separators/trailing dots, normcase settles case — and on POSIX it is
    # the identity, so two folders differing in case stay two folders there.
    assert same_folder("C:/Proj/app", "C:/Proj/app/")  # trailing separator
    assert same_folder("C:/Proj/app", "C:/Proj/./app")  # a dot segment
    assert not same_folder("C:/Proj/app", "C:/Proj/app2")  # a prefix is not the same folder
    assert not same_folder("C:/Proj/app", "C:/Proj/appX")  # not even a near-miss
    if os.name == "nt":
        assert same_folder(r"C:\Proj\app", r"C:\proj\APP")  # case folds where the platform says so
    else:
        assert not same_folder("/proj/app", "/proj/APP")  # case is significant on POSIX


def test_in_project_keeps_unattributed_receipts_reachable_by_the_empty_query() -> None:
    # "" is a query in its own right, not "no filter": it asks for the receipts that have NO
    # workspace. Comparing it as a path would break that — normpath("") is ".", so an unattributed
    # receipt would answer to a query for the current directory and "" would stop finding the
    # receipts it exists to find.
    assert _in_project("", "")  # the empty query finds the unattributed receipt
    assert not _in_project("", "C:/Proj")  # a filtered view never adopts them
    assert not _in_project("C:/Proj", "")  # an attributed receipt is not unattributed
    assert _in_project("C:/Proj", "C:/Proj/")
    assert not _in_project("C:/Proj", "C:/Other")


def test_load_runs_filters_by_workspace_and_keeps_unattributed_out_of_it(tmp_path: Path) -> None:
    # None (the default) returns everything — what every existing caller means. A workspace filter
    # shows only that project's receipts, and a receipt with no workspace is NOT included in a
    # filtered result: showing it under whichever project happens to be open is fabricated evidence
    # in the one view whose job is to say what a configuration was worth.
    path = tmp_path / "runs.jsonl"
    append_run(path, RunReceipt(ts="t1", task="here", workspace="C:/Proj"))
    append_run(path, RunReceipt(ts="t2", task="elsewhere", workspace="C:/Other"))
    append_run(path, RunReceipt(ts="t3", task="unattributed"))

    assert [r.task for r in load_runs(path)] == ["here", "elsewhere", "unattributed"]
    assert [r.task for r in load_runs(path, workspace="C:/Proj/")] == ["here"]
    assert [r.task for r in load_runs(path, workspace="C:/Other")] == ["elsewhere"]
    assert [r.task for r in load_runs(path, workspace="")] == ["unattributed"]


def test_load_runs_reads_a_malformed_line_in_the_middle(tmp_path: Path) -> None:
    # A malformed line must be SKIPPED, not read as end-of-file: stopping there would silently
    # truncate the run history and quietly under-report what the agent did. The existing test puts
    # the bad line last, where a `break` is indistinguishable from the `continue` it must be.
    path = tmp_path / "runs.jsonl"
    append_run(path, RunReceipt(ts="t1", task="first"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
    append_run(path, RunReceipt(ts="t2", task="second"))
    assert [r.task for r in load_runs(path)] == ["first", "second"]


def test_build_receipt_defaults_are_the_honest_shape_of_a_minimal_run() -> None:
    # A bare call — no kwargs — is the shape of the receipt the persist path writes for a run with
    # no profile, no workspace and no delivery check. The defaults are the contract: "user" because
    # every receipt written before the screen stopped asking came from a typed command, "" because
    # attributing a run to a project it may not have come from would put invented evidence into the
    # one view whose job is to say what happened where.
    receipt = build_receipt(AutonomousResult(answer="a", success=True), "t", None, "ts")
    assert receipt.verify_source == "user"
    assert receipt.profile_source == "user"
    assert receipt.workspace == ""
    assert receipt.profile is None
    assert receipt.delivered_matches_verified is None
    assert receipt.usd is None  # no attempts: unknown, never a partial sum


def test_build_receipt_reads_a_bare_attempt_without_the_optional_fields() -> None:
    """build_receipt duck-types its result (it cannot import `autonomous` without a cycle), so an
    attempt object with no optional fields at all must read as "nothing recorded" rather than
    raising — the honest answer for a record that predates them."""

    class _BareAttempt:
        index = 1
        verified = True
        reverted = False
        success = True
        verify_output = "ok"
        diff_summary = ""
        feedback = ""

    class _BareResult:
        answer = "a"
        success = True
        paused = False
        attempts = [_BareAttempt()]

    receipt = build_receipt(_BareResult(), "t", None, "ts")  # type: ignore[arg-type]
    a = receipt.attempts[0]
    assert a.evidence == "none"
    assert a.run_id == "" and a.diff_productive is None
    assert a.side_effects == [] and a.diff_flags == [] and a.tool_names == []
    assert a.usd is None and a.overhead_usd is None
    assert a.prompt_tokens == 0 and a.completion_tokens == 0
    assert a.model == "" and a.cache_read_tokens is None and a.provider == ""
    assert a.discarded_at == "" and a.verified_fingerprint == ""
    assert a.failure_class == "" and a.failure_evidence == "" and a.system_sha == ""
    assert a.truncated_steps is None and a.dropped_tool_calls is None
    assert receipt.stopped_reason == "" and receipt.ending == "unknown"
    assert receipt.stagnant is None
    assert receipt.usd is None  # no priced attempt: unknown, never a partial sum


def test_build_receipt_coerces_none_fields_to_their_honest_defaults() -> None:
    # None in → the field's "nothing recorded" value out. `or ""` / `or 0` / `or None` are not
    # decoration: a duck-typed attempt that carries None (a caller that could not measure it) must
    # not crash the persist path — after the work was already paid for — nor invent a value.
    result = AutonomousResult(
        answer="a",
        success=True,
        attempts=[
            Attempt(
                index=1,
                answer="x",
                approved=True,
                verified=True,
                reverted=False,
                success=True,
                evidence=None,  # type: ignore[arg-type]
                run_id=None,  # type: ignore[arg-type]
                diff_productive=None,
                side_effects=None,  # type: ignore[arg-type]
                diff_flags=None,  # type: ignore[arg-type]
                tool_names=None,  # type: ignore[arg-type]
                usd=None,
                overhead_usd=None,
                prompt_tokens=None,  # type: ignore[arg-type]
                completion_tokens=None,  # type: ignore[arg-type]
                model=None,  # type: ignore[arg-type]
                cache_read_tokens=None,
                provider=None,  # type: ignore[arg-type]
                discarded_at=None,  # type: ignore[arg-type]
                verified_fingerprint=None,  # type: ignore[arg-type]
                failure_class=None,  # type: ignore[arg-type]
                failure_evidence=None,  # type: ignore[arg-type]
                system_sha=None,  # type: ignore[arg-type]
                truncated_steps=None,
                dropped_tool_calls=None,
            )
        ],
    )
    result.stopped_reason = None  # type: ignore[assignment]
    result.ending = None  # type: ignore[assignment]
    result.stagnant = None
    receipt = build_receipt(result, "t", None, "ts")
    a = receipt.attempts[0]
    assert a.evidence == "none"
    assert a.run_id == "" and a.diff_productive is None
    assert a.side_effects == [] and a.diff_flags == [] and a.tool_names == []
    assert a.usd is None and a.overhead_usd is None
    assert a.prompt_tokens == 0 and a.completion_tokens == 0
    assert a.model == "" and a.cache_read_tokens is None and a.provider == ""
    assert a.discarded_at == "" and a.verified_fingerprint == ""
    assert a.failure_class == "" and a.failure_evidence == "" and a.system_sha == ""
    assert a.truncated_steps is None and a.dropped_tool_calls is None
    assert receipt.stopped_reason == "" and receipt.ending == "unknown"
    assert receipt.stagnant is None


def test_build_receipt_carries_every_field_the_attempt_measured() -> None:
    # The receipt is the durable record of what an attempt cost and did. A field that reaches the
    # builder with a real value and leaves with the default is a receipt that lies by omission —
    # so every mapped field is asserted with a non-default value, not just the ones a past bug
    # happened to expose.
    attempt = Attempt(
        index=3,
        answer="x",
        approved=True,
        verified=True,
        reverted=False,
        success=True,
        verify_output="3 passed",
        diff_summary="modified: a.py",
        evidence="verifier",
        run_id="run-abc",
        diff_productive=True,
        side_effects=["send_email"],
        diff_flags=["http_post(non_literal)"],
        tool_names=["read_file", "edit_file"],
        usd=0.123456,
        overhead_usd=0.023456,
        prompt_tokens=1200,
        completion_tokens=340,
        model="model-x",
        cache_read_tokens=77,
        provider="route-1",
        discarded_at="C:\\trash\\attempt-3",
        verified_fingerprint="ab12cd34",
        failure_class="failing_test",
        failure_evidence="assert 0 == 1 failed",
        system_sha="beefcafe",
        truncated_steps=2,
        dropped_tool_calls=1,
    )
    receipt = build_receipt(
        AutonomousResult(
            answer="done",
            success=True,
            attempts=[attempt],
            stopped_reason="final",
            ending="success",
            stagnant=False,
        ),
        "the task",
        "pytest -q",
        "2026-10-05T00:00:00+00:00",
        profile="balanced",
        verify_source="inferred:pytest.ini",
        profile_source="system",
        workspace=r"C:\Proj",
        delivered_matches_verified=True,
    )
    a = receipt.attempts[0]
    assert a.index == 3 and a.verified is True and a.reverted is False and a.success is True
    assert a.verify_output == "3 passed" and a.diff_summary == "modified: a.py"
    assert a.evidence == "verifier" and a.run_id == "run-abc"
    assert a.diff_productive is True and a.side_effects == ["send_email"]
    assert a.diff_flags == ["http_post(non_literal)"] and a.tool_names == ["read_file", "edit_file"]
    assert a.usd == 0.123456 and a.overhead_usd == 0.023456
    assert a.prompt_tokens == 1200 and a.completion_tokens == 340
    assert a.model == "model-x" and a.cache_read_tokens == 77 and a.provider == "route-1"
    assert a.discarded_at == r"C:\trash\attempt-3" and a.verified_fingerprint == "ab12cd34"
    assert a.failure_class == "failing_test"
    assert a.failure_evidence == "assert 0 == 1 failed" and a.system_sha == "beefcafe"
    assert a.truncated_steps == 2 and a.dropped_tool_calls == 1
    assert receipt.ts == "2026-10-05T00:00:00+00:00"
    assert receipt.task == "the task" and receipt.answer == "done"
    assert receipt.success is True and receipt.paused is False
    assert receipt.verify_command == "pytest -q"
    assert receipt.verify_source == "inferred:pytest.ini"
    assert receipt.profile_source == "system"
    assert receipt.stopped_reason == "final" and receipt.ending == "success"
    assert receipt.stagnant is False
    assert receipt.profile == "balanced" and receipt.workspace == r"C:\Proj"
    assert receipt.usd == 0.123456  # total_usd of the one priced attempt
    assert receipt.delivered_matches_verified is True


def test_build_receipt_bounds_the_diff_flag_and_tool_lists() -> None:
    # A pathological run must not bloat runs.jsonl: the lists are capped like the other bounded
    # fields, and the cap is a hard bound, not a suggestion.
    attempt = Attempt(
        index=1,
        answer="x",
        approved=True,
        verified=True,
        reverted=False,
        success=True,
        diff_flags=[f"flag{i:02d}" for i in range(60)],
        tool_names=[f"tool{i:02d}" for i in range(250)],
        failure_evidence="E" * 600,
    )
    a = build_receipt(
        AutonomousResult(answer="a", success=True, attempts=[attempt]), "t", None, "ts"
    ).attempts[0]
    assert len(a.diff_flags) == 50 and a.diff_flags[49] == "flag49"
    assert len(a.tool_names) == 200 and a.tool_names[199] == "tool199"
    assert len(a.failure_evidence) == 500
