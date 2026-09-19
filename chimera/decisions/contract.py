"""The questions, the answer, the backend seam and the :class:`Decider` that joins them to a map.

Every question reduces to a :class:`Choice` — a key, the framing, and a closed set of options, with
the subset of options whose probability is the question's ``p``. A :class:`Noul` is a Choice over
``yes``/``no`` with ``p = P(yes)``; a :class:`Score` is a Choice over ordered levels whose reading
is an expectation. Backends answer the Choice; the Decider keys the calibration map on the
*decision* the caller names (``"governance.danger"``, ``"verifier.claim_true"``), the backend, the
model and a hash of the instrument — the fixed text around the state — so a map fitted on the bench's
wording never silently applies to a reworded question (the §2aa/§2ad failure: same numbers, different
instrument).

A backend that raises is a **halt**, never an answer: the Answer carries ``halt`` and no ``p``, and
the caller keeps its other layers. The rules-and-ledger path of the kernel does not go away because a
model server is down.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from chimera.decisions.calibration import CalibrationMaps, prompt_hash

MAX_OPTIONS = 255


@dataclass(frozen=True)
class Choice:
    """A question with a closed set of options — the primitive every other kind reduces to."""

    key: str
    """The question's name: the JSON key the model writes, the key in a Decisions request."""
    instructions: str
    """The framing — the system text of the model backends, the ``instructions`` of a Decisions
    request. A backend renders it verbatim, so a map fitted on one wording is keyed on it."""
    options: tuple[str, ...]
    """At least two, distinct after case-folding, at most 255; rendered in this order."""
    criteria: dict[str, str] = field(default_factory=dict)
    """Option → what it means. Rendered as one line per option after the instructions when given;
    empty keeps the instructions as the whole framing (what the bench's judge prompt already is)."""
    event: tuple[str, ...] = ()
    """The options whose probability is the question's ``p`` — ``("BLOCK", "REVIEW")`` reads
    "dangerous" off a verdict. Empty: the answer has shares (where the backend gives them) and a
    choice, and no ``p`` — nothing to calibrate."""
    event_name: str = ""
    """What ``p`` is the probability *of*, for the backend that asks the model to write it:
    ``p_dangerous``. Defaults to the key."""

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("a question needs a key")
        if not 2 <= len(self.options) <= MAX_OPTIONS:
            raise ValueError(f"a Choice needs 2–{MAX_OPTIONS} options, got {len(self.options)}")
        folded = [o.strip().casefold() for o in self.options]
        if len(set(folded)) != len(folded) or any(not o for o in folded):
            raise ValueError("options must be distinct and non-empty")
        unknown = [e for e in self.event if e not in self.options]
        if unknown:
            raise ValueError(f"event names options the question does not have: {unknown}")
        if len(self.event) == len(self.options):
            raise ValueError("an event over every option has probability 1 by construction")

    @property
    def p_name(self) -> str:
        return self.event_name or self.key


@dataclass(frozen=True)
class Noul:
    """A yes/no question read as P(yes)."""

    key: str
    instructions: str
    criteria: dict[str, str] = field(default_factory=dict)
    """``{"true": …, "false": …}`` — what makes the answer yes, what makes it no."""

    def as_choice(self) -> Choice:
        crit = {}
        if self.criteria.get("true"):
            crit["yes"] = self.criteria["true"]
        if self.criteria.get("false"):
            crit["no"] = self.criteria["false"]
        return Choice(self.key, self.instructions, ("yes", "no"), criteria=crit, event=("yes",), event_name=self.key)


@dataclass(frozen=True)
class Score:
    """Ordered levels, 2–10, read as an expectation over their index (0 … n−1)."""

    key: str
    instructions: str
    levels: tuple[str, ...]
    criteria: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 2 <= len(self.levels) <= 10:
            raise ValueError(f"a Score needs 2–10 levels, got {len(self.levels)}")

    def as_choice(self) -> Choice:
        return Choice(self.key, self.instructions, self.levels, criteria=dict(self.criteria))


Question = Noul | Choice | Score


def as_choice(question: Question) -> Choice:
    return question if isinstance(question, Choice) else question.as_choice()


@dataclass(frozen=True)
class Reading:
    """What a backend produced for one question, before any map: the parts it actually has."""

    choice: str | None
    """The option the backend chose — the argmax of its shares, or the word the model wrote."""
    shares: dict[str, float] | None
    """A distribution over the options when the backend has one (logprobs, the Decisions API);
    ``None`` for a backend that gives a number and a word (verbalized)."""
    p: float | None
    """P(event) as the backend gave it; ``None`` when the question has no event or nothing came."""
    mass: float | None = None
    """Local logprobs only: how much of the label token's mass was on the options at all."""
    usd: float | None = None
    raw: str = ""
    """The first 200 characters the model wrote, for the reader who checks."""
    logprobs_came: bool | None = None
    """Whether the route returned token log-probabilities — ``None`` where the question is moot."""


@runtime_checkable
class DecisionBackend(Protocol):
    name: str
    model: str

    def instrument(self, question: Question) -> str:
        """The fixed text around the state — what a calibration map is keyed on."""
        ...

    def ask(self, state: str, question: Question) -> Reading:
        """One call. Raises on transport failure; the Decider records that as a halt. A backend
        that has no native form for a kind asks it as the Choice it reduces to."""
        ...


@dataclass(frozen=True)
class Answer:
    """One decision's answer with its receipt."""

    decision: str
    key: str
    backend: str
    model: str
    prompt_hash: str
    choice: str | None
    shares: dict[str, float] | None
    raw_p: float | None
    p: float | None
    """``raw_p`` through the map when one applied, else ``raw_p`` itself — read ``calibrated``."""
    calibrated: bool
    """``raw_p`` went through a map fitted on this decision, backend, model and instrument."""
    map: str | None
    """The map this instrument has, whether or not a number came to pass through it — so the
    receipt of a halt still says the decision is one with a map, and the receipt of a reworded
    question says it is not."""
    mass: float | None
    seconds: float
    usd: float | None
    halt: str | None = None
    raw: str = ""
    logprobs_came: bool | None = None

    @property
    def answered(self) -> bool:
        return self.halt is None and (self.p is not None or self.choice is not None)

    def expectation(self, question: Score) -> float | None:
        """The expected level index under the shares, for a Score; ``None`` without shares."""
        if not self.shares:
            return None
        total = sum(self.shares.get(level, 0.0) for level in question.levels)
        if total <= 0:
            return None
        return sum(i * self.shares.get(level, 0.0) for i, level in enumerate(question.levels)) / total

    def receipt(self) -> dict[str, Any]:
        """What travels with the decision: who answered, whether a map existed, what came."""
        out: dict[str, Any] = {
            "decision": self.decision, "question": self.key, "backend": self.backend, "model": self.model,
            "prompt_hash": self.prompt_hash, "calibrated": self.calibrated, "seconds": round(self.seconds, 3),
        }
        if self.map:
            out["map"] = self.map
        if self.p is not None:
            out["p"] = round(self.p, 4)
        if self.raw_p is not None and self.calibrated:
            out["raw_p"] = round(self.raw_p, 4)
        if self.choice is not None:
            out["choice"] = self.choice
        if self.mass is not None:
            out["mass"] = round(self.mass, 4)
        if self.logprobs_came is not None:
            out["logprobs_came"] = self.logprobs_came
        if self.usd is not None:
            out["usd"] = self.usd
        if self.halt:
            out["halt"] = self.halt
        return out


class Decider:
    """A backend plus the maps: asks, calibrates when a map for exactly this instrument exists."""

    def __init__(self, backend: DecisionBackend, maps: CalibrationMaps | None = None) -> None:
        self.backend = backend
        self.maps = maps if maps is not None else CalibrationMaps()

    def decide(self, decision: str, state: str, question: Question) -> Answer:
        choice = as_choice(question)
        digest = prompt_hash(self.backend.name, self.backend.model, self.backend.instrument(question))
        t0 = time.perf_counter()
        halt: str | None = None
        try:
            reading = self.backend.ask(state, question)
        except Exception as exc:  # noqa: BLE001 — a halt, recorded as one, never a verdict
            halt = f"{type(exc).__name__}: {str(exc)[:200]}"
            reading = Reading(choice=None, shares=None, p=None)
        seconds = time.perf_counter() - t0
        found = self.maps.find(decision, self.backend.name, self.backend.model, digest)
        raw_p = reading.p
        if raw_p is not None:
            raw_p = min(max(float(raw_p), 0.0), 1.0)
        p = found.apply(raw_p) if (found is not None and raw_p is not None) else raw_p
        return Answer(
            decision=decision, key=choice.key, backend=self.backend.name, model=self.backend.model,
            prompt_hash=digest, choice=reading.choice, shares=reading.shares, raw_p=raw_p, p=p,
            calibrated=found is not None and raw_p is not None, map=found.id if found is not None else None,
            mass=reading.mass, seconds=seconds, usd=reading.usd, halt=halt, raw=reading.raw,
            logprobs_came=reading.logprobs_came,
        )

    def has_map(self, decision: str, question: Question) -> bool:
        """Whether a map exists for this decision on this backend — knowable before asking."""
        digest = prompt_hash(self.backend.name, self.backend.model, self.backend.instrument(question))
        return self.maps.find(decision, self.backend.name, self.backend.model, digest) is not None


def render_criteria(question: Choice) -> str:
    """The option lines a model backend appends when the question carries criteria."""
    if not question.criteria:
        return ""
    return "\n" + "\n".join(f"- {o}: {question.criteria[o]}" for o in question.options if o in question.criteria)


def option_words(options: Sequence[str]) -> str:
    return " | ".join(f'"{o}"' for o in options)
