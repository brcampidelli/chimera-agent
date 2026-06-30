"""Cascade rubric evaluation (DailyReport, 2606.12871).

Evaluate an answer across ordered, importance-weighted dimensions — by default
**instruction-following → factuality → rationality** — as a *cascade*: a downstream
dimension is only scored if the upstream one clears its gate. This prevents meaningless
checks (e.g. fact-checking content that never followed the instruction) and yields an
interpretable per-dimension breakdown plus a single weighted score.

Each dimension's ``check`` is injected ((answer, task) -> 0..1), so the cascade logic is
fully testable; a model-backed judge can supply the real checks via :func:`model_judge`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

Check = Callable[[str, str], float]
JudgeFn = Callable[[str, str, str], float]  # (answer, task, criterion) -> 0..1


@dataclass
class Dimension:
    name: str
    weight: float
    check: Check
    gate: float = 0.5  # downstream dimensions run only if this scores >= gate


@dataclass
class RubricResult:
    scores: dict[str, float]
    overall: float
    stopped_at: str | None = None  # the dimension that gated the cascade, if any


def evaluate_cascade(answer: str, task: str, dimensions: list[Dimension]) -> RubricResult:
    """Score the dimensions in order; stop the cascade at the first to miss its gate."""
    total_weight = sum(d.weight for d in dimensions)
    scores: dict[str, float] = {}
    weighted = 0.0
    stopped_at: str | None = None
    for dim in dimensions:
        score = dim.check(answer, task)
        scores[dim.name] = score
        weighted += dim.weight * score
        if score < dim.gate:
            stopped_at = dim.name  # downstream dims are left unscored (contribute 0)
            break
    overall = weighted / total_weight if total_weight else 0.0
    return RubricResult(scores=scores, overall=overall, stopped_at=stopped_at)


def cascade_dimensions(judge: JudgeFn) -> list[Dimension]:
    """The default importance-weighted cascade, backed by a judge."""
    return [
        Dimension(
            "instruction_following",
            0.4,
            lambda a, t: judge(a, t, "Does the answer follow the instruction and stay in scope?"),
        ),
        Dimension(
            "factuality",
            0.4,
            lambda a, t: judge(a, t, "Are the claims in the answer factually correct?"),
        ),
        Dimension(
            "rationality",
            0.2,
            lambda a, t: judge(a, t, "Is the answer well-reasoned, coherent and useful?"),
        ),
    ]


_NUMBER = re.compile(r"(?:0?\.\d+|[01](?:\.0+)?)")


def model_judge(backend: object, model: str | None = None) -> JudgeFn:
    """A judge that asks a model to score a criterion in [0, 1]."""

    def judge(answer: str, task: str, criterion: str) -> float:
        from chimera.providers.gateway import Message

        prompt = (
            f"Task:\n{task}\n\nAnswer:\n{answer}\n\nCriterion: {criterion}\n"
            "Reply with ONLY a number from 0.0 (fails) to 1.0 (fully meets)."
        )
        raw = backend.complete(  # type: ignore[attr-defined]
            [Message(role="user", content=prompt)], model=model, temperature=0.0
        ).content
        match = _NUMBER.search(raw)
        return max(0.0, min(1.0, float(match.group(0)))) if match else 0.0

    return judge
