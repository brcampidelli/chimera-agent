"""Tests for run receipts (append-only per-run proof log) and their capture by the AutonomousAgent."""

from __future__ import annotations

from pathlib import Path

from chimera.api.runs import AttemptReceipt, RunReceipt, append_run, build_receipt, load_runs
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
