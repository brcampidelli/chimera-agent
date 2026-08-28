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
from dataclasses import dataclass, field

#: ``None`` means **the judge could not be read**, which is not the same as scoring zero. A check
#: that cannot answer is an apparatus failure, and folding it into the lowest score makes "I could
#: not read the reply" indistinguishable from "I read it and it fails" — the reading that, under
#: verify-or-revert, throws away correct work.
Check = Callable[[str, str], float | None]
JudgeFn = Callable[[str, str, str], float | None]  # (answer, task, criterion) -> 0..1 or None


@dataclass
class Dimension:
    name: str
    weight: float
    check: Check
    gate: float = 0.5  # downstream dimensions run only if this scores >= gate


@dataclass
class RubricResult:
    scores: dict[str, float]
    #: ``None`` when **nothing** could be scored, so there is no verdict here at all. Deliberately
    #: not 0.0: the one production caller compares this against a threshold, and `None >= 0.6`
    #: raises where `0.0 >= 0.6` quietly reverts the work. mypy makes every caller narrow it, which
    #: puts the guard in the flow instead of in a convention somebody has to remember.
    overall: float | None
    stopped_at: str | None = None  # the dimension that gated the cascade, if any
    #: Dimensions whose judge could not be read. They are out of BOTH sums — scoring them zero
    #: would punish the answer for the judge's failure, and leaving them in the denominator would
    #: do the same thing more quietly.
    unscored: list[str] = field(default_factory=list)


def evaluate_cascade(answer: str, task: str, dimensions: list[Dimension]) -> RubricResult:
    """Score the dimensions in order; stop the cascade at the first to miss its gate."""
    scores: dict[str, float] = {}
    unscored: list[str] = []
    weighted = 0.0
    stopped_at: str | None = None
    for dim in dimensions:
        score = dim.check(answer, task)
        if score is None:
            # An unreadable judge must not gate the cascade either: stopping here would deny the
            # remaining dimensions their say for a reason that says nothing about the answer.
            unscored.append(dim.name)
            continue
        scores[dim.name] = score
        weighted += dim.weight * score
        if score < dim.gate:
            stopped_at = dim.name  # downstream dims are left unscored (contribute 0)
            break
    # The denominator is every dimension EXCEPT the unreadable ones. Dimensions the cascade gated
    # away stay in it on purpose — contributing zero out of their full weight is the penalty a gate
    # exists to apply. Only a judge that failed to answer leaves the denominator, because its
    # absence is the apparatus's fault and charging the answer for it is the bug this fixes.
    unreadable = set(unscored)
    denominator = sum(d.weight for d in dimensions if d.name not in unreadable)
    overall = weighted / denominator if denominator else None
    return RubricResult(
        scores=scores, overall=overall, stopped_at=stopped_at, unscored=unscored
    )


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
    """A judge that asks a model to score a criterion in [0, 1], or ``None`` if it did not answer.

    The ``None`` is the point. This used to return ``0.0`` whenever the reply held no number — a
    refusal, an empty completion, a provider error string, prose in the wrong language — so a judge
    that could not read produced the same output as a judge that read and failed the work. Under
    verify-or-revert that reading deletes correct work, and nothing anywhere said it had happened.
    Distinguishing the two is what lets the caller retry, abstain, or drop the item from the
    denominator instead of guessing.
    """

    def judge(answer: str, task: str, criterion: str) -> float | None:
        from chimera.providers.gateway import Message

        prompt = (
            f"Task:\n{task}\n\nAnswer:\n{answer}\n\nCriterion: {criterion}\n"
            "Reply with ONLY a number from 0.0 (fails) to 1.0 (fully meets)."
        )
        raw = backend.complete(  # type: ignore[attr-defined]
            [Message(role="user", content=prompt)], model=model, temperature=0.0
        ).content
        match = _NUMBER.search(raw)
        return max(0.0, min(1.0, float(match.group(0)))) if match else None

    return judge
