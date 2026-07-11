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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from chimera.evolution.context import EvolutionContext

from chimera.orchestration.artifacts import ArtifactStore, build_envelope
from chimera.orchestration.budget import BudgetedBackend, BudgetExceeded, EffortPolicy, TokenBudget
from chimera.orchestration.envelope_verify import EnvelopeVerifier
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
from chimera.providers.gateway import CompletionResult, Message, SupportsComplete
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
    "real contradictions honestly, and do not invent findings absent from the summaries."
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
_FILE_REF = re.compile(r"\b[\w-]+\.(?:md|txt|pdf|csv|tsv|json|ya?ml|docx?|html?|log|rst|ipynb)\b", re.I)
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
class HierarchyResult:
    answer: str
    shape: TaskShape
    envelopes: list[ResultEnvelope] = field(default_factory=list)
    receipts: list[DelegationReceipt] = field(default_factory=list)
    fell_back: bool = False
    total_tokens: int | None = None
    counterfactual_tokens: int | None = None


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
    ) -> None:
        self.gateway = gateway
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

    # ------------------------------------------------------------------ public

    def run(self, task: str) -> HierarchyResult:
        shape = classify_task(task)

        # Guard 1 — shape (Cognition rule): write/simple tasks stay single-agent.
        if shape != "parallel_read":
            return self._fallback(task, shape, reason=f"shape={shape}")

        # Guard 2 — global profitability: don't delegate when inline is cheaper.
        # EXCEPTION: 2+ distinct sources is the measured guaranteed-gain region — the
        # (D-1)/D sweep proves isolation wins there — so the crude blank-context estimate
        # is not allowed to veto it.
        sources = count_sources(task)
        if sources < 2:
            probe = TaskSpec(task_id="probe", objective=task)
            estimate = estimate_profitability(
                probe, orchestrator_context_chars=len(task) * 8 + 24_000
            )
            if not estimate.profitable:
                return self._fallback(task, shape, reason="unprofitable estimate")
        else:
            _log.debug("%d distinct sources -> guaranteed-gain region, skipping profit veto", sources)

        specs, decompose_tokens, decompose_estimated = self._decompose_metered(task)
        if not specs:
            return self._fallback(task, shape, reason="decomposition failed")
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
        envelopes, receipts = self._dispatch(specs)
        if not envelopes:
            return self._fallback(task, shape, reason="all delegations failed")

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
        return HierarchyResult(
            answer=answer,
            shape=shape,
            envelopes=envelopes,
            receipts=all_receipts,
            fell_back=False,
            total_tokens=measured,
            counterfactual_tokens=counterfactual,
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

    def dry_run(self, task: str) -> dict[str, object]:
        """Classification + decomposition + profitability estimate — zero worker spend."""
        shape = classify_task(task)
        out: dict[str, object] = {"shape": shape}
        probe = TaskSpec(task_id="probe", objective=task)
        estimate = estimate_profitability(
            probe, orchestrator_context_chars=len(task) * 8 + 24_000
        )
        out["profitable_estimate"] = estimate.profitable
        out["estimate_margin"] = estimate.margin
        if shape == "parallel_read":
            specs = self.decompose(task)
            out["subtasks"] = [s.objective for s in specs]
            out["workers"] = self.config.effort.workers_for(shape, len(specs))
            out["budget_per_worker"] = self.config.effort.budget_for(shape)
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

        # The aggregate inline counterfactual loads the orchestrator context ONCE for the whole task,
        # not once per subtask — so each receipt's counterfactual charges only a 1/D share of it, or
        # summing D rows would over-count the context (D-1)x and inflate the reported saving.
        n = max(1, len(specs))
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as pool:
            results = list(pool.map(partial(self._run_one, n_subtasks=n), specs))
        envelopes = [env for env, _ in results if env is not None]
        receipts = [rec for _, rec in results if rec is not None]
        if self.receipts_path is not None:
            for receipt in receipts:
                append_delegation(self.receipts_path, receipt)
        return envelopes, receipts

    def _run_one(
        self, spec: TaskSpec, *, n_subtasks: int = 1
    ) -> tuple[ResultEnvelope | None, DelegationReceipt | None]:
        # Per-subtask gate: a trivially small spec is cheaper answered inline by the
        # trusted top model than delegated through the worker+verify machinery.
        if (
            self.config.inline_below_spec_tokens
            and estimate_tokens(spec.render()) < self.config.inline_below_spec_tokens
        ):
            return self._run_inline_subtask(spec, n_subtasks=n_subtasks)
        budget = TokenBudget(spec.effort.max_tokens)
        backend = BudgetedBackend(self.gateway, budget, mode="hard")
        worker = RoleAgent(
            Role("worker", WORKER_SYSTEM, model=self.mid_model),
            backend,
            max_steps=spec.effort.max_steps,
        )
        # Recorded on the receipt for audit; the ENFORCING gate is the whole-task one
        # in run() (Guard 2). Per-subtask inline execution is future work.
        gate = estimate_profitability(spec, orchestrator_context_chars=24_000)
        # The receipt's counterfactual shares the orchestrator context across the D subtasks (loaded
        # once inline), so the summed aggregate isn't inflated; the gate above keeps full context.
        cf = _shared_counterfactual(spec, n_subtasks)
        try:
            raw = worker.act(spec.render())
        except BudgetExceeded:
            raw = ""
        except Exception as exc:  # noqa: BLE001 -- a provider error must not nuke the batch
            _log.warning("worker %s failed: %s", spec.task_id, exc)
            raw = ""
        envelope = build_envelope(
            spec, raw, self.store,
            status="ok" if raw.strip() else "failed",
            gaps=[] if raw.strip() else ["worker produced no output (budget or provider error)"],
        )
        # A result is trustworthy input to the synthesizer ONLY if it passes
        # verification. If the bounded re-ask also fails, the envelope is dropped
        # (audited via the receipt) rather than folded in as an unverified claim.
        verified = False
        if raw.strip():
            outcome = self.verifier.verify(spec, envelope)
            verified = outcome.passed
            if not verified:
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
            return None, receipt
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
        if self.config.fuse_final and self.fusion is not None and _conflicting(envelopes):
            _log.debug("envelopes conflict — engaging fusion for the final synthesis")
            result = self.fusion.complete(
                [Message(role="system", content=_SYNTH_SYSTEM),
                 Message(role="user", content=prompt)]
            )
        else:
            result = self.gateway.complete(
                [Message(role="system", content=_SYNTH_SYSTEM),
                 Message(role="user", content=prompt)],
                model=self.top_model,
            )
        tokens, estimated = _result_tokens(result, prompt + (result.content or ""))
        return result.content, tokens, estimated

    def _fallback(self, task: str, shape: TaskShape, *, reason: str) -> HierarchyResult:
        """Single-agent path (top model, one shot) — the always-correct default.

        The decision itself is audited: a receipt row records the fallback with
        the counterfactual so `chimera delegations` shows why nothing was saved.
        """
        _log.debug("falling back to single-agent path (%s)", reason)
        result = self.gateway.complete(
            [Message(role="user", content=task)], model=self.top_model
        )
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
        return HierarchyResult(
            answer=result.content,
            shape=shape,
            receipts=[receipt],
            fell_back=True,
            total_tokens=tokens,
        )

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
