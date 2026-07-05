"""Tier-2 autonomous task runner — plan, execute, supervise, verify-or-revert.

Ties the pieces together into a single-task autonomous loop:

1. assemble ownership-scoped **Spine** context for the task
2. **plan** the task into steps
3. snapshot the workspace, then **execute** with the Worker (the agent loop)
4. a **Manager** reviews the result (generate-vs-verify)
5. **verify** with executable evidence; on failure (or rejection) **revert** to the
   snapshot and retry with feedback, up to a budget — and, when an escalate worker is
   given, run the retry on it (issue #3): once an attempt fails the task has *proven*
   hard, so the retry pays for fusion. Difficulty read from the review surface.
6. record the attempt in the **experience buffer**

Every dependency is injectable, so the whole loop is testable without a network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from chimera.core.agent import AgentResult
from chimera.core.checkpoint import WorkspaceGuard
from chimera.core.planner import Plan, Planner
from chimera.core.spine import assemble_spine
from chimera.core.supervisor import Manager
from chimera.core.verify import Verifier
from chimera.ecosystem.events import events_from_transcript
from chimera.ecosystem.trajectory import TrajectoryCollector
from chimera.evolution.experience import ExperienceBuffer, Outcome, format_lessons
from chimera.evolution.stagnation import StagnationDetector
from chimera.telemetry import get_logger

_log = get_logger("core.autonomous")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


class Worker(Protocol):
    """Anything that can execute a task and return a result (the agent loop)."""

    def run(self, task: str) -> AgentResult: ...


class SupportsRemember(Protocol):
    """Anything that can store a durable fact (a MemoryManager)."""

    def remember(self, content: str, *, key: str | None = None) -> object: ...


class SupportsAutoEvolve(Protocol):
    """Turns a recurring success into a learned skill (an AutoSkillEvolver)."""

    def maybe_evolve(self, task: str, solution: str, prior_successes: int) -> object: ...


class SupportsCardContext(Protocol):
    """Retrieves TRS skill-card context relevant to a task (a CardRetriever)."""

    def card_context(self, task: str) -> str: ...


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
        escalate_worker: Worker | None = None,
        stagnation: StagnationDetector | None = None,
        planner: Planner | None = None,
        manager: Manager | None = None,
        verifier: Verifier | None = None,
        guard: WorkspaceGuard | None = None,
        experience: ExperienceBuffer | None = None,
        trajectories: TrajectoryCollector | None = None,
        memory: SupportsRemember | None = None,
        auto_evolver: SupportsAutoEvolve | None = None,
        cards: SupportsCardContext | None = None,
        spine_workspace: Path | None = None,
        config: AutonomousConfig | None = None,
    ) -> None:
        self.worker = worker
        self.escalate_worker = escalate_worker
        self.stagnation = stagnation
        self.planner = planner
        self.manager = manager
        self.verifier = verifier
        self.guard = guard
        self.experience = experience
        self.trajectories = trajectories
        self.memory = memory
        self.auto_evolver = auto_evolver
        self.cards = cards
        self.spine_workspace = spine_workspace
        self.config = config or AutonomousConfig()

    def run(self, task: str) -> AutonomousResult:
        spine = assemble_spine(self.spine_workspace, task) if self.spine_workspace else ""
        # Behavioural loop: fold lessons from PRIOR runs (recalled before this run
        # records anything) into the planner + worker context, so the agent avoids
        # repeating past failure modes. Advisory only — verify-or-revert below still
        # decides success, so a misleading lesson can't corrupt the workspace.
        lessons = self._recall_lessons(task)
        # Retrieved TRS skill cards (Improvement #1): distilled Do/Avoid/Check hints from
        # past runs, injected so the worker/planner reuse what worked and avoid known
        # failure modes. Advisory only — verify-or-revert still decides success.
        card_ctx = self.cards.card_context(task) if self.cards is not None else ""
        context = "\n\n".join(part for part in (spine, lessons, card_ctx) if part)
        # how many times this task pattern has already succeeded / failed (before this
        # run) — the recurrence signals that gate auto-skill-evolution (a pattern card on
        # recurring success, an anti-pattern card on recurring failure)
        prior_successes = self._count_prior_successes(task)
        prior_failures = self._count_prior_failures(task)
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
            # Observed difficulty (issue #3): the first attempt uses the cost-aware worker;
            # once an attempt has failed (index > 1) the task has proven hard, so retries run
            # on the escalated fusion worker when one is given. Falls back to the same worker.
            worker = (
                self.escalate_worker
                if index > 1 and self.escalate_worker is not None
                else self.worker
            )
            if worker is self.escalate_worker:
                _log.debug("attempt %d: task proved hard, escalating retry to fusion worker", index)
            agent_result = worker.run(prompt)
            answer = agent_result.answer

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
            outcome: Outcome = "success" if ok else "failure"
            if self.experience is not None:
                self.experience.record(task, outcome, detail=(fb or vout)[:500])
            if self.trajectories is not None:
                # Each attempt is a (task -> answer) trajectory; multiple attempts on
                # one task give success/failure pairs — the raw signal for DPO. The
                # per-step tool events feed the SkillCoach process-quality filter.
                self.trajectories.record(
                    task,
                    answer,
                    outcome=outcome,
                    reward=1.0 if ok else 0.0,
                    steps=agent_result.steps,
                    events=events_from_transcript(
                        [m for m in agent_result.transcript if isinstance(m, dict)]
                    ),
                )

            if ok:
                _log.debug("task succeeded on attempt %d", index)
                self._remember_success(task, answer)
                if self.auto_evolver is not None:
                    self.auto_evolver.maybe_evolve(task, answer, prior_successes)
                return AutonomousResult(answer=answer, success=True, attempts=attempts, plan=plan)

            feedback = fb or (
                f"Verification failed:\n{vout}" if vout else "The attempt did not pass verification."
            )
            # Step-level failure attribution (SkillAdaptor): if a tool step errored,
            # point the retry at the FIRST faulty step instead of letting one early
            # error diffuse across the whole next attempt.
            hint = self._fault_hint(agent_result)
            if hint:
                feedback = f"{feedback}\n\n{hint}" if feedback else hint

            # Anti-stagnation (crowding-score analog, arXiv 2606.29717): when successive
            # attempts keep failing the *same* way, refining is a local optimum — fold in a
            # pivot instruction so the next attempt tries a fundamentally different approach.
            # Advisory only; the escalated worker still supplies the stronger model.
            if self.stagnation is not None:
                self.stagnation.record_signature(hint or vout or feedback)
                if self.stagnation.assess().stagnant:
                    _log.debug("attempt %d: stagnation detected; injecting pivot advice", index)
                    feedback = f"{feedback}\n\n{self.stagnation.advice()}"

        # The run ultimately failed: if this failure pattern recurs, distill an advisory
        # anti-pattern card so future attempts are warned. Guarded — the capability is
        # optional, so an evolver that only learns from successes is left untouched.
        if self.auto_evolver is not None:
            evolve_failure = getattr(self.auto_evolver, "maybe_evolve_failure", None)
            if callable(evolve_failure):
                evolve_failure(task, feedback, prior_failures)

        last = attempts[-1].answer if attempts else ""
        return AutonomousResult(answer=last, success=False, attempts=attempts, plan=plan)

    def _recall_lessons(self, task: str) -> str:
        if self.experience is None:
            return ""
        return format_lessons(self.experience.relevant(task))

    def _count_prior_successes(self, task: str) -> int:
        if self.experience is None:
            return 0
        return sum(1 for exp in self.experience.relevant(task, k=25) if exp.outcome == "success")

    def _count_prior_failures(self, task: str) -> int:
        if self.experience is None:
            return 0
        return sum(1 for exp in self.experience.relevant(task, k=25) if exp.outcome == "failure")

    def _remember_success(self, task: str, answer: str) -> None:
        """On a verified success, curate one deduped long-term memory fact.

        Only verified successes reach here (the verify-or-revert gate), so failed
        or unverified work is never memorised. The MemoryManager dedups by key, so
        re-solving the same task UPDATEs the entry rather than bloating memory.
        """
        if self.memory is None:
            return
        snippet = next((line.strip() for line in answer.splitlines() if line.strip()), "")[:160]
        fact = f"Accomplished: {task}" + (f" — {snippet}" if snippet else "")
        self.memory.remember(fact, key=f"solve:{_slug(task)}")

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
    def _fault_hint(result: AgentResult) -> str:
        """Localize the first failed tool step (SkillAdaptor) to sharpen the retry."""
        from chimera.evolution.attribution import localize_fault

        transcript = [msg for msg in result.transcript if isinstance(msg, dict)]
        fault = localize_fault(transcript)
        if fault is None:
            return ""
        return f"Step-level diagnosis — the first failing step was tool `{fault.tool}`: {fault.error[:200]}"

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
