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
from typing import Literal

from chimera.orchestration.artifacts import ArtifactStore, build_envelope
from chimera.orchestration.budget import BudgetedBackend, BudgetExceeded, EffortPolicy, TokenBudget
from chimera.orchestration.envelope_verify import EnvelopeVerifier
from chimera.orchestration.receipts import (
    DelegationReceipt,
    append_delegation,
    estimate_profitability,
    estimate_tokens,
    make_receipt,
)
from chimera.orchestration.roles import Role, RoleAgent
from chimera.orchestration.spec import EffortBudget, ResultEnvelope, TaskSpec
from chimera.providers.gateway import Message, SupportsComplete
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


def classify_task(task: str) -> TaskShape:
    """DETERMINISTIC task-shape heuristic — never an LLM call (anti-scope rule).

    write-intent -> sequential_write; short single-question -> simple; read-heavy
    multi-part -> parallel_read. Biased toward falling back: the single-agent
    path is always correct, the hierarchy is an optimization.
    """
    low = task.lower()
    if any(marker in low for marker in _WRITE_MARKERS):
        return "sequential_write"
    is_read = any(marker in low for marker in _READ_MARKERS)
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
        fusion: SupportsComplete | None = None,
        receipts_path: Path | None = None,
        config: HierarchyConfig | None = None,
    ) -> None:
        self.gateway = gateway
        self.weak_model = weak_model
        self.mid_model = mid_model
        self.top_model = top_model
        self.store = store
        self.verifier = verifier or EnvelopeVerifier(
            store=store, backend=gateway, model=weak_model,
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
        probe = TaskSpec(task_id="probe", objective=task)
        estimate = estimate_profitability(
            probe, orchestrator_context_chars=len(task) * 8 + 24_000
        )
        if not estimate.profitable:
            return self._fallback(task, shape, reason="unprofitable estimate")

        specs = self.decompose(task)
        if not specs:
            return self._fallback(task, shape, reason="decomposition failed")
        return self.run_prepared(task, specs, shape=shape)

    def run_prepared(
        self, task: str, specs: list[TaskSpec], *, shape: TaskShape = "parallel_read"
    ) -> HierarchyResult:
        """Run with a caller-supplied decomposition (recipes know their own split —
        no top-model decompose call is spent)."""
        envelopes, receipts = self._dispatch(specs)
        if not envelopes:
            return self._fallback(task, shape, reason="all delegations failed")

        answer, synth_tokens = self._synthesize(task, envelopes)
        measured = sum(r.total_tokens for r in receipts) + synth_tokens
        counterfactual = sum(r.counterfactual_tokens or 0 for r in receipts) or None
        return HierarchyResult(
            answer=answer,
            shape=shape,
            envelopes=envelopes,
            receipts=receipts,
            fell_back=False,
            total_tokens=measured,
            counterfactual_tokens=counterfactual,
        )

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
        raw = self._ask_top(_DECOMPOSE_SYSTEM, task)
        specs = self._parse_specs(raw)
        if specs is None:  # one bounded repair attempt
            raw = self._ask_top(
                _DECOMPOSE_SYSTEM,
                f"{task}\n\nYour previous reply was not a valid JSON array. "
                "Reply with ONLY the JSON array, no prose.",
            )
            specs = self._parse_specs(raw)
        if not specs:
            return []
        cap = self.config.effort.workers_for("parallel_read", len(specs))
        return specs[:cap]

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
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as pool:
            results = list(pool.map(self._run_one, specs))
        envelopes = [env for env, _ in results if env is not None]
        receipts = [rec for _, rec in results if rec is not None]
        if self.receipts_path is not None:
            for receipt in receipts:
                append_delegation(self.receipts_path, receipt)
        return envelopes, receipts

    def _run_one(self, spec: TaskSpec) -> tuple[ResultEnvelope | None, DelegationReceipt | None]:
        # Per-subtask gate: a trivially small spec is cheaper answered inline by the
        # trusted top model than delegated through the worker+verify machinery.
        if (
            self.config.inline_below_spec_tokens
            and estimate_tokens(spec.render()) < self.config.inline_below_spec_tokens
        ):
            return self._run_inline_subtask(spec)
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
                    if self.verifier.verify(spec, candidate).passed:
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
            counterfactual_tokens=gate.inline_est_tokens,
            counterfactual_model=self.top_model,
            profitable_estimate=gate.profitable,
        )
        if not verified:
            return None, receipt
        return envelope, receipt

    def _run_inline_subtask(
        self, spec: TaskSpec
    ) -> tuple[ResultEnvelope | None, DelegationReceipt | None]:
        """Trivial subtask handled by the trusted top model directly — no worker, no
        verification (the top tier is the same one that synthesizes). The receipt is
        tier='top' with the delegation counterfactual, so `chimera delegations` shows
        the inline decision was audited, not hidden."""
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

    def _synthesize(self, task: str, envelopes: list[ResultEnvelope]) -> tuple[str, int]:
        """Top model over SUMMARIES ONLY; fusion only on real conflict (Self-MoA rule)."""
        summaries = "\n\n".join(
            f"### {env.task_id}\n{env.summary}"
            + (f"\n(gaps: {'; '.join(env.gaps)})" if env.gaps else "")
            for env in envelopes
        )
        prompt = f"## Task\n{task}\n\n## Worker summaries\n{summaries}"
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
        tokens = (result.prompt_tokens or 0) + (result.completion_tokens or 0)
        if tokens == 0:
            tokens = estimate_tokens(prompt + (result.content or ""))
        return result.content, tokens

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
        return HierarchyResult(
            answer=result.content,
            shape=shape,
            receipts=[receipt],
            fell_back=True,
            total_tokens=tokens,
        )

    def _ask_top(self, system: str, user: str) -> str:
        return self.gateway.complete(
            [Message(role="system", content=system), Message(role="user", content=user)],
            model=self.top_model,
            temperature=0.2,
        ).content


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
