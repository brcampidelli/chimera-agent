"""Tier-2 autonomous task runner — plan, execute, supervise, verify-or-revert.

Ties the pieces together into a single-task autonomous loop:

1. assemble ownership-scoped **Spine** context for the task
2. **plan** the task into steps
3. snapshot the workspace, then **execute** with the Worker (the agent loop)
4. a **Manager** reviews the result (generate-vs-verify)
5. **verify** with executable evidence; on failure (or rejection) **revert** to the
   snapshot and retry with feedback, up to a budget
6. record the attempt in the **experience buffer**

Every dependency is injectable, so the whole loop is testable without a network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from chimera.core.agent import AgentResult
from chimera.core.checkpoint import WorkspaceGuard
from chimera.core.planner import Plan, Planner
from chimera.core.spine import assemble_spine
from chimera.core.supervisor import Manager
from chimera.core.verify import Verifier
from chimera.evolution.experience import ExperienceBuffer, Outcome
from chimera.telemetry import get_logger

_log = get_logger("core.autonomous")


class Worker(Protocol):
    """Anything that can execute a task and return a result (the agent loop)."""

    def run(self, task: str) -> AgentResult: ...


@dataclass
class AutonomousConfig:
    max_attempts: int = 3
    use_planner: bool = True
    use_manager: bool = True


@dataclass
class Attempt:
    index: int
    answer: str
    approved: bool
    verified: bool
    reverted: bool
    success: bool = False
    feedback: str = ""
    verify_output: str = ""


@dataclass
class AutonomousResult:
    answer: str
    success: bool
    attempts: list[Attempt] = field(default_factory=list)
    plan: Plan | None = None


class AutonomousAgent:
    """Runs a task autonomously with planning, supervision and verify-or-revert."""

    def __init__(
        self,
        worker: Worker,
        *,
        planner: Planner | None = None,
        manager: Manager | None = None,
        verifier: Verifier | None = None,
        guard: WorkspaceGuard | None = None,
        experience: ExperienceBuffer | None = None,
        spine_workspace: Path | None = None,
        config: AutonomousConfig | None = None,
    ) -> None:
        self.worker = worker
        self.planner = planner
        self.manager = manager
        self.verifier = verifier
        self.guard = guard
        self.experience = experience
        self.spine_workspace = spine_workspace
        self.config = config or AutonomousConfig()

    def run(self, task: str) -> AutonomousResult:
        context = assemble_spine(self.spine_workspace, task) if self.spine_workspace else ""
        plan = (
            self.planner.plan(task, context=context)
            if self.planner and self.config.use_planner
            else None
        )

        attempts: list[Attempt] = []
        feedback = ""
        for index in range(1, self.config.max_attempts + 1):
            snapshot = self.guard.snapshot() if self.guard else None
            prompt = self._compose(task, plan, context, feedback)
            answer = self.worker.run(prompt).answer

            # Executable evidence is ground truth: when a verifier is present it
            # decides success, and the Manager is consulted only for feedback on a
            # failing attempt. Otherwise the Manager's approval is the gate. This
            # stops a strict reviewer from vetoing — and reverting — verified-correct
            # work just because it judged the narration rather than the artifact.
            verified, vout = self._verify()
            if self.verifier is not None:
                ok = verified
                approved, fb = (True, "") if verified else self._review(task, answer, context)
            else:
                approved, fb = self._review(task, answer, context)
                ok = approved

            attempt = Attempt(index, answer, approved, verified, False, ok, fb, vout)
            if not ok and snapshot is not None and self.guard is not None:
                self.guard.restore(snapshot)
                attempt.reverted = True

            attempts.append(attempt)
            if self.experience is not None:
                outcome: Outcome = "success" if ok else "failure"
                self.experience.record(task, outcome, detail=(fb or vout)[:500])

            if ok:
                _log.debug("task succeeded on attempt %d", index)
                return AutonomousResult(answer=answer, success=True, attempts=attempts, plan=plan)

            feedback = fb or (
                f"Verification failed:\n{vout}" if vout else "The attempt did not pass verification."
            )

        last = attempts[-1].answer if attempts else ""
        return AutonomousResult(answer=last, success=False, attempts=attempts, plan=plan)

    def _review(self, task: str, answer: str, context: str) -> tuple[bool, str]:
        if self.manager is None or not self.config.use_manager:
            return True, ""
        review = self.manager.review(task, answer, context=context)
        return review.approved, review.feedback

    def _verify(self) -> tuple[bool, str]:
        if self.verifier is None:
            return True, ""
        result = self.verifier.verify()
        return result.passed, result.output

    @staticmethod
    def _compose(task: str, plan: Plan | None, context: str, feedback: str) -> str:
        parts: list[str] = []
        if context:
            parts.append(context)
        if plan is not None and plan.steps:
            parts.append("Plan:\n" + plan.as_text())
        parts.append(f"Task: {task}")
        if feedback:
            parts.append(f"Feedback from the previous attempt (address this):\n{feedback}")
        return "\n\n".join(parts)
