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

import string
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, runtime_checkable

from chimera.decisions.calibration import CalibrationMaps, prompt_hash
from chimera.decisions.log import DecisionLog

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

    def neutral(self) -> NeutralChoice:
        """This question with neutral option identifiers — ``A``, ``B``, ``C``… — and the meaning
        moved into the criteria (study 22, I3). Under polar labels a model reads the label, not the
        rubric: "Type-Safe Is Not Error-Free" (arXiv 2609.26758) swapped rubrics under yes/no and
        changed 76.9% of answers, and 6.5% under neutral 0/1. The neutral form is a different
        instrument — a different hash, no map until a bench fits one — and
        :meth:`NeutralChoice.restore` names the answer in the original options again."""
        if len(self.options) > len(string.ascii_uppercase):
            raise ValueError(f"neutral labels cover at most {len(string.ascii_uppercase)} options")
        letters = tuple(string.ascii_uppercase[: len(self.options)])
        to_original = dict(zip(letters, self.options, strict=True))
        to_letter = {o: letter for letter, o in to_original.items()}
        criteria = {
            to_letter[o]: (f"{o} — {self.criteria[o]}" if self.criteria.get(o) else o) for o in self.options
        }
        choice = Choice(
            key=self.key, instructions=self.instructions, options=letters, criteria=criteria,
            event=tuple(to_letter[e] for e in self.event), event_name=self.event_name,
        )
        return NeutralChoice(choice=choice, to_original=to_original)


@dataclass(frozen=True)
class NeutralChoice:
    """A :class:`Choice` asked under neutral letters, and the way back to its own options."""

    choice: Choice
    to_original: dict[str, str]

    def restore(self, answer: Answer) -> Answer:
        """The answer with its choice and shares named in the original options. ``p`` needs no
        change: the event was carried over letter for letter."""
        shares = {self.to_original.get(k, k): v for k, v in answer.shares.items()} if answer.shares else answer.shares
        choice = self.to_original.get(answer.choice, answer.choice) if answer.choice is not None else None
        return replace(answer, choice=choice, shares=shares)


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
    resolved_model: str = ""
    """The build that actually answered, as the route names it — ``jev-1.13-20260917`` behind the
    alias ``typesafe/jev-1.13``, ``qwen3:4b@Q4_K_M`` behind the tag ``qwen3:4b``. Empty when the
    route does not say (every gateway in study 21 hid it). A map is fitted on one build; the Decider
    refuses to apply it to another."""


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
    resolved_model: str = ""
    """The build that answered, when the route names it (see :attr:`Reading.resolved_model`)."""
    note: str = ""
    """Why a map that exists was not applied — the build differs from the one it was fitted on."""
    cached: bool = False
    """The reading came from the :class:`DecisionCache`, not from a call made for this answer."""
    log_id: str = ""
    """The id of this answer's line in the decision log, when the Decider keeps one — what an
    outcome later names to label it. Empty without a log, or when the line could not be written."""

    @property
    def answered(self) -> bool:
        return self.halt is None and (self.p is not None or self.choice is not None)

    @property
    def confidence(self) -> float | None:
        """How peaked the shares are: ``(K*p_max - 1)/(K - 1)`` — 0 for uniform, 1 for all mass on
        one option. A **shape statistic**, never a probability of being right and never a threshold
        (study 22, I5: study 21 measured the Choice mass +0.11 above the Noul, ECE 0.221 vs 0.120;
        only a calibrated ``p`` crosses a line). ``None`` without shares."""
        if not self.shares:
            return None
        k = len(self.shares)
        total = sum(self.shares.values())
        if k < 2 or total <= 0:
            return None
        p_max = max(self.shares.values()) / total
        return max(0.0, min(1.0, (k * p_max - 1.0) / (k - 1.0)))

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
        if self.resolved_model:
            out["resolved_model"] = self.resolved_model
        if self.note:
            out["note"] = self.note
        if self.cached:
            out["cached"] = True
        if self.log_id:
            out["log_id"] = self.log_id
        if self.halt:
            out["halt"] = self.halt
        return out


CacheKey = tuple[str, str, str, str]


class DecisionCache:
    """Readings already paid for, keyed on (backend, model, instrument hash, state) — LRU, per
    process, thread-safe.

    Repeated shell commands are the common case on the governance surface, and a reading at
    ``temperature 0`` of the same text under the same instrument is the reading. The key is the
    state **exactly as sent**: normalizing whitespace would return a reading for a text the model
    never saw (inside quotes, ``a  b`` and ``a b`` are different commands). Only a reading that
    chose an option is stored — a halt is an exception and never reaches the cache, and a reading
    with no choice may be a transient truncation the next call does not repeat.

    What the cache cannot see: a model re-pulled under the same tag mid-process. The reading keeps
    the build it was read on (``resolved_model``), so the receipt still names it and the Decider
    still refuses a map fitted on another build.
    """

    def __init__(self, max_entries: int = 1024) -> None:
        if max_entries < 1:
            raise ValueError("a cache needs room for at least one reading")
        self.max_entries = max_entries
        self._entries: OrderedDict[CacheKey, Reading] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: CacheKey) -> Reading | None:
        with self._lock:
            reading = self._entries.get(key)
            if reading is None:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return reading

    def put(self, key: CacheKey, reading: Reading) -> None:
        if reading.choice is None:
            return
        with self._lock:
            self._entries[key] = reading
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)


class Decider:
    """A backend plus the maps: asks, calibrates when a map for exactly this instrument exists."""

    def __init__(
        self,
        backend: DecisionBackend,
        maps: CalibrationMaps | None = None,
        *,
        cache: DecisionCache | None = None,
        log: DecisionLog | None = None,
    ) -> None:
        self.backend = backend
        self.maps = maps if maps is not None else CalibrationMaps()
        self.cache = cache
        self.log = log

    def decide(self, decision: str, state: str, question: Question) -> Answer:
        choice = as_choice(question)
        digest = prompt_hash(self.backend.name, self.backend.model, self.backend.instrument(question))
        t0 = time.perf_counter()
        halt: str | None = None
        key: CacheKey = (self.backend.name, self.backend.model, digest, state)
        cached = self.cache.get(key) if self.cache is not None else None
        if cached is not None:
            reading = cached
        else:
            try:
                reading = self.backend.ask(state, question)
            except Exception as exc:  # noqa: BLE001 — a halt, recorded as one, never a verdict
                halt = f"{type(exc).__name__}: {str(exc)[:200]}"
                reading = Reading(choice=None, shares=None, p=None)
            else:
                if self.cache is not None:
                    self.cache.put(key, reading)
        seconds = time.perf_counter() - t0
        found = self.maps.find(decision, self.backend.name, self.backend.model, digest)
        raw_p = reading.p
        if raw_p is not None:
            raw_p = min(max(float(raw_p), 0.0), 1.0)
        # A map fitted on one build does not apply to another. The alias the map is keyed on can move
        # (the vendor's ``jev-1.13`` resolved to ``jev-1.13-20260917`` on 09-19; an Ollama tag is
        # whatever was last pulled), and every gateway in study 21 hid the build — so when both sides
        # name one and they differ, the number is a prior, not a calibrated probability, and the
        # receipt says so (§2ad: the API's semantics are part of the experiment).
        note = ""
        usable = found
        if found is not None and found.resolved_model and reading.resolved_model and found.resolved_model != reading.resolved_model:
            note = f"map fitted on {found.resolved_model}, this answer came from {reading.resolved_model}"
            usable = None
        p = usable.apply(raw_p) if (usable is not None and raw_p is not None) else raw_p
        answer = Answer(
            decision=decision, key=choice.key, backend=self.backend.name, model=self.backend.model,
            prompt_hash=digest, choice=reading.choice, shares=reading.shares, raw_p=raw_p, p=p,
            calibrated=usable is not None and raw_p is not None, map=found.id if found is not None else None,
            mass=reading.mass, seconds=seconds, usd=reading.usd, halt=halt, raw=reading.raw,
            logprobs_came=reading.logprobs_came, resolved_model=reading.resolved_model, note=note,
            cached=cached is not None,
        )
        if self.log is not None:
            # Every answer, halts included: a halt is a fact about availability the report counts.
            entry_id = self.log.answer(answer.receipt(), state, raw_p=raw_p)
            if entry_id:
                answer = replace(answer, log_id=entry_id)
        return answer

    def decide_many(self, decision: str, state: str, questions: Iterable[Question]) -> dict[str, Answer]:
        """One state, several atomic questions (study 22, I6), each read **in isolation** — its own
        call, its own instrument, its own map. Sequential: a local server serves one request at a
        time by default, and a hosted backend's cost is per call either way. The questions do not
        share a prompt prefix — the state sits after each question's instructions, as the bench
        measured it — so N questions cost N full reads; putting the state first to share the prefix
        is a different instrument and needs its own bench before a map applies to it."""
        answers: dict[str, Answer] = {}
        for question in questions:
            key = as_choice(question).key
            if key in answers:
                raise ValueError(f"two questions share the key {key!r}")
            answers[key] = self.decide(decision, state, question)
        return answers

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
