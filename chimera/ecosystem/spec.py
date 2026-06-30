"""Self-optimizable agent spec + LLM-guided meta-search (OpenJarvis, 2605.17172).

The agent's behaviour is decomposed into a few **editable primitives** (an
:class:`AgentSpec`), so the whole system can be optimized as one unit instead of tuning
components in isolation. :func:`search_spec` runs the OpenJarvis loop: a proposer (an LLM
diagnosing failures) suggests a *coordinated* edit across primitives, the spec is
re-scored, and the edit is kept only on **non-regression** — the gate that keeps the
search from drifting backwards. Scorer and proposer are injected, so the search is fully
testable; :func:`model_proposer` supplies the LLM-guided one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

_SPEC_FIELDS = {"model", "system_prompt", "max_steps", "fusion_panel", "memory_k"}


@dataclass
class AgentSpec:
    """The editable primitives of the agent, optimizable together."""

    model: str | None = None  # Intelligence
    system_prompt: str = ""  # Agents (reasoning loop)
    max_steps: int = 8  # Engine (runtime budget)
    fusion_panel: list[str] = field(default_factory=list)  # Intelligence diversity
    memory_k: int = 3  # Tools & Memory

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentSpec:
        return cls(**{k: v for k, v in data.items() if k in _SPEC_FIELDS})


Scorer = Callable[[AgentSpec], float]
Proposer = Callable[[AgentSpec, float], AgentSpec]


@dataclass
class SearchStep:
    spec: AgentSpec
    score: float
    accepted: bool


@dataclass
class SpecSearchResult:
    best: AgentSpec
    best_score: float
    history: list[SearchStep]


def search_spec(
    initial: AgentSpec, scorer: Scorer, proposer: Proposer, *, rounds: int = 3
) -> SpecSearchResult:
    """Propose → evaluate → keep-on-non-regression, for ``rounds`` rounds."""
    best = initial
    best_score = scorer(initial)
    history = [SearchStep(initial, best_score, True)]
    for _ in range(max(0, rounds)):
        candidate = proposer(best, best_score)
        score = scorer(candidate)
        accepted = score >= best_score  # non-regression acceptance gate
        history.append(SearchStep(candidate, score, accepted))
        if score > best_score:  # advance only on a strict improvement
            best, best_score = candidate, score
    return SpecSearchResult(best=best, best_score=best_score, history=history)


def model_proposer(backend: object, model: str | None = None) -> Proposer:
    """A proposer that asks a model to diagnose the spec and emit a coordinated edit."""

    def propose(spec: AgentSpec, score: float) -> AgentSpec:
        from chimera.providers.gateway import Message

        prompt = (
            f"Current agent spec (JSON):\n{json.dumps(spec.to_dict())}\n\n"
            f"Current benchmark score: {score:.3f}\n"
            "Diagnose likely weaknesses and propose an improved spec. Reply with ONLY a JSON "
            "object using the same keys (model, system_prompt, max_steps, fusion_panel, memory_k)."
        )
        raw = backend.complete(  # type: ignore[attr-defined]
            [Message(role="user", content=prompt)], model=model, temperature=0.3
        ).content
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return spec
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return spec
        return AgentSpec.from_dict({**spec.to_dict(), **data})  # merge the edit onto current

    return propose
