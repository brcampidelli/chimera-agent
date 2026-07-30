"""What is allowed to count as success, and on whose authority.

The default path used to accept a convincing paragraph. With no `--verify`, the verdict fell to the
Manager — which receives `(task, answer, context)` and never sees the diff, the transcript, or a
single file. Combined with an unchanged workspace that is exactly the empty-patch failure the code
already documented in autonomous.py ("SWE-bench run 1: 11/19 empty patches"), and it was reachable
by default.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core import AutonomousAgent, AutonomousConfig, WorkspaceGuard
from chimera.core.agent import AgentResult
from chimera.core.verify import VerificationResult


class _TalkingWorker:
    """Answers convincingly and touches nothing — the narrate-instead-of-act shape."""

    def __init__(self, answer: str = "I fixed the bug in the parser.") -> None:
        self.answer = answer
        self.runs = 0

    def run(self, task: str) -> AgentResult:
        self.runs += 1
        return AgentResult(answer=self.answer, steps=1, stopped_reason="final")


class _EditingWorker:
    """Actually writes a file, so the diff gate measures a productive change."""

    def __init__(self, workspace: Path, name: str = "fix.py") -> None:
        self.workspace = workspace
        self.name = name
        self.runs = 0

    def run(self, task: str) -> AgentResult:
        self.runs += 1
        (self.workspace / self.name).write_text(f"# attempt {self.runs}\n", encoding="utf-8")
        return AgentResult(answer="applied the fix", steps=1, stopped_reason="final")


class _ApprovingManager:
    """Approves anything — an LLM reading prose, which is what the default path relied on."""

    def review(self, task: str, answer: str, context: str) -> Any:
        from chimera.core.supervisor import Review

        return Review(approved=True, feedback="")


class _PassingVerifier:
    def verify(self) -> VerificationResult:
        return VerificationResult(True, "ok")


def _agent(workspace: Path, worker: Any, **kwargs: Any) -> AutonomousAgent:
    return AutonomousAgent(
        worker=worker,
        manager=kwargs.pop("manager", _ApprovingManager()),
        planner=None,
        verifier=kwargs.pop("verifier", None),
        guard=WorkspaceGuard(workspace),
        config=AutonomousConfig(max_attempts=1, use_planner=False),
        **kwargs,
    )


def test_prose_alone_no_longer_counts_as_success(tmp_path: Path) -> None:
    # No verifier, an approving manager, and a workspace that never changed. Before, this was a
    # reported success. It is the exact failure the code documents and did not block.
    agent = _agent(tmp_path, _TalkingWorker())
    result = agent.run("fix the parser bug")

    assert result.success is False
    assert "No file was changed" in result.attempts[-1].feedback


def test_a_real_edit_still_passes_on_the_managers_word(tmp_path: Path) -> None:
    # The gate must not punish work that actually happened. Files changed + an approval is
    # evidence, even without an executable verifier.
    agent = _agent(tmp_path, _EditingWorker(tmp_path))
    result = agent.run("fix the parser bug")

    assert result.success is True
    assert result.attempts[-1].evidence == "diff+manager"


def test_a_verifier_still_decides_even_with_no_diff(tmp_path: Path) -> None:
    # An executable verifier is ground truth and outranks the diff heuristic: a task can legitimately
    # pass without touching a file (a query, a check, an already-correct state).
    agent = _agent(tmp_path, _TalkingWorker(), verifier=_PassingVerifier())
    result = agent.run("confirm the suite is green")

    assert result.success is True
    assert result.attempts[-1].evidence == "verifier"


def test_evidence_names_the_authority_that_approved(tmp_path: Path) -> None:
    # A receipt that says "success" without saying on whose authority invites the reader to assume
    # the strongest one. These labels are what let a run be audited later.
    verified = _agent(tmp_path, _EditingWorker(tmp_path), verifier=_PassingVerifier()).run("t")
    assert verified.attempts[-1].evidence == "verifier"

    unverified = _agent(tmp_path / "b", _TalkingWorker()).run("t")
    assert unverified.attempts[-1].evidence == "none"


def test_require_diff_still_fires_when_a_verifier_is_present(tmp_path: Path) -> None:
    # The explicit flag is unchanged: with --require-diff, an unchanged workspace fails even when a
    # verifier passed. Used by the SWE-bench arms, where an empty patch is never a solve.
    agent = _agent(tmp_path, _TalkingWorker(), verifier=_PassingVerifier(), require_diff=True)
    result = agent.run("edit the file")

    assert result.success is False
    assert "No file was changed" in result.attempts[-1].feedback


def test_gate_stays_silent_when_the_diff_cannot_be_measured(tmp_path: Path) -> None:
    # With no workspace guard the diff is None, not False — we do not KNOW whether anything changed,
    # and failing on an unknown would break every non-filesystem task.
    agent = AutonomousAgent(
        worker=_TalkingWorker(),
        manager=_ApprovingManager(),
        planner=None,
        verifier=None,
        guard=None,
        config=AutonomousConfig(max_attempts=1, use_planner=False),
    )
    result = agent.run("summarise the architecture")

    assert result.success is True
    assert result.attempts[-1].evidence == "manager"
