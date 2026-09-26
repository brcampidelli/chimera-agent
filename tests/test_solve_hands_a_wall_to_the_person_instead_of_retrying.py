"""`chimera solve` stops at a page only the person can pass, instead of grading it and retrying.

The turn loop has ended a run as ``stopped_reason="handover"`` since the browser situation module
(study 25, S11), and the solve loop around it did not know the word. With the module on, an attempt
stopped at a sign-in went on to verification, failed it, was reviewed, reverted and retried — and
the retry walked into the same sign-in, on a stronger (dearer) model when one was configured.

Now the handover is an ending of its own: one attempt, recorded and not reverted, no verifier, no
reviewer, no anti-pattern learned, and the answer that opens with the page. Free: no model call.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

from chimera.api.runs import build_receipt
from chimera.core import AutonomousAgent, AutonomousConfig, WorkspaceGuard
from chimera.core.agent import AgentConfig, AgentResult
from chimera.core.verify import VerificationResult

_HANDED = (
    "Handed over to you: https://git.example.com/login asks for a sign-in (a password field (e2)). "
    "I stopped there.\n\nThe page asks you to sign in to your account."
)


class _Worker:
    """Stops at a wall on every attempt, like a real one would; optionally writes a file first."""

    def __init__(self, *, workspace: pathlib.Path | None = None, reason: str = "handover") -> None:
        self.config = AgentConfig(model="m")
        self.workspace = workspace
        self.reason = reason
        self.runs = 0

    def run(self, task: str, **kw: Any) -> AgentResult:
        self.runs += 1
        if self.workspace is not None:
            (self.workspace / "notes.md").write_text("found the settings page\n", encoding="utf-8")
        answer = _HANDED if self.reason == "handover" else "done"
        return AgentResult(answer=answer, steps=3, stopped_reason=self.reason, usd=0.001)


class _Verifier:
    def __init__(self) -> None:
        self.calls = 0

    def verify(self) -> VerificationResult:
        self.calls += 1
        return VerificationResult(False, "tests failed")


class _Manager:
    def __init__(self) -> None:
        self.calls = 0

    def review(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - must not be reached
        self.calls += 1
        raise AssertionError("a handover must not be reviewed")


class _Evolver:
    def __init__(self) -> None:
        self.failures = 0

    def maybe_evolve(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        raise AssertionError("a handover is not a success to learn from")

    def maybe_evolve_failure(self, *args: Any, **kwargs: Any) -> None:
        self.failures += 1


def _auto(worker: Any, **kw: Any) -> AutonomousAgent:
    attempts = kw.pop("max_attempts", 3)
    cfg = AutonomousConfig(max_attempts=attempts, use_planner=False, use_manager=False)
    return AutonomousAgent(worker, config=cfg, **kw)


def test_a_handover_ends_the_run_after_one_attempt_as_handover() -> None:
    worker, escalate = _Worker(), _Worker()
    verifier = _Verifier()
    auto = _auto(worker, verifier=verifier, escalate_worker=escalate)
    result = auto.run("check my repo settings")

    assert worker.runs == 1 and escalate.runs == 0, "no retry, and no dearer model for the retry"
    assert result.ending == "handover" and result.stopped_reason == "handover"
    assert result.success is False, "the task is not done; the person has a step to take"
    assert verifier.calls == 0, "a run that stopped on purpose is not graded"
    assert result.answer == _HANDED, "the answer opens with the page, as the turn loop wrote it"


def test_the_stopped_attempt_is_recorded_with_its_cost() -> None:
    result = _auto(_Worker()).run("check my repo settings")

    assert len(result.attempts) == 1
    attempt = result.attempts[0]
    assert attempt.answer == _HANDED and attempt.usd == 0.001
    assert attempt.verified is False and attempt.reverted is False


def test_what_the_attempt_wrote_is_kept_and_recorded(tmp_path: pathlib.Path) -> None:
    """The person picks the task up where it stopped, so the work before the wall stays."""
    result = _auto(_Worker(workspace=tmp_path), guard=WorkspaceGuard(tmp_path)).run("do it")

    assert (tmp_path / "notes.md").read_text(encoding="utf-8") == "found the settings page\n"
    assert result.attempts[0].diff_productive is True
    assert result.attempts[0].diffs, "the diff is on the record, as on the cancel path"


def test_a_handover_teaches_no_anti_pattern_and_is_not_a_stall() -> None:
    evolver = _Evolver()
    from chimera.evolution import StagnationDetector

    result = _auto(
        _Worker(), auto_evolver=evolver, stagnation=StagnationDetector(window=2)
    ).run("check my repo settings")

    assert evolver.failures == 0, "a sign-in is not evidence the approach was wrong"
    assert result.stagnant is None, "it waits on a person; it summarises no failures"


def test_the_reviewer_is_not_consulted() -> None:
    manager = _Manager()
    cfg = AutonomousConfig(max_attempts=3, use_planner=False, use_manager=True)
    AutonomousAgent(_Worker(), config=cfg, manager=manager).run("check my repo settings")
    assert manager.calls == 0


def test_the_receipt_says_handover() -> None:
    result = _auto(_Worker()).run("check my repo settings")
    receipt = build_receipt(result, "check my repo settings", None, "2026-09-26T00:00:00Z")

    assert receipt.ending == "handover" and receipt.stopped_reason == "handover"


def test_an_ordinary_final_still_goes_through_verify_and_retry() -> None:
    """The control: only the handover word short-cuts the loop."""
    worker, verifier = _Worker(reason="final"), _Verifier()
    result = _auto(worker, verifier=verifier, max_attempts=2).run("do it")

    assert worker.runs == 2 and verifier.calls == 2
    assert result.ending == "exhausted"


def test_the_terminal_names_the_ending_as_a_handover_not_a_failure() -> None:
    """`chimera solve` printed "failed" for every unsuccessful ending; this one is not a failure."""
    import inspect

    from chimera.cli import main

    source = inspect.getsource(main.solve)
    tree = ast.parse(source.lstrip())
    compares = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and any(isinstance(c, ast.Constant) and c.value == "handover" for c in node.comparators)
    ]
    assert compares, "solve no longer tells a handover apart from a failure"
    assert "handed over to you" in source
