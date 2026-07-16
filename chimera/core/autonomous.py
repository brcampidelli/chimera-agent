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

import inspect
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from chimera.evolution.diff_gate import FileDiff
    from chimera.fusion.probe_log import ProbeLog

from chimera.core.agent import AgentResult
from chimera.core.checklist import RequirementChecklist
from chimera.core.checkpoint import WorkspaceGuard
from chimera.core.contract import CompletionContract
from chimera.core.events import AgentEvent, EventSink
from chimera.core.events import attempt as _ev_attempt
from chimera.core.events import edit as _ev_edit
from chimera.core.events import final as _ev_final
from chimera.core.events import result as _ev_result
from chimera.core.events import status as _ev_status
from chimera.core.ledger import ProgressLedger, TaskLedger
from chimera.core.planner import Plan, Planner
from chimera.core.repomap import build_repo_map
from chimera.core.runstate import RunCheckpointer
from chimera.core.spec_test import SpecTestGenerator, SpecTestVerifier
from chimera.core.spine import assemble_spine
from chimera.core.strong_verify import StrongVerifier
from chimera.core.supervisor import Manager
from chimera.core.task_normalizer import normalize_task
from chimera.core.verify import Verifier
from chimera.ecosystem.events import events_from_transcript
from chimera.ecosystem.trajectory import TrajectoryCollector
from chimera.evolution.experience import ExperienceBuffer, Outcome, format_lessons
from chimera.evolution.playbook import Playbook
from chimera.evolution.stagnation import StagnationDetector
from chimera.evolution.trace_probe import anti_pattern_hint
from chimera.telemetry import get_logger

_log = get_logger("core.autonomous")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


def _format_requirements(requirements: list[Any]) -> str:
    """Render extracted requirements as an up-front acceptance checklist for the worker's context.

    Putting the requirements in front of the worker on attempt 1 (not just feeding back the dropped
    ones after a failed coverage grade) makes it target every constraint from the start — the main
    reason a weak model silently drops a 'must include / must not' clause.
    """
    if not requirements:
        return ""
    lines = "\n".join(f"- [{r.kind}] {r.text}" for r in requirements)
    return (
        "Requirements — your solution must satisfy ALL of these; verify each before finishing:\n"
        f"{lines}"
    )


class Worker(Protocol):
    """Anything that can execute a task and return a result (the agent loop).

    ``on_edit`` is optional and structural: a worker that supports it receives ``(path, patch)`` for
    each file it edits mid-run (the live per-edit diff). The loop only passes it to workers whose
    ``run`` actually accepts it (checked by signature), so a Worker without it is never broken.
    """

    def run(
        self, task: str, *, on_edit: Callable[[str, str], None] | None = None
    ) -> AgentResult: ...


class SupportsRemember(Protocol):
    """Anything that can store a durable fact (a MemoryManager)."""

    def remember(
        self, content: str, *, key: str | None = None, provenance: str = "clean"
    ) -> object: ...


class SupportsAutoEvolve(Protocol):
    """Turns a recurring success into a learned skill (an AutoSkillEvolver)."""

    def maybe_evolve(
        self, task: str, solution: str, prior_successes: int, *, tainted: bool = False
    ) -> object: ...

    def maybe_distill_correction(
        self, task: str, failed: str, passed: str, *, tainted: bool = False
    ) -> object: ...


class SupportsRunTainted(Protocol):
    """Reports whether the current run consumed untrusted content (a TaintLedger)."""

    def run_tainted(self) -> bool: ...

    def record_fetch(self, source: str, content: str = ...) -> object: ...


class SupportsCardContext(Protocol):
    """Retrieves TRS skill-card context relevant to a task (a CardRetriever)."""

    last_retrieved: list[str]  # names of the cards the last card_context injected (credited on outcome)

    def card_context(self, task: str) -> str: ...


@dataclass
class AutonomousConfig:
    max_attempts: int = 3
    use_planner: bool = True
    use_manager: bool = True
    normalize_task: bool = False  # reshape a rambling bug-report task before planning (arXiv 2607.07593)


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
    diff_summary: str = ""
    diffs: list[FileDiff] = field(default_factory=list)  # real per-file unified diffs (pre-revert)


@dataclass
class AutonomousResult:
    answer: str
    success: bool
    attempts: list[Attempt] = field(default_factory=list)
    plan: Plan | None = None
    paused: bool = False  # interrupted for human approval (see AutonomousAgent.pause_on_taint)
    stopped_reason: str = ""  # why the loop ended early; "cancelled" on a cooperative stop, else ""


class AutonomousAgent:
    """Runs a task autonomously with planning, supervision and verify-or-revert."""

    def __init__(
        self,
        worker: Worker,
        *,
        should_stop: Callable[[], bool] | None = None,
        escalate_worker: Worker | None = None,
        stagnation: StagnationDetector | None = None,
        progress_ledger: ProgressLedger | None = None,
        replan_on_stall: bool = False,
        pause_on_taint: bool = False,
        repo_map: bool = False,
        checklist: RequirementChecklist | None = None,
        spec_test_generator: SpecTestGenerator | None = None,
        workspace: Path | None = None,
        strong_verifier: StrongVerifier | None = None,
        playbook: Playbook | None = None,
        contract: CompletionContract | None = None,
        taint: SupportsRunTainted | None = None,
        planner: Planner | None = None,
        plan: Plan | None = None,
        manager: Manager | None = None,
        verifier: Verifier | None = None,
        probe_log: ProbeLog | None = None,
        guard: WorkspaceGuard | None = None,
        experience: ExperienceBuffer | None = None,
        trajectories: TrajectoryCollector | None = None,
        memory: SupportsRemember | None = None,
        auto_evolver: SupportsAutoEvolve | None = None,
        cards: SupportsCardContext | None = None,
        spine_workspace: Path | None = None,
        on_event: EventSink | None = None,
        checkpointer: RunCheckpointer | None = None,
        run_log: Path | None = None,
        config: AutonomousConfig | None = None,
    ) -> None:
        self.worker = worker
        # Cooperative stop check (opt-in): consulted at the top of each attempt so a caller can cancel
        # the run BETWEEN attempts. An in-flight worker call is a blocking model step that cannot be
        # interrupted, so cancellation is honest — it halts before the NEXT attempt starts, never mid-
        # call. None (the default) makes the loop byte-identical to before.
        self.should_stop = should_stop
        self.escalate_worker = escalate_worker
        self.stagnation = stagnation
        self.progress_ledger = progress_ledger
        self.replan_on_stall = replan_on_stall
        self.pause_on_taint = pause_on_taint
        self.repo_map = repo_map
        self.checklist = checklist
        self.spec_test_generator = spec_test_generator
        self.workspace = workspace
        self.strong_verifier = strong_verifier
        self.playbook = playbook
        self.contract = contract
        self.taint = taint
        self.planner = planner
        # A pre-built plan supplied by the caller (e.g. the desktop "plan mode": the user previewed
        # and approved/edited the planner's output). When set, it is used verbatim INSTEAD of calling
        # the planner — the run follows the exact plan the human reviewed, and no planning call is made.
        self.provided_plan = plan
        self.manager = manager
        self.verifier = verifier
        self.probe_log = probe_log
        self.guard = guard
        self.experience = experience
        self.trajectories = trajectories
        self.memory = memory
        self.auto_evolver = auto_evolver
        self.cards = cards
        self.spine_workspace = spine_workspace
        self.on_event = on_event
        self.checkpointer = checkpointer
        self.run_log = run_log
        self.config = config or AutonomousConfig()

    def _emit(self, event: AgentEvent) -> None:
        """Deliver a progress event to the sink, if one is set (never breaks the loop)."""
        if self.on_event is None:
            return
        try:
            self.on_event(event)
        except Exception as exc:  # noqa: BLE001 — a broken sink must not fail the run
            _log.warning("event sink raised, dropping event: %s", exc)

    def _emit_edit(self, path: str, patch: str) -> None:
        """Forward a live per-edit diff (from the worker) as an ``edit`` event through the sink.

        The worker hands us ``(path, patch)`` — the REAL unified diff of a file it just changed, read
        from disk before/after the write (never fabricated). No-op when no sink is attached; a broken
        sink is swallowed by ``_emit``.
        """
        if self.on_event is None:
            return
        self._emit(_ev_edit(path, patch))

    def _run_worker(self, worker: Worker, prompt: str) -> AgentResult:
        """Run the worker, passing ``on_edit`` ONLY when it supports it and a sink is attached.

        Backward-compatible: a worker whose ``run`` doesn't accept ``on_edit`` (or when no event sink
        is set) is called exactly as before — ``worker.run(prompt)`` — so nothing breaks. The support
        check reads the real signature; a TypeError fallback covers any wrapper that hides it.
        """
        if self.on_event is None:
            return worker.run(prompt)
        try:
            supports_on_edit = "on_edit" in inspect.signature(worker.run).parameters
        except (TypeError, ValueError):  # unintrospectable callable (C impl / odd wrapper)
            supports_on_edit = False
        if not supports_on_edit:
            return worker.run(prompt)
        try:
            return worker.run(prompt, on_edit=self._emit_edit)
        except TypeError:  # signature lied (e.g. **kwargs-only) — fall back to the plain call
            return worker.run(prompt)

    def run(self, task: str, *, thread_id: str | None = None) -> AutonomousResult:
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
        # Long-term memory readback (M19-A3): the solve path WROTE verified facts to memory but never
        # read them back, so cross-run knowledge was write-only. Recall the relevant facts (duck-typed
        # on memory.search) and inject them as advisory context — verify-or-revert still decides, so a
        # misleading recalled fact can't corrupt the workspace; tainted facts carry their provenance.
        facts_ctx = self._recall_facts(task)
        # Repo-map: a structural table of contents of the workspace, so the worker jumps to the
        # right file instead of exploring blind. Opt-in and bounded (see build_repo_map).
        repo_ctx = ""
        if self.repo_map and self.spine_workspace is not None:
            digest = build_repo_map(self.spine_workspace)
            if digest:
                repo_ctx = f"Repository map (file: top-level symbols):\n{digest}"
        # ACE playbook: accumulated, delta-curated strategy bullets, injected as advisory context
        # so the worker/planner reuse what has worked across runs (grow-and-refine, anti-collapse).
        playbook_ctx = self.playbook.render() if self.playbook is not None else ""
        # Requirement checklist (opt-in): extract the task's atomic requirements ONCE up front and
        # inject them into context, so the worker targets every requirement from the FIRST attempt
        # (not just discovers the dropped ones via a failed coverage grade on retry). Extraction is
        # task-level and stable, so it's done once here and reused by the coverage gate below.
        requirements = self.checklist.extract(task) if self.checklist is not None else []
        requirements_ctx = _format_requirements(requirements)
        # Spec-grounded test generation (arXiv 2607.06636): when the user gave no --verify command,
        # turn the weak LLM coverage-grade proxy into EXECUTABLE pytest grounded in the extracted
        # requirements — it catches wrong code the coverage grade rubber-stamps (the false positive
        # that corrupts the fitness gate). It slots into the verifier slot, so the coverage grade
        # below is skipped (that path is gated on `verifier is None`). Non-blocking if nothing usable
        # is generated. Run-scoped: these agents are built per task, so setting the verifier here is
        # safe (it is only set when the user configured none).
        if self.spec_test_generator is not None and self.verifier is None and requirements:
            self.verifier = SpecTestVerifier(
                self.spec_test_generator, task, requirements, self.workspace or Path.cwd()
            )
        # M15-A5: sanitize the RECALLED / EVOLVED artifacts (lessons, skill cards, playbook) before
        # injecting them — a memory or skill distilled during a tainted run could carry chat-template
        # control tokens that try to spoof an instruction turn. The current-run parts (spine, repo,
        # requirements) are the user's own workspace/task and are left intact.
        from chimera.governance.sanitize import sanitize_untrusted

        lessons = sanitize_untrusted(lessons)
        card_ctx = sanitize_untrusted(card_ctx)
        playbook_ctx = sanitize_untrusted(playbook_ctx)
        facts_ctx = sanitize_untrusted(facts_ctx)
        context = "\n\n".join(
            part
            for part in (spine, repo_ctx, lessons, card_ctx, facts_ctx, playbook_ctx, requirements_ctx)
            if part
        )
        # how many times this task pattern has already succeeded / failed (before this
        # run) — the recurrence signals that gate auto-skill-evolution (a pattern card on
        # recurring success, an anti-pattern card on recurring failure)
        prior_successes = self._count_prior_successes(task)
        prior_failures = self._count_prior_failures(task)
        # Bug-report normalization (arXiv 2607.07593): reshape a long, rambling bug-report task into a
        # salient-facts-first form for the planner and worker prompt. Only the PROMPT text is
        # normalized — the raw `task` stays the identity used for memory keys / experience below, so a
        # normalized run still dedups against the same task. Deterministic no-op on non-bug or short tasks.
        plan_task = normalize_task(task) if self.config.normalize_task else task
        # A caller-supplied plan (plan mode) is used as-is and skips the planning call entirely — the
        # run executes the exact steps the human approved. Otherwise plan normally (when enabled).
        if self.provided_plan is not None:
            plan = self.provided_plan
        elif self.planner and self.config.use_planner:
            plan = self.planner.plan(plan_task, context=context)
        else:
            plan = None
        # Outer-loop ledger (Magentic-One): accumulates *why* attempts fail so a re-plan on
        # stall is smarter than the first plan. Only when re-planning is enabled and there's a
        # planner to re-run — otherwise the stall path keeps the cheap advisory pivot.
        task_ledger = (
            TaskLedger(task=task)
            if self.replan_on_stall and self.planner and self.config.use_planner
            else None
        )
        attempts: list[Attempt] = []
        feedback = ""
        start_index = 1
        # Durable resume (LangGraph-style thread): if this thread has a live checkpoint, restore
        # the loop state and continue from where the crash left off instead of starting over.
        if self.checkpointer is not None and thread_id:
            saved = self.checkpointer.load(thread_id)
            if saved is not None:
                task = str(saved.get("task", task))
                attempts = [Attempt(**a) for a in saved.get("attempts", [])]
                # HITL 'ignore' (deny): the tainted result was NOT sanctioned — end the run denied,
                # never finalizing the flagged answer, and clear the thread.
                if saved.get("denied"):
                    self._clear_checkpoint(thread_id)
                    self._emit(_ev_final(False, ""))
                    return AutonomousResult(
                        answer="", success=False, attempts=attempts, plan=plan
                    )
                # HITL 'accept'/'edit': finalize the EXACT reviewed answer as-is (no re-run) —
                # approval is of the specific output (edited or not), not a re-execution.
                if saved.get("approved") and saved.get("paused_answer") is not None:
                    # The pre-computed answer being approved wasn't produced by THIS process's card
                    # retrieval (line ~230), so don't credit those cards a use/success — that would
                    # inflate the measured win-rate that drives promotion. Clear the retrieval first.
                    if self.cards is not None:
                        self.cards.last_retrieved = []
                    return self._finalize_success(
                        task, str(saved["paused_answer"]), attempts, prior_successes, plan,
                        thread_id, tainted=bool(saved.get("was_tainted", True)),
                        # Carry the diff-gate verdict across the pause so an approved hollow success
                        # is still not learned (None on legacy checkpoints → learns, as before).
                        productive=saved.get("productive"),
                    )
                feedback = str(saved.get("feedback", ""))
                start_index = int(saved.get("next_index", 1))
                steps = saved.get("plan_steps")
                plan = Plan(steps=list(steps), raw=str(saved.get("plan_raw", ""))) if steps is not None else None
                # Re-seed taint: the pre-crash run consumed untrusted content, so this resumed run is
                # tainted too even if it fetches nothing new (it may succeed off residual workspace
                # state). Without this the fresh ledger reads clean and the anti-poisoning gates
                # (outbound strip, tainted provenance, pause-on-taint) silently no-op on resume.
                if saved.get("was_tainted") and self.taint is not None and not self.taint.run_tainted():
                    self.taint.record_fetch("resumed-tainted-state")
                self._emit(_ev_status(f"resumed thread {thread_id} at attempt {start_index}"))

        self._emit(_ev_status("planning complete" if plan else "starting"))
        for index in range(start_index, self.config.max_attempts + 1):
            # Cooperative cancel (checked BEFORE the attempt starts): an in-flight model call can't be
            # interrupted, so a stop request halts the loop here — after the previous attempt finished,
            # before this one begins. The already-completed attempts are returned intact.
            if self.should_stop is not None and self.should_stop():
                return self._finalize_cancelled(task, attempts, plan, thread_id)
            self._emit(_ev_attempt(index, self.config.max_attempts))
            snapshot = self.guard.snapshot() if self.guard else None
            prompt = self._compose(plan_task, plan, context, feedback)
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
            # Re-check right before the (uninterruptible) worker call, so a stop that arrived while the
            # snapshot was being taken still halts before we pay for a model step.
            if self.should_stop is not None and self.should_stop():
                return self._finalize_cancelled(task, attempts, plan, thread_id)
            agent_result = self._run_worker(worker, prompt)
            answer = agent_result.answer

            # Executable evidence is ground truth: when a verifier is present it
            # decides success, and the Manager is consulted only for feedback on a
            # failing attempt. Otherwise the Manager's approval is the gate. This
            # stops a strict reviewer from vetoing — and reverting — verified-correct
            # work just because it judged the narration rather than the artifact.
            verified, vout, abstained = self._verify()
            # A verifier that ABSTAINED (e.g. spec-test generation produced no tests) is NOT
            # authoritative — treat this attempt as if there were no verifier, so the Manager review
            # and the coverage checklist still run instead of accepting on an empty non-block.
            verifier_active = self.verifier is not None and not abstained
            # PROBE proxy (M18-5): in probe mode compute the cheap manager judgment even on a passing
            # attempt, so the logged (proxy, reward) pair is unbiased; reused below → no extra call.
            probe_proxy: bool | None = None
            proxy_fb = ""
            if self.probe_log is not None and self.manager is not None:
                probe_proxy, proxy_fb = self._review(task, answer, context)
            if verifier_active:
                ok = verified
                if verified:
                    approved, fb = True, ""
                elif probe_proxy is not None:
                    approved, fb = probe_proxy, proxy_fb
                else:
                    approved, fb = self._review(task, answer, context)
            else:
                approved, fb = self._review(task, answer, context)
                ok = approved
            # Record the paired observation for PROBE: arm = which worker ran, proxy = the cheap
            # manager verdict, reward = the verified outcome (only with a real verifier + manager).
            if self.probe_log is not None and verifier_active and probe_proxy is not None:
                arm = "escalate" if worker is self.escalate_worker else "worker"
                self.probe_log.record(
                    arm=arm, proxy=1.0 if probe_proxy else 0.0, reward=1.0 if verified else 0.0
                )

            # Completion contract (Hermes): a declared, machine-checkable AND gate. Even a
            # verified/approved attempt fails if the contract isn't met — and the unmet
            # clauses are fed back so the next attempt fixes exactly what's missing. Catches
            # the model narrating success it didn't achieve.
            if ok and self.contract is not None and self.contract:
                contract_result = self.contract.evaluate(answer)
                if not contract_result.satisfied:
                    ok = False
                    detail = "Completion contract not met:\n" + "\n".join(
                        f"- {reason}" for reason in contract_result.failures
                    )
                    fb = f"{fb}\n\n{detail}" if fb else detail

            # Requirement-coverage gate (opt-in): grade the answer against the extracted
            # requirements; unmet ones fail the attempt and are fed back for a targeted retry —
            # the model must fix exactly the constraints it dropped. Complements the contract
            # (artifacts) with coverage; degrades to no-misses on any grader error.
            # Skipped when an executable verifier is present: the tests are stricter ground truth and
            # already passed here, so an extra LLM coverage grade is a wasted (slow) model call on the
            # happy path. The checklist is a proxy verifier for when you have no tests, not a second
            # opinion on top of them. (Requirements are still injected up front, so the worker targets
            # them from attempt 1 regardless.)
            if ok and self.checklist is not None and requirements and not verifier_active:
                misses = self.checklist.grade(task, answer, requirements)
                if misses:
                    ok = False
                    detail = "Requirements not covered:\n" + "\n".join(f"- {m}" for m in misses)
                    fb = f"{fb}\n\n{detail}" if fb else detail

            # Independent strong verification (opt-in), gated to HARD turns only: a turn that
            # needed a retry (index > 1) proved hard, so a stronger independent judge grading the
            # result pays off — without the cost of verifying every easy pass or the
            # self-enhancement bias of a model checking itself.
            if ok and self.strong_verifier is not None and index > 1:
                passed, score = self.strong_verifier.verify(task, answer)
                if not passed:
                    ok = False
                    detail = (
                        f"Independent verification scored this {score:.0%} (below the bar) — the "
                        "result is likely wrong or incomplete. Reconsider and fix it."
                    )
                    fb = f"{fb}\n\n{detail}" if fb else detail

            attempt = Attempt(index, answer, approved, verified, False, ok, fb, vout)
            self._emit(_ev_result(index, ok, detail=(fb or vout)[:200]))
            # Diff-gate (nanobot "Dream"): certify what the attempt *actually* changed from the
            # real workspace snapshot, BEFORE any revert — the machine truth, not the model's claim.
            diff_productive: bool | None = None
            diff_summary: str | None = None
            if snapshot is not None and self.guard is not None:
                from chimera.evolution.diff_gate import diff_snapshots, unified_diffs

                after = self.guard.snapshot()  # one capture feeds both the summary and the per-file diffs
                pdiff = diff_snapshots(snapshot, after)
                diff_productive = pdiff.is_productive
                diff_summary = pdiff.audit_summary()
                attempt.diff_summary = diff_summary or ""
                attempt.diffs = unified_diffs(snapshot, after)  # real diffs, BEFORE any revert below
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
                    diff_productive=diff_productive,
                    diff_summary=diff_summary,
                )

            if ok:
                _log.debug("task succeeded on attempt %d", index)
                run_tainted = self.taint.run_tainted() if self.taint is not None else False
                # Human-in-the-loop interrupt: a result produced under untrusted influence is
                # not auto-accepted. Persist it and pause for sign-off (approve -> finalize,
                # deny -> drop). The safety valve for the lethal trifecta.
                if run_tainted and self.pause_on_taint:
                    self._save_checkpoint(
                        thread_id, task, index, feedback, plan, attempts,
                        awaiting_approval=True, paused_answer=answer, was_tainted=True,
                        # Persist the diff-gate verdict (M19-A2): a hollow success (empty diff) must
                        # STILL be blocked from minting a skill/memory when it's approved on resume —
                        # otherwise the HITL path silently bypasses the anti-hollow-learning gate.
                        productive=diff_productive,
                    )
                    self._emit(_ev_status(f"paused for approval — tainted run (thread {thread_id})"))
                    return AutonomousResult(
                        answer=answer, success=False, attempts=attempts, plan=plan, paused=True
                    )
                return self._finalize_success(
                    task, answer, attempts, prior_successes, plan, thread_id,
                    tainted=run_tainted, productive=diff_productive,
                )

            # Always surface the concrete verification output (the failing test/assert) on the
            # retry — it is the single most actionable signal for fixing the exact defect — ALONGSIDE
            # any manager feedback, rather than letting the manager's prose shadow it.
            # Only frame vout as "Verification failed" when the verifier was actually authoritative;
            # an abstention ("no runnable tests") is not a failure and must not read as one.
            _verify_fb = f"Verification failed:\n{vout}" if (vout and verifier_active) else ""
            feedback = "\n\n".join(p for p in (fb, _verify_fb) if p) or (
                "The attempt did not pass verification."
            )
            # Step-level failure attribution (SkillAdaptor): if a tool step errored,
            # point the retry at the FIRST faulty step instead of letting one early
            # error diffuse across the whole next attempt.
            hint = self._fault_hint(agent_result)
            if hint:
                feedback = f"{feedback}\n\n{hint}" if feedback else hint

            # Trace anti-patterns (TraceProbe): cheap, auditable process smells on a failed attempt —
            # a search-loop (kept exploring without acting) or a verification-skip (edited without
            # checking). Advisory retry coaching only; the verifier above already decided the outcome.
            probe_hint = anti_pattern_hint(
                events_from_transcript([m for m in agent_result.transcript if isinstance(m, dict)])
            )
            if probe_hint:
                feedback = f"{feedback}\n\n{probe_hint}" if feedback else probe_hint

            # Progress ledger (Magentic-One inner loop): a structured self-check turns the
            # generic "it failed" into a concrete instruction for the next attempt — what
            # lifts a weak model that would otherwise re-try the same dead end. Advisory:
            # the verifier already decided this attempt failed, so we use only next_focus
            # (and progressing, which feeds stagnation below), never the ledger's 'complete'.
            if self.progress_ledger is not None:
                assessment = self.progress_ledger.assess(
                    task, answer, feedback, attempt=index, max_attempts=self.config.max_attempts
                )
                if assessment.next_focus:
                    feedback = f"{feedback}\n\nNext, focus on: {assessment.next_focus}"
                if not assessment.progressing and self.stagnation is not None:
                    # An explicit "not progressing" is a first-class stall signal for the
                    # anti-stagnation detector, on top of the failure-signature heuristic.
                    self.stagnation.record_signature("progress-ledger: not progressing")

            # Anti-stagnation (crowding-score analog, arXiv 2606.29717): when successive
            # attempts keep failing the *same* way, refining is a local optimum — fold in a
            # pivot instruction so the next attempt tries a fundamentally different approach.
            # Advisory only; the escalated worker still supplies the stronger model.
            if self.stagnation is not None:
                self.stagnation.record_signature(hint or vout or feedback)
                if self.stagnation.assess().stagnant:
                    if task_ledger is not None and self.planner is not None:
                        # Dual-ledger re-plan: record WHY it's stuck, then rebuild the plan with
                        # that accumulated cause so the retry is fundamentally different — not the
                        # same plan reworded. Strictly stronger than the advisory pivot.
                        task_ledger.add_guess((hint or vout or feedback)[:200])
                        task_ledger.note_replan()
                        plan = self.planner.plan(
                            plan_task, context="\n\n".join(p for p in (context, task_ledger.context()) if p)
                        )
                        feedback = f"{feedback}\n\nRe-planned after repeated failure. {task_ledger.summary()}"
                        self._emit(_ev_status(f"re-planned after stall {task_ledger.summary()}"))
                        _log.debug("attempt %d: stall -> dual-ledger re-plan", index)
                    else:
                        _log.debug("attempt %d: stagnation detected; injecting pivot advice", index)
                        feedback = f"{feedback}\n\n{self.stagnation.advice()}"

            # Durable checkpoint: this attempt failed, so persist the state to resume from the
            # NEXT attempt if the process dies. (A successful attempt returns above and clears
            # the thread, so only mid-run, still-failing state is ever checkpointed.)
            # Persist taint (Zombie Agents): a fresh process resumes with an EMPTY ledger, so a
            # later attempt succeeding off residual tainted workspace state would finalize as
            # 'clean' — bypassing the outbound-strip, tainted-provenance and pause-on-taint gates.
            self._save_checkpoint(
                thread_id, task, index + 1, feedback, plan, attempts,
                was_tainted=self.taint.run_tainted() if self.taint is not None else False,
            )

        # The run ultimately failed: if this failure pattern recurs, distill an advisory
        # anti-pattern card so future attempts are warned. Guarded — the capability is
        # optional, so an evolver that only learns from successes is left untouched.
        if self.auto_evolver is not None:
            evolve_failure = getattr(self.auto_evolver, "maybe_evolve_failure", None)
            if callable(evolve_failure):
                run_tainted = self.taint.run_tainted() if self.taint is not None else False
                evolve_failure(task, feedback, prior_failures, tainted=run_tainted)

        self._record_card_outcome(False)
        self._clear_checkpoint(thread_id)  # exhausted the budget — a terminal state, not resumable
        last = attempts[-1].answer if attempts else ""
        self._emit(_ev_final(False, last))
        result = AutonomousResult(answer=last, success=False, attempts=attempts, plan=plan)
        self._persist_receipt(result, task)
        return result

    def _finalize_cancelled(
        self,
        task: str,
        attempts: list[Attempt],
        plan: Plan | None,
        thread_id: str | None,
    ) -> AutonomousResult:
        """Cooperative stop: the caller asked to cancel between attempts. Return a well-formed result
        (``success=False``, ``stopped_reason="cancelled"``) carrying the attempts completed so far.

        Deliberately NOT treated as a genuine failure: a user cancellation is not evidence the approach
        was wrong, so it does NOT distill an anti-pattern card or credit a card failure (unlike the
        budget-exhausted return above). The checkpoint is cleared — a user-cancelled run is terminal,
        not resumable — and a receipt is persisted, mirroring the exhaustion path's construction.
        """
        self._emit(_ev_status("cancelled"))
        self._clear_checkpoint(thread_id)
        last = attempts[-1].answer if attempts else ""
        self._emit(_ev_final(False, last))
        result = AutonomousResult(
            answer=last, success=False, attempts=attempts, plan=plan, stopped_reason="cancelled"
        )
        self._persist_receipt(result, task)
        return result

    def _finalize_success(
        self,
        task: str,
        answer: str,
        attempts: list[Attempt],
        prior_successes: int,
        plan: Plan | None,
        thread_id: str | None,
        *,
        tainted: bool,
        productive: bool | None = None,
    ) -> AutonomousResult:
        """Commit a successful result: remember it, evolve a skill, clear the thread, return."""
        # M15-A3: on a tainted run only, strip any chat-template/control tokens the model may have
        # echoed from untrusted content — outbound leak defense. Gated on ``tainted`` so a clean run
        # that legitimately discusses such tokens (a coding answer) is never mangled.
        if tainted:
            from chimera.governance.sanitize import strip_leaked_control_tokens

            answer = strip_leaked_control_tokens(answer)
        # Diff-gate the LEARNING (nanobot "Dream" / M19-A2): a "hollow success" — the verifier
        # passed but the real workspace snapshot shows an EMPTY diff — must not mint a skill or a
        # memory fact, or the flywheel learns from work that never happened. ``productive is False``
        # fires ONLY when a guard was present AND the diff was empty; ``None`` (no workspace, e.g. a
        # Q&A answer with nothing to diff) never blocks, so legitimate no-artifact tasks still learn.
        learn = productive is not False
        # Anti-poisoning provenance (Zombie Agents): artifacts from a tainted run stay marked
        # even after human approval — approval sanctions the action, not the content's trust.
        if learn:
            self._remember_success(task, answer, tainted=tainted)
        if learn and self.auto_evolver is not None:
            self.auto_evolver.maybe_evolve(task, answer, prior_successes, tainted=tainted)
            # M15-B4: if the run FAILED before it passed, distill the verified failed→passed
            # correction into an anti-pattern card — the eval, not a human, supplies the signal.
            last_failed = next((a for a in reversed(attempts) if not a.success), None)
            if last_failed is not None and last_failed.answer.strip():
                self.auto_evolver.maybe_distill_correction(
                    task, last_failed.answer, answer, tainted=tainted
                )
        # Diff-gate the card telemetry too (M19-A2): a hollow success (verifier passed, empty diff)
        # must not raise a retrieved card's win rate — that rate is the measured promote/demote
        # signal, and crediting a success for work that never happened bypasses the gate the same
        # way minting a skill would. ``learn is False`` ⇒ neutral (no use, no success credit).
        if learn:
            self._record_card_outcome(True)
        self._clear_checkpoint(thread_id)
        self._emit(_ev_final(True, answer))
        result = AutonomousResult(answer=answer, success=True, attempts=attempts, plan=plan)
        self._persist_receipt(result, task)
        return result

    def _persist_receipt(self, result: AutonomousResult, task: str) -> None:
        """Append a run receipt recording how this finished run PROVED its work (read-only evidence).

        Best-effort: persisting a receipt must NEVER break or fail a run, so any error (disk,
        serialization) is swallowed with a debug log. Reads the verify command off the verifier when
        it exposes one (``CommandVerifier.command``); ``None`` for a run with no executable verifier.
        """
        if self.run_log is None:
            return
        try:
            from chimera.api.runs import append_run, build_receipt

            verify_command = getattr(self.verifier, "command", None)
            receipt = build_receipt(
                result, task, verify_command, datetime.now(UTC).isoformat()
            )
            append_run(self.run_log, receipt)
        except Exception as exc:  # noqa: BLE001 — receipt persistence is best-effort, never fatal
            _log.debug("run receipt skipped: %s", exc)

    def _save_checkpoint(
        self,
        thread_id: str | None,
        task: str,
        next_index: int,
        feedback: str,
        plan: Plan | None,
        attempts: list[Attempt],
        **extra: Any,
    ) -> None:
        """Persist resumable loop state for ``thread_id`` (no-op without a checkpointer/thread)."""
        if self.checkpointer is None or not thread_id:
            return
        state: dict[str, Any] = {
            "task": task,
            "next_index": next_index,
            "feedback": feedback,
            "plan_steps": plan.steps if plan is not None else None,
            "plan_raw": plan.raw if plan is not None else "",
            "attempts": [asdict(a) for a in attempts],
            **extra,
        }
        self.checkpointer.save(thread_id, state)

    def _clear_checkpoint(self, thread_id: str | None) -> None:
        if self.checkpointer is not None and thread_id:
            self.checkpointer.delete(thread_id)

    def _record_card_outcome(self, success: bool) -> None:
        """Credit the run's outcome to the injected skill cards (per-skill telemetry)."""
        recorder = getattr(self.cards, "record_outcome", None)
        if callable(recorder):
            recorder(success)

    def _recall_lessons(self, task: str) -> str:
        if self.experience is None:
            return ""
        return format_lessons(self.experience.relevant(task))

    def _recall_facts(self, task: str, *, k: int = 5) -> str:
        """Read back relevant long-term memory facts for this task (M19-A3).

        Duck-typed on ``memory.search`` so any memory with a search method works; a store without
        one simply yields nothing. Mirrors ``MemoryManager.profile``'s provenance surfacing — a
        tainted fact is labelled inline so the model weighs it less, never as verified instruction.
        Advisory only: recall never raises (degrades to empty), and verify-or-revert still decides.
        """
        search = getattr(self.memory, "search", None)
        if not callable(search):
            return ""
        try:
            hits = search(task, k=k)
        except Exception as exc:  # noqa: BLE001 — recall is advisory, never fail the run
            _log.debug("memory readback failed: %s", exc)
            return ""
        lines = [
            f"- {getattr(item, 'content', '')}"
            + (
                " [unverified: learned from untrusted content]"
                if getattr(item, "provenance", "clean") == "tainted"
                else ""
            )
            for item in (hits or [])
            if str(getattr(item, "content", "")).strip()
        ]
        if not lines:
            return ""
        return "Relevant prior facts (advisory):\n" + "\n".join(lines)

    def _count_prior_successes(self, task: str) -> int:
        if self.experience is None:
            return 0
        return sum(1 for exp in self.experience.relevant(task, k=25) if exp.outcome == "success")

    def _count_prior_failures(self, task: str) -> int:
        if self.experience is None:
            return 0
        return sum(1 for exp in self.experience.relevant(task, k=25) if exp.outcome == "failure")

    def _remember_success(self, task: str, answer: str, *, tainted: bool = False) -> None:
        """On a verified success, curate one deduped long-term memory fact.

        Only verified successes reach here (the verify-or-revert gate), so failed
        or unverified work is never memorised. The MemoryManager dedups by key, so
        re-solving the same task UPDATEs the entry rather than bloating memory.
        A tainted run's fact carries that provenance into the store.
        """
        if self.memory is None:
            return
        snippet = next((line.strip() for line in answer.splitlines() if line.strip()), "")[:160]
        fact = f"Accomplished: {task}" + (f" — {snippet}" if snippet else "")
        self.memory.remember(
            fact, key=f"solve:{_slug(task)}", provenance="tainted" if tainted else "clean"
        )

    def _review(self, task: str, answer: str, context: str) -> tuple[bool, str]:
        if self.manager is None or not self.config.use_manager:
            return True, ""
        review = self.manager.review(task, answer, context=context)
        return review.approved, review.feedback

    def _verify(self) -> tuple[bool, str, bool]:
        """Returns (passed, output, abstained). ``abstained`` = the verifier had nothing runnable to
        check, so the caller must fall back to its other gates instead of accepting on it."""
        if self.verifier is None:
            return True, "", True
        result = self.verifier.verify()
        return result.passed, result.output, result.abstained

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
