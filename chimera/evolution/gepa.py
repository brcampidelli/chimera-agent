"""GEPA — reflective, Pareto-guided prompt evolution for learned skills.

Plain refinement (``SkillEvolver.refine``) mutates a template once from failure examples. GEPA
closes the loop: it evaluates a candidate template on a *set* of graded task instances, reflects
in natural language on a failing rollout to propose an improved template, and keeps a **Pareto
frontier** of candidates rather than only the best-on-average — the paper's key trick, since a
candidate that wins on a few hard instances carries signal a greedy average would throw away. It
runs under a rollout budget and returns the best template plus the frontier.

Both model-touching seams are injected — an ``Executor`` that runs a template on an input and a
``Reflector`` that proposes a mutation from feedback — so the whole search is testable without a
network. The defaults wrap the provider gateway.

This is a native reimplementation of the GEPA algorithm (Genetic-Pareto reflective evolution) over
Chimera's own gateway, not a wrapper around an external package — the same choice made for the
fusion engine. Honest limit: minibatch screening (evaluate a mutation on a small batch before
paying for the full set) is a real GEPA efficiency we have not added yet; every candidate here is
scored on the full instance set.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from chimera.evolution.learned_skill import LearnedSkill
from chimera.providers.gateway import Message, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("evolution.gepa")

Scorer = Callable[[str], float]  # maps a produced output to a score in [0, 1]

_EXECUTE_SYSTEM = (
    "Follow the instruction exactly and output only the requested result, with no preamble."
)
_REFLECT_SYSTEM = (
    "You improve an instruction template used by a weaker model. You are given the current template "
    "and feedback describing a case where it underperformed: the filled inputs, the output it "
    "produced, and why that output scored poorly. Diagnose the weakness and rewrite the template so "
    "that kind of case succeeds, keeping every {placeholder} variable intact. Reply with ONLY the "
    "improved template text — no preamble, no explanation."
)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


@dataclass(frozen=True)
class TaskInstance:
    """One graded task: inputs that fill the template + a scorer over the produced output."""

    input: dict[str, str]
    scorer: Scorer


@dataclass
class Candidate:
    """A template variant with its per-instance scores (and the outputs that earned them)."""

    template: str
    scores: tuple[float, ...]
    outputs: tuple[str, ...] = ()
    parent: int | None = None

    @property
    def mean(self) -> float:
        return sum(self.scores) / len(self.scores) if self.scores else 0.0


@dataclass
class GEPAResult:
    best_template: str
    best_mean: float
    seed_mean: float
    candidates: list[Candidate] = field(default_factory=list)
    frontier: list[int] = field(default_factory=list)  # indices of non-dominated candidates
    rollouts: int = 0

    @property
    def improved(self) -> bool:
        return self.best_mean > self.seed_mean


class SupportsExecute(Protocol):
    """Runs a filled template and returns the model's output."""

    def run(self, template: str, task_input: dict[str, str]) -> str: ...


class SupportsReflect(Protocol):
    """Proposes an improved template given the current one and failure feedback."""

    def propose(self, template: str, feedback: str) -> str: ...


class BackendExecutor:
    """Default executor: fill the template and ask the model (no code execution)."""

    def __init__(self, backend: SupportsComplete, model: str | None = None) -> None:
        self.backend = backend
        self.model = model

    def run(self, template: str, task_input: dict[str, str]) -> str:
        try:
            prompt = template.format(**task_input)
        except (KeyError, IndexError) as exc:
            return f"[template error: {exc}]"  # scores low, never crashes the search
        return self.backend.complete(
            [Message(role="system", content=_EXECUTE_SYSTEM), Message(role="user", content=prompt)],
            model=self.model,
            temperature=0.0,
        ).content


class BackendReflector:
    """Default reflector: ask the model to rewrite the template from failure feedback."""

    def __init__(self, backend: SupportsComplete, model: str | None = None) -> None:
        self.backend = backend
        self.model = model

    def propose(self, template: str, feedback: str) -> str:
        raw = self.backend.complete(
            [
                Message(role="system", content=_REFLECT_SYSTEM),
                Message(role="user", content=f"Current template:\n{template}\n\nFeedback:\n{feedback}"),
            ],
            model=self.model,
            temperature=0.4,
        ).content
        return raw.strip() or template  # an empty rewrite degrades to the original


def _dominates(a: Candidate, b: Candidate) -> bool:
    """True if a is >= b on every instance and strictly better on at least one (Pareto)."""
    pairs = list(zip(a.scores, b.scores, strict=True))
    return all(x >= y for x, y in pairs) and any(x > y for x, y in pairs)


def _frontier(candidates: list[Candidate]) -> list[int]:
    return [
        i
        for i, a in enumerate(candidates)
        if not any(_dominates(b, a) for j, b in enumerate(candidates) if j != i)
    ]


def _pareto_pool(candidates: list[Candidate]) -> list[int]:
    """Indices of candidates that top at least one instance — GEPA's diversity-preserving pool.

    Selecting the next parent from here (not the best-on-average) keeps a candidate that wins only
    on a few hard instances in the running, which is exactly the signal a greedy search discards.
    """
    n = len(candidates[0].scores)
    best = [max(c.scores[i] for c in candidates) for i in range(n)]
    winners = [
        idx for idx, c in enumerate(candidates) if any(c.scores[i] >= best[i] for i in range(n))
    ]
    return winners or list(range(len(candidates)))


class GEPAOptimizer:
    """Reflective Pareto search over prompt templates under a rollout budget."""

    def __init__(self, executor: SupportsExecute, reflector: SupportsReflect, *, max_stall: int = 5) -> None:
        self.executor = executor
        self.reflector = reflector
        self.max_stall = max_stall

    def _evaluate(
        self, template: str, instances: list[TaskInstance]
    ) -> tuple[tuple[float, ...], tuple[str, ...]]:
        outputs = tuple(self.executor.run(template, inst.input) for inst in instances)
        scores = tuple(_clamp(inst.scorer(out)) for inst, out in zip(instances, outputs, strict=True))
        return scores, outputs

    def _feedback(self, cand: Candidate, instances: list[TaskInstance]) -> str | None:
        """Concrete feedback from the candidate's worst instance, or None if it is already perfect."""
        worst = min(range(len(instances)), key=lambda i: cand.scores[i])
        if cand.scores[worst] >= 1.0:
            return None
        filled = ", ".join(f"{k}={v}" for k, v in instances[worst].input.items())
        produced = cand.outputs[worst] if cand.outputs else "(unknown)"
        return (
            f"Inputs: {filled}\nOutput produced: {produced}\n"
            f"This scored {cand.scores[worst]:.2f} out of 1.0 — too low. Rewrite the template so "
            "this kind of case succeeds."
        )

    def optimize(
        self,
        seed_template: str,
        instances: list[TaskInstance],
        *,
        budget: int = 20,
        seed: int = 0,
    ) -> GEPAResult:
        """Evolve ``seed_template`` against ``instances``; return the best template and the frontier."""
        if not instances:
            raise ValueError("GEPA needs at least one task instance")
        rng = random.Random(seed)
        seed_scores, seed_outputs = self._evaluate(seed_template, instances)
        candidates = [Candidate(seed_template, seed_scores, seed_outputs, parent=None)]
        seen = {seed_template}
        rollouts = len(instances)
        stall = 0
        while rollouts + len(instances) <= budget and stall < self.max_stall:
            parent_idx = rng.choice(_pareto_pool(candidates))
            parent = candidates[parent_idx]
            feedback = self._feedback(parent, instances)
            if feedback is None:  # this parent is perfect — nothing to reflect on
                stall += 1
                continue
            child_template = self.reflector.propose(parent.template, feedback)
            if child_template in seen:  # no new direction; don't spend rollouts re-scoring it
                stall += 1
                continue
            seen.add(child_template)
            stall = 0
            child_scores, child_outputs = self._evaluate(child_template, instances)
            rollouts += len(instances)
            candidates.append(Candidate(child_template, child_scores, child_outputs, parent=parent_idx))
        best = max(range(len(candidates)), key=lambda i: candidates[i].mean)
        _log.debug(
            "gepa: %d candidates, best mean %.3f vs seed %.3f in %d rollouts",
            len(candidates),
            candidates[best].mean,
            candidates[0].mean,
            rollouts,
        )
        return GEPAResult(
            best_template=candidates[best].template,
            best_mean=candidates[best].mean,
            seed_mean=candidates[0].mean,
            candidates=candidates,
            frontier=_frontier(candidates),
            rollouts=rollouts,
        )


def _bump(version: str) -> str:
    parts = version.split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except (ValueError, IndexError):
        return version
    return ".".join(parts)


def optimize_template(
    backend: SupportsComplete,
    seed_template: str,
    instances: list[TaskInstance],
    *,
    model: str | None = None,
    budget: int = 20,
    seed: int = 0,
) -> GEPAResult:
    """Convenience wiring: run GEPA with the gateway-backed default executor and reflector."""
    optimizer = GEPAOptimizer(BackendExecutor(backend, model), BackendReflector(backend, model))
    return optimizer.optimize(seed_template, instances, budget=budget, seed=seed)


def evolve_skill(
    backend: SupportsComplete,
    skill: LearnedSkill,
    instances: list[TaskInstance],
    *,
    model: str | None = None,
    budget: int = 20,
    seed: int = 0,
) -> tuple[LearnedSkill, GEPAResult]:
    """GEPA-evolve a skill's ``prompt_template``; return an improved copy only if it beats the seed.

    Returns ``(skill, result)`` unchanged when the search finds no lift — the same keep-or-discard
    discipline as the rest of the evolution engine: a mutation that does not measurably help is not
    adopted.
    """
    result = optimize_template(
        backend, skill.prompt_template, instances, model=model, budget=budget, seed=seed
    )
    if not result.improved:
        return skill, result
    improved = LearnedSkill(
        name=skill.name,
        description=skill.description,
        prompt_template=result.best_template,
        version=_bump(skill.version),
        trigger=skill.trigger,
        do=skill.do,
        avoid=skill.avoid,
        check=skill.check,
        risk=skill.risk,
        triggers=list(skill.triggers),
        kind=skill.kind,
        status=skill.status,
        provenance=skill.provenance,
        backend=backend,
        model=model,
    )
    return improved, result
