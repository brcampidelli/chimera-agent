"""Verified answers for grounded turns — the policy study 26 measured, as the product runs it.

`bench/verified_cascade/RESULTS.md` (2026-09-27, 400 paired items): an answer drafted from provided
excerpts, read by a System One verifier as one ``Choice`` (``supported`` / ``unsupported`` /
``declined``) and shipped only when ``supported`` at p >= 0.8, shipped **a third fewer wrong
answers** than the drafting model alone (33 → 21 of 400, 11 fixed and 0 broken, Holm p = 0.002), at
**1.87× the cost with the local verifier** (``qwen3:4b``) and 3.74× with Jev. The lexical gate the
cascade shipped matched the baseline at 10.8× the cost. This module is that policy — arm D, and B
when the owner chooses Jev — with the registered secondary variant the owner chose: the third
outcome **ships the decline** ("the sources provided don't cover this"), 16 hand-offs instead of 225.

**What counts as grounded — the only shape that was measured.** A turn's final answer written from
sources the product handed the model (attached documents, recalled memory facts, retrieved chunks),
drafted in a step that made **no tool call**. The sources travel to the verifier as the state's
``excerpts``, exactly as the bench sent them: **no sources, no gate** (:meth:`GroundedTurn.make`
returns ``None``). A tool-using step is never gated: `bench/tool_router` (B4) measured a router in
front of the agent loop making every executor worse, and nothing in study 26 speaks for tool turns.

**The policy, step for step as `bench/verified_cascade/harness.py::verified` replays it:**

1. Read the draft. ``supported`` with p >= threshold → ship it.
2. ``declined`` → ship the decline (the draft itself: it says the sources don't cover it).
3. Anything else → escalate to the strong model, read that answer the same way; ``supported`` at the
   threshold → ship it, otherwise ship the decline.

**The verifier and its fallback.** It is the System One backend the owner chose in Settings
(``decision_backend`` / ``decision_model``). When that is the local backend (the default) and the
local model cannot answer — Ollama down, the model not pulled, or a read that halts — the chain
falls back to ``typesafe/jev-1.13`` if an OpenRouter key is configured, else to the old lexical gate.
A backend the owner chose explicitly other than local has no fallback: it is their instrument. The
receipt names the verifier that actually ran and why any other was skipped — never silently.

**A verifier failure never loses the answer and is never read as a verdict.** The decisions package
fails closed: a halt is not an answer (`chimera/decisions/contract.py`). Here that means a draft the
verifier could not read ships **as unverified** — the badge says so and the receipt carries the halt
— rather than being withheld (which would turn an Ollama restart into lost work) or labelled
verified (which would read a halt as ``supported``). An answer the verifier did read as unsupported
is withheld, and kept on the receipt so the person can still see what was drafted.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from chimera.decisions.contract import Answer, Choice, Decider
from chimera.telemetry import get_logger

_log = get_logger("fusion.verified")

#: The decision's name — a calibration map for it, if a deployment ever fits one, is keyed on this.
DECISION = "verified_answers.grounded_answer"
#: The registered threshold (§4.1), applied to the raw number: no map exists for this decision.
DEFAULT_THRESHOLD = 0.8
#: The model the bench escalated to (§4.1, arm B/D's frontier rung).
MEASURED_ESCALATION_MODEL = "openrouter/openai/gpt-6-sol"
#: The verifier the local backend falls back to when the owner has a key and no local model runs.
FALLBACK_SYSTEM_ONE = "typesafe/jev-1.13"
#: Shipped when the sources do not cover the question and no drafted decline is available to ship.
DECLINE_TEXT = "The sources provided don't cover this."
#: How long a failed/successful local probe is trusted, so a stopped Ollama costs one probe, not one
#: per turn, and a restarted one is noticed within the minute.
PROBE_TTL_S = 30.0
PROBE_TIMEOUT_S = 1.5
#: What a withheld answer keeps on the receipt — enough to read, not a second copy of a long reply.
WITHHELD_CHARS = 4000

# The question, word for word as the bench asked it (`bench/verified_cascade/harness.py`): a map or a
# threshold read off one wording does not transfer to another (§2aa).
INSTRUCTIONS = "Is this answer supported by the excerpts?"
CRITERIA = {
    "supported": "The answer addresses the question, and every fact, number and policy it states appears in the excerpts.",
    "unsupported": "The answer states facts that are not in the excerpts, contradicts the excerpts, or answers a different question.",
    "declined": "The answer says the excerpts do not cover the question and adds no facts.",
}
QUESTION = Choice(
    key="grounded_answer", instructions=INSTRUCTIONS, options=("supported", "unsupported", "declined"),
    criteria=dict(CRITERIA), event=("supported",), event_name="p_supported",
)

Outcome = Literal["supported", "escalated", "declined", "unverified", "lexical"]
Source = Literal["attachments", "memory", "rag"]


def grounded_state(excerpts: Sequence[str], question: str, answer: str) -> str:
    """The state the verifier reads, keys in the bench's order, one answer per call (never batched:
    batching moved ``p`` by 0.275 in `bench/jev_decisions` §12 B2)."""
    return json.dumps({"excerpts": list(excerpts), "question": question, "answer": answer}, ensure_ascii=False)


@dataclass(frozen=True)
class GroundedTurn:
    """A question and the sources its answer must come from. Built only through :meth:`make`."""

    excerpts: tuple[str, ...]
    question: str
    sources: tuple[Source, ...]

    @classmethod
    def make(cls, excerpts: Sequence[str], question: str, sources: Sequence[Source]) -> GroundedTurn | None:
        """``None`` — no gate — when there is no question or no non-empty excerpt: a verifier asked
        whether an answer is supported by nothing reads every answer as unsupported."""
        kept = tuple(e.strip() for e in excerpts if e and e.strip())
        q = (question or "").strip()
        if not kept or not q:
            return None
        return cls(excerpts=kept, question=q, sources=tuple(dict.fromkeys(sources)))


@dataclass(frozen=True)
class Redraft:
    """The strong model's answer to the same turn."""

    text: str
    model: str
    usd: float = 0.0
    error: str = ""


Escalate = Callable[[], Redraft]


# ------------------------------------------------------------------------------------ the verifiers
@dataclass(frozen=True)
class Read:
    """One verifier reading, reduced to what the policy needs and the receipt shows."""

    label: str | None
    p: float | None
    usd: float = 0.0
    halt: str = ""
    resolved_model: str = ""

    def accepts(self, threshold: float) -> bool:
        return self.label == "supported" and self.p is not None and self.p >= threshold


class Slot(Protocol):
    """One verifier in the chain."""

    backend: str
    model: str

    def unavailable(self) -> str:
        """``""`` when it can be asked now, else a reason word for the receipt."""
        ...

    def read(self, turn: GroundedTurn, answer: str) -> Read: ...


class DecisionSlot:
    """A System One backend behind a :class:`Decider` (the shipped maps, the decision log)."""

    def __init__(self, decider: Decider, *, probe: Callable[[], str] | None = None) -> None:
        self.decider = decider
        self.backend = decider.backend.name
        self.model = decider.backend.model
        self._probe = probe

    def unavailable(self) -> str:
        return self._probe() if self._probe is not None else ""

    def read(self, turn: GroundedTurn, answer: str) -> Read:
        got: Answer = self.decider.decide(DECISION, grounded_state(turn.excerpts, turn.question, answer), QUESTION)
        # The event's probability is the confidence (§4.1: P(supported)); the label is the written
        # choice (Decisions API) or the argmax (local) — the Decider already made it one or the other.
        return Read(
            label=got.choice, p=got.p, usd=float(got.usd or 0.0), halt=got.halt or "",
            resolved_model=got.resolved_model,
        )


class LexicalSlot:
    """The cascade's old gate, kept as the last fallback: non-empty and no refusal opener. It does
    not read grounding at all — study 26 measured it level with no gate — so its pass is shown as a
    lexical check, never as verified."""

    backend = "lexical"
    model = "default_gate"

    def unavailable(self) -> str:
        return ""

    def read(self, turn: GroundedTurn, answer: str) -> Read:
        from chimera.fusion.cascade import default_gate
        from chimera.providers.gateway import CompletionResult

        ok = default_gate(CompletionResult(content=answer, model="lexical"))
        return Read(label="supported" if ok else "unsupported", p=1.0 if ok else 0.0)


# ------------------------------------------------------------------------------------ the result
@dataclass
class VerifiedAnswer:
    """What ships, and the ``grounded`` block of the receipt (``verified`` on a coding turn's receipt
    already names the workspace's test command, so this one has its own key)."""

    text: str
    outcome: Outcome
    verifier: dict[str, Any]
    label: str | None = None
    p: float | None = None
    threshold: float = DEFAULT_THRESHOLD
    escalated: bool = False
    escalated_model: str = ""
    escalated_label: str | None = None
    escalated_p: float | None = None
    decline_shipped: bool = False
    halt: str = ""
    usd_verifier: float = 0.0
    usd_escalation: float = 0.0
    seconds: float = 0.0
    sources: tuple[str, ...] = ()
    withheld: list[str] = field(default_factory=list)

    @property
    def usd_extra(self) -> float:
        return round(self.usd_verifier + self.usd_escalation, 6)

    def receipt(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "outcome": self.outcome,
            "verifier": dict(self.verifier),
            "label": self.label,
            "p": None if self.p is None else round(self.p, 4),
            "threshold": self.threshold,
            "escalated": self.escalated,
            "escalated_model": self.escalated_model or None,
            "escalated_label": self.escalated_label,
            "escalated_p": None if self.escalated_p is None else round(self.escalated_p, 4),
            "decline_shipped": self.decline_shipped,
            "halt": self.halt or None,
            "usd_extra": self.usd_extra,
            "usd_verifier": round(self.usd_verifier, 6),
            "usd_escalation": round(self.usd_escalation, 6),
            "seconds": round(self.seconds, 3),
            "sources": list(self.sources),
            "withheld": [w[:WITHHELD_CHARS] for w in self.withheld],
        }
        return out


#: Why a turn with sources was not gated, in words for the terminal (the desktop has its own copy
#: in ten languages, keyed on the same reason words).
NOT_APPLIED_REASONS = {
    "tool_calls": "the turn used tools, and the check was measured only on answers written without them",
    "not_final": "the turn stopped before a final answer",
    "sources_too_long": "the documents are longer than anything the check was measured on",
}


def badge(block: dict[str, Any] | None) -> str:
    """The one-line badge the terminal prints under a grounded answer ("" when not gated)."""
    if not block:
        return ""
    outcome = block.get("outcome")
    if outcome == "not_applied":
        why = NOT_APPLIED_REASONS.get(str(block.get("reason") or ""), str(block.get("reason") or ""))
        return f"- answer not checked against the attached documents: {why}"
    who = (block.get("verifier") or {}).get("model") or ""
    if outcome in ("supported", "escalated"):
        via = f" via {block.get('escalated_model')}" if outcome == "escalated" else ""
        return f"✓ verified against the sources ({who}{via})"
    if outcome == "declined":
        return "∅ the sources provided don't cover this"
    if outcome == "lexical":
        return "~ lexical check only (no System One verifier available)"
    return f"! verifier unavailable — answer not verified ({block.get('halt') or 'no reading'})"


# ------------------------------------------------------------------------------------ the policy
class GroundedVerifier:
    """The measured policy over a chain of verifiers (first available wins, a halt falls through)."""

    def __init__(self, chain: Sequence[Slot], *, threshold: float = DEFAULT_THRESHOLD) -> None:
        if not chain:
            raise ValueError("a verifier chain needs at least one verifier")
        self.chain = list(chain)
        self.threshold = threshold

    def _first_read(self, turn: GroundedTurn, draft: str) -> tuple[Slot | None, Read | None, list[dict[str, str]]]:
        """The first verifier in the chain that gives a reading of the draft; the skipped ones with
        why. A halt on the last one is returned as that slot's reading (the halt is on it)."""
        skipped: list[dict[str, str]] = []
        for i, slot in enumerate(self.chain):
            last = i == len(self.chain) - 1
            reason = slot.unavailable()
            if reason and not last:
                skipped.append({"backend": slot.backend, "model": slot.model, "reason": reason})
                continue
            if reason:
                return slot, Read(label=None, p=None, halt=reason), skipped
            read = slot.read(turn, draft)
            if read.halt and not last:
                skipped.append({"backend": slot.backend, "model": slot.model, "reason": f"halt: {read.halt}"})
                continue
            return slot, read, skipped
        return None, None, skipped

    def verify(self, turn: GroundedTurn, draft: str, *, escalate: Escalate | None) -> VerifiedAnswer:
        t0 = time.perf_counter()
        slot, first, skipped = self._first_read(turn, draft)
        if slot is None or first is None:  # unreachable: the chain is non-empty and its last slot answers
            raise RuntimeError("the verifier chain produced no reading")
        who: dict[str, Any] = {"backend": slot.backend, "model": slot.model}
        if first.resolved_model:
            who["resolved_model"] = first.resolved_model
        if skipped:
            who["fell_back_from"] = skipped
        result = VerifiedAnswer(
            text=draft, outcome="unverified", verifier=who, label=first.label, p=first.p,
            threshold=self.threshold, usd_verifier=first.usd, sources=turn.sources,
        )
        lexical = slot.backend == LexicalSlot.backend
        try:
            if first.halt or first.label is None:
                # Fails closed on the verdict, open on the answer: shipped, and marked unverified.
                result.halt = first.halt or "no reading"
                return result
            if first.accepts(self.threshold):
                result.outcome = "lexical" if lexical else "supported"
                return result
            if first.label == "declined":
                result.outcome, result.decline_shipped = "declined", True
                return result
            return self._escalate(result, slot, turn, draft, escalate, lexical)
        finally:
            result.seconds = time.perf_counter() - t0

    def _escalate(
        self, result: VerifiedAnswer, slot: Slot, turn: GroundedTurn, draft: str,
        escalate: Escalate | None, lexical: bool,
    ) -> VerifiedAnswer:
        result.escalated = True
        result.withheld.append(draft)
        redraft = escalate() if escalate is not None else Redraft(text="", model="", error="no strong model to escalate to")
        result.escalated_model = redraft.model
        result.usd_escalation = redraft.usd
        if redraft.error or not redraft.text.strip():
            # The draft was READ as unsupported; with nothing better to ship, the decline goes out
            # and the reason escalation failed stays on the receipt.
            result.halt = f"escalation: {redraft.error or 'empty answer'}"
            return self._decline(result)
        second = slot.read(turn, redraft.text)
        result.usd_verifier += second.usd
        result.escalated_label, result.escalated_p = second.label, second.p
        if second.halt or second.label is None:
            # The strong model's answer, unread: shipped as unverified rather than lost (the draft it
            # replaces was read as unsupported, so it is the better of the two answers in hand).
            result.text, result.halt = redraft.text, second.halt or "no reading"
            result.outcome = "unverified"
            return result
        if second.accepts(self.threshold):
            result.text = redraft.text
            result.outcome = "lexical" if lexical else "escalated"
            return result
        if second.label == "declined":
            result.text = redraft.text
        else:
            result.withheld.append(redraft.text)
        return self._decline(result)

    @staticmethod
    def _decline(result: VerifiedAnswer) -> VerifiedAnswer:
        if result.text in result.withheld:
            result.text = DECLINE_TEXT
        result.outcome, result.decline_shipped = "declined", True
        return result


# ------------------------------------------------------------------------------------ assembly
_probe_cache: dict[tuple[str, str], tuple[float, str]] = {}


def local_probe(base_url: str, model: str, *, now: Callable[[], float] = time.monotonic) -> str:
    """``""`` when Ollama at ``base_url`` has ``model`` pulled; else ``ollama_<reason>`` or
    ``model_absent``. Cached :data:`PROBE_TTL_S` either way. Never raises."""
    key = (base_url, model)
    hit = _probe_cache.get(key)
    t = now()
    if hit is not None and t - hit[0] < PROBE_TTL_S:
        return hit[1]
    from chimera.providers.ollama import installed_models

    listing = installed_models(base_url, timeout_s=PROBE_TIMEOUT_S)
    if not listing.reachable:
        reason = f"ollama_{listing.reason or 'unreachable'}"
    else:
        tags = set(listing.models)
        # `qwen3:4b` and `qwen3:4b-...`/`:latest` spellings: an exact tag or the bare name with :latest.
        reason = "" if (model in tags or f"{model}:latest" in tags) else "model_absent"
    _probe_cache[key] = (t, reason)
    return reason


def escalation_model(settings: Any) -> str:
    """The strong model an unsupported draft escalates to.

    The measured one, ``gpt-6-sol``, when the owner's keys can reach it (an OpenRouter key): the
    bench's 1.87× and its 11-fixed/0-broken were read with it, and the tier ladder's ``top`` for the
    default cost mode (``glm-5.3``) was never measured on this task. Without an OpenRouter key the
    ladder's top is the strongest model the owner has configured. An explicit
    ``CHIMERA_VERIFIED_ANSWERS_ESCALATE_MODEL`` wins over both.
    """
    explicit = str(getattr(settings, "verified_answers_escalate_model", "") or "").strip()
    if explicit:
        return explicit
    try:
        providers = list(settings.configured_providers())
    except Exception:  # noqa: BLE001 — a settings object without keys reads as "no OpenRouter"
        providers = []
    if "openrouter" in providers:
        return MEASURED_ESCALATION_MODEL
    return str(settings.tier_ladder().top)


def build_verifier(settings: Any, *, gateway: Any | None = None) -> GroundedVerifier | None:
    """The verifier the settings describe, or ``None`` when ``CHIMERA_VERIFIED_ANSWERS`` is off."""
    if not bool(getattr(settings, "verified_answers", True)):
        return None
    from chimera.decisions.factory import build_decider, openrouter_key_set

    threshold = float(getattr(settings, "verified_answers_threshold", DEFAULT_THRESHOLD))
    backend = (settings.decision_backend or "local_logprob").strip()
    chain: list[Slot] = []
    try:
        decider = build_decider(settings, gateway=gateway)
    except Exception as exc:  # noqa: BLE001 — a misconfigured backend is a fallback, and said so
        _log.warning("verified answers: decision backend %s could not be built: %s", backend, exc)
        decider = None
    if backend != "local_logprob":
        # The owner chose this instrument; no silent substitution. A backend that cannot be built
        # leaves the lexical gate, and the receipt says the chosen one was skipped.
        if decider is not None:
            return GroundedVerifier([DecisionSlot(decider)], threshold=threshold)
        return GroundedVerifier([_Unbuildable(backend, settings.decision_model or ""), LexicalSlot()], threshold=threshold)
    if decider is not None:
        base = str(settings.ollama_base_url)
        model = decider.backend.model
        chain.append(DecisionSlot(decider, probe=lambda: local_probe(base, model)))
    if openrouter_key_set(settings):
        chain.append(DecisionSlot(_jev_decider(settings)))
    chain.append(LexicalSlot())
    return GroundedVerifier(chain, threshold=threshold)


def _jev_decider(settings: Any) -> Decider:
    from pathlib import Path

    from chimera.decisions.calibration import CalibrationMaps
    from chimera.decisions.contract import DecisionCache
    from chimera.decisions.factory import _openrouter_key, maps_path
    from chimera.decisions.log import DecisionLog
    from chimera.decisions.openrouter import OpenRouterDecisionsBackend

    maps = CalibrationMaps.shipped().merged(CalibrationMaps.load(maps_path(settings)))
    return Decider(
        OpenRouterDecisionsBackend(_openrouter_key(settings), FALLBACK_SYSTEM_ONE), maps,
        cache=DecisionCache(), log=DecisionLog.for_home(Path(settings.home)),
    )


class _Unbuildable:
    """A chosen backend that could not be constructed (a missing key): skipped, and named."""

    def __init__(self, backend: str, model: str) -> None:
        self.backend = backend
        self.model = model

    def unavailable(self) -> str:
        return "not_buildable"

    def read(self, turn: GroundedTurn, answer: str) -> Read:  # pragma: no cover — never asked
        return Read(label=None, p=None, halt="not_buildable")


#: The largest source set the bench read (13,864 characters across four excerpts; median 3,976).
#: Beyond it the turn is not the measured shape — and the local verifier's default Ollama context
#: would truncate the excerpts silently, so its reading would be about text it never saw.
MAX_SOURCE_CHARS = 14_000

# The escalation rung's prompt, byte for byte the bench's drafting prompt (§4.1) — what Sol answered
# under when it fixed 11 and broke 0. The strong model gets the sources and the question, no history
# and no tools: the shape that was measured, and a fraction of the agent's own prompt.
DRAFT_SYSTEM = (
    "You answer questions using only the excerpts in the user's message. Use no other knowledge. "
    "If the excerpts answer the question, answer it concisely; every fact, number, name, flag or "
    "setting you state must appear in the excerpts. If the excerpts do not contain the answer, say "
    "that the provided excerpts do not cover it, and add no facts or guesses. Answer in the language "
    "of the question."
)


#: The turn note a surface adds when a turn carries sources: the measured drafter's rule, scoped to
#: questions about the sources (the agent's turn is not only a question about them). Not measured on
#: its own — it is the part of the bench's drafting prompt the agent's prompt did not already say.
GROUNDED_NOTE = (
    "The user attached documents to this message. If the question is about them, answer using "
    "only what they say: every fact, number, name, flag or setting you state must appear in them. "
    "If they do not contain the answer, say that the provided documents do not cover it, and add "
    "no facts or guesses. Your answer will be checked against them."
)


def draft_messages(turn: GroundedTurn) -> list[dict[str, str]]:
    body = "\n\n".join(f"[{i}] {text}" for i, text in enumerate(turn.excerpts, 1))
    return [
        {"role": "system", "content": DRAFT_SYSTEM},
        {"role": "user", "content": f"Excerpts:\n{body}\n\nQuestion: {turn.question}"},
    ]


def not_applied(reason: str, **extra: Any) -> dict[str, Any]:
    """The receipt block of a turn that had sources and was NOT gated, with why — so "no badge"
    can be told apart from "the gate was off" by whoever reads the receipt."""
    return {"outcome": "not_applied", "reason": reason, **extra}


class GroundedAnswers:
    """The verifier plus its escalation rung, and the rule for which turns it applies to."""

    def __init__(self, verifier: GroundedVerifier, redraft: Callable[[GroundedTurn], Redraft]) -> None:
        self.verifier = verifier
        self.redraft = redraft

    @staticmethod
    def applies(turn: GroundedTurn | None, *, tool_names: Sequence[str], stopped_reason: str) -> str:
        """``""`` when this turn is the measured shape, else the reason it is not gated.

        No tool call anywhere in the turn — stricter than "none in the final step": an answer
        after a tool call may rest on what the tool returned, which the verifier would not be
        shown, and would read as unsupported. ``final`` only: an answer closed at the step ceiling
        or by the loop breaker is not a drafted answer.
        """
        if turn is None:
            return "no_sources"
        if tool_names:
            return "tool_calls"
        if stopped_reason != "final":
            return "not_final"
        if sum(len(e) for e in turn.excerpts) > MAX_SOURCE_CHARS:
            return "sources_too_long"
        return ""

    def check(
        self, turn: GroundedTurn | None, draft: str, *, tool_names: Sequence[str], stopped_reason: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """``(answer to ship, receipt block)``. The block is ``None`` for a turn with no sources."""
        reason = self.applies(turn, tool_names=tool_names, stopped_reason=stopped_reason)
        if turn is None:
            return draft, None
        if reason:
            return draft, not_applied(reason, sources=list(turn.sources))
        result = self.verifier.verify(turn, draft, escalate=lambda: self.redraft(turn))
        return result.text, result.receipt()


def build_grounded_answers(settings: Any, gateway: Any) -> GroundedAnswers | None:
    """The product's grounded-answer check, or ``None`` when ``CHIMERA_VERIFIED_ANSWERS`` is off."""
    verifier = build_verifier(settings, gateway=gateway)
    if verifier is None:
        return None
    model = escalation_model(settings)
    # 4,000 output tokens, as the bench gave Sol: a reasoning route spends part of a small budget
    # thinking and returns nothing (`bench/jev_decisions/RESULTS.md` §10).
    return GroundedAnswers(
        verifier, lambda turn: redraft_with(gateway, draft_messages(turn), model, max_tokens=4000)
    )


def check_answer(
    build: Callable[[], GroundedAnswers | None],
    turn: GroundedTurn | None,
    draft: str,
    *,
    tool_names: Sequence[str],
    stopped_reason: str,
) -> tuple[str, dict[str, Any] | None, float]:
    """What a surface calls after its turn: ``(answer to ship, receipt block, extra usd)``.

    ``build`` is called only for a turn with sources, so a turn without any pays nothing. Nothing
    here may fail the turn: an unexpected error ships the draft as unverified, with the error on the
    receipt — a verifier that crashes is still a verifier that did not verify.
    """
    if turn is None:
        return draft, None, 0.0
    try:
        checker = build()
        if checker is None:
            return draft, None, 0.0
        answer, block = checker.check(turn, draft, tool_names=tool_names, stopped_reason=stopped_reason)
    except Exception as exc:  # noqa: BLE001 — the answer is already paid for and must not be lost
        _log.warning("verified answers: the check failed: %s", exc)
        block = {
            "outcome": "unverified", "verifier": {}, "halt": f"{type(exc).__name__}: {str(exc)[:200]}",
            "escalated": False, "decline_shipped": False, "usd_extra": 0.0, "sources": list(turn.sources),
            "withheld": [],
        }
        return draft, block, 0.0
    extra = float(block.get("usd_extra") or 0.0) if block else 0.0
    return answer, block, extra


def redraft_with(gateway: Any, messages: Sequence[Any], model: str, **kwargs: Any) -> Redraft:
    """The strong model answering the same messages, no tools — the escalation rung. Never raises:
    a failure is carried on the :class:`Redraft` and ends as a shipped decline, with the reason."""
    from chimera.orchestration.receipts import price_completion

    try:
        result = gateway.complete(list(messages), model=model, **kwargs)
    except Exception as exc:  # noqa: BLE001 — escalation failing is a receipt line, not a crash
        return Redraft(text="", model=model, error=f"{type(exc).__name__}: {str(exc)[:200]}")
    cost = price_completion(result)
    return Redraft(text=str(result.content or ""), model=str(result.model or model), usd=float(cost.usd or 0.0))
