"""The reviewer judged a paragraph and called it judging the work.

`Manager.review` receives `(task, answer, context)` and had never seen the diff, the transcript or
a single file — a fact `tests/test_success_gate.py` states in its own module docstring without
drawing the consequence. Measured on the shipped rc39: four runs, five attempts, **five
rejections**, every one of them for work the receipt's own summary proves was done. The cleanest:

    tool: write_file ok  {"path": "README.md"}  -> "wrote 33 chars to README.md"
    tool: list_dir   ok  {"path": "."}          -> "README.md"
    result: attempt 1 failed
            "the README.md must be physically created, not merely described as created"

and the receipt for that same attempt:

    {"diff_summary": "diff: +1 new, ~0 changed, -0 removed (README.md)",
     "diff_productive": true, "reverted": true, "success": false}

The receipt carries the refutation of the stated reason and reverts anyway. Then the failure is
distilled into an anti-pattern skill card — `phantom_file_creation`, "assuming a file exists
because you described creating it" — so the hallucination is written to disk and outlives the run
that produced it.

The fix is not a new rule. It is showing the reviewer what the attempt did, which required moving
the diff computation ahead of the review; it used to run after every gate had already voted.

**Evidence, not criterion.** The narrowing in
`test_the_judge_only_holds_you_to_the_agreement.py` exists because a recalled fact from another
project made something enforceable that nobody agreed to. A diff cannot do that: it describes what
the answer is a claim *about*, so it can only stop the reviewer being wrong about whether the work
happened.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core import AutonomousAgent, AutonomousConfig, WorkspaceGuard
from chimera.core.agent import AgentResult
from chimera.core.supervisor import Review


class _WritingWorker:
    """Writes a real file and says so — the shape every one of the five rejections had."""

    def __init__(self, workspace: Path, name: str = "README.md") -> None:
        self.workspace = workspace
        self.name = name

    def run(self, task: str) -> AgentResult:
        (self.workspace / self.name).write_text("# Padaria Aurora\n", encoding="utf-8")
        return AgentResult(answer="I created the README.", steps=1, stopped_reason="final")


class _SilentWorker:
    """Touches nothing, and says it did the work anyway."""

    def run(self, task: str) -> AgentResult:
        return AgentResult(answer="I created the README.", steps=1, stopped_reason="final")


class _RecordingManager:
    """Approves everything and keeps what it was told, which is the thing under test."""

    def __init__(self) -> None:
        self.contexts: list[str] = []

    def review(self, task: str, answer: str, context: str) -> Review:
        self.contexts.append(context)
        return Review(approved=True)


def _run(workspace: Path, worker: Any, manager: Any) -> Any:
    return AutonomousAgent(
        worker=worker,
        manager=manager,
        planner=None,
        verifier=None,
        guard=WorkspaceGuard(workspace),
        config=AutonomousConfig(max_attempts=1, use_planner=False),
    ).run("create a README describing the project")


def test_the_reviewer_is_told_which_file_the_attempt_wrote(tmp_path: Path) -> None:
    """Mechanism to wiring. Everything else here is unreachable if the reviewer is still handed
    the answer's prose and nothing about the disk."""
    manager = _RecordingManager()
    _run(tmp_path, _WritingWorker(tmp_path), manager)

    assert manager.contexts, "the reviewer was never called, so this proved nothing"
    context = manager.contexts[-1]
    assert "README.md" in context, "the reviewer still cannot see what the attempt wrote"
    assert "what-this-attempt-changed-on-disk" in context


def test_the_reviewer_is_told_when_nothing_changed(tmp_path: Path) -> None:
    """The other half, and the one that keeps this from being a way to approve anything: measured
    and empty is not the same as unmeasured. A reviewer told nothing goes on assuming, and the
    empty-patch failure this project has measured before is the assumption going the other way."""
    manager = _RecordingManager()
    _run(tmp_path, _SilentWorker(), manager)

    assert manager.contexts
    context = manager.contexts[-1]
    assert "what-this-attempt-changed-on-disk" in context
    assert "no productive change" in context, (
        "the reviewer is left to guess whether the silence means nothing happened or nothing "
        "was looked at"
    )


def test_the_evidence_carries_the_diff_body_not_only_the_filename(tmp_path: Path) -> None:
    """A filename answers "did anything happen"; the body is what lets a reviewer judge whether
    what happened is the thing that was asked for."""
    manager = _RecordingManager()
    _run(tmp_path, _WritingWorker(tmp_path), manager)

    assert "Padaria Aurora" in manager.contexts[-1]


def test_the_agreement_is_still_all_that_can_be_enforced(tmp_path: Path) -> None:
    """The control for the narrowing this sits on top of. Adding evidence must not smuggle the
    worker's context back in: no recalled fact, no skill card, no repository map."""
    manager = _RecordingManager()
    _run(tmp_path, _WritingWorker(tmp_path), manager)

    context = manager.contexts[-1]
    # The spine the WORKER gets always names the workspace root; the reviewer's must not.
    assert str(tmp_path) not in context
    assert "Repository map" not in context


def test_the_diff_is_measured_before_the_review_runs(tmp_path: Path) -> None:
    """Ordering, asserted where it is decided, because this is the part a later refactor would
    undo without noticing: the diff block used to sit after every gate had voted, and a reviewer
    cannot be shown a number that has not been computed yet."""
    import inspect

    source = inspect.getsource(AutonomousAgent.run)
    assert source.index("diffs = unified_diffs(") < source.index(
        "task, answer, attempt_judge_context"
    ), "the diff is computed after the review again, so the evidence is always empty"
