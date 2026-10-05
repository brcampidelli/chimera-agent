"""Harness-enforced token budgets for delegations (M16-A4).

The evidence rule: effort scaling must live in the HARNESS, not the prompt —
over-delegation and runaway loops are documented failure modes, and a model
asked nicely to "be brief" is not a control mechanism. :class:`BudgetedBackend`
wraps any ``SupportsComplete`` and enforces a hard ceiling: pre-call it refuses
(or soft-notifies) when the budget is spent and clamps ``max_tokens`` to the
remainder; post-call it consumes what the provider reports.

Honesty: when a provider reports no usage (free tiers often don't), the
chars/4 fallback is used and the budget is flagged ``estimated`` — the flag
propagates into receipts so estimated numbers never masquerade as measured.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any, Literal

from chimera.orchestration.receipts import estimate_tokens
from chimera.providers.gateway import CompletionResult, MessageLike, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("orchestration.budget")


#: What a turn spends before it says so, when nobody set a number. A dollar is roughly 75x an
#: ordinary turn on the measured install and only a run that went wrong reaches it, which is the point
#: of a warning instead of a ceiling: it costs the person nothing until the moment it is useful.
DEFAULT_SPEND_WARN_USD = 1.0


class BudgetExceeded(RuntimeError):
    """Raised (hard mode) when a call would exceed the delegation's token budget."""


class SpendExceeded(BudgetExceeded):
    """Raised when a call would exceed the RUN's dollar ceiling.

    A subclass, so every ``except BudgetExceeded`` already written keeps working — but its own type,
    because the two ceilings answer different questions and a caller that can tell them apart can
    say which one the person watching should raise.
    """


class SpendBudget:
    """A dollar ceiling for one run, and a rule about what to do when the price is unknown.

    Tokens and dollars are different axes and this is deliberately a sibling of
    :class:`TokenBudget` rather than a second mechanism elsewhere: a project with two spend limiters
    is a project where the two disagree, and the disagreement surfaces at 3 a.m. on the machine that
    trades money.

    **An unpriced call stops a run that has a CEILING, and only warns one that does not.** A ceiling
    that skips what it cannot price still shows green while the real spend climbs, and it climbs
    fastest on exactly the models nobody bothered to price; so when the owner set a number, an
    unpriced call stops the run and names the model, one line of
    :func:`~chimera.fusion.receipts.set_price` away. Without a ceiling there is nothing to skip:
    the same call is a warning (``price_unknown``, see :meth:`take_notices`) and the run goes on.
    The owner decided on 2026-09-27 that a missing price, by itself, is a notice and not a stop; the
    ceiling they typed themselves is the one thing that keeps its refusal.

    **A ceiling is optional now.** ``max_usd`` defaults to infinity, and ``warn_usd`` says when to
    speak instead of when to stop. The desktop used to arm a US$1 ceiling on every turn; that is now
    a warning at US$1, and a ceiling exists only when the person types one.

    A **local** model is priced at zero rather than treated as unknown, because it is not unknown —
    an Ollama run spends electricity, and a dollar cap is not about electricity. That distinction is
    resolved in :func:`~chimera.fusion.receipts.resolve_price`, so every consumer of the price table
    gets it, not just this class.
    """

    def __init__(self, max_usd: float = math.inf, *, warn_usd: float | None = None) -> None:
        if max_usd <= 0:
            raise ValueError("max_usd must be positive")
        if warn_usd is not None and warn_usd <= 0:
            raise ValueError("warn_usd must be positive")
        self.max_usd = max_usd
        self.warn_usd = warn_usd
        self._spent = 0.0
        #: Dollars set aside for calls that are in flight right now: each one's worst case, held
        #: until it settles. Counted against the ceiling exactly like money already spent, which is
        #: what lets :class:`SpendCappedBackend` release its lock during the call.
        self._reserved = 0.0
        #: True once any charge was a reservation kept for a call that raised, rather than usage a
        #: provider reported. Sticky, like :attr:`unpriced_model`: one guessed row taints the total.
        self._estimated = False
        self._unpriced_model: str | None = None
        #: The warnings already sent. Each is said once: a line repeated on every step is a line
        #: nobody reads by the third one.
        self._told: set[str] = set()

    @property
    def capped(self) -> bool:
        """True when the person set a ceiling. Only then can a call be refused for money."""
        return self.max_usd != math.inf

    def take_notices(self) -> list[tuple[str, str, dict[str, Any]]]:
        """The warnings that became true since the last call, each once: ``(code, text, data)``.

        ``price_unknown`` — a call was made on a model with no price and there is no ceiling to
        protect, so the run goes on and says that its spend is not being counted. ``spend_warn`` —
        the run has spent ``warn_usd``. Neither stops anything; a run with a ceiling is stopped by
        :meth:`blocked` as before.
        """
        out: list[tuple[str, str, dict[str, Any]]] = []
        if (
            self._unpriced_model is not None
            and not self.capped
            and "price_unknown" not in self._told
        ):
            self._told.add("price_unknown")
            out.append((
                "price_unknown",
                f"the price of {self._unpriced_model} is unknown, so this turn's spend is not counted",
                {"model": self._unpriced_model},
            ))
        if (
            self.warn_usd is not None
            and self._spent >= self.warn_usd
            and "spend_warn" not in self._told
        ):
            self._told.add("spend_warn")
            out.append((
                "spend_warn",
                f"this turn has spent ${self._spent:.2f}",
                {"usd": round(self._spent, 4), "warn_usd": self.warn_usd},
            ))
        return out

    @property
    def spent(self) -> float:
        return round(self._spent, 6)

    @property
    def reserved(self) -> float:
        """The worst case of the calls still in flight, already counted against the ceiling."""
        return round(self._reserved, 6)

    @property
    def estimated(self) -> bool:
        """True when part of :attr:`spent` is a reservation kept for a call that failed.

        Read by :meth:`blocked`, whose refusal says so; no receipt carries it yet, because no
        receipt reports this budget's total (the hierarchy and crew routes price their runs from
        the outcome, not from here)."""
        return self._estimated

    @property
    def remaining(self) -> float:
        return max(0.0, round(self.max_usd - self._spent - self._reserved, 6))

    def reserve(self, usd: float) -> None:
        """Set aside a call's worst case before it starts. Settle it with :meth:`release` and the
        real usage, or keep it with :meth:`forfeit` when the call raised."""
        self._reserved += max(0.0, usd)

    def release(self, usd: float) -> None:
        """Give back a reservation whose call settled; the caller then records what it really cost."""
        self._reserved = max(0.0, self._reserved - max(0.0, usd))

    def forfeit(self, usd: float) -> None:
        """Charge a reservation in full because its call raised.

        A provider can bill and then fail: a timeout after generation, a stream cut after the tokens
        were produced. The call's real cost is unknown and the reservation is the most it could have
        been, so it is charged and :attr:`estimated` is set (and named in :meth:`blocked`'s refusal)
        rather than the call being assumed free. :class:`SpendCappedBackend` calls this only for an
        error that may have been billed; one that provably left before the request did is released.
        """
        self.release(usd)
        self._spent += max(0.0, usd)
        self._estimated = True

    @property
    def unpriced_model(self) -> str | None:
        """The first model whose price could not be resolved, if any. Sticky: once the run has spent
        an unknown amount, every later total is unknown too, and clearing it would restore a
        confidence the run no longer has."""
        return self._unpriced_model

    def blocked(self) -> str | None:
        """Why the next call must not happen, or None to proceed.

        Checked BEFORE the call, so the money is never spent to discover it was over budget.
        """
        if self._unpriced_model is not None and self.capped:
            return (
                f"the price of {self._unpriced_model} is unknown, so the spend so far cannot be "
                "known either; set a price for it or run without a budget"
            )
        # Reservations count as spent: a call in flight may cost its whole worst case, and a check
        # that ignored it is the race `SpendCappedBackend` used to close by queueing every call.
        committed = self._spent + self._reserved
        if self.capped and committed >= self.max_usd:
            held = f" (${self._reserved:.4f} of it in flight)" if self._reserved > 0 else ""
            # Said where the number is shown. A total that is partly the reservations of failed
            # calls reads as measured unless the sentence says otherwise, and this sentence is the
            # one place a capped run reports its spend to the person (the SSE error frame).
            guessed = (
                "; estimated: includes the full reservations of calls that failed"
                if self._estimated
                else ""
            )
            return f"spend cap reached: ${committed:.4f} of ${self.max_usd:.4f}{held}{guessed}"
        return None

    def charge(self, usd: float | None, *, label: str = "") -> None:
        """Charge work whose price is already in dollars — or record that it has none.

        The door for a NESTED run that kept its own meter: ``chimera solve`` prices every attempt
        itself, and a conversation that hands a task to that loop has one number to subtract, not a
        list of calls to re-price. Without this the money spent inside the nested run would be
        invisible to the ceiling above it, and a REPL command that can be typed twice would have a
        ceiling that resets — which is not a ceiling.

        ``None`` means the nested work could not be priced, and it is treated exactly as an
        unpriced call is: sticky, and every later total is unknown. ``label`` names what could not
        be priced, so :meth:`blocked` can say which thing to go and price.
        """
        if usd is None:
            if self._unpriced_model is None:
                self._unpriced_model = label or "(unpriced work)"
            return
        # Clamped at zero: a refund is not a thing that happens here, and a negative would let one
        # mis-priced leg buy back a ceiling the run had already reached.
        self._spent += max(0.0, usd)

    def record(self, model: str, prompt_tokens: int | None, completion_tokens: int | None) -> None:
        """Charge one completed call at its own model's rate.

        The model that ANSWERED, not the one requested — a cascade, a fusion panel or a failover can
        reply on a different model, and pricing the requested one invents a number for a call that
        never happened. Same convention as :class:`~chimera.orchestration.metering.MeteredBackend`.
        """
        from chimera.orchestration.receipts import price_delegation

        usd = price_delegation(model, prompt_tokens, completion_tokens) if model else None
        self.charge(usd, label=model or "(unnamed model)")

    def record_result(self, result: object) -> None:
        """Charge one completed call by its STAGES when it has them, else by the model that answered.

        A fused turn answers as ``model="fusion"`` — a label no price table resolves — so every such
        turn was charged $0.00 and the cap never fired on the most expensive call the app makes.
        Stages are priced at their own models' rates, which is what the receipt code has always done
        for display and what the ceiling never did for enforcement.
        """
        from chimera.orchestration.receipts import price_completion

        cost = price_completion(result)
        if cost.unpriced is not None and self._unpriced_model is None:
            self._unpriced_model = cost.unpriced
        # Added even when part is unknown: the priced stages really were spent, and a floor with the
        # gap named beats a zero. `blocked()` refuses the next call either way.
        self._spent += cost.usd


class TokenBudget:
    """A mutable token allowance for one delegation.

    ``consume`` prefers provider-reported usage; the chars/4 fallback flips
    :attr:`estimated` permanently (one estimated row taints the total — that is
    the point: you can no longer present it as fully measured).
    """

    def __init__(self, max_tokens: int) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        self.max_tokens = max_tokens
        self._spent = 0
        self._estimated = False
        self._cache_read = 0
        self._cache_write = 0

    @property
    def spent(self) -> int:
        return self._spent

    @property
    def cache_read(self) -> int:
        """Prompt-cache HIT tokens accumulated across this delegation (billed cheap)."""
        return self._cache_read

    @property
    def cache_write(self) -> int:
        """Prompt-cache WRITE tokens accumulated across this delegation."""
        return self._cache_write

    def note_cache(self, read: int | None, write: int | None) -> None:
        """Record provider-reported cache tokens (subset of the prompt tokens already
        counted in ``spent`` — kept separately so receipts can price the real dollars)."""
        self._cache_read += read or 0
        self._cache_write += write or 0

    @property
    def remaining(self) -> int:
        return max(0, self.max_tokens - self._spent)

    @property
    def exhausted(self) -> bool:
        return self._spent >= self.max_tokens

    @property
    def estimated(self) -> bool:
        """True if ANY consumption relied on the chars/4 fallback."""
        return self._estimated

    def consume(
        self,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        *,
        text_fallback: str = "",
    ) -> None:
        """Record spend. If EITHER side of provider usage is missing, the count can't
        be trusted as measured -> fall back to chars/4 on ``text_fallback`` and flag
        estimated (a partial ``prompt=1200, completion=None`` must not pass as exact)."""
        if prompt_tokens is None or completion_tokens is None:
            self._spent += estimate_tokens(text_fallback)
            self._estimated = True
        else:
            self._spent += prompt_tokens + completion_tokens


class BudgetedBackend:
    """A ``SupportsComplete`` that enforces a :class:`TokenBudget` around another backend.

    Modes:
    - ``hard`` (default): a call at/over budget raises :class:`BudgetExceeded`.
    - ``soft``: returns a truncation-notice result instead of calling the model —
      the caller sees an explicit "[budget exhausted]" answer, never a silent cut.
    - ``count_only``: never blocks; just meters. Used to instrument a BASELINE arm
      symmetrically in A/B benches so token accounting is identical in both arms.
    """

    def __init__(
        self,
        inner: SupportsComplete,
        budget: TokenBudget,
        *,
        mode: Literal["hard", "soft", "count_only"] = "hard",
    ) -> None:
        self.inner = inner
        self.budget = budget
        self.mode = mode

    def complete(
        self,
        messages: list[MessageLike],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        if self.mode != "count_only" and self.budget.exhausted:
            if self.mode == "hard":
                raise BudgetExceeded(
                    f"delegation budget exhausted: {self.budget.spent}/{self.budget.max_tokens} tokens"
                )
            _log.warning("budget exhausted (soft): returning truncation notice")
            return CompletionResult(
                content=(
                    "[budget exhausted] This delegation hit its token budget "
                    f"({self.budget.max_tokens} tokens). Summarize what you have."
                ),
                model=model or "budget",
            )

        effective_max = max_tokens
        if self.mode != "count_only":
            # Clamp the response size to what's left so the last call can't blow past
            # the ceiling by more than the prompt side.
            remaining = self.budget.remaining
            effective_max = min(max_tokens, remaining) if max_tokens else remaining

        result = self.inner.complete(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=effective_max,
            tools=tools,
            **kwargs,
        )
        prompt_text = "\n".join(str(m) for m in messages)
        self.budget.consume(
            result.prompt_tokens,
            result.completion_tokens,
            text_fallback=prompt_text + (result.content or ""),
        )
        self.budget.note_cache(result.cache_read_tokens, result.cache_write_tokens)
        return result


def _never_billed(exc: BaseException) -> bool:
    """True when ``exc`` provably left before a provider could bill anything.

    Only these give their reservation back. A missing key is raised before any request is built; a
    nested ceiling refuses before its call; a context overflow or a content-policy refusal is the
    provider declining the prompt (``RecoveryAction.ABORT``), which generates nothing. Everything
    else (a timeout, a cut stream, an unknown error, an interrupt that may have landed mid-call)
    may have been billed after the tokens were produced, and keeps its reservation.

    Forfeiting all of them made a loop that retried after a context overflow reach its typed
    ``max_usd`` having spent nothing: each refusal charged the whole completion ceiling.
    """
    from chimera.providers.failover import FailoverReason, classify
    from chimera.providers.gateway import MissingCredentialsError

    if isinstance(exc, (MissingCredentialsError, BudgetExceeded)):
        return True
    if not isinstance(exc, Exception):
        return False
    return classify(exc) in (FailoverReason.CONTEXT_OVERFLOW, FailoverReason.CONTENT_POLICY)


class SpendCappedBackend:
    """A ``SupportsComplete`` that enforces ONE dollar ceiling across a whole fan-out.

    :class:`SpendBudget` is built inside ``Agent.run`` from ``AgentConfig.max_usd``, deliberately —
    one Agent serves several runs and a cap carried between them would refuse the second task for
    what the first spent. That is right for an agent and wrong for a fan-out: N workers each get
    their own ceiling, so a "$1 run" can spend N dollars, and the decompose and the synthesis are
    not inside any worker at all.

    So the ceiling goes around the BACKEND instead, once, and every call the run makes passes
    through it — decompose, each worker, each verifier re-ask, the synthesis. Wrapping outermost is
    what makes that true: a per-delegation :class:`BudgetedBackend` layered on top of this one still
    reaches the model through here.

    **Reserve, call, settle.** A fan-out calls this from N threads, and checking the ceiling, calling
    and recording must not let N threads all read "under budget" and then all spend. That used to be
    solved by holding the lock across the whole model call, which made every capped fan-out run its
    members one at a time (study 30 measured 4 workers x 1.0 s at 4.01 s capped, 1.00 s uncapped),
    fused panels included, since the hierarchy route builds ``FusionEngine(capped)``. Now each call
    reserves its worst case (prompt estimate + ``max_tokens``, at the rate of the dearest model in
    its fallback chain) under the lock, runs without it, and settles on the usage the provider
    reports. The reservation counts against the ceiling while the call is in flight, so admission
    is no looser than the queue was as long as the reservation covers what the call really costs;
    the two known gaps (retries inside one call, a low prompt estimate) are in :meth:`_worst_case`.

    A call that RAISES after it may have reached a provider keeps its reservation and sets
    ``SpendBudget.estimated``, which the refusal sentence names: it may have been billed (a timeout
    after generation, a cut stream), and the old code charged it nothing. An error that provably
    left first (no key, a nested ceiling, a context overflow or content-policy refusal) releases it.

    **The cap can still be passed by one call**, as it could before: admission checks
    ``spent + reserved < max_usd``, not ``+ this call's worst case``, so the last call admitted may
    end the run up to its own worst case past the ceiling (and past that if the chars/4 prompt
    estimate was low). Refusing a call whose worst case alone does not fit would change which runs
    are allowed to start, which is the owner's call under the 2026-09-27 limits decision.

    A call whose worst case cannot be priced (no model or no completion bound known, or a model
    with no price) still holds the lock for its whole duration when there is a ceiling. Reserving
    zero for it would reopen the race; queueing it is the old behaviour, kept where nothing better
    is known.
    """

    def __init__(self, inner: SupportsComplete, budget: SpendBudget) -> None:
        self.inner = inner
        self.budget = budget
        self._lock = threading.Lock()

    def complete(self, messages: list[MessageLike], **kwargs: Any) -> CompletionResult:
        hold = self._worst_case(messages, kwargs)
        if hold is None and self.budget.capped:
            with self._lock:
                why = self.budget.blocked()
                if why is not None:
                    raise SpendExceeded(why)
                result = self.inner.complete(messages, **kwargs)
                self.budget.record_result(result)
                return result

        with self._lock:
            why = self.budget.blocked()
            if why is not None:
                raise SpendExceeded(why)
            if hold is not None:
                self.budget.reserve(hold)
        try:
            result = self.inner.complete(messages, **kwargs)
        except BaseException as exc:
            if hold is not None:
                with self._lock:
                    if _never_billed(exc):
                        self.budget.release(hold)
                    else:
                        self.budget.forfeit(hold)
            raise
        with self._lock:
            if hold is not None:
                self.budget.release(hold)
            # By stages when the turn has them: a fused turn answers as `model="fusion"`, which no
            # price table resolves, and charging that at $0.00 would let the cap sleep through the
            # most expensive call the app makes.
            self.budget.record_result(result)
        return result

    def _worst_case(self, messages: list[MessageLike], kwargs: dict[str, Any]) -> float | None:
        """The most this call can cost, in dollars, or None when that cannot be known.

        Priced over every model the call may ANSWER on, not only the one it asks first.
        ``LLMGateway.planned_calls`` names the primary and each configured fallback with its own
        completion bound, and the reservation is the dearest of them: a fallback that costs more
        than the primary answers inside the same ``complete()``, and a ``:free`` or local primary
        prices at $0, which reserved nothing and admitted every thread (10 against a $1 cap, $10
        spent, before this). Any leg that cannot be priced makes the whole call unknown.

        A backend that names only its primary (``planned_call``) or nothing at all is trusted only
        when that primary costs something: a $0 primary on a backend that cannot list its fallbacks
        says nothing about what will answer, so it is treated as unknown and queued.

        What this does not cover: retries INSIDE one ``complete()`` (a key rotated after a timeout
        is a second attempt, and if the provider billed the first the reservation counted one).
        The serial lock never charged those either: it recorded the answering attempt only.
        """
        from chimera.orchestration.receipts import price_delegation

        model: object = kwargs.get("model")
        bound: object = kwargs.get("max_tokens")
        legs: list[tuple[object, object]]
        whole_chain = False
        plan_all = getattr(self.inner, "planned_calls", None)
        plan_one = getattr(self.inner, "planned_call", None)
        try:
            if callable(plan_all):
                legs = list(plan_all(model, bound))
                whole_chain = True
            elif callable(plan_one):
                legs = [plan_one(model, bound)]
            else:
                legs = [(model, bound)]
        except Exception:  # a backend that cannot plan is one whose worst case is unknown
            return None
        text = "\n".join(str(m) for m in messages)
        tools = kwargs.get("tools")
        if tools:
            text += str(tools)
        prompt = estimate_tokens(text)
        costs: list[float] = []
        for leg_model, leg_bound in legs:
            if not isinstance(leg_model, str) or not leg_model or not isinstance(leg_bound, int):
                return None
            usd = price_delegation(leg_model, prompt, leg_bound)
            if usd is None:
                return None
            costs.append(usd)
        if not costs:
            return None
        hold = max(costs)
        if hold <= 0 and not whole_chain:
            return None
        return hold

    def __getattr__(self, name: str) -> Any:
        """Everything else is the wrapped backend's. A gateway carries more than ``complete``, and a
        wrapper that hides the rest breaks callers that never spend a cent."""
        return getattr(self.inner, name)


@dataclass(frozen=True)
class EffortPolicy:
    """Harness-enforced effort scaling: how many workers / how big a budget per shape.

    Anthropic's documented failure mode is the lead agent spawning many subagents
    for trivial asks; these numbers cap that in code, not in prose.
    """

    simple_budget: int = 3_000
    complex_budget: int = 8_000
    max_parallel_workers: int = 4

    def workers_for(self, shape: str, subtask_count: int) -> int:
        """How many workers a task shape may actually get (never more than asked)."""
        if shape == "simple":
            return min(1, subtask_count)
        return max(1, min(subtask_count, self.max_parallel_workers))

    def budget_for(self, shape: str) -> int:
        """Per-delegation token budget for a task shape."""
        return self.simple_budget if shape == "simple" else self.complex_budget
