"""A failure has a class before it has a retry.

The loop shipped the two losing arms of arXiv 2606.01416's fault-injection study: a retry briefed
with generic feedback (manager prose plus verifier output) and an unconditional escalation. It had
no notion of *what kind* of failure had happened, so a hollow success, a failing assertion, a step
ceiling and a manager's rejection all produced the same brief. Recovery aimed at the class won at
every matched budget in the study — 98.8% against 94.5% — and by the most at one attempt.

Three things are pinned here, in the order a reviewer would ask for them:

1. **Each class is detected from what an ``Attempt`` carries, and the evidence is asserted**, not
   only the label. A detector that fires on the wrong field produces the right class on the
   fixture that motivated it and the wrong evidence — which is what the evidence assertions catch.
2. **``generic`` — the default — briefs the retry byte for byte as before.** The two frozen prompts
   below were captured from the UNMODIFIED loop at ``d5f1d40`` (the branch point), by driving it
   with the same fixtures and printing ``repr(worker.prompts[1])``. They are literals on purpose:
   composing them from the same helpers the loop uses would let both drift together.
3. **The class reaches the receipt in both modes.** The matched-budget sweep (the plan's item 1+7,
   step 2) needs the class of every failure the generic arm produced, not only the ones targeting
   acted on.

Everything here is free: no model call, no network.

Sabotage record — each guard was broken on disk, its test confirmed red, the file restored and
byte-compared to the pre-sabotage copy, and the test confirmed green again:

* ``classify_failure`` reading ``attempt.feedback`` where it reads ``attempt.verify_output`` →
  8 red, ``test_a_failing_test_carries_its_id_and_message`` first among them (the class drops
  to UNKNOWN or REVERTED; the evidence assertion is what names the wrong field).
* ``_acted`` treating ``read_file`` as an acting tool (``or name == "read_file"``) → 3 red,
  ``test_an_unchanged_tree_with_no_acting_tool_is_tool_skip`` among them (HOLLOW_SUCCESS instead).
* The loop targeting in BOTH modes (the ``self.recovery == "targeted"`` guard deleted) → 3 red:
  both byte-identity tests and ``test_generic_mode_emits_no_targeting_event``.
* ``build_receipt`` no longer copying ``failure_class`` → 1 red,
  ``test_the_class_is_on_the_attempt_and_the_receipt_in_both_modes``.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from dataclasses import asdict
from typing import Any

import pytest

from chimera.api.runs import build_receipt
from chimera.core import AutonomousAgent, AutonomousConfig, WorkspaceGuard
from chimera.core.agent import AgentConfig, AgentResult
from chimera.core.autonomous import Attempt, AutonomousResult
from chimera.core.events import AgentEvent
from chimera.core.failure_class import (
    RECOVERY_MODES,
    ClassifiedFailure,
    FailureClass,
    classify_failure,
    targeted_feedback,
)
from chimera.core.supervisor import Review
from chimera.core.verify import VerificationResult
from chimera.evolution.diff_gate import FileDiff

# What pytest prints for one failing assertion — the verifier output every fixture below uses.
PYTEST_OUTPUT = (
    "============================= test session starts ==============================\n"
    "collected 1 item\n"
    "\n"
    "tests/test_calc.py F                                                     [100%]\n"
    "\n"
    "=================================== FAILURES ===================================\n"
    "___________________________________ test_add ___________________________________\n"
    "\n"
    "    def test_add():\n"
    ">       assert add(1, 2) == 4\n"
    "E       assert 3 == 4\n"
    "E        +  where 3 = add(1, 2)\n"
    "\n"
    "tests/test_calc.py:12: AssertionError\n"
    "=========================== short test summary info ============================\n"
    "FAILED tests/test_calc.py::test_add - assert 3 == 4\n"
    "============================== 1 failed in 0.02s ===============================\n"
)

# The generic string the loop composed for the failing-test fixture BEFORE this change (see the
# module docstring). Fixture A: the verifier fails with the output above, the manager rejects with
# prose, and a real edit to calc.py is reverted.
FROZEN_A = (
    "Task: fix add() in calc.py so the tests pass\n"
    "\n"
    "Feedback from the previous attempt (address this):\n"
    "manager: handle the empty case\n"
    "\n"
    "Verification failed:\n"
    "============================= test session starts ==============================\n"
    "collected 1 item\n"
    "\n"
    "tests/test_calc.py F                                                     [100%]\n"
    "\n"
    "=================================== FAILURES ===================================\n"
    "___________________________________ test_add ___________________________________\n"
    "\n"
    "    def test_add():\n"
    ">       assert add(1, 2) == 4\n"
    "E       assert 3 == 4\n"
    "E        +  where 3 = add(1, 2)\n"
    "\n"
    "tests/test_calc.py:12: AssertionError\n"
    "=========================== short test summary info ============================\n"
    "FAILED tests/test_calc.py::test_add - assert 3 == 4\n"
    "============================== 1 failed in 0.02s ===============================\n"
)

# Fixture B, captured the same way: no verifier, the manager approves, the tree is unchanged, so
# the diff gate fails the attempt with its own sentence.
FROZEN_B = (
    "Task: fix add() in calc.py so the tests pass\n"
    "\n"
    "Feedback from the previous attempt (address this):\n"
    "No file was changed. This task requires editing code: an explanation is not a fix. Locate "
    "the responsible file and make the edit."
)

TASK = "fix add() in calc.py so the tests pass"


def _attempt(**fields: Any) -> Attempt:
    """A failed attempt with nothing on it, then whatever the test sets."""
    attempt = Attempt(
        index=1, answer="I changed add()", approved=False, verified=False, reverted=False
    )
    for name, value in fields.items():
        setattr(attempt, name, value)
    return attempt


# --- 1. each class, with its evidence -------------------------------------------------------------


def test_the_dollar_ceiling_is_budget_and_the_evidence_is_the_reason() -> None:
    got = classify_failure(_attempt(), stopped_reason="spend")

    assert got == ClassifiedFailure(FailureClass.BUDGET, "stopped_reason=spend")


def test_the_step_ceiling_is_timeout() -> None:
    got = classify_failure(_attempt(), stopped_reason="max_steps")

    assert got == ClassifiedFailure(FailureClass.TIMEOUT, "stopped_reason=max_steps")


def test_an_unchanged_tree_with_no_acting_tool_is_tool_skip() -> None:
    got = classify_failure(_attempt(diff_productive=False, tool_names=["read_file", "read_file"]))

    assert got.cls is FailureClass.TOOL_SKIP
    assert "read_file" in got.evidence and "no write/exec call" in got.evidence
    assert "diff_productive=False" in got.evidence


def test_an_unchanged_tree_after_an_edit_tool_is_hollow_success() -> None:
    got = classify_failure(
        _attempt(
            diff_productive=False,
            tool_names=["edit_file"],
            diff_summary="diff: no productive change",
        )
    )

    assert got == ClassifiedFailure(
        FailureClass.HOLLOW_SUCCESS, "diff_productive=False; diff: no productive change"
    )


def test_an_unmeasured_tree_fires_neither_unchanged_class() -> None:
    """``None`` is "nobody looked". Firing on it would call every guard-less failure hollow."""
    got = classify_failure(_attempt(diff_productive=None, tool_names=[]))

    assert got.cls is FailureClass.UNKNOWN


def test_a_verifier_that_could_not_load_the_code_is_build_error() -> None:
    output = (
        "ImportError while importing test module 'tests/test_calc.py'.\n"
        "tests/test_calc.py:1: in <module>\n"
        "    from calc import add\n"
        "E   ModuleNotFoundError: No module named 'calc'\n"
    )
    got = classify_failure(_attempt(evidence="verifier", verify_output=output))

    assert got == ClassifiedFailure(
        FailureClass.BUILD_ERROR, "E   ModuleNotFoundError: No module named 'calc'"
    )


def test_a_failing_test_carries_its_id_and_message() -> None:
    got = classify_failure(_attempt(evidence="verifier", verify_output=PYTEST_OUTPUT))

    assert got == ClassifiedFailure(
        FailureClass.FAILING_TEST, "FAILED tests/test_calc.py::test_add - assert 3 == 4"
    )


def test_a_verifier_that_abstained_is_not_read_as_a_verdict() -> None:
    """``evidence`` is ``verifier`` exactly when one judged; anything else is not its verdict."""
    got = classify_failure(_attempt(evidence="none", verify_output=PYTEST_OUTPUT))

    assert got.cls is FailureClass.UNKNOWN


def test_a_unittest_failure_is_a_failing_test_too() -> None:
    output = (
        "FAIL: test_add (tests.test_calc.CalcTest.test_add)\n"
        "Traceback (most recent call last):\n"
        '  File "tests/test_calc.py", line 12, in test_add\n'
        "AssertionError: 3 != 4\n"
    )
    got = classify_failure(_attempt(evidence="verifier", verify_output=output))

    assert got == ClassifiedFailure(
        FailureClass.FAILING_TEST, "FAIL: test_add (tests.test_calc.CalcTest.test_add)"
    )


def test_a_mis_invoked_command_is_not_a_failing_test() -> None:
    """``ERROR:`` at the start of a line is also how argparse complains; that names no test."""
    got = classify_failure(
        _attempt(evidence="verifier", verify_output="ERROR: usage: pytest [options] [file_or_dir]")
    )

    assert got.cls is FailureClass.UNKNOWN


def test_a_rollback_the_verifier_does_not_explain_is_reverted() -> None:
    """The measured case: a custom check script whose output names no test and no assertion."""
    diff = FileDiff("index.html", "@@ -1 +1 @@\n-<div>\n+<div role='dialog'>")
    got = classify_failure(
        _attempt(
            reverted=True,
            diffs=[diff],
            evidence="verifier",
            verify_output="PASS - nucleo ok\nFAIL (1):\n  - janelas sem role=dialog",
        )
    )

    assert got == ClassifiedFailure(FailureClass.REVERTED, "reverted=True; 1 file(s): index.html")


def test_a_rollback_of_nothing_is_not_reverted() -> None:
    """The guard restores after every failed attempt; with no diff, nothing was undone."""
    got = classify_failure(_attempt(reverted=True, diffs=[]))

    assert got.cls is FailureClass.UNKNOWN


def test_nothing_matching_is_unknown_with_no_evidence() -> None:
    assert classify_failure(_attempt()) == ClassifiedFailure(FailureClass.UNKNOWN, "")


def test_a_successful_attempt_has_no_failure_class() -> None:
    with pytest.raises(ValueError, match="successful"):
        classify_failure(_attempt(success=True))


def test_every_class_is_a_lowercase_string_the_receipt_can_carry() -> None:
    for member in FailureClass:
        assert isinstance(member.value, str) and member.value == member.value.lower()


# --- 2. precedence: from "never finished" to "finished and was judged" ---------------------------


def test_a_build_error_outranks_the_tests_it_prevented() -> None:
    output = (
        "E   ImportError: cannot import name 'add' from 'calc'\n"
        "FAILED tests/test_calc.py::test_add - ImportError: cannot import name 'add'\n"
    )
    got = classify_failure(_attempt(evidence="verifier", verify_output=output))

    assert got.cls is FailureClass.BUILD_ERROR


def test_an_untouched_tree_outranks_the_failing_test_on_it() -> None:
    """A test failing on a tree the attempt never touched is the starting state, not a verdict."""
    got = classify_failure(
        _attempt(
            evidence="verifier",
            verify_output=PYTEST_OUTPUT,
            diff_productive=False,
            tool_names=["read_file"],
        )
    )

    assert got.cls is FailureClass.TOOL_SKIP


def test_a_ceiling_outranks_everything_the_attempt_left_behind() -> None:
    got = classify_failure(
        _attempt(
            evidence="verifier",
            verify_output=PYTEST_OUTPUT,
            reverted=True,
            diffs=[FileDiff("calc.py", "+x")],
        ),
        stopped_reason="max_steps",
    )

    assert got.cls is FailureClass.TIMEOUT


def test_a_specific_verdict_outranks_the_rollback() -> None:
    """Every failed attempt with a guard is rolled back; REVERTED is for the ones nothing names."""
    got = classify_failure(
        _attempt(
            evidence="verifier",
            verify_output=PYTEST_OUTPUT,
            reverted=True,
            diffs=[FileDiff("calc.py", "+x")],
        )
    )

    assert got.cls is FailureClass.FAILING_TEST


# --- 3. the brief each class gets -----------------------------------------------------------------

GENERIC = "manager: handle the empty case\n\nVerification failed:\n" + PYTEST_OUTPUT


def test_the_failing_test_brief_is_the_assertion_and_the_location() -> None:
    attempt = _attempt(evidence="verifier", verify_output=PYTEST_OUTPUT)

    text = targeted_feedback(classify_failure(attempt), attempt, generic=GENERIC)

    assert "FAILED tests/test_calc.py::test_add - assert 3 == 4" in text
    assert "E       assert 3 == 4" in text
    assert "at tests/test_calc.py:12" in text
    assert "manager: handle the empty case" not in text, (
        "targeted REPLACES the generic brief; appending to it would measure 'more text', not "
        "'aimed text'"
    )


def test_the_build_error_brief_puts_the_load_failure_first() -> None:
    output = (
        'Traceback (most recent call last):\n  File "calc.py", line 3\n    def add(a, b)\n'
        "SyntaxError: expected ':'\n"
    )
    attempt = _attempt(evidence="verifier", verify_output=output)

    text = targeted_feedback(classify_failure(attempt), attempt, generic=GENERIC)

    assert text.startswith("The code does not build or import")
    assert "SyntaxError: expected ':'" in text and "at calc.py:3" in text


def test_the_reverted_brief_shows_what_was_undone_and_why() -> None:
    diff = FileDiff("index.html", "@@ -1 +1 @@\n-<div>\n+<div role='dialog'>")
    attempt = _attempt(
        reverted=True,
        diffs=[diff],
        evidence="verifier",
        verify_output="FAIL (1):\n  - janelas sem role=dialog",
    )

    text = targeted_feedback(classify_failure(attempt), attempt, generic="g")

    assert "do not repeat it, fix the cause" in text
    assert "janelas sem role=dialog" in text, "the verifier output is the cause"
    assert "--- index.html" in text and "+<div role='dialog'>" in text, "the reverted diff itself"


def test_the_reverted_brief_uses_the_gate_reason_when_no_verifier_judged() -> None:
    attempt = _attempt(
        reverted=True,
        diffs=[FileDiff("a.py", "+x")],
        evidence="none",
        feedback="Completion contract not met:\n- file_exists:README.md",
    )

    text = targeted_feedback(classify_failure(attempt), attempt, generic="g")

    assert "file_exists:README.md" in text


def test_the_tool_skip_brief_demands_a_tool_call() -> None:
    attempt = _attempt(diff_productive=False, tool_names=[])

    text = targeted_feedback(classify_failure(attempt), attempt, generic="g")

    assert "next step must be a tool call" in text
    assert "edit_file" in text and "run_shell" in text, "it names the tools that count as acting"


def test_the_hollow_brief_names_the_files_the_task_named() -> None:
    attempt = _attempt(diff_productive=False, tool_names=["edit_file"])

    text = targeted_feedback(
        classify_failure(attempt),
        attempt,
        generic="g",
        task="fix add() in calc.py so tests/test_calc.py passes",
    )

    assert "Files the task names: calc.py, tests/test_calc.py." in text
    assert "must write to one of them" in text


def test_the_hollow_brief_without_a_named_file_does_not_invent_one() -> None:
    attempt = _attempt(diff_productive=False, tool_names=["edit_file"])

    text = targeted_feedback(classify_failure(attempt), attempt, generic="g", task="make it pass")

    assert "Files the task names" not in text
    assert "Locate the responsible file" in text


def test_the_timeout_brief_names_the_ceiling() -> None:
    attempt = _attempt()

    text = targeted_feedback(
        classify_failure(attempt, stopped_reason="max_steps"), attempt, generic="g"
    )

    assert "step ceiling" in text and "stopped_reason=max_steps" in text


def test_unknown_is_the_generic_brief_unchanged() -> None:
    attempt = _attempt()

    assert targeted_feedback(classify_failure(attempt), attempt, generic=GENERIC) == GENERIC


def test_budget_has_no_retry_to_brief() -> None:
    attempt = _attempt()
    classified = classify_failure(attempt, stopped_reason="spend")

    assert targeted_feedback(classified, attempt, generic=GENERIC) == GENERIC


def test_every_class_has_a_brief() -> None:
    """The guard for the next class somebody adds: a member with no branch must still answer."""
    attempt = _attempt(reverted=True, diffs=[FileDiff("a.py", "+x")], verify_output="x")
    for cls in FailureClass:
        text = targeted_feedback(ClassifiedFailure(cls, "evidence"), attempt, generic="generic")
        assert isinstance(text, str) and text


# --- 4. through the real loop: generic is byte-identical, targeted is aimed ----------------------


class _Recorder:
    """Records every prompt it is given; optionally rewrites calc.py so the guard sees an edit."""

    def __init__(
        self, tools: list[str], *, workspace: pathlib.Path | None = None, writes: str | None = None
    ) -> None:
        self.prompts: list[str] = []
        self.tools = tools
        self.workspace = workspace
        self.writes = writes

    def run(self, task: str, **kw: Any) -> AgentResult:
        self.prompts.append(task)
        if self.workspace is not None and self.writes:
            (self.workspace / self.writes).write_text(
                "def add(a, b):\n    return a - b\n", encoding="utf-8"
            )
        return AgentResult(
            answer="I changed add()", steps=1, stopped_reason="final", tool_names=list(self.tools)
        )


class _FailsOnce:
    def __init__(self, output: str) -> None:
        self.calls = 0
        self.output = output

    def verify(self) -> VerificationResult:
        self.calls += 1
        return VerificationResult(
            passed=self.calls > 1, output=self.output if self.calls == 1 else "ok"
        )


class _AlwaysFails:
    def verify(self) -> VerificationResult:
        return VerificationResult(passed=False, output=PYTEST_OUTPUT)


class _Rejects:
    def review(self, task: str, answer: str, context: str = "") -> Review:
        return Review(approved=False, feedback="manager: handle the empty case")


class _Approves:
    def review(self, task: str, answer: str, context: str = "") -> Review:
        return Review(approved=True, feedback="")


def _workspace(tmp_path: pathlib.Path) -> pathlib.Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    return tmp_path


def _fixture_a(tmp_path: pathlib.Path, **kw: Any) -> tuple[_Recorder, AutonomousResult]:
    """Fixture A of the module docstring."""
    ws = _workspace(tmp_path)
    worker = _Recorder(["read_file", "edit_file"], workspace=ws, writes="calc.py")
    auto = AutonomousAgent(
        worker,
        verifier=kw.pop("verifier", _FailsOnce(PYTEST_OUTPUT)),
        manager=_Rejects(),  # type: ignore[arg-type]
        guard=WorkspaceGuard(ws),
        config=AutonomousConfig(
            max_attempts=kw.pop("max_attempts", 2), use_planner=False, use_manager=True
        ),
        **kw,
    )
    return worker, auto.run(TASK)


def _fixture_b(tmp_path: pathlib.Path, **kw: Any) -> tuple[_Recorder, AutonomousResult]:
    """Fixture B of the module docstring."""
    ws = _workspace(tmp_path)
    worker = _Recorder(["read_file"], workspace=ws, writes=None)
    auto = AutonomousAgent(
        worker,
        manager=_Approves(),  # type: ignore[arg-type]
        guard=WorkspaceGuard(ws),
        config=AutonomousConfig(max_attempts=2, use_planner=False, use_manager=True),
        **kw,
    )
    return worker, auto.run(TASK)


def test_generic_mode_is_the_default() -> None:
    assert AutonomousAgent(_Recorder([])).recovery == "generic"  # type: ignore[arg-type]


def test_generic_mode_briefs_the_retry_byte_for_byte_as_before(tmp_path: pathlib.Path) -> None:
    worker, result = _fixture_a(tmp_path)

    assert result.success, "the second attempt passes; the prompt it got is what is under test"
    assert worker.prompts[1] == FROZEN_A


def test_generic_mode_on_the_unchanged_tree_is_byte_identical_too(tmp_path: pathlib.Path) -> None:
    worker, _ = _fixture_b(tmp_path)

    assert worker.prompts[1] == FROZEN_B


def test_targeted_mode_briefs_the_retry_on_the_failing_test(tmp_path: pathlib.Path) -> None:
    worker, result = _fixture_a(tmp_path, recovery="targeted")

    assert result.success
    prompt = worker.prompts[1]
    assert prompt != FROZEN_A
    assert "FAILED tests/test_calc.py::test_add - assert 3 == 4" in prompt
    assert "at tests/test_calc.py:12" in prompt
    assert "manager: handle the empty case" not in prompt


def test_targeted_mode_on_the_unchanged_tree_forces_the_edit(tmp_path: pathlib.Path) -> None:
    worker, result = _fixture_b(tmp_path, recovery="targeted")

    assert result.attempts[0].failure_class == "tool_skip", "read_file alone is not acting"
    assert "next step must be a tool call" in worker.prompts[1]


def test_the_class_is_on_the_attempt_and_the_receipt_in_both_modes(tmp_path: pathlib.Path) -> None:
    for mode in ("generic", "targeted"):
        _, result = _fixture_a(tmp_path / mode, recovery=mode)

        first = result.attempts[0]
        assert first.failure_class == "failing_test", mode
        assert first.failure_evidence == "FAILED tests/test_calc.py::test_add - assert 3 == 4"
        assert result.attempts[1].failure_class == "", "a success is not classified"

        receipt = build_receipt(result, TASK, None, "2026-09-08T00:00:00Z")
        assert receipt.attempts[0].failure_class == "failing_test"
        assert receipt.attempts[0].failure_evidence == first.failure_evidence
        assert '"failure_class":"failing_test"' in receipt.model_dump_json().replace(" ", "")


def test_the_last_attempt_is_classified_though_no_retry_reads_it(tmp_path: pathlib.Path) -> None:
    _, result = _fixture_a(tmp_path, verifier=_AlwaysFails())

    assert result.ending == "exhausted"
    assert [a.failure_class for a in result.attempts] == ["failing_test", "failing_test"]


class _Evolver:
    """Records the feedback the exhausted run distils an anti-pattern from."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def maybe_evolve(self, task: str, solution: str, prior: int, *, tainted: bool = False) -> None:
        return None

    def maybe_distill_correction(
        self, task: str, failed: str, passed: str, *, tainted: bool = False
    ) -> None:
        return None

    def maybe_evolve_failure(
        self, task: str, feedback: str, prior: int, *, tainted: bool = False
    ) -> None:
        self.failures.append(feedback)


def test_targeting_only_briefs_a_retry_that_exists(tmp_path: pathlib.Path) -> None:
    """The last attempt's feedback feeds the anti-pattern card; a prompt flag must not move it."""
    evolver = _Evolver()
    _fixture_a(
        tmp_path,
        verifier=_AlwaysFails(),
        max_attempts=1,
        recovery="targeted",
        auto_evolver=evolver,
    )

    assert len(evolver.failures) == 1
    assert "Verification failed:" in evolver.failures[0], "the generic brief, as before"
    assert "Fix exactly this" not in evolver.failures[0]


def test_targeted_mode_counts_its_injections(tmp_path: pathlib.Path) -> None:
    """A bench arm whose targeting never fired measured a plumbing failure, not the idea."""
    events: list[AgentEvent] = []
    _fixture_a(tmp_path, recovery="targeted", on_event=events.append)

    statuses = [e.text for e in events if e.kind == "status"]
    assert "targeted recovery failing_test (attempt 1)" in statuses


def test_generic_mode_emits_no_targeting_event(tmp_path: pathlib.Path) -> None:
    events: list[AgentEvent] = []
    _fixture_a(tmp_path, on_event=events.append)

    assert not [e for e in events if e.text.startswith("targeted recovery")]


def test_a_run_stopped_on_money_classifies_the_partial_attempt_as_budget() -> None:
    class _StoppedOnSpend:
        def __init__(self) -> None:
            self.config = AgentConfig(model="m", max_usd=0.001)

        def run(self, task: str, **kw: Any) -> AgentResult:
            return AgentResult(answer="spend cap reached: $0.0030", steps=1, stopped_reason="spend")

    auto = AutonomousAgent(
        _StoppedOnSpend(),  # type: ignore[arg-type]
        config=AutonomousConfig(max_attempts=3, use_planner=False, use_manager=False),
    )
    result = auto.run("do it")

    assert result.ending == "spend"
    assert result.attempts[0].failure_class == "budget"
    assert result.attempts[0].failure_evidence == "stopped_reason=spend"


def test_a_cancelled_attempt_is_not_classified() -> None:
    """A cancel is the user's act, not the attempt's failure — the loop already says so."""

    class _Cancelled:
        def run(self, task: str, **kw: Any) -> AgentResult:
            return AgentResult(answer="", steps=1, stopped_reason="cancelled")

    auto = AutonomousAgent(
        _Cancelled(),  # type: ignore[arg-type]
        config=AutonomousConfig(max_attempts=2, use_planner=False, use_manager=False),
    )
    result = auto.run("do it")

    assert result.ending == "cancelled"
    assert result.attempts[0].failure_class == ""


def test_a_misspelled_mode_is_refused_not_ignored() -> None:
    with pytest.raises(ValueError, match="recovery"):
        AutonomousAgent(_Recorder([]), recovery="targetted")  # type: ignore[arg-type]


def test_the_class_survives_the_round_trip_a_checkpoint_does() -> None:
    attempt = Attempt(1, "x", False, False, True)
    attempt.failure_class, attempt.failure_evidence = "reverted", "reverted=True; 1 file(s): a.py"

    revived = Attempt(**asdict(attempt))

    assert (revived.failure_class, revived.failure_evidence) == (
        "reverted",
        "reverted=True; 1 file(s): a.py",
    )
    old = Attempt(
        **{"index": 0, "answer": "a", "approved": True, "verified": True, "reverted": False}
    )
    assert old.failure_class == "" and old.failure_evidence == ""


def test_a_receipt_from_an_attempt_that_predates_the_field_reads_empty() -> None:
    class _OldAttempt:
        index, verified, reverted, success = 1, False, True, False
        verify_output, diff_summary, feedback, diffs, usd = "", "", "", [], None

    class _OldResult:
        answer, success, paused, plan = "x", False, False, None
        attempts = [_OldAttempt()]

    receipt = build_receipt(_OldResult(), "t", None, "2026-09-08T00:00:00Z")  # type: ignore[arg-type]

    assert receipt.attempts[0].failure_class == "" and receipt.attempts[0].failure_evidence == ""


# --- 5. the flag, and the wire from it to the loop ------------------------------------------------


def test_solve_offers_the_flag_and_it_defaults_to_generic() -> None:
    from chimera.cli.main import solve

    param = inspect.signature(solve).parameters["recovery"]

    assert getattr(param.default, "default", param.default) == "generic"


def test_the_flag_is_handed_to_the_loop() -> None:
    """Found by the sibling test's sweep: a flag can exist, be valid, and reach nothing."""
    from chimera.cli import main

    tree = ast.parse(inspect.getsource(main.solve))
    agents = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "AutonomousAgent"
    ]
    assert agents, "solve no longer builds an AutonomousAgent; this guard needs rewriting"
    assert any(
        kw.arg == "recovery" and isinstance(kw.value, ast.Name) and kw.value.id == "recovery"
        for call in agents
        for kw in call.keywords
    ), "solve takes --recovery and never hands it to the loop"


def test_the_cli_and_the_loop_agree_on_the_modes() -> None:
    assert set(RECOVERY_MODES) == {"generic", "targeted"}
