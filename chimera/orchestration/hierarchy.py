"""Hierarchical orchestrator (M16-A7): top model decomposes/verifies/synthesizes,
mid-tier workers execute under contract, tokens are budgeted, savings are measured.

The evidence this design follows, clause by clause:
- Orchestrator-worker wins on PARALLEL/READ-HEAVY tasks (Anthropic: Opus lead +
  Sonnet workers +90.2% vs Opus alone) and LOSES on sequential-write/coding
  (Cognition) -> a deterministic classifier routes write-shaped and trivial tasks
  to the single-agent path (``fell_back=True``), and the profitability gate stops
  delegation when inline is cheaper. Both decisions are logged, so the guard
  itself is auditable.
- Delegation is contract-based (MAST: vague specs 41.8% + handoff loss 36.9% of
  failures): a :class:`TaskSpec` goes down, a bounded :class:`ResultEnvelope`
  comes back; bulk goes to the artifact store; the verifier gates each envelope.
- The orchestrator synthesizes over SUMMARIES ONLY — never artifacts, never
  transcripts. Fusion engages only when ``fuse_final`` and the envelopes actually
  conflict (Self-MoA: don't fuse by default).
- Cache-aware: every worker of the tier shares the byte-identical static system
  prefix (:data:`WORKER_SYSTEM`); the volatile TaskSpec renders after it. A
  worker's model never changes mid-task.
- Effort scaling is harness-enforced (:class:`~chimera.orchestration.budget.EffortPolicy`
  + :class:`~chimera.orchestration.budget.BudgetedBackend`), not prompted.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from chimera.evolution.context import EvolutionContext

from chimera.orchestration.artifacts import ArtifactStore, build_envelope
from chimera.orchestration.budget import (
    BudgetedBackend,
    BudgetExceeded,
    EffortPolicy,
    SpendExceeded,
    TokenBudget,
)
from chimera.orchestration.envelope_verify import EnvelopeVerifier
from chimera.orchestration.events import OrchEvent, OrchEventSink
from chimera.orchestration.receipts import (
    DelegationReceipt,
    ProfitEstimate,
    append_delegation,
    estimate_profitability,
    estimate_tokens,
    make_receipt,
)
from chimera.orchestration.roles import Role, RoleAgent
from chimera.orchestration.spec import EffortBudget, ResultEnvelope, TaskSpec
from chimera.providers.gateway import CompletionResult, Message, MessageLike, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("orchestration.hierarchy")

TaskShape = Literal["parallel_read", "sequential_write", "simple"]

#: Static worker system prompt — BYTE-IDENTICAL for every worker of the tier, on
#: purpose: an identical prefix is a shared provider-cache prefix across workers.
#: Volatile task material (the rendered TaskSpec) always goes AFTER this.
WORKER_SYSTEM = (
    "You are a focused sub-worker in a hierarchical agent. You receive ONE task "
    "specification with an objective, an expected output format, and boundaries. "
    "Do exactly that task — nothing beyond the boundaries. Be concise and factual. "
    "Lead with your findings; do not repeat the task or the context back. "
    # Added after a fan-out asked about a 16-line module and produced a confident description of
    # a class that did not exist. The workers had no tools then; now that they can be given read
    # tools, the instruction has to say that guessing is not an option — a model asked about a
    # named file will otherwise answer from what such a file usually contains.
    "If the task names a file and you have a tool that can read it, READ IT. Never describe a "
    "file you have not opened: if you cannot open it, say so and report nothing about its "
    "contents. "
    "If you could not verify something, say so under a final 'Gaps' heading."
)

_DECOMPOSE_SYSTEM = (
    "You are the lead orchestrator of a hierarchical agent. Split the user's task into "
    "INDEPENDENT subtasks that can run in parallel — each self-contained, no subtask "
    "depending on another's output. Reply with ONLY a JSON array; each item: "
    '{"objective": str, "output_format": str, "boundaries": str}. '
    "Use the smallest number of subtasks that covers the task (1 is fine)."
)

_SYNTH_SYSTEM = (
    "You are the lead orchestrator. Below are verified summaries from your sub-workers. "
    "Synthesize ONE final answer to the user's task from them. Resolve overlaps, note "
    "real contradictions honestly, and do not invent findings absent from the summaries. "
    # The workers answer each other; this answers a person. Without the instruction the language
    # came out of whatever the summaries happened to be in — the same Portuguese question got a
    # Portuguese answer on one run and an English one on the next, in an app whose entire
    # interface is translated into ten languages.
    "Answer in the SAME LANGUAGE the user's task is written in."
)

# Write/edit intent markers -> sequential_write (multi-agent parallelism loses here).
_WRITE_MARKERS = (
    "write ", "create ", "edit ", "modify ", "fix ", "refactor", "implement",
    "delete ", "rename ", "install ", "deploy", "commit", "patch ",
    "escreva", "crie ", "edite", "modifique", "corrija", "implemente", "instale",
)
_READ_MARKERS = (
    "research", "compare", "summarize", "summarise", "analyze", "analyse", "review ",
    "audit", "survey", "collect", "gather", "list ", "extract", "read ",
    "pesquise", "compare", "resuma", "analise", "audite", "colete", "extraia", "leia",
)
_MULTIPART = re.compile(r"\b(and|e|,|;)\b", re.IGNORECASE)

# Distinct-source detectors (deterministic). Two or more sources + read intent is the
# measured guaranteed-gain region: bench/hierarchy_sweep shows a single agent re-sends
# ALL D docs every turn while scoped workers read one each, so the token saving is
# (D-1)/D — measured 49.9% / 66.7% / 74.8% / 79.9% at D=2..5 on deepseek. Below D=2
# there is nothing to isolate and fan-out only adds overhead (bench/hierarchy: +47%).
# Source extensions, including CODE. The original list was documents only — md, txt, pdf,
# csv, json, yaml and friends — which was right while the only caller was `brief`, a research
# recipe over articles. It became wrong the moment a desktop screen put this in front of a
# project: "analyse carrinho.py and test_carrinho.py" names two distinct sources, scored zero,
# and so never reached the measured guaranteed-gain region in the one domain the product is
# for. A file is a file; what makes two of them worth isolating is that there are two, not
# what they are written in.
_FILE_REF = re.compile(
    r"\b[\w-]+\.(?:"
    r"md|txt|pdf|csv|tsv|json|ya?ml|docx?|html?|log|rst|ipynb|toml|ini|cfg|xml|sql"
    r"|py|pyi|ts|tsx|js|jsx|mjs|cjs|rs|go|java|kt|rb|php|cs|swift|c|h|cpp|hpp|cc|sh|ps1"
    r")\b",
    re.I,
)
_DOC_REF = re.compile(r"\b(?:doc(?:ument)?|file|source|report|section|chapter)\s+[A-Z0-9][\w-]*", re.I)
_URL_REF = re.compile(r"https?://\S+")


def count_sources(task: str) -> int:
    """Count DISTINCT document-like sources named in the task (files, doc/source X,
    URLs). Deterministic, no LLM. Two or more => the multi-doc isolation regime where
    the hierarchy's token win is guaranteed by the sweep (see the constants above)."""
    hits: set[str] = set()
    for pattern in (_FILE_REF, _DOC_REF, _URL_REF):
        hits.update(m.group(0).lower() for m in pattern.finditer(task))
    return len(hits)


def classify_task(task: str) -> TaskShape:
    """DETERMINISTIC task-shape heuristic — never an LLM call (anti-scope rule).

    write-intent -> sequential_write; short single-question -> simple; read-heavy
    multi-part -> parallel_read. Biased toward falling back: the single-agent path is
    always correct, the hierarchy is an optimization. TWO OR MORE distinct sources +
    read intent short-circuits to parallel_read even from terse phrasing — that's the
    measured guaranteed-gain region (the (D-1)/D sweep), so we don't want a length/part
    heuristic to miss it.
    """
    low = task.lower()
    if any(marker in low for marker in _WRITE_MARKERS):
        return "sequential_write"
    is_read = any(marker in low for marker in _READ_MARKERS)
    if is_read and count_sources(task) >= 2:
        return "parallel_read"
    parts = len(_MULTIPART.findall(task))
    if is_read and (parts >= 2 or len(task) >= 200):
        return "parallel_read"
    return "simple"


@dataclass
class HierarchyConfig:
    max_workers: int = 4
    fuse_final: bool = True
    """Engage fusion at synthesis ONLY when worker envelopes conflict."""
    worker_max_steps: int = 6
    effort: EffortPolicy = field(default_factory=EffortPolicy)
    spot_rate: float = 0.2
    inline_below_spec_tokens: int = 0
    """Per-subtask gate (opt-in; 0 = off). A subtask whose rendered spec is smaller
    than this is answered INLINE by the trusted top model in one call — skipping the
    worker spawn + verification round-trip whose ~fixed framing would otherwise
    dominate a trivial task's cost. Heuristic: the hierarchy's real token win is a
    WHOLE-TASK context-isolation effect (synthesis over 2k summaries, not full docs);
    this only trims the dispatch overhead on subtasks too small to benefit from it,
    and a subtask's output size can't be known before running, so keep it conservative."""


@dataclass
class TaskPlan:
    """What the orchestrator would do with a task, decided once and reusable.

    ``specs`` is the point: a caller can hand these straight to :meth:`run_prepared` and get the
    plan it was shown, instead of a second decomposition that may split the task differently.
    """

    shape: TaskShape
    specs: list[TaskSpec] = field(default_factory=list)
    profitable: bool = False
    margin: float = 0.0
    workers: int = 0
    budget_per_worker: int = 0
    decompose_spent: bool = False
    """Whether producing this plan cost a model call. False on the fallback branch, where the
    classifier and the estimate are both arithmetic — so "no worker tokens" and "nothing at all"
    are different claims and a display can tell them apart."""

    @property
    def would_fall_back(self) -> bool:
        return self.shape != "parallel_read"


@dataclass
class HierarchyResult:
    answer: str
    shape: TaskShape
    envelopes: list[ResultEnvelope] = field(default_factory=list)
    receipts: list[DelegationReceipt] = field(default_factory=list)
    fell_back: bool = False
    total_tokens: int | None = None
    counterfactual_tokens: int | None = None
    cancelled: bool = False
    """Stopped between units by ``should_stop``. The envelopes that had already verified are
    still here; ``answer`` is empty, because synthesising them would spend the top-model call the
    caller just asked not to spend."""


#: Stop reasons that mean the worker was CUT OFF rather than finished. Its text is then a report
#: about the run, not about the task, and must not be verified or synthesised as a finding.
_CUT_OFF_REASONS = frozenset({"budget", "spend", "max_steps", "tool_loop", "cancelled"})


class HierarchicalOrchestrator:
    """Top model decomposes -> budgeted mid workers execute -> verifier gates ->
    top model synthesizes over summaries. Falls back to single-agent whenever the
    evidence says hierarchy loses."""

    def __init__(
        self,
        gateway: SupportsComplete,
        *,
        weak_model: str,
        mid_model: str,
        top_model: str,
        store: ArtifactStore,
        verifier: EnvelopeVerifier | None = None,
        verifier_model: str | None = None,
        fusion: SupportsComplete | None = None,
        receipts_path: Path | None = None,
        config: HierarchyConfig | None = None,
        evolution: EvolutionContext | None = None,
        on_event: OrchEventSink | None = None,
        should_stop: Callable[[], bool] | None = None,
        worker_tools: Callable[[], Any] | None = None,
        identity: str = "",
    ) -> None:
        self.gateway = gateway
        # The owner's rendered instructions. Keyword-only with an empty default, so every existing
        # caller and test is untouched.
        #
        # It reaches the two stages that answer a PERSON — the synthesizer and the single-agent
        # fallback — and deliberately not the decomposer or the sub-workers. The decomposer must
        # emit only a JSON array, and "always answer in Portuguese" against "reply with ONLY a JSON
        # array" is a conflict, not a preference. The workers answer the synthesizer rather than
        # the user, and `WORKER_SYSTEM` already dictates their form; the owner's line about how to
        # answer would fight it, on N calls per run, for text nobody reads directly.
        #
        # Without this, an owner who set the app to Portuguese got English out of this screen: the
        # same `render()` that carries the persona carries the "always answer in {language}" line.
        self.identity = identity
        # Both keyword-only with a None default, so every existing caller and test is untouched.
        # On the constructor rather than on run(): `run_prepared`, `_dispatch` and `_run_one` all
        # need them, and threading a pair of parameters through four private signatures is a wider
        # blast radius than one attribute.
        #
        # The sink is called from N worker threads at once and this class does NOT serialise it.
        # The production sink is an SSE bridge whose body is `loop.call_soon_threadsafe(...)`,
        # already safe; a lock here would serialise workers around a callback whose cost the
        # orchestrator cannot see. Sinks that need one wrap themselves in `thread_safe_sink`.
        self.on_event = on_event
        # Cooperative and checked BETWEEN units, never inside a call in flight. A model call that
        # has started is already paid for, so stopping it buys nothing and loses the answer.
        self.should_stop = should_stop
        # A factory for the workers' tool registry, or None to keep them tool-free.
        #
        # Tool-free was the original design and it was right for the original caller: `brief`
        # hands a worker the text it must summarise, so a worker with no tools cannot wander. It
        # became wrong the moment a screen put this in front of a PROJECT, because the natural
        # request there is "analyse carrinho.py" — and a worker that cannot open the file answers
        # anyway. Measured, not reasoned: asked about a 16-line module with two functions, the
        # fan-out described a class with cupons and stock control, and all three workers passed
        # verification, because the verifier checks that a summary is faithful to what the worker
        # WROTE, never that what the worker wrote is true.
        #
        # A factory rather than a registry so each worker gets its own instance — a registry can
        # carry per-run state, and sharing one across a thread pool is how that state gets mixed.
        #
        # ⚠️ TOOLS MOVE THE BREAK-EVEN POINT, and by how much is not yet measured. The (D-1)/D
        # sweep quoted above `count_sources` was run with TOOL-FREE workers handed their documents
        # in the prompt; a worker that fetches its own source also pays for a tool loop and for the
        # tool schemas in every turn. First measurement of the new shape: two small Python files,
        # 8072 tokens measured against an 8000-token inline counterfactual — the fan-out lost.
        # Context isolation still holds for LARGE sources, where one worker reading one document
        # beats one agent re-sending all of them, but the crossover is now somewhere above
        # "two files of twenty lines" and nobody has found it. bench/hierarchy_sweep needs a
        # tool-enabled arm before this saving is quoted again.
        self.worker_tools = worker_tools
        # M19-A4: the shared flywheel, READ-and-write-telemetry only. A fan-out has no
        # verify-or-revert signal, so it reads retrieved cards + recalled facts into the top
        # model's synthesis and records the run as an experience lesson + card credit — but never
        # distils a skill (distillation stays on the verified solve/lifecycle path).
        self.evolution = evolution
        self.weak_model = weak_model
        self.mid_model = mid_model
        self.top_model = top_model
        self.store = store
        # M18-2: the spot-check auditor runs on `verifier_model` when given — a DISTINCT model slug
        # (cross-provider via the router) so it doesn't grade its own family's output. Defaults to the
        # weak model, which already differs from the mid-tier worker it audits.
        self.verifier = verifier or EnvelopeVerifier(
            store=store, backend=gateway, model=weak_model, verifier_model=verifier_model,
            spot_rate=(config or HierarchyConfig()).spot_rate,
        )
        self.fusion = fusion
        self.receipts_path = receipts_path
        self.config = config or HierarchyConfig()

    # ------------------------------------------------------------------ events

    def _emit(self, kind: str, *, text: str = "", task_id: str = "", **data: object) -> None:
        """Hand one frame to the sink, and never let the sink break the run.

        The swallow is not defensive habit, it is the same rule ``_recall_block`` already follows
        two hundred lines down: progress reporting is advisory. A consumer whose queue is full or
        whose socket just closed must not kill a worker whose tokens have already been paid for —
        the run's job is to produce an answer, not to keep an audience.
        """
        if self.on_event is None:
            return
        try:
            self.on_event(OrchEvent(kind, text, task_id, dict(data)))  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 -- see the docstring
            _log.debug("event sink raised (ignored): %s", exc)

    def _stopped(self) -> bool:
        return self.should_stop is not None and self.should_stop()

    # ------------------------------------------------------------------ public

    def run(self, task: str) -> HierarchyResult:
        shape = classify_task(task)
        # Hoisted from the profitability guard below so the frame can carry it: the source count
        # is what decides whether that guard even applies, and a consumer showing "why this shape"
        # without it is showing half the reason. `count_sources` is deterministic and free.
        sources = count_sources(task)
        self._emit("classified", text=shape, shape=shape, sources=sources)

        # Guard 1 — shape (Cognition rule): write/simple tasks stay single-agent.
        if shape != "parallel_read":
            return self._fallback(task, shape, reason=f"shape={shape}", code="shape")

        # Guard 2 — global profitability: don't delegate when inline is cheaper.
        # EXCEPTION: 2+ distinct sources is the measured guaranteed-gain region — the
        # (D-1)/D sweep proves isolation wins there — so the crude blank-context estimate
        # is not allowed to veto it.
        if sources < 2:
            probe = TaskSpec(task_id="probe", objective=task)
            estimate = estimate_profitability(
                probe, orchestrator_context_chars=len(task) * 8 + 24_000
            )
            if not estimate.profitable:
                return self._fallback(
                    task, shape, reason="unprofitable estimate", code="unprofitable"
                )
        else:
            _log.debug("%d distinct sources -> guaranteed-gain region, skipping profit veto", sources)

        specs, decompose_tokens, decompose_estimated = self._decompose_metered(task)
        if not specs:
            return self._fallback(
                task, shape, reason="decomposition failed", code="decompose_failed"
            )
        return self.run_prepared(
            task, specs, shape=shape,
            overhead_tokens=decompose_tokens, overhead_estimated=decompose_estimated,
        )

    def run_prepared(
        self,
        task: str,
        specs: list[TaskSpec],
        *,
        shape: TaskShape = "parallel_read",
        overhead_tokens: int = 0,
        overhead_estimated: bool = False,
    ) -> HierarchyResult:
        """Run with a caller-supplied decomposition (recipes know their own split —
        no top-model decompose call is spent, hence ``overhead_tokens=0`` by default)."""
        # Emitted here rather than in run() so the recipe path gets it too: `brief` calls
        # run_prepared directly with a decomposition it already had, and a consumer watching that
        # run should see the same frame as one watching a model-decomposed one.
        self._emit(
            "decomposed",
            text=f"{len(specs)} subtasks",
            specs=[
                {
                    "task_id": spec.task_id,
                    "objective": spec.objective,
                    "output_format": spec.output_format,
                    "boundaries": spec.boundaries,
                }
                for spec in specs
            ],
            overhead_tokens=overhead_tokens,
        )
        envelopes, receipts = self._dispatch(specs)
        # Ordered before the empty check, not after it, and a test caught why: cancelling makes
        # every worker return nothing, so an emptiness check that runs first sees "all delegations
        # failed" and routes a CANCELLED run into the single-agent fallback — a whole top-model
        # call, which is the one cost stopping exists to avoid.
        if self._stopped():
            # Before synthesis for the same reason. The verified envelopes are returned as they
            # stand: a consumer that streamed them already has every summary this run produced.
            return self._finish(
                HierarchyResult(
                    answer="", shape=shape, envelopes=envelopes, receipts=receipts, cancelled=True,
                    total_tokens=sum(r.total_tokens for r in receipts) or None,
                )
            )
        if not envelopes:
            return self._fallback(
                task, shape, reason="all delegations failed", code="workers_failed"
            )

        answer, synth_tokens, synth_estimated = self._synthesize(task, envelopes)
        self._record_outcome(task, answer)
        # Meter the orchestrator's OWN overhead (decompose + synthesis) as receipts, or the
        # "saving" would credit the hierarchy for a measured cost that omits its overhead while
        # the counterfactual is a full inline agent. Counterfactual=0: a single inline agent pays
        # no decompose/synth overhead, so this overhead correctly REDUCES the reported saving.
        overhead = self._overhead_receipts(
            task, overhead_tokens, overhead_estimated, synth_tokens, synth_estimated
        )
        all_receipts = receipts + overhead
        measured = sum(r.total_tokens for r in all_receipts)
        counterfactual = sum(r.counterfactual_tokens or 0 for r in receipts) or None
        return self._finish(
            HierarchyResult(
                answer=answer,
                shape=shape,
                envelopes=envelopes,
                receipts=all_receipts,
                fell_back=False,
                total_tokens=measured,
                counterfactual_tokens=counterfactual,
            )
        )

    def _overhead_receipts(
        self, task: str, decompose_tokens: int, decompose_estimated: bool,
        synth_tokens: int, synth_estimated: bool,
    ) -> list[DelegationReceipt]:
        """Receipts for the orchestrator's own top-model calls (decompose + synth). cf=0 (inline
        pays no orchestration overhead), so they add to measured cost AND subtract from the saving."""
        out: list[DelegationReceipt] = []
        for label, toks, est in (
            ("decompose", decompose_tokens, decompose_estimated),
            ("synthesis", synth_tokens, synth_estimated),
        ):
            if toks <= 0:
                continue
            out.append(make_receipt(
                TaskSpec(task_id=label, objective=task),
                tier="top", model=self.top_model,
                prompt_tokens=toks, completion_tokens=0, tokens_estimated=est,
                counterfactual_tokens=0, counterfactual_model=self.top_model,
            ))
        if self.receipts_path is not None:
            for receipt in out:
                append_delegation(self.receipts_path, receipt)
        return out

    def plan(self, task: str) -> TaskPlan:
        """Everything a caller needs to decide, INCLUDING the subtasks themselves.

        Split out of :meth:`dry_run` because throwing the specs away was a real defect, not a
        tidiness question. A preview decomposed, showed the objectives, dropped the specs — and
        the run then decomposed AGAIN. Decomposition is a model call at a non-zero temperature,
        so the second one returns a different split: a screen could promise one worker and deliver
        three. The plan a person approves has to be the plan that runs.
        """
        shape = classify_task(task)
        probe = TaskSpec(task_id="probe", objective=task)
        estimate = estimate_profitability(
            probe, orchestrator_context_chars=len(task) * 8 + 24_000
        )
        if shape != "parallel_read":
            return TaskPlan(shape=shape, profitable=estimate.profitable, margin=estimate.margin)
        specs = self.decompose(task)
        return TaskPlan(
            shape=shape,
            specs=specs,
            profitable=estimate.profitable,
            margin=estimate.margin,
            workers=self.config.effort.workers_for(shape, len(specs)),
            budget_per_worker=self.config.effort.budget_for(shape),
            decompose_spent=True,
        )

    def dry_run(self, task: str) -> dict[str, object]:
        """Classification + decomposition + profitability estimate — zero worker spend."""
        plan = self.plan(task)
        out: dict[str, object] = {
            "shape": plan.shape,
            "profitable_estimate": plan.profitable,
            "estimate_margin": plan.margin,
        }
        if plan.shape == "parallel_read":
            out["subtasks"] = [s.objective for s in plan.specs]
            out["workers"] = plan.workers
            out["budget_per_worker"] = plan.budget_per_worker
        else:
            out["would_fall_back"] = True
        return out

    # --------------------------------------------------------------- internals

    def decompose(self, task: str) -> list[TaskSpec]:
        """Top model -> JSON subtasks, pydantic-validated, ONE repair retry, N capped."""
        return self._decompose_metered(task)[0]

    def _decompose_metered(self, task: str) -> tuple[list[TaskSpec], int, bool]:
        """decompose() + the tokens it actually spent (so run() can meter the overhead honestly).

        Returns (specs, total_decompose_tokens, any_estimated)."""
        r1 = self._complete_top(_DECOMPOSE_SYSTEM, task)
        tokens, estimated = _result_tokens(r1, _DECOMPOSE_SYSTEM + task + (r1.content or ""))
        specs = self._parse_specs(r1.content)
        if specs is None:  # one bounded repair attempt
            repair = (
                f"{task}\n\nYour previous reply was not a valid JSON array. "
                "Reply with ONLY the JSON array, no prose."
            )
            r2 = self._complete_top(_DECOMPOSE_SYSTEM, repair)
            t2, e2 = _result_tokens(r2, _DECOMPOSE_SYSTEM + repair + (r2.content or ""))
            tokens += t2
            estimated = estimated or e2
            specs = self._parse_specs(r2.content)
        if not specs:
            return [], tokens, estimated
        cap = self.config.effort.workers_for("parallel_read", len(specs))
        return specs[:cap], tokens, estimated

    def _parse_specs(self, raw: str) -> list[TaskSpec] | None:
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(data, list) or not data:
            return None
        specs: list[TaskSpec] = []
        budget = EffortBudget(
            max_tokens=self.config.effort.budget_for("parallel_read"),
            max_steps=self.config.worker_max_steps,
        )
        for i, item in enumerate(data):
            if not isinstance(item, dict) or not str(item.get("objective", "")).strip():
                return None
            specs.append(
                TaskSpec(
                    task_id=f"sub-{i + 1}",
                    objective=str(item.get("objective", "")).strip(),
                    output_format=str(item.get("output_format", "")).strip(),
                    boundaries=str(item.get("boundaries", "")).strip(),
                    effort=budget,
                )
            )
        return specs

    def _dispatch(self, specs: list[TaskSpec]) -> tuple[list[ResultEnvelope], list[DelegationReceipt]]:
        """Parallel budgeted workers; each raw output -> envelope -> verifier -> receipt."""
        from functools import partial

        from chimera.concurrency import run_all_with_deadline
        from chimera.orchestration.isolation import _batch_deadline

        # The aggregate inline counterfactual loads the orchestrator context ONCE for the whole task,
        # not once per subtask — so each receipt's counterfactual charges only a 1/D share of it, or
        # summing D rows would over-count the context (D-1)x and inflate the reported saving.
        n = max(1, len(specs))
        # Under a deadline, because this was the last fan-out in the package running without one.
        # ``pool.map`` waits forever, and _batch_deadline's own docstring records what that costs
        # once a live consumer is attached: a worker that never returns holds the batch open, the
        # SSE stream never closes, and the ``finally`` that sweeps the cancel registration never
        # runs — so the run leaks for the life of the process. One deadline for the whole batch,
        # never one per worker: N slow workers must not cost N x timeout.
        #
        # Keyed by position, not by ``task_id``: the ids come from a model's JSON and nothing
        # guarantees they are distinct. A duplicate would silently drop a worker's result here.
        Unit = tuple[ResultEnvelope | None, DelegationReceipt | None]
        units: list[tuple[str, Callable[[], Unit]]] = [
            (f"{i}:{spec.task_id}", partial(self._run_one, spec, n_subtasks=n))
            for i, spec in enumerate(specs)
        ]
        outcomes = run_all_with_deadline(
            units,
            max_workers=self.config.max_workers,
            timeout=_batch_deadline(None),
            # Without this, Stop could not end this wait. Cooperative flags are read BETWEEN units —
            # before one starts, after its model call returns — so a worker parked INSIDE a model
            # call never reads one, and the wait fell through to the batch deadline: four hours, in
            # a desktop app with somebody watching, while `/cancel` answered `{"ok": true}` to a run
            # it had not touched. `run_isolated` was given this argument when a live consumer was
            # first attached to the crew; the two call sites are the same shape and only one got it.
            cancelled=self._stopped,
        )
        results: list[tuple[ResultEnvelope | None, DelegationReceipt | None]] = []
        for i, spec in enumerate(specs):
            outcome = outcomes[f"{i}:{spec.task_id}"]
            if outcome.timed_out and self._stopped():
                # Abandoned the same way — the thread runs on, its result is discarded — but for a
                # reason the person watching supplied themselves. Telling somebody who just pressed
                # Stop that their subtask "overran the batch deadline" describes the mechanism and
                # misnames the cause, and it is the one wording they can prove wrong.
                _log.info("worker %s abandoned because the run was cancelled", spec.task_id)
                self._emit("worker_rejected", task_id=spec.task_id,
                           text="cancelled before it reported", reason="cancelled")
                results.append((None, None))
            elif outcome.timed_out:
                # Abandoned, not cancelled — the thread runs on and its result is discarded. Said
                # out loud rather than counted as a silent failure, because a subtask missing from
                # the synthesis for a reason nobody logged is the hardest kind of gap to notice.
                _log.warning("worker %s overran the batch deadline and was abandoned", spec.task_id)
                self._emit("worker_rejected", task_id=spec.task_id,
                           text="overran the batch deadline", reason="deadline")
                results.append((None, None))
            elif outcome.error is not None:
                _log.warning("worker %s raised: %s", spec.task_id, outcome.error)
                results.append((None, None))
            else:
                results.append(outcome.value or (None, None))
        envelopes = [env for env, _ in results if env is not None]
        receipts = [rec for _, rec in results if rec is not None]
        if self.receipts_path is not None:
            for receipt in receipts:
                append_delegation(self.receipts_path, receipt)
        return envelopes, receipts

    def _run_one(
        self, spec: TaskSpec, *, n_subtasks: int = 1
    ) -> tuple[ResultEnvelope | None, DelegationReceipt | None]:
        # Checked here, before the first model call, because this is where cancelling pays. With
        # max_workers below the number of subtasks the queued ones have not started yet: stopping
        # here is the difference between abandoning one call in flight and never making the rest.
        if self._stopped():
            return None, None
        # Per-subtask gate: a trivially small spec is cheaper answered inline by the
        # trusted top model than delegated through the worker+verify machinery.
        inline = bool(
            self.config.inline_below_spec_tokens
            and estimate_tokens(spec.render()) < self.config.inline_below_spec_tokens
        )
        # One emission for both paths, with the tier that will actually run it. Reporting every
        # subtask as a mid-tier worker would misprice the run in the consumer's own display, since
        # an inline subtask is answered by the top model and charged as such.
        self._emit(
            "worker_started",
            text=spec.objective,
            task_id=spec.task_id,
            objective=spec.objective,
            tier="top" if inline else "mid",
            model=self.top_model if inline else self.mid_model,
            max_tokens=spec.effort.max_tokens,
        )
        if inline:
            return self._run_inline_subtask(spec, n_subtasks=n_subtasks)
        budget = TokenBudget(spec.effort.max_tokens)
        backend = BudgetedBackend(self.gateway, budget, mode="hard")
        worker = RoleAgent(
            Role("worker", WORKER_SYSTEM, model=self.mid_model),
            backend,
            tools=self.worker_tools() if self.worker_tools is not None else None,
            max_steps=spec.effort.max_steps,
        )
        # Recorded on the receipt for audit; the ENFORCING gate is the whole-task one
        # in run() (Guard 2). Per-subtask inline execution is future work.
        gate = estimate_profitability(spec, orchestrator_context_chars=24_000)
        # The receipt's counterfactual shares the orchestrator context across the D subtasks (loaded
        # once inline), so the summed aggregate isn't inflated; the gate above keeps full context.
        cf = _shared_counterfactual(spec, n_subtasks)
        cut_off = ""
        try:
            raw = worker.act(spec.render())
            # A worker with tools never raises `BudgetExceeded` — `Agent.run` catches it and returns
            # the message AS THE ANSWER, deliberately ("the run did what it was told to do with the
            # money it was given"). That is right for the coding loop and wrong here: the string
            # "delegation budget exhausted: 1336/400 tokens" is not a finding, and treating it as one
            # produced a verified green card on a run that read nothing. Reproduced at a 400-token
            # cap: 44-character summary, zero evidence, `verified (accepted)`.
            #
            # The API path always has tools, so this WAS the ordinary case, not a corner.
            cut_off = worker.last_stop if worker.last_stop in _CUT_OFF_REASONS else ""
        except BudgetExceeded as exc:
            # The tool-free path RAISES where the agent loop returns the message as an answer,
            # so `last_stop` never moves off "final" here. Same event, opposite mechanics — and
            # without this line the one branch that cannot lie about its cause would have been
            # the one reported as "the provider failed".
            cut_off, raw = ("spend" if isinstance(exc, SpendExceeded) else "budget"), ""
        except Exception as exc:  # noqa: BLE001 -- a provider error must not nuke the batch
            _log.warning("worker %s failed: %s", spec.task_id, exc)
            raw = ""
        if cut_off:
            _log.info("worker %s was cut off (%s); not treating its output as a finding",
                      spec.task_id, cut_off)
        produced = bool(raw.strip()) and not cut_off
        envelope = build_envelope(
            spec, raw, self.store,
            status="ok" if produced else "failed",
            gaps=[] if produced else [
                f"worker stopped early ({cut_off}) before reporting" if cut_off
                else "worker produced no output (budget or provider error)"
            ],
        )
        # A result is trustworthy input to the synthesizer ONLY if it passes
        # verification. If the bounded re-ask also fails, the envelope is dropped
        # (audited via the receipt) rather than folded in as an unverified claim.
        verified = False
        reasked = False
        outcome = None
        if produced:
            outcome = self.verifier.verify(spec, envelope)
            verified = outcome.passed
            if not verified and self._stopped():
                # The re-ask is a whole second model call — the single largest thing a cancel
                # mid-dispatch can still avoid paying for.
                _log.debug("cancelled before the re-ask for %s", spec.task_id)
            elif not verified:
                # One bounded re-ask with the verifier's objection folded in.
                try:
                    raw2 = worker.act(
                        spec.render()
                        + f"\n\n## Verifier objection (fix this)\n{outcome.detail}"
                    )
                    candidate = build_envelope(spec, raw2, self.store)
                    # Force the spot check on the re-ask: the first verification already caught this
                    # worker being unfaithful, so the retry must be audited, not re-accepted on the
                    # free schema+criteria gates ~80% of the time.
                    if self.verifier.verify(spec, candidate, force_spot=True).passed:
                        envelope = candidate
                        verified = True
                        reasked = True
                except Exception as exc:  # noqa: BLE001 -- re-ask is best-effort
                    _log.debug("re-ask for %s failed: %s", spec.task_id, exc)
        # The budget already sums prompt+completion per call; the receipt keeps the
        # total under prompt_tokens (split is meaningless post-aggregation) and the
        # estimated flag says whether any of it came from the chars/4 fallback.
        receipt = make_receipt(
            spec,
            tier="mid",
            model=self.mid_model,
            prompt_tokens=budget.spent,
            completion_tokens=0,
            tokens_estimated=budget.estimated,
            counterfactual_tokens=cf.inline_est_tokens,
            counterfactual_model=self.top_model,
            profitable_estimate=gate.profitable,
            cache_read_tokens=budget.cache_read or None,
            cache_write_tokens=budget.cache_write or None,
        )
        if not verified:
            # Two different failures, kept apart. "The worker produced nothing" is a budget or a
            # provider fault; "the verifier refused it" is a judgement with a stage and a reason.
            # Collapsing them into one message is how a provider outage gets read as a model that
            # cannot follow a contract.
            if outcome is None:
                self._emit(
                    "worker_rejected",
                    text=f"stopped early ({cut_off})" if cut_off else "no output",
                    task_id=spec.task_id,
                    # `cut_off` is its own reason. Folding it into "no_output" hid the one case a
                    # user can act on — raise the budget — inside a string that also means "the
                    # provider broke", and the two want opposite responses.
                    reason=cut_off or "no_output",
                    detail=(
                        f"worker stopped early ({cut_off}) before reporting"
                        if cut_off
                        else "worker produced no output (provider error or empty answer)"
                    ),
                    tokens=budget.spent,
                )
            else:
                self._emit(
                    "worker_rejected", text=outcome.stage, task_id=spec.task_id,
                    reason="verifier", stage=outcome.stage, detail=outcome.detail,
                    tokens=budget.spent,
                )
            return None, receipt
        self._emit(
            "worker_verified",
            text=f"verified ({outcome.stage if outcome else 'inline'})",
            task_id=spec.task_id,
            stage=outcome.stage if outcome else "",
            # WHICH gates ran, so the card can name the check instead of implying all three. For
            # ordinary output this is ("schema",) alone — criteria needs `regex:` lines in a prose
            # `output_format`, and the spot check needs evidence refs that only exist above the
            # 8000-char cap. A badge reading "verificado · accepted" over a one-gate verdict is the
            # screen making a claim the data does not carry.
            checks_run=list(outcome.checks_run) if outcome else [],
            reasked=reasked,
            tokens=budget.spent,
            # The summary's SIZE, not the summary. This is a progress frame; the envelope carries
            # the text, and a fan-out of eight 8k summaries down a live channel is a firehose the
            # consumer then has to defend itself against.
            summary_chars=len(envelope.summary or ""),
            evidence_refs=list(envelope.evidence_refs or []),
            gaps=list(envelope.gaps or []),
        )
        return envelope, receipt

    def _run_inline_subtask(
        self, spec: TaskSpec, *, n_subtasks: int = 1
    ) -> tuple[ResultEnvelope | None, DelegationReceipt | None]:
        """Trivial subtask handled by the trusted top model directly — no worker, no
        verification (the top tier is the same one that synthesizes). The receipt is
        tier='top' with the DELEGATE counterfactual (what delegating this one would have cost —
        no orchestrator-context repetition to share), so `chimera delegations` shows the inline
        decision was audited, not hidden. ``n_subtasks`` is accepted for a uniform dispatch signature."""
        gate = estimate_profitability(spec, orchestrator_context_chars=24_000)
        result = self.gateway.complete(
            [Message(role="system", content=WORKER_SYSTEM),
             Message(role="user", content=spec.render())],
            model=self.top_model,
        )
        raw = result.content or ""
        tokens = (result.prompt_tokens or 0) + (result.completion_tokens or 0)
        estimated = tokens == 0
        if estimated:
            tokens = estimate_tokens(spec.render() + raw)
        receipt = make_receipt(
            spec,
            tier="top",
            model=self.top_model,
            prompt_tokens=tokens,
            completion_tokens=0,
            tokens_estimated=estimated,
            counterfactual_tokens=gate.delegate_est_tokens,  # what delegating would have cost
            counterfactual_model=self.mid_model,
            profitable_estimate=gate.profitable,
        )
        if not raw.strip():
            return None, receipt
        return build_envelope(spec, raw, self.store, status="ok"), receipt

    def _synthesize(
        self, task: str, envelopes: list[ResultEnvelope]
    ) -> tuple[str, int, bool]:
        """Top model over SUMMARIES ONLY; fusion only on real conflict (Self-MoA rule).

        Returns (answer, synth_tokens, estimated) — the tokens are metered as orchestrator overhead."""
        summaries = "\n\n".join(
            f"### {env.task_id}\n{env.summary}"
            + (f"\n(gaps: {'; '.join(env.gaps)})" if env.gaps else "")
            for env in envelopes
        )
        prompt = f"## Task\n{task}\n\n## Worker summaries\n{summaries}"
        recall = self._recall_block(task)
        if recall:
            prompt = f"## Prior knowledge (advisory)\n{recall}\n\n{prompt}"
        fusion = (
            self.fusion
            if self.config.fuse_final and self.fusion is not None and _conflicting(envelopes)
            else None
        )
        use_fusion = fusion is not None
        # Emitted here and not before the call in run_prepared, because `fused` is only
        # decided at this point — announcing it earlier would be announcing a guess.
        self._emit(
            "synthesizing", text=f"{len(envelopes)} summaries",
            envelopes=len(envelopes), fused=use_fusion,
        )
        synth_system = self._owned(_SYNTH_SYSTEM)
        if fusion is not None:
            _log.debug("envelopes conflict — engaging fusion for the final synthesis")
            result = fusion.complete(
                [Message(role="system", content=synth_system),
                 Message(role="user", content=prompt)]
            )
        else:
            result = self.gateway.complete(
                [Message(role="system", content=synth_system),
                 Message(role="user", content=prompt)],
                model=self.top_model,
            )
        tokens, estimated = _result_tokens(result, prompt + (result.content or ""))
        return result.content, tokens, estimated

    def _fallback(
        self, task: str, shape: TaskShape, *, reason: str, code: str = "shape"
    ) -> HierarchyResult:
        """Single-agent path (top model, one shot) — the always-correct default.

        The decision itself is audited: a receipt row records the fallback with
        the counterfactual so `chimera delegations` shows why nothing was saved.

        ``code`` is ``reason`` in a form a consumer can branch on. ``reason`` is prose written for
        a log line and it has already changed shape once; a UI matching on its text would be a UI
        that breaks when someone rewords a debug message. The four values are ``shape``,
        ``unprofitable``, ``decompose_failed`` and ``workers_failed``.
        """
        _log.debug("falling back to single-agent path (%s)", reason)
        # Emitted from the single site every fallback passes through, so no route out of the
        # hierarchy can forget to say it happened.
        self._emit("fell_back", text=reason, shape=shape, reason=code)
        # This path answers the user directly, so the owner's instructions belong here even more
        # plainly than at synthesis — and it was sending no system message at all.
        messages: list[MessageLike] = [Message(role="user", content=task)]
        if self.identity:
            messages.insert(0, Message(role="system", content=self.identity))
        result = self.gateway.complete(messages, model=self.top_model)
        tokens = (result.prompt_tokens or 0) + (result.completion_tokens or 0)
        estimated = tokens == 0
        if estimated:
            tokens = estimate_tokens(task + (result.content or ""))
        receipt = make_receipt(
            TaskSpec(task_id=f"fallback-{uuid.uuid4().hex[:8]}", objective=task, context=reason),
            tier="top",
            model=self.top_model,
            prompt_tokens=result.prompt_tokens if not estimated else tokens,
            completion_tokens=result.completion_tokens if not estimated else 0,
            tokens_estimated=estimated,
            profitable_estimate=False,
        )
        if self.receipts_path is not None:
            append_delegation(self.receipts_path, receipt)
        self._record_outcome(task, result.content)
        return self._finish(
            HierarchyResult(
                answer=result.content,
                shape=shape,
                receipts=[receipt],
                fell_back=True,
                total_tokens=tokens,
            )
        )

    def _finish(self, result: HierarchyResult) -> HierarchyResult:
        """The one place a run ends, so ``done`` cannot be missed on a path someone adds later."""
        self._emit(
            "done",
            text="fell back" if result.fell_back else "synthesised",
            shape=result.shape,
            fell_back=result.fell_back,
            cancelled=result.cancelled,
            envelopes=len(result.envelopes),
            receipts=len(result.receipts),
            total_tokens=result.total_tokens,
            counterfactual_tokens=result.counterfactual_tokens,
            # The answer travels on `done` and nowhere else: it is the one frame a consumer that
            # missed the stream still needs in full.
            answer=result.answer,
        )
        return result

    def _recall_block(self, task: str) -> str:
        """Advisory prior-knowledge for the top model (M19-A4 read half): retrieved skill cards +
        recalled memory facts, sanitized. Empty without an evolution context or when nothing matches.
        Injected ONLY into the top model's synthesis prompt — never the byte-identical worker prefix.
        """
        if self.evolution is None:
            return ""
        parts: list[str] = []
        cards = self.evolution.cards
        if cards is not None:
            ctx = cards.card_context(task)
            if ctx:
                parts.append(ctx)
        search = getattr(self.evolution.memory, "search", None)
        if callable(search):
            try:
                hits = search(task, k=5)
            except Exception as exc:  # noqa: BLE001 — recall is advisory, never fail the run
                _log.debug("hierarchy memory readback failed: %s", exc)
                hits = []
            facts = "\n".join(
                f"- {getattr(h, 'content', '')}"
                for h in (hits or [])
                if str(getattr(h, "content", "")).strip()
            )
            if facts:
                parts.append("Relevant prior facts:\n" + facts)
        if not parts:
            return ""
        from chimera.governance.sanitize import sanitize_untrusted

        return sanitize_untrusted("\n\n".join(parts))

    def _record_outcome(self, task: str, answer: str) -> None:
        """Record the run to the shared evolution context (M19-A4 write half): an experience lesson
        + skill-card credit. Never distils a skill — a fan-out has no verify-or-revert signal, so it
        accrues telemetry only (the honest gate)."""
        if self.evolution is not None:
            self.evolution.record_external(task, answer, success=bool(answer and answer.strip()))

    def _owned(self, system: str) -> str:
        """``system`` with the owner's instructions in front of it, or unchanged when there are none.

        In front rather than appended: the stage prompt is the more specific instruction, and the
        convention everywhere else in the stack is that the closer-to-the-task text comes last.
        """
        return f"{self.identity}\n\n{system}" if self.identity else system

    def _complete_top(self, system: str, user: str) -> CompletionResult:
        return self.gateway.complete(
            [Message(role="system", content=system), Message(role="user", content=user)],
            model=self.top_model,
            temperature=0.2,
        )

    def _ask_top(self, system: str, user: str) -> str:
        return self._complete_top(system, user).content


def _shared_counterfactual(spec: TaskSpec, n_subtasks: int) -> ProfitEstimate:
    """Per-subtask inline counterfactual that shares the orchestrator context across the D subtasks.

    A single inline agent loads the ~24k-char orchestrator context ONCE for the whole task; charging
    the full context in every subtask's counterfactual would over-count it (D-1)x when summed, and
    inflate the reported saving. So the receipt's counterfactual gets a 1/D share of that context.
    (The per-subtask profitability veto keeps the full context — that's a genuinely per-subtask
    question: "if I don't delegate THIS one, I pay full context + this subtask".)"""
    share = max(1, 24_000 // max(1, n_subtasks))
    return estimate_profitability(spec, orchestrator_context_chars=share)


def _result_tokens(result: CompletionResult, fallback_text: str) -> tuple[int, bool]:
    """Measured (prompt+completion) tokens, or a chars/4 estimate when the provider reported none.

    Returns (tokens, estimated) so the flag can propagate onto the receipt — an estimate must never
    masquerade as a measurement (the receipts' honesty rule)."""
    tokens = (result.prompt_tokens or 0) + (result.completion_tokens or 0)
    if tokens == 0:
        return estimate_tokens(fallback_text), True
    return tokens, False


def _conflicting(envelopes: list[ResultEnvelope]) -> bool:
    """Cheap lexical disagreement check between worker summaries (no model call).

    Two signals must BOTH hold for a pair (conservative — fusion is the expensive
    path): (a) a contradiction marker or a self-reported gap in at least one of the
    two summaries, AND (b) real term overlap between them (Jaccard >= 0.25), so
    they're discussing the same thing and a disagreement is meaningful rather than
    two unrelated subtasks. A genuine contradiction carrying none of the markers
    cannot be caught without a model call — that is the honest lexical ceiling.
    """
    if len(envelopes) < 2:
        return False
    markers = ("however", "contradict", "instead", "disagree", "but the", "not the")

    def terms(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]{4,}", text.lower()))

    term_sets = [terms(env.summary) for env in envelopes]
    flagged = [
        any(m in env.summary.lower() for m in markers) or bool(env.gaps)
        for env in envelopes
    ]
    for i in range(len(envelopes)):
        for j in range(i + 1, len(envelopes)):
            a, b = term_sets[i], term_sets[j]
            if not a or not b:
                continue
            if len(a & b) / len(a | b) >= 0.25 and (flagged[i] or flagged[j]):
                return True
    return False
