"""A gate in front of every HOSTED decision ask: rate, then money — study 27, phase 3.

`decision-gate`'s contract, in our terms. A hosted backend (`hosted_verbalized`,
`openrouter_decisions`) is the user's key and the user's spend, and until this module nothing in the
decision path knew about either: the gateway rotates keys and cools a rate-limited credential for a
minute, but it is shared with chat and fusion, and a decision made in the middle of a burst had no
way to wait its turn.

* **Rate.** A sliding sixty-second window of requests and tokens. When the window holds 80% of a
  configured requests-per-minute or tokens-per-minute budget the next ask *waits* for the oldest
  entry to age out, up to :attr:`GateLimits.max_wait`, and past that it is refused rather than left
  hanging. A ``429`` with a ``Retry-After`` blocks the gate for that long, the same way.
* **Money.** A daily USD ceiling over what the decision log says today's hosted answers cost. An
  answer whose price was unknown makes the day unknown, and with a ceiling set that is a refusal,
  for the reason ``SpendBudget`` gives: a ceiling that skips what it cannot price shows green while
  the real spend climbs.

**The refusal is the one thing designed here.** A gated ask raises :class:`GateRefused`, which the
:class:`~chimera.decisions.contract.Decider` records as a halt *with the gate named on the receipt*
(``gate: "budget"`` or ``"rate"``), never as an answer and never as silence. Nothing is waved through
because the meter ran out; what the REVIEW band does with a halt that names the gate is
`chimera/governance/band.py`'s business, and it is the next change.

Off unless a limit is set: with none, :func:`gate_for` returns None and no backend is wrapped.

Every clock is injected (``clock``, ``wall``, ``sleep``), so the tests saturate the window without
waiting a minute.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from chimera.decisions.contract import DecisionBackend, Question, Reading

#: The window the per-minute budgets are measured over.
WINDOW_SECONDS = 60.0
#: Wait at this share of a budget already in flight (decision-gate's default).
WAIT_AT = 0.8
#: The longest a single ask is held. Past it the gate refuses instead of hanging a decision, and
#: through `Decider.decide` that is a halt the band answers, not a stalled agent.
MAX_WAIT_SECONDS = 30.0
#: What a 429 without a `Retry-After` is taken to mean.
DEFAULT_RETRY_AFTER = 5.0
#: The backends this gate applies to: the ones that spend someone's money. A local model spends
#: electricity, and a dollar ceiling is not about electricity.
HOSTED = frozenset({"hosted_verbalized", "openrouter_decisions"})


class GateRefused(RuntimeError):
    """The gate would not let this ask through. ``reason`` is ``"budget"`` or ``"rate"``."""

    def __init__(self, reason: str, text: str) -> None:
        super().__init__(text)
        self.reason = reason


@dataclass(frozen=True)
class GateLimits:
    rpm: int | None = None
    tpm: int | None = None
    daily_usd: float | None = None
    wait_at: float = WAIT_AT
    max_wait: float = MAX_WAIT_SECONDS

    @property
    def active(self) -> bool:
        return any(v is not None for v in (self.rpm, self.tpm, self.daily_usd))


def parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    """Seconds to wait from a ``Retry-After`` value: a number of seconds, or an HTTP date. None for
    a value that is neither, so the caller picks its own default instead of a guessed one."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, when.timestamp() - (time.time() if now is None else now))


def retry_after_of(exc: BaseException) -> float | None:
    """How long to hold off after ``exc``, when it was a 429 (httpx or a litellm-style error), else
    None. A 429 that names no wait is :data:`DEFAULT_RETRY_AFTER`."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
    if status != 429:
        return None
    headers = getattr(response, "headers", None) or {}
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except AttributeError:
        raw = None
    parsed = parse_retry_after(raw)
    return DEFAULT_RETRY_AFTER if parsed is None else parsed


def spend_today(path: Path, *, wall: float) -> tuple[float, int]:
    """``(usd, unknown)`` for today's (UTC) hosted answers in the decision log: what they cost, and
    how many carry no price. A cached reading cost nothing, and a halt made no call."""
    if not path.is_file():
        return 0.0, 0
    day_start = datetime.fromtimestamp(wall, UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    spent, unknown = 0.0, 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0.0, 0
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("kind") != "answer":
            continue
        if float(entry.get("at") or 0.0) < day_start:
            break  # the log is chronological: everything older is another day
        if entry.get("backend") not in HOSTED or entry.get("halt") or entry.get("cached"):
            continue
        usd = entry.get("usd")
        if isinstance(usd, int | float):
            spent += float(usd)
        else:
            unknown += 1
    return spent, unknown


class SpendRateGate:
    """One gate per process and per set of limits, shared by every backend it fronts."""

    def __init__(
        self,
        limits: GateLimits,
        *,
        ledger: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.limits = limits
        self._ledger = ledger
        self._clock, self._wall, self._sleep = clock, wall, sleep
        self._lock = threading.Lock()
        self._events: deque[tuple[float, int]] = deque()
        self._blocked_until = 0.0
        self._day: int | None = None
        self._spent = 0.0
        self._unknown = 0

    # -- money -----------------------------------------------------------------------------

    def _roll_day(self) -> None:
        """Re-read the log when the UTC day changes, and once at the start: a second process writing
        to the same log is seen at the next day, not on every ask (the cost of reading it each time
        is the whole file)."""
        today = int(self._wall() // 86400)
        if self._day != today:
            self._day = today
            self._spent, self._unknown = (
                spend_today(self._ledger, wall=self._wall()) if self._ledger is not None else (0.0, 0)
            )

    def _refuse_on_money(self) -> None:
        ceiling = self.limits.daily_usd
        if ceiling is None:
            return
        if self._unknown:
            raise GateRefused(
                "budget",
                f"{self._unknown} of today's hosted decisions carry no price, so the day's spend cannot be "
                "known and the daily ceiling cannot be honoured",
            )
        if self._spent >= ceiling:
            raise GateRefused("budget", f"daily decision ceiling reached: ${self._spent:.4f} of ${ceiling:.4f}")

    # -- rate ------------------------------------------------------------------------------

    def _purge(self, now: float) -> None:
        while self._events and now - self._events[0][0] >= WINDOW_SECONDS:
            self._events.popleft()

    def _wait_needed(self, tokens: int, now: float) -> float:
        """Seconds until this ask fits, 0 when it fits now."""
        waits = [max(0.0, self._blocked_until - now)]
        events = list(self._events)
        if self.limits.rpm is not None:
            allowed = max(1, math.floor(self.limits.wait_at * self.limits.rpm))
            if len(events) >= allowed:
                waits.append(events[len(events) - allowed][0] + WINDOW_SECONDS - now)
        if self.limits.tpm is not None and events:
            budget = self.limits.wait_at * self.limits.tpm
            total = sum(t for _, t in events) + tokens
            for at, used in events:  # oldest first: expire until it fits
                if total <= budget:
                    break
                total -= used
                waits.append(at + WINDOW_SECONDS - now)
        return max(waits)

    def admit(self, tokens: int) -> None:
        """Let one ask through, or wait for it to fit, or raise :class:`GateRefused`."""
        waited = 0.0
        while True:
            with self._lock:
                self._roll_day()
                self._refuse_on_money()
                now = self._clock()
                self._purge(now)
                wait = self._wait_needed(tokens, now)
                if wait <= 0.0:
                    self._events.append((now, tokens))
                    return
                if waited + wait > self.limits.max_wait:
                    raise GateRefused(
                        "rate",
                        f"the decision rate budget is in use; the wait ({waited + wait:.0f}s) exceeds "
                        f"{self.limits.max_wait:.0f}s",
                    )
            self._sleep(wait)
            waited += wait

    def note_rate_limited(self, seconds: float) -> None:
        """A 429 said to hold off for ``seconds``."""
        with self._lock:
            self._blocked_until = max(self._blocked_until, self._clock() + max(0.0, seconds))

    def record(self, usd: float | None) -> None:
        """Add what a finished ask cost; ``None`` is an unpriced answer and makes the day unknown."""
        with self._lock:
            if usd is None:
                self._unknown += 1
            else:
                self._spent += max(0.0, float(usd))


def estimate_tokens(state: str) -> int:
    """The state plus a modest reply. A budget check needs an order of magnitude, not a tokenizer."""
    return len(state) // 4 + 256


class GatedBackend:
    """A hosted backend behind a :class:`SpendRateGate`. Same protocol as the backend it wraps."""

    def __init__(self, inner: DecisionBackend, gate: SpendRateGate) -> None:
        self.inner = inner
        self.gate = gate
        self.name = inner.name
        self.model = inner.model

    def instrument(self, question: Question) -> str:
        return self.inner.instrument(question)

    def ask(self, state: str, question: Question) -> Reading:
        self.gate.admit(estimate_tokens(state))
        try:
            reading = self.inner.ask(state, question)
        except Exception as exc:
            hold = retry_after_of(exc)
            if hold is not None:
                self.gate.note_rate_limited(hold)
            raise
        self.gate.record(reading.usd)
        return reading

    def __getattr__(self, item: str) -> Any:
        # Whatever else the wrapped backend carries (timeout, gateway, reader) stays reachable.
        return getattr(self.inner, item)


_GATES: dict[tuple[Any, ...], SpendRateGate] = {}
_GATES_LOCK = threading.Lock()


def gate_for(settings: Any) -> SpendRateGate | None:
    """The process's gate for these settings, or None when no limit is set. One per (home, limits):
    every decider built from the same settings shares one window, which is the point of it."""
    limits = GateLimits(
        rpm=getattr(settings, "decision_rpm", None) or None,
        tpm=getattr(settings, "decision_tpm", None) or None,
        daily_usd=getattr(settings, "decision_daily_usd", None) or None,
    )
    if not limits.active:
        return None
    home = Path(getattr(settings, "home", "."))
    key = (str(home), limits)
    with _GATES_LOCK:
        gate = _GATES.get(key)
        if gate is None:
            gate = _GATES[key] = SpendRateGate(limits, ledger=home / "decisions" / "decisions.jsonl")
        return gate


def gated(backend: DecisionBackend, settings: Any) -> DecisionBackend:
    """``backend`` behind the gate when it is hosted and a limit is set, else ``backend`` itself."""
    if backend.name not in HOSTED:
        return backend
    gate = gate_for(settings)
    return backend if gate is None else GatedBackend(backend, gate)
