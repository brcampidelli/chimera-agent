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

import copy
import inspect
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from chimera.core.context_budget import RunState
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
from chimera.core.events import tool as _ev_tool
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
from chimera.orchestration.budget import SpendBudget, SpendCappedBackend, SpendExceeded
from chimera.telemetry import get_logger

_log = get_logger("core.autonomous")

#: Per side of a remembered fact — the task's opening line and the answer's. A fact is
#: recalled into a later run's context, so its size is a recurring cost, not a one-off.
_FACT_CHARS = 160

# --diff-feedback wording and bound. Fixed in bench/retry_lift/PREREGISTRATION.md BEFORE the run that
# measures it, because framing and truncation are the most temptingly tunable knobs in the whole
# experiment — "it didn't work, let me reword the prompt" is how a null becomes a fabricated win.
# Changing either is an amendment, committed before the run that uses it.
_DIFF_FEEDBACK_HEADER = "You already tried this and it FAILED verification. This exact change was reverted:"
_DIFF_FEEDBACK_FOOTER = (
    "Do not re-derive this edit. Diagnose why it was wrong, then take a different approach."
)
_DIFF_FEEDBACK_MAX_CHARS = 2000


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


def _side_effects(steplog: Any) -> list[str]:
    """Which out-of-checkout side-effect tools this attempt actually called, in first-call order.

    The verdict code holds the step log — it is already read here for drift — while the Manager holds
    no tool registry at all. That asymmetry is useful: the loop can see what the run *did* without
    handing the evaluator anything it could act with.

    This is recorded and nothing more. It matters because the diff gate answers "did the workspace
    change?", and for a run that sent mail or posted a webhook that is the wrong question: an empty
    diff there does not mean nothing happened, and a receipt that only carries the diff invites the
    reader to conclude it did. Whether the *verdict* should consult this is a real design question
    about retry safety, deliberately not decided here.
    """
    from chimera.governance.ledger import SIDE_EFFECT_TOOLS

    if steplog is None:
        return []
    seen: list[str] = []
    for step in getattr(steplog, "steps", ()):
        for call in getattr(step, "tools", ()):
            name = getattr(call, "name", "")
            # Only calls that actually ran: a tool the ledger blocked, or one that errored before
            # reaching the network, did not produce the effect this list exists to warn about.
            if name in SIDE_EFFECT_TOOLS and getattr(call, "ok", False) and name not in seen:
                seen.append(name)
    return seen


def _without_verifier_artifacts(snapshot: Any) -> Any:
    """A copy of ``snapshot`` with files the VERIFIER wrote removed.

    The diff gate's whole claim is "machine truth, never self-report": it asks whether the workspace
    actually changed. A file the verifier created is not the agent's work, and counting it turns the
    gate into a rubber stamp for exactly the runs it was built to catch.

    A copy rather than a mutation, because the caller keeps the real snapshot for
    ``--keep-workspace``: the file is still on disk for anyone who wants to read the generated tests.
    """
    from dataclasses import replace

    from chimera.core.spec_test import _TEST_FILE

    if _TEST_FILE not in snapshot.present:
        return snapshot
    return replace(
        snapshot,
        present=snapshot.present - {_TEST_FILE},
        files={k: v for k, v in snapshot.files.items() if k != _TEST_FILE},
    )


def _rendered_diff(diffs: list[FileDiff]) -> str:
    """Render per-file diffs as one bounded patch body for retry feedback.

    Truncation is explicit rather than silent: a clipped body says so, so the model does not read a
    half-diff as the whole change it made.
    """
    body = "\n".join(f"--- {d.path}\n{d.patch}".rstrip() for d in diffs if d.patch)
    if len(body) > _DIFF_FEEDBACK_MAX_CHARS:
        body = body[:_DIFF_FEEDBACK_MAX_CHARS] + "\n... [diff truncated]"
    return body


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

    All three keyword arguments are optional and structural: ``on_edit`` receives ``(path, patch)``
    for each file the worker changes mid-run (the live per-edit diff), ``on_tool`` fires once per
    tool call with its outcome, and ``should_stop`` is polled by the worker so a cancel takes effect
    inside an attempt rather than at the end of it. The loop passes each one only to workers whose
    ``run`` actually accepts it (checked by signature), so a Worker that supports none is never
    broken — an external agent driven over ACP accepts none of the three, and the loop's own
    between-attempt cancel still applies to it.
    """

    def run(
        self,
        task: str,
        *,
        on_edit: Callable[[str, str], None] | None = None,
        on_tool: Callable[[Any], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> AgentResult: ...


class SupportsRemember(Protocol):
    """Anything that can store a durable fact (a MemoryManager)."""

    def remember(
        self,
        content: str,
        *,
        key: str | None = None,
        provenance: str = "clean",
        project: str | None = None,
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
    #: Onde o trabalho revertido foi guardado inteiro, ou "" quando nao houve reversao.
    #:
    #: `diffs` acima existe e nao serve para recuperar nada: o recibo corta cada patch em 4.000
    #: caracteres. Medido numa corrida real — um `index.html` de 581 linhas virou um souvenir
    #: truncado, e era a UNICA copia. A reversao estava certa (o verificador reprovou), mas a
    #: tentativa que a produziu tinha custado 374 mil tokens e o nucleo dela passava todos os
    #: testes; o que se perdeu nao foi trabalho ruim, foi trabalho quase pronto.
    discarded_at: str = ""
    #: What actually decided this attempt: "verifier" (a command exited 0), "diff+manager" (files
    #: changed and an LLM approved the answer), "manager" (an LLM approved prose, nothing else
    #: checked), or "none". A receipt that says "success" without saying on whose authority invites
    #: the reader to assume the strongest one.
    evidence: str = "none"
    #: Did the attempt change the workspace? ``None`` means *could not be measured* (no snapshot,
    #: no guard) — deliberately NOT the same as ``False`` ("measured, and nothing changed"). The
    #: third state is the whole point: collapsing "we could not tell" into either boolean is how a
    #: gate that reads honestly today starts lying later.
    diff_productive: bool | None = None
    #: List-rate cost of the worker's model calls for THIS attempt, or ``None`` when the model's
    #: price is unknown. Never 0.0 as a stand-in: "we do not know what this cost" and "this cost
    #: nothing" are different facts, and a receipt that collapses them makes the cheap-looking
    #: configuration the one nobody can check.
    usd: float | None = None
    #: The share of ``usd`` that was NOT the worker — planner, manager, checklist, strong-verify.
    #: Reported separately because it is the number a model-per-role profile actually moves: the
    #: profiles differ mostly in who plans and who reviews, so a receipt that only shows the total
    #: hides the thing the choice was about.
    overhead_usd: float | None = None
    #: Prompt/completion tokens for this attempt. Always known when the provider reported them, so
    #: they remain comparable across runs even where the price is not.
    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: Every tool this attempt called, in order.
    #:
    #: ``AgentResult`` has carried this all along and it died at this boundary: the loop kept the
    #: attempt's cost and dropped what the attempt *did*. That made a whole class of question
    #: unanswerable from a finished run — how many edits a task took, whether a tool is ever
    #: reached, which tool a regression follows — and `bench/edit_tools/` is the first measurement
    #: to need it. Propagated rather than counted, because a count answers one question and the
    #: sequence answers the ones nobody has asked yet.
    tool_names: list[str] = field(default_factory=list)
    #: The model slug that actually answered this attempt (the EDITOR's model under role routing).
    model: str = ""
    #: The id this attempt's trace line was written under, or "" when no trace was written.
    #:
    #: One attempt IS one agent run, which is one trace line — so this is the exact key that joins a
    #: verified outcome to the context it was carrying. Without it the two files can only be joined
    #: on a truncated task, which collides precisely where the join matters: a bench repeats one
    #: task, and a run with three attempts writes three indistinguishable lines.
    run_id: str = ""
    #: Names of out-of-checkout side-effect tools this attempt actually called (send_email,
    #: http_post, …), read off the step log. Recorded, never acted on: an empty diff means
    #: something very different once a run has already sent mail, and a reader of the receipt
    #: should be able to see that without re-deriving it from a transcript.
    side_effects: list[str] = field(default_factory=list)


@dataclass
class AutonomousResult:
    answer: str
    success: bool
    attempts: list[Attempt] = field(default_factory=list)
    plan: Plan | None = None
    paused: bool = False  # interrupted for human approval (see AutonomousAgent.pause_on_taint)
    stopped_reason: str = ""  # why the loop ended early; "cancelled" on a cooperative stop, else ""


#: Char budget for the diff bodies handed to the reviewer. Enough to show what a small edit did
#: without turning every review into a second copy of the repository.
_JUDGE_DIFF_CHARS = 4000


#: How much reverted work is kept, and it is a bound on BOTH axes because either alone can be
#: defeated. A count alone lets one enormous diff fill a disk; a size alone lets thousands of tiny
#: ones fill an inode table. Measured on a real install: 7 files in 18 hours at ~2 KB each, one per
#: reverted attempt, growing forever — small today and unbounded, which is the shape nobody notices
#: until it is a problem.
_DISCARDED_MAX_FILES = 500
_DISCARDED_MAX_BYTES = 50 * 1024 * 1024


def _podar_descartados(pasta: Path) -> int:
    """Keep the newest reverted diffs, drop the oldest, and return how many were dropped.

    Oldest first, deliberately: recovering work you were just doing is the whole point of the
    folder, and a reverted attempt from last month is not something anyone comes back for. The path
    stays in the old receipt after its file goes — a dangling pointer to work gone for months is a
    smaller cost than a folder with no ceiling, and it is the only one of the two anyone can see.

    Best-effort like the write it follows: failing to prune must never be why a revert fails.
    """
    try:
        arquivos = sorted(
            (f for f in pasta.glob("*.diff") if f.is_file()), key=lambda f: f.stat().st_mtime
        )
        total = sum(f.stat().st_size for f in arquivos)
    except OSError as exc:  # pragma: no cover - defensive
        _log.debug("nao consegui listar os descartados para poda: %s", exc)
        return 0
    removidos = 0
    for f in arquivos:
        if len(arquivos) - removidos <= _DISCARDED_MAX_FILES and total <= _DISCARDED_MAX_BYTES:
            break
        try:
            tamanho = f.stat().st_size
            f.unlink()
            total -= tamanho
            removidos += 1
        except OSError as exc:  # pragma: no cover - defensive
            _log.debug("nao consegui remover %s: %s", f, exc)
    if removidos:
        _log.info("descartados: %d arquivo(s) antigo(s) removido(s)", removidos)
    return removidos


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
        pause_always: bool = False,
        repo_map: bool = False,
        checklist: RequirementChecklist | None = None,
        given_requirements: list[Any] | None = None,
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
        diff_feedback: bool = False,
        keep_workspace: bool = False,
        require_diff: bool = False,
        experience: ExperienceBuffer | None = None,
        trajectories: TrajectoryCollector | None = None,
        memory: SupportsRemember | None = None,
        auto_evolver: SupportsAutoEvolve | None = None,
        cards: SupportsCardContext | None = None,
        spine_workspace: Path | None = None,
        on_event: EventSink | None = None,
        checkpointer: RunCheckpointer | None = None,
        run_log: Path | None = None,
        run_profile: str | None = None,
        # Where the verify command came from, so a receipt can never be read as a choice nobody
        # made. Carried on the agent rather than derived from the verifier: the verifier only knows
        # the string it was handed, and whether a person typed it or this app read it off
        # `pyproject.toml` is a fact about the request — exactly the fact a receipt must not lose.
        verify_source: str = "user",
        # Who chose the profile. Same discipline as `verify_source`: a receipt must never be
        # readable as a decision nobody made.
        profile_source: str = "user",
        meter: Any | None = None,
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
        self.pause_always = pause_always
        self.repo_map = repo_map
        self.checklist = checklist
        #: Requirements a PERSON already read and edited, in place of extracting them here.
        #: The whole value of a checklist a human can correct is that the list the run is
        #: graded against is the one they approved — re-extracting would quietly discard
        #: every edit and grade against a list nobody saw, which is worse than no checklist
        #: at all: it looks reviewed. An empty list is a real answer ("nothing to gate on")
        #: and is distinct from None ("nobody was asked").
        self.given_requirements = given_requirements
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
        self.diff_feedback = diff_feedback
        self.keep_workspace = keep_workspace
        self.require_diff = require_diff
        self.experience = experience
        self.trajectories = trajectories
        self.memory = memory
        self.auto_evolver = auto_evolver
        self.cards = cards
        self.spine_workspace = spine_workspace
        self.on_event = on_event
        self.checkpointer = checkpointer
        self.run_log = run_log
        #: An opaque label for the configuration this run used, recorded on the receipt so runs can
        #: be grouped later. The loop never interprets it — it does not know what a "profile" is,
        #: and should not: the moment the core starts branching on this label it stops being a
        #: record of what happened and becomes another thing that can be wrong.
        self.run_profile = run_profile
        self.verify_source = verify_source
        self.profile_source = profile_source
        #: Records what the NON-worker parts cost — planner, manager, checklist, strong-verify.
        #: Those call ``backend.complete`` directly and were never priced anywhere, which made a
        #: model-per-role profile cheapest-looking exactly where it spends most. Optional: without
        #: one, receipts carry the worker-only cost they always did.
        self.meter = meter
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

    def _emit_tool(self, activity: Any) -> None:
        """Forward one tool call from the worker as a ``tool`` event through the sink.

        This is what makes a run legible while it is running. Without it the only frames between
        "attempt 1/3" and the verdict are the edits, so a run that spends four steps reading and
        searching before it writes anything looks, from outside, like a run that is doing nothing.

        Defensive about the payload: ``on_tool`` is a Protocol seam and a stubbed worker in a test
        may hand back something that is only shaped like a ``ToolActivity``.
        """
        if self.on_event is None:
            return
        self._emit(
            _ev_tool(
                str(getattr(activity, "name", "")),
                getattr(activity, "arguments", None) or {},
                bool(getattr(activity, "ok", False)),
                str(getattr(activity, "observation", "") or ""),
            )
        )

    def _run_worker(
        self, worker: Worker, prompt: str, *, spend: SpendBudget | None = None
    ) -> AgentResult:
        """Run the worker, passing the live callbacks it actually supports and a sink exists for.

        Backward-compatible in both directions: with no event sink the call is the bare
        ``worker.run(prompt)`` it always was, and a worker whose ``run`` accepts neither callback
        (any Worker implementation that predates them) is called the same way. The support check
        reads the real signature; a TypeError fallback covers a wrapper whose signature lied.

        ``should_stop`` travels the same way, and it is NOT an event: it is checked even with no
        sink, because a cancel must work in a headless run too. Passing it down is what makes Stop
        take effect within a step instead of at the end of the attempt — the loop's own checks are
        between attempts, so without this a stop request waits out every remaining model call and
        every tool they trigger.
        """
        try:
            accepted = set(inspect.signature(worker.run).parameters)
        except (TypeError, ValueError):  # unintrospectable callable (C impl / odd wrapper)
            accepted = set()
        kwargs: dict[str, Any] = {}
        if self.should_stop is not None and "should_stop" in accepted:
            kwargs["should_stop"] = self.should_stop
        # NOT an event either, and passed for the same reason `should_stop` is: without it every
        # attempt builds its own ceiling from the same `max_usd` and the run's real limit becomes
        # `max_usd * max_attempts`. A worker that predates the parameter is called without it and
        # keeps the old per-attempt behaviour rather than failing.
        if spend is not None and "spend" in accepted:
            kwargs["spend"] = spend
        if self.on_event is not None:
            if "on_edit" in accepted:
                kwargs["on_edit"] = self._emit_edit
            if "on_tool" in accepted:
                kwargs["on_tool"] = self._emit_tool
        if not kwargs:
            return worker.run(prompt)
        try:
            return worker.run(prompt, **kwargs)
        except TypeError:  # signature lied (e.g. **kwargs-only) — fall back to the plain call
            return worker.run(prompt)

    def _run_budget(self) -> SpendBudget | None:
        """One ceiling for this whole run, read off the worker that will spend the money.

        Duck-typed on the worker for the same reason `_seed_run_state` is: `Worker` is a protocol,
        and an implementation that carries no `config.max_usd` simply has no ceiling to share — it
        then behaves exactly as before, building its own per-call budget if it has one.
        """
        cap = getattr(getattr(self.worker, "config", None), "max_usd", None)
        return SpendBudget(cap) if cap else None

    def _capped_manager(self, spend: SpendBudget | None) -> Manager | None:
        """The reviewer, drawing on the run's money instead of on nothing.

        The reviewer is a model call per attempt and it sat outside every ceiling: a run given a
        cap still paid for reviews after reaching it. The ceiling goes around the BACKEND, once —
        the shape `SpendCappedBackend` was written for, and the reason it refuses rather than
        merely records.

        A COPY, never a wrapper installed on `self.manager`: this one is per-run today, and a
        mutation would make this run's spending follow a shared reviewer into the next one.

        `copy.copy` rather than `Manager(...)`, and the difference is not style. Rebuilding the
        class from its fields silently discards a subclass and every overridden `review` — the
        reviewer would keep its name and lose its behaviour, which is the kind of substitution
        nothing downstream can detect. A reviewer with no `backend` to wrap is returned untouched.
        """
        if spend is None or self.manager is None:
            return self.manager
        inner = getattr(self.manager, "backend", None)
        if inner is None:
            return self.manager
        capped = copy.copy(self.manager)
        capped.backend = SpendCappedBackend(inner, spend)
        return capped

    def _seed_run_state(self, worker: Worker, task: str, plan: Plan | None) -> None:
        """Tell the worker what it was asked and which plan it is on, in the fields that mean those.

        Not "the plan was missing". The plan already survived compaction, by accident: the worker is
        prompted with ``_compose`` output, which embeds ``Plan:\\n…``, and ``Agent.run`` copies its
        whole prompt into ``run_state.task`` when no caller set one. So the real defect is FORM —
        the plan was restored inside the field labelled *"The task you were given"*, along with the
        entire spine/repo-map/lessons context block that compaction had just paid to remove.

        That accident turns harmful on a re-plan. ``Agent.run`` freezes ``task`` on the first
        attempt, so it keeps the composed prompt of attempt 1 — plan A — verbatim, and
        ``as_message`` renders it FIRST, above the tail where the worker is executing plan B.
        Writing plan B into ``plan`` and leaving ``task`` alone would restore both and let the agent
        pick. Seeding the raw task here pre-empts the copy (``Agent.run`` only fills a task nobody
        set), which is what makes one plan the only plan.

        Here rather than in each caller because the plan is a local written at four points — given
        by the caller, self-planned, restored from a checkpoint, replaced by a stall re-plan — and
        no caller sees the last three. This is the one place downstream of all four, so it is also
        after the checkpoint restore *discards* the plan the run made moments earlier: seeded at the
        planning call instead, a resumed run would carry the plan it threw away.

        ``RunState.tasks`` is deliberately untouched. It is a task list *with status* — it exists so
        finished work is not redone — and bare steps assert that nothing is done. This loop counts
        attempts, not steps, so it has no completion to report and inventing one would be a lie in
        the field whose entire purpose is to be believed.

        Duck-typed for the same reason ``_run_worker`` introspects: ``Worker`` promises only
        ``run``. An agent driven over ACP has no ``run_state``, and a worker we cannot restore into
        is not a broken worker.
        """
        state: RunState | None = getattr(worker, "run_state", None)
        if state is None:
            return
        # Both assigned every attempt, and the escalate worker is a different Agent with its own
        # RunState that only ever runs attempts > 1 — the same attempts a re-plan lands on. Values
        # written once would be stale exactly where they are read.
        state.task = task
        state.plan = plan.as_text() if plan is not None else ""

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
            # The task biases the ranking: a file the task names by stem outranks one nothing
            # mentions, so the budget is spent nearest the work rather than on the graph's hubs.
            digest = build_repo_map(self.spine_workspace, task=task)
            if digest:
                repo_ctx = f"Repository map (file: top-level symbols):\n{digest}"
        # ACE playbook: accumulated, delta-curated strategy bullets, injected as advisory context
        # so the worker/planner reuse what has worked across runs (grow-and-refine, anti-collapse).
        playbook_ctx = self.playbook.render() if self.playbook is not None else ""
        # Requirement checklist (opt-in): extract the task's atomic requirements ONCE up front and
        # inject them into context, so the worker targets every requirement from the FIRST attempt
        # (not just discovers the dropped ones via a failed coverage grade on retry). Extraction is
        # task-level and stable, so it's done once here and reused by the coverage gate below.
        if self.given_requirements is not None:
            requirements = list(self.given_requirements)
        else:
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
        #: What the JUDGE is allowed to hold the work to — the agreed criteria, and nothing else.
        #:
        #: The worker's context and the reviewer's used to be the same string, and that turned
        #: everything advisory into something enforceable. Measured on a real run: a globally-scoped
        #: memory fact naming another project's path ("the Cafe Aurora test project lives in
        #: Desktop/teste-chimera/…") reached the reviewer, which then failed a run for not putting
        #: that path in a README nobody had asked to contain it. The attempt's own diff proves the
        #: file was written; verify-or-revert then deleted it, and with one attempt there was no
        #: retry. Correct work destroyed on a criterion nobody agreed to.
        #:
        #: `requirements_ctx` stays because it IS the agreement — a checklist somebody read and
        #: edited before the run. Everything else is material for the worker to USE: recalled facts,
        #: distilled lessons, skill cards, the playbook, the repository map, file bodies read before
        #: the work. Useful to think with; not a contract to be judged against.
        judge_context = requirements_ctx
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
        # For --keep-workspace: the post-edit snapshot of the LAST attempt, so an external grader
        # (SWE-bench, CI, a human) can judge the agent's final work even when Chimera's own verifier
        # rejected it and rolled the tree back. Between-attempt reverts still happen (each attempt stays
        # independent); only the final on-disk state is restored to this on a failed run.
        last_after = None
        # ONE ceiling for the whole run, not one per attempt. `Agent.run` builds a `SpendBudget`
        # from `AgentConfig.max_usd` and this loop calls `run` once per attempt, so a three-attempt
        # run used to get three separate ceilings — measured: a run asking for $0.000002 spent
        # $0.0129 and every attempt started again at zero. Same reasoning as `SpendCappedBackend`,
        # which already says it for fan-outs: the money is the RUN's, so the budget is the run's.
        spend = self._run_budget()
        # The reviewer draws on the same money. It is a model call the user pays for, made once per
        # attempt, and it was outside every ceiling — so a capped run still paid for reviews after
        # the cap. Wrapped, never mutated: the Manager is per-run here, but a wrapper installed on
        # a shared one would leak this run's spending into the next.
        manager = self._capped_manager(spend)
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
            # After the choice, so this lands on the worker that actually runs the attempt: the
            # escalate worker is a separate Agent with its own RunState. `task`, not `plan_task` —
            # the field documents itself as the request verbatim, and a resume rebinds `task` from
            # the checkpoint while `plan_task` was normalised before that and would be stale.
            self._seed_run_state(worker, task, plan)
            # Re-check right before the worker call, so a stop that arrived while the snapshot was
            # being taken still halts before we pay for a model step. The call is no longer
            # uninterruptible: `_run_worker` hands `should_stop` down, and a worker that accepts it
            # returns at its next step boundary.
            if self.should_stop is not None and self.should_stop():
                return self._finalize_cancelled(task, attempts, plan, thread_id)
            agent_result = self._run_worker(worker, prompt, spend=spend)
            # A worker that cut itself short produced a PARTIAL attempt, and verifying, reviewing and
            # scoring one is worse than useless: those are model calls the user has already asked us
            # not to make, spent to judge work that stopped halfway — and the failing verdict would
            # be recorded as evidence the task is hard.
            #
            # On `stopped_reason`, deliberately NOT on the flag. The flag says a stop was requested
            # at some point; only the reason says THIS attempt is incomplete. A worker that does not
            # accept `should_stop` runs to the end even after a stop arrives, and that attempt is
            # whole — discarding it would throw away a finished piece of work and, with
            # `max_attempts=1`, return a run with no attempts at all.
            if getattr(agent_result, "stopped_reason", "") == "cancelled":
                return self._finalize_cancelled(
                    task, attempts, plan, thread_id,
                    agent_result=agent_result, index=index, snapshot=snapshot,
                )
            # Same argument as the comment above, for the other reason a worker cuts itself short.
            # A worker that stopped because the money ran out has produced a partial attempt, and
            # verifying, reviewing and RETRYING it are exactly "model calls the user has already
            # asked us not to make" — measured: three attempts ran under a cap the first call had
            # already passed, and each one paid for a reviewer to criticise a worker that was only
            # saying it had hit the limit.
            if getattr(agent_result, "stopped_reason", "") in ("spend", "budget"):
                return self._finalize_capped(
                    task, attempts, plan, thread_id, agent_result, index
                )
            answer = agent_result.answer
            # Surface a degrading trajectory where a person will see it, not only in the trace. It
            # is advisory: the attempt is judged on its result as always, and the run continues.
            steplog = getattr(agent_result, "steplog", None)
            if steplog is not None and steplog.drift.drifting:
                self._emit(_ev_status(f"context drift — {steplog.drift.summary}"))

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

            # MOVED UP, ahead of the review. It used to be computed after every gate had already
            # voted, which left the reviewer judging the answer's PROSE with no way to know what
            # the attempt did to the disk. Nothing between here and the old position writes to the
            # workspace — the contract, the coverage checklist and the strong verifier all read
            # text — so the numbers are identical. They are simply available in time to be
            # evidence instead of only a description.
            # Diff-gate (nanobot "Dream"): certify what the attempt *actually* changed from the
            # real workspace snapshot, BEFORE any revert — the machine truth, not the model's claim.
            # Computed BEFORE the Attempt is finalized so --require-diff can act on it: the gate has to
            # be able to fail the attempt, not just describe it after the verdict is already sealed.
            diff_productive: bool | None = None
            diff_summary: str | None = None
            diffs: list[FileDiff] = []
            if snapshot is not None and self.guard is not None:
                from chimera.evolution.diff_gate import diff_snapshots, unified_diffs

                after = self.guard.snapshot()  # one capture feeds both the summary and the per-file diffs
                last_after = after  # remember for --keep-workspace (restored after the loop if it fails)
                # `--gen-tests` writes its test file INTO the workspace, and it does so between the
                # two snapshots (`_verify()` runs above). Left in, the verifier's own artifact counts
                # as the run's work: `is_productive` becomes True on every attempt, so `--require-diff`
                # — the gate that exists because SWE-bench run 1 returned 11/19 empty patches — can
                # never fire while tests are being generated. With `--diff-feedback` it is worse than
                # inert: the retry is handed the test file under "you already tried this and FAILED",
                # feedback about an edit the model did not make.
                after = _without_verifier_artifacts(after)
                pdiff = diff_snapshots(snapshot, after)
                diff_productive = pdiff.is_productive
                diff_summary = pdiff.audit_summary()
                diffs = unified_diffs(snapshot, after)  # real diffs, BEFORE any revert below

            # What the attempt actually did, handed to the reviewer as EVIDENCE.
            #
            # The reviewer receives `(task, answer, context)` and has never seen the diff, the
            # transcript or a single file — a fact tests/test_success_gate.py had already written
            # down without drawing the consequence. Measured on rc39: four runs, five attempts,
            # five rejections, every one for work the receipt's own `diff_summary` proves was done.
            # One verbatim, "the README.md must be physically created, not merely described as
            # created", on an attempt whose receipt reads `diff: +1 new (README.md)` and
            # `diff_productive: true`. Under verify-or-revert the file was then deleted, and the
            # failure was distilled into a permanent anti-pattern card — so the hallucination
            # outlived the run that produced it.
            #
            # This is evidence, NOT a criterion, and that distinction is the whole point of the
            # narrowing above: a recalled fact from another project can make something enforceable
            # that nobody agreed to, while a diff can only describe what the answer is a claim
            # ABOUT. It cannot add a requirement; it can only stop the reviewer being wrong about
            # whether the work happened.
            evidence_ctx = ""
            if diff_summary:
                bodies: list[str] = []
                budget = _JUDGE_DIFF_CHARS
                for file_diff in diffs:
                    if budget <= 0:
                        break
                    body = file_diff.patch[:budget]
                    budget -= len(body)
                    bodies.append("--- " + file_diff.path + "\n" + body)
                evidence_ctx = (
                    "<<what-this-attempt-changed-on-disk>>\n"
                    + diff_summary
                    + "\n"
                    + "\n".join(bodies)
                    + "\n<<end>>"
                )
            # No branch for "measured and nothing changed": `audit_summary()` already says
            # `diff: no productive change`, so that case arrives through the line above with the
            # distinction intact. A second branch for it would have been dead code — written, and
            # caught by the test that asserted the wrong sentence.
            attempt_judge_context = (
                (judge_context + "\n\n" + evidence_ctx).strip() if evidence_ctx else judge_context
            )
            # PROBE proxy (M18-5): in probe mode compute the cheap manager judgment even on a passing
            # attempt, so the logged (proxy, reward) pair is unbiased; reused below → no extra call.
            probe_proxy: bool | None = None
            proxy_fb = ""
            proxy_abstained = False
            if self.probe_log is not None and self.manager is not None:
                probe_proxy, proxy_fb, proxy_abstained = self._review(
                    task, answer, attempt_judge_context, manager=manager
                )
            # Whether a manager actually judged THIS attempt, as opposed to being configured. Only
            # a real judgment may be named as one in the receipt below.
            review_abstained = True
            if verifier_active:
                ok = verified
                if verified:
                    # Nobody was asked — the verifier already decided. `abstained` stays True
                    # because it means "no manager judged", and here none did.
                    approved, fb = True, ""
                elif probe_proxy is not None:
                    approved, fb, review_abstained = probe_proxy, proxy_fb, proxy_abstained
                else:
                    approved, fb, review_abstained = self._review(
                        task, answer, attempt_judge_context, manager=manager
                    )
            else:
                approved, fb, review_abstained = self._review(
                    task, answer, attempt_judge_context, manager=manager
                )
                # An abstaining reviewer is the only gate here and just declined to be one. Letting
                # `ok` ride on it would decide the attempt on a non-answer; the contract, coverage
                # and diff gates below still run and can still fail it.
                ok = True if review_abstained else approved
            # Record the paired observation for PROBE: arm = which worker ran, proxy = the cheap
            # manager verdict, reward = the verified outcome (only with a real verifier + manager).
            # An abstaining proxy is not a cheap verdict, it is no verdict; logging it as 0/1 would
            # put the reviewer's silence into the pair as if it were a judgment about the arm.
            if (
                self.probe_log is not None
                and verifier_active
                and probe_proxy is not None
                and not proxy_abstained
            ):
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
                # The same evidence the reviewer gets, for the same reason. This gate runs only
                # when there is no active verifier — which is exactly what an abstaining one
                # produces — so it inherits the decisive vote precisely when the tests could not
                # speak, and grading prose there deletes work the diff proves was done.
                misses = self.checklist.grade(task, answer, requirements, evidence=evidence_ctx)
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

            # --require-diff: for a code task, an answer that changed nothing is not a success, however
            # convincing its prose. Without this the diff-gate is a passive observer — with no verifier
            # `ok = approved`, and the Manager judges the answer TEXT, so a confident explanation of the
            # bug passes while the file is untouched (SWE-bench run 1: 11/19 empty patches). Fails the
            # attempt and feeds the reason back, so the retry is told to actually edit. Only when the
            # diff was genuinely measured — `None` means no workspace guard, i.e. we cannot know.
            # Two ways this gate fires. `--require-diff` is the explicit one. The second is the
            # important one: when NO verifier ran, the only thing standing between "the model wrote
            # a convincing paragraph" and "success" is a Manager that never sees the diff, the
            # transcript, or a single file — it judges the answer text alone. Combined with an
            # unchanged workspace, that is precisely the empty-patch failure measured above, and it
            # was reachable by default. An attempt that changed nothing and that nothing verified
            # is not a success; it is an explanation.
            unverified_and_unchanged = not verifier_active and diff_productive is False
            if diff_productive is False and (self.require_diff or unverified_and_unchanged):
                ok = False
                detail = (
                    "No file was changed. This task requires editing code: an explanation is not a "
                    "fix. Locate the responsible file and make the edit."
                )
                fb = f"{fb}\n\n{detail}" if fb else detail

            # A manager that was asked and answered nothing readable is, for the receipt, the same
            # as no manager: it did not approve anything. Naming it would be the fabrication-by-
            # omission that `_manager_ran` was written to stop, one step further along.
            manager_judged = self._manager_ran() and not review_abstained
            if verifier_active:
                evidence = "verifier"
            elif diff_productive:
                # "diff+manager" claims two authorities. With no manager configured the only real
                # one is the diff, and saying so is the difference between a receipt and a label.
                evidence = "diff+manager" if manager_judged else "diff"
            elif ok and manager_judged:
                evidence = "manager"
            else:
                # Includes the case that used to read "manager": approved by nobody, because nobody
                # was asked. `none` is what that is.
                evidence = "none"
            attempt = Attempt(index, answer, approved, verified, False, ok, fb, vout,
                              evidence=evidence)
            attempt.diff_summary = diff_summary or ""
            attempt.diffs = diffs
            attempt.diff_productive = diff_productive
            attempt.side_effects = _side_effects(steplog)
            # The key that joins this outcome to the trace line the same run just wrote. Read off
            # the worker's result, like  above and for the same reason: it is the worker that
            # knows, and re-deriving it here would be a second place for the two to disagree.
            attempt.run_id = str(getattr(agent_result, 'run_id', '') or '')
            # What the attempt DID, read off the same result as the id above and for the same
            # reason: the worker is what knows, and re-deriving it here would be a second place for
            # the two to disagree.
            attempt.tool_names = [str(n) for n in (getattr(agent_result, 'tool_names', None) or [])]
            # What this attempt charged. Read off the worker's own result rather than recomputed:
            # `usd` is None there whenever the model's price is unknown, and that None has to
            # survive all the way to the receipt for the "was it worth it?" view to stay honest.
            # Worker + overhead. `take()` RESETS, so attempt 2's review is attributed to attempt 2
            # rather than to a running total — and `add_usd` keeps `None` poisonous, because a
            # receipt that adds only the legs it happened to price reports the run as cheaper than
            # it was, always in the same direction.
            from chimera.orchestration.metering import add_usd

            over_usd, over_prompt, over_completion = (
                self.meter.take() if self.meter is not None else (0.0, 0, 0)
            )
            attempt.usd = add_usd(getattr(agent_result, "usd", None), over_usd)
            attempt.overhead_usd = over_usd
            attempt.prompt_tokens = int(getattr(agent_result, "prompt_tokens", 0) or 0) + over_prompt
            attempt.completion_tokens = (
                int(getattr(agent_result, "completion_tokens", 0) or 0) + over_completion
            )
            attempt.model = str(getattr(agent_result, "model", "") or "")
            self._emit(_ev_result(index, ok, detail=(fb or vout)[:200]))
            if not ok and snapshot is not None and self.guard is not None:
                # Antes de apagar, guardar. `restore` e' a decisao certa — o verificador reprovou —
                # mas ela e' irreversivel, e sem esta linha a unica copia do que a tentativa
                # escreveu passa a ser o patch cortado em 4.000 caracteres dentro do recibo.
                attempt.discarded_at = self._preserve_discarded(attempt, index)
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
                # ``pause_always`` is the same interrupt with a different trigger: hold EVERY
                # successful run for sign-off, not just a tainted one. Taint-triggered pausing is
                # the right default because it stops only when there is a reason; a reviewer who
                # wants to see each change before it counts as done has to be able to say so, and
                # the alternative — a UI control that quietly maps onto the taint trigger — would
                # be a switch that does nothing most of the time.
                if (run_tainted and self.pause_on_taint) or self.pause_always:
                    self._save_checkpoint(
                        thread_id, task, index, feedback, plan, attempts,
                        awaiting_approval=True, paused_answer=answer, was_tainted=run_tainted,
                        # Persist the diff-gate verdict (M19-A2): a hollow success (empty diff) must
                        # STILL be blocked from minting a skill/memory when it's approved on resume —
                        # otherwise the HITL path silently bypasses the anti-hollow-learning gate.
                        productive=diff_productive,
                    )
                    reason = "tainted run" if run_tainted else "every run held for sign-off"
                    self._emit(_ev_status(f"paused for approval — {reason} (thread {thread_id})"))
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
            # Retry-conditioning (--diff-feedback): the agent already captured what this attempt
            # ACTUALLY wrote (above, pre-revert) and every consumer of it is telemetry — it never
            # re-enters a prompt. So the retry is told THAT it failed but never shown the code it
            # wrote, and since the workspace was just reverted, nothing on disk records the wrong
            # path either: re-deriving the same patch is unobstructed. Feeding the diff back closes
            # that. Opt-in and measured, not assumed — showing a wrong patch can also ANCHOR a model
            # on it, which is the registered counter-hypothesis (see bench/retry_lift).
            # Guarded on there BEING a next attempt: this is retry conditioning, so building it after
            # the final attempt would inflate the injection count (the pre-registration's validity
            # gate) with feedback no model ever reads.
            if (self.diff_feedback and index < self.config.max_attempts
                    and attempt.diffs and (body := _rendered_diff(attempt.diffs))):
                feedback = f"{feedback}\n\n{_DIFF_FEEDBACK_HEADER}\n{body}\n{_DIFF_FEEDBACK_FOOTER}"
                # Emitted so a bench can COUNT injections: a run where this never fires measured a
                # plumbing failure, not the idea (learning-lift runs 1-2 were lost to exactly that,
                # and the retry_lift pre-registration makes it a hard validity gate).
                self._emit(_ev_status(f"diff-feedback injected {len(body)} chars (attempt {index})"))
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
                        # Countable for the same reason as the diff injection above: a bench arm
                        # whose pivot never fires measured nothing, and must say so.
                        self._emit(_ev_status(f"stagnation pivot injected (attempt {index})"))

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
        # --keep-workspace: leave the last attempt's edits on disk for an external grader, undoing the
        # in-loop revert of that final attempt. Only meaningful on failure (a success never reverts).
        if self.keep_workspace and last_after is not None and self.guard is not None:
            self.guard.restore(last_after)
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
        *,
        agent_result: Any = None,
        index: int = 0,
        snapshot: Any = None,
    ) -> AutonomousResult:
        """Cooperative stop: the caller asked to cancel between attempts. Return a well-formed result
        (``success=False``, ``stopped_reason="cancelled"``) carrying the attempts completed so far.

        Deliberately NOT treated as a genuine failure: a user cancellation is not evidence the approach
        was wrong, so it does NOT distill an anti-pattern card or credit a card failure (unlike the
        budget-exhausted return above). The checkpoint is cleared — a user-cancelled run is terminal,
        not resumable — and a receipt is persisted, mirroring the exhaustion path's construction.

        ``agent_result`` is the attempt that was cut short, when there is one. Two of the three cancel
        sites fire before any model call and pass nothing; the third fires with a worker that already
        ran, and dropping THAT one was the defect. Its sibling :meth:`_finalize_capped` carries the
        measurement in a comment: a receipt reading ``usd: null, attempts: []`` for a run that had
        just spent money showed a paid run as free on the Cost screen.

        ``snapshot`` lets the partial attempt record what it wrote. The files are NOT reverted —
        whoever pressed Stop may want the work, and `guard.restore` only runs on the verified path —
        so `reverted: false` with `diffs: []` read as "it changed nothing" about a workspace that had
        changed. Recording is the fix; reverting would destroy work somebody may have wanted.
        """
        if agent_result is not None:
            attempts = [*attempts, self._partial_attempt(agent_result, index, snapshot)]
        self._emit(_ev_status("cancelled"))
        self._clear_checkpoint(thread_id)
        last = (getattr(agent_result, "answer", "") or "") or (attempts[-1].answer if attempts else "")
        self._emit(_ev_final(False, last))
        result = AutonomousResult(
            answer=last, success=False, attempts=attempts, plan=plan, stopped_reason="cancelled"
        )
        self._persist_receipt(result, task)
        return result

    def _partial_attempt(self, agent_result: Any, index: int, snapshot: Any = None) -> Attempt:
        """An attempt that was cut short: what it cost, and what it left on disk.

        Shared by the two stop paths, because the bookkeeping is the same and having it in one of
        them was how the other went without it for so long. The meter is drained here — the overhead
        it holds belongs to this attempt and would otherwise be billed to whatever runs next.
        """
        from chimera.orchestration.metering import add_usd

        over_usd, over_prompt, over_completion = (
            self.meter.take() if self.meter is not None else (0.0, 0, 0)
        )
        parcial = Attempt(index, getattr(agent_result, "answer", "") or "", False, False, False, False)
        parcial.usd = add_usd(getattr(agent_result, "usd", None), over_usd)
        parcial.overhead_usd = over_usd
        parcial.prompt_tokens = int(getattr(agent_result, "prompt_tokens", 0) or 0) + over_prompt
        parcial.completion_tokens = (
            int(getattr(agent_result, "completion_tokens", 0) or 0) + over_completion
        )
        parcial.model = str(getattr(agent_result, "model", "") or "")
        parcial.run_id = str(getattr(agent_result, "run_id", "") or "")
        parcial.evidence = "none"
        if snapshot is not None and self.guard is not None:
            # Measured, not assumed, and the same call the verified path makes. `diff_productive:
            # null` for an attempt that wrote a file is precisely the claim that field exists to
            # prevent.
            from chimera.evolution.diff_gate import diff_snapshots, unified_diffs

            depois = _without_verifier_artifacts(self.guard.snapshot())
            pdiff = diff_snapshots(snapshot, depois)
            parcial.diff_productive = pdiff.is_productive
            parcial.diff_summary = pdiff.audit_summary()
            parcial.diffs = unified_diffs(snapshot, depois)
        return parcial

    def _finalize_capped(
        self,
        task: str,
        attempts: list[Attempt],
        plan: Plan | None,
        thread_id: str | None,
        agent_result: AgentResult,
        index: int,
    ) -> AutonomousResult:
        """The money ran out: return what was bought, and stop buying.

        Shaped on `_finalize_cancelled`, not on the exhaustion path, and the difference is the
        point: running out of money is not evidence the approach was wrong, so this distils no
        anti-pattern card and credits no card failure. The run did what it was told to do with the
        money it was given.

        The worker's own message is the answer, because it names WHICH ceiling and what it had
        spent — "the run failed" for a run that simply reached its cap is the four-word ending this
        release spent its time removing.
        """
        # The stopped attempt is RECORDED, not dropped. It called a model — that is how the cap was
        # reached — and returning here before the normal bookkeeping left a receipt reading
        # `usd: null, attempts: []` for a run that had just spent money. Measured on a real run:
        # the answer said "spend cap reached: $0.0030" while the receipt reported nothing, so the
        # Cost screen showed a paid run as free. A cap that hides its own spending is worse than
        # no cap: the number it exists to protect is the number it erases.
        # Through the shared helper, which was extracted FROM this block. Leaving the copy here
        # would restore the condition that let the cancel path fall behind: the same bookkeeping
        # written twice, corrected once.
        attempts = [*attempts, self._partial_attempt(agent_result, index)]

        self._emit(_ev_status("stopped on budget"))
        self._clear_checkpoint(thread_id)
        last = agent_result.answer or (attempts[-1].answer if attempts else "")
        self._emit(_ev_final(False, last))
        result = AutonomousResult(
            answer=last, success=False, attempts=attempts, plan=plan, stopped_reason="spend"
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

    def _preserve_discarded(self, attempt: Attempt, index: int) -> str:
        """Escreve o diff INTEIRO da tentativa em disco e devolve o caminho, ou "" se nada houver.

        Fora do `runs.jsonl` de proposito: aquele arquivo e' lido inteiro por varias telas e o corte
        em 4.000 caracteres existe para ele nao inchar. Os dois requisitos — log limitado e trabalho
        recuperavel — so' convivem se forem arquivos diferentes.

        Melhor esforco em todos os sentidos: um erro aqui nao pode impedir a reversao, que e' a
        parte que mantem o workspace consistente.
        """
        if not attempt.diffs or self.run_log is None:
            return ""
        try:
            # O id vem da tentativa, que ja o leu do worker algumas linhas acima — o agente nao tem um.
            destino = (
                self.run_log.parent / "discarded" / f"{attempt.run_id or 'run'}-{index}.diff"
            )
            destino.parent.mkdir(parents=True, exist_ok=True)
            corpo = [
                f"# tentativa {index} revertida: o verificador reprovou",
                f"# {len(attempt.diffs)} arquivo(s); isto e' o que estava escrito antes da reversao",
                "",
            ]
            for d in attempt.diffs:
                corpo.append(f"--- {getattr(d, 'path', '?')}")
                corpo.append(str(getattr(d, "patch", "") or ""))
                corpo.append("")
            destino.write_text("\n".join(corpo), encoding="utf-8")
            _log.info("trabalho revertido guardado em %s", destino)
            _podar_descartados(destino.parent)
            return str(destino)
        except Exception as exc:  # noqa: BLE001 — guardar nunca pode impedir reverter
            _log.warning("nao consegui guardar o trabalho revertido: %s", exc)
            return ""

    def _persist_receipt(self, result: AutonomousResult, task: str) -> None:
        """Append a run receipt recording how this finished run PROVED its work (read-only evidence).

        Best-effort in the sense that it must NEVER break or fail a run — but not silent, and not
        all-or-nothing. Both of those were true until a bench caught it: on one task, three runs in
        a row wrote no row at all and TWO OF THEM HAD SUCCEEDED, with the work still on disk and its
        test passing. A receipt that failed to serialise disappeared behind a `debug` line, which no
        user and no bench ever sees, and it disappeared BY TASK rather than at random — so every
        consumer of these rows, the Cost screen included, undercounted systematically and quietly.

        So a failure now (a) logs at WARNING with the reason, and (b) falls back to a minimal row
        that still carries the tokens and the workspace. A run whose full proof trail cannot be
        serialised is still a run that cost money, and the cost is the part nothing else can
        reconstruct.

        Reads the verify command off the verifier when it exposes one (``CommandVerifier.command``);
        ``None`` for a run with no executable verifier.
        """
        if self.run_log is None:
            return
        try:
            from chimera.api.runs import append_run, build_receipt

            verify_command = getattr(self.verifier, "command", None)
            receipt = build_receipt(
                result, task, verify_command, datetime.now(UTC).isoformat(),
                profile=self.run_profile,
                verify_source=self.verify_source,
                profile_source=self.profile_source,
                # The directory this run was actually confined to, not the one a caller meant. A run
                # built without a workspace records none rather than the process cwd, which would be
                # a guess wearing the same clothes as a fact.
                workspace=str(self.workspace) if self.workspace else "",
            )
            append_run(self.run_log, receipt)
        except Exception as exc:  # noqa: BLE001 — receipt persistence is best-effort, never fatal
            _log.warning("run receipt failed to persist (%s: %s); writing a minimal row instead",
                         type(exc).__name__, exc)
            self._persist_minimal_receipt(result, task, exc)

    def _persist_minimal_receipt(
        self, result: AutonomousResult, task: str, cause: Exception
    ) -> None:
        """The fallback: the tokens and the outcome, with none of the fields that can fail.

        Written by hand rather than through ``build_receipt`` on purpose — the builder is what just
        raised, so re-entering it would lose the row for the same reason twice. Everything here is a
        primitive, and the whole thing is wrapped again because a fallback that can itself throw is
        not a fallback.
        """
        try:
            import json as _json

            row = {
                "ts": datetime.now(UTC).isoformat(),
                "task": str(task)[:2000],
                "success": bool(getattr(result, "success", False)),
                "workspace": str(self.workspace) if self.workspace else "",
                "partial": True,
                "partial_reason": f"{type(cause).__name__}: {cause}"[:500],
                "attempts": [
                    {
                        "index": int(getattr(a, "index", 0) or 0),
                        "success": bool(getattr(a, "success", False)),
                        "prompt_tokens": int(getattr(a, "prompt_tokens", 0) or 0),
                        "completion_tokens": int(getattr(a, "completion_tokens", 0) or 0),
                        "usd": getattr(a, "usd", None),
                        "model": str(getattr(a, "model", "") or ""),
                    }
                    for a in getattr(result, "attempts", None) or []
                ],
            }
            assert self.run_log is not None
            self.run_log.parent.mkdir(parents=True, exist_ok=True)
            with self.run_log.open("a", encoding="utf-8") as handle:
                handle.write(_json.dumps(row, ensure_ascii=True) + "\n")
        except Exception as exc:  # noqa: BLE001 — still never fatal
            _log.warning("minimal run receipt also failed: %s", exc)

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
        # The task's OPENING LINE, bounded — the same treatment the answer already got, and for
        # the same reason. A memory fact is meant to be recalled; the whole request is a
        # transcript. Measured on a real install: four facts of 630-950 characters, each one an
        # entire project brief followed by an entire answer, sitting in the context budget of every
        # later run in that folder. The same four under this rule are 31-36 characters of task.
        #
        # First line rather than a prefix of the whole: a brief opens with what it wants and
        # continues with how, so the opening line is the part that identifies it — and a prefix
        # cut at 160 lands mid-sentence in the middle of the instructions.
        head = next((line.strip() for line in task.splitlines() if line.strip()), "")[:_FACT_CHARS]
        snippet = next(
            (line.strip() for line in answer.splitlines() if line.strip()), ""
        )[:_FACT_CHARS]
        fact = f"Accomplished: {head}" + (f" — {snippet}" if snippet else "")
        # Scoped to the folder the work happened in. "Accomplished: <task>" is about THIS
        # codebase, and a note from one project arriving as context in another is the noise this
        # exists to stop. A run with no workspace has no project and stays global.
        self.memory.remember(
            fact,
            # Keyed on the FULL task, never on the shortened head: two briefs that open the same
            # way — "Leia BRIEF.md e construa…" was four of them — are different work, and a key
            # built from the head would fold them into one entry that overwrites itself.
            key=f"solve:{_slug(task)}",
            provenance="tainted" if tainted else "clean",
            project=str(self.workspace) if self.workspace else None,
        )

    def _review(
        self, task: str, answer: str, context: str, *, manager: Manager | None = None
    ) -> tuple[bool, str, bool]:
        """Returns (approved, feedback, abstained). ``abstained`` = nobody actually judged — either
        no manager is configured, or the one configured replied with nothing readable. Same shape
        and same meaning as :meth:`_verify`'s third value, and for the same reason: the caller must
        fall back to its other gates instead of reading a non-answer as either answer."""
        if not self._manager_ran():
            return True, "", True
        # `manager` is the same reviewer wrapped in the run's ceiling; `self.manager` is the bare
        # one. `_manager_ran` still asks the field, because whether a reviewer EXISTS is a fact
        # about the agent and must not change with whether a budget was set.
        reviewer = manager if manager is not None else self.manager
        assert reviewer is not None
        try:
            review = reviewer.review(task, answer, context=context)
        except SpendExceeded:
            # The ceiling refused the reviewer's call. ABSTAINED, not rejected: a reviewer that was
            # never allowed to look has not judged, and reading its silence as a veto would revert
            # work the money had already bought. The run ends on the next pass at no cost — the
            # worker draws on this same budget, so its first call is refused before it is made.
            _log.info("review skipped: the run reached its dollar ceiling")
            return True, "", True
        return review.approved, review.feedback, review.abstained

    def _manager_ran(self) -> bool:
        """Whether a manager actually looked at this attempt.

        The vacuous `True` above is correct — with no reviewer configured, nothing should veto the
        work. What was wrong is that the caller could not tell the two apart, so an attempt with no
        manager at all came out labelled ``evidence="manager"``: a receipt naming an authority that
        never existed. The receipt's whole job is to say who approved, and it was the one field that
        could be fabricated by omission.
        """
        return self.manager is not None and bool(self.config.use_manager)

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
