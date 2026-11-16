"""Why a turn of a Code conversation did not read the prompt cache.

Classification from facts, never from a model: the causes are a fixed list, each one a rule over
numbers this process already has (the gap since the last turn, the route's documented lifetime, the
fingerprints the step log keeps). The order of the rules is the order of certainty. A miss with a
known expiry is an expiry, whatever else also changed, because the entry was gone either way.

``unknown`` is a legitimate answer. It means no rule fired, and it says so instead of picking the
most plausible cause.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from chimera.core.steplog import StepLog

CAUSES: tuple[str, ...] = (
    "none",
    "first_turn",
    "ttl_expired",
    "prefix_changed",
    "tools_changed",
    "model_or_route_changed",
    "provider_no_cache",
    "unknown",
)


@dataclass(frozen=True)
class CacheFacts:
    """Everything the classifier reads. ``None`` on a comparison means it could not be made."""

    cache_read_tokens: int | None
    first_turn: bool
    explicit_cache: bool
    ttl_seconds: float | None
    gap_seconds: float | None
    model_changed: bool | None = None
    route_changed: bool | None = None
    system_changed: bool | None = None
    prefix_changed: bool | None = None
    tools_changed: bool | None = None


def classify_cache_miss(facts: CacheFacts) -> str:
    """One cause from :data:`CAUSES`. Pure: the same facts always give the same answer."""
    if facts.cache_read_tokens is None:
        # The provider said nothing about the cache. That is not a miss, so there is no cause to name.
        return "unknown"
    if facts.cache_read_tokens > 0:
        return "none"
    if facts.first_turn:
        return "first_turn"
    if facts.model_changed or facts.route_changed:
        return "model_or_route_changed"
    if (
        facts.ttl_seconds is not None
        and facts.gap_seconds is not None
        and facts.gap_seconds > facts.ttl_seconds
    ):
        return "ttl_expired"
    # The prefix fingerprint covers the system message AND the tool schemas, so it moves when only the
    # tools did. The two parts are therefore read first, and the combined one only for what neither
    # explains (a developer-role message, say). A change of both is the system's: it is named first.
    if facts.system_changed:
        return "prefix_changed"
    if facts.tools_changed:
        return "tools_changed"
    if facts.prefix_changed:
        return "prefix_changed"
    # After the changes: a route that caches by itself still misses when its prefix moved, and
    # saying "this provider does not cache" over a real change would hide the cause.
    if not facts.explicit_cache:
        return "provider_no_cache"
    return "unknown"


def _changed(before: object, after: object) -> bool | None:
    """Whether a value changed between two turns, or None when either side never recorded it."""
    if not before or not after:
        return None
    return before != after


def turn_record(*, ended_at: float, model: str, steplog: StepLog) -> dict[str, Any]:
    """What the next turn of this conversation needs to know about this one: when it ended and what
    it opened with. Stored on the session document, so it survives a restart.

    The fingerprints are those of the turn's FIRST step, and the next turn's are read from ITS first
    step: the two calls that open a turn are the ones that carry the same system message and the
    same tool schema when nothing changed. A later step can send a narrower tool list (the tool
    router picks one per step), and comparing against it would call an ordinary turn a change.
    ``CHIMERA_DEFER_TOOLS`` keeps the declared list the same from step to step, so it does not
    disturb the comparison either way.
    """
    first = steplog.steps[0] if steplog.steps else None
    return {
        "ended_at": float(ended_at),
        "model": model,
        "provider": steplog.provider or "",
        "system_fingerprint": first.system_fingerprint if first else "",
        "tools_fingerprint": first.tools_fingerprint if first else "",
        "prefix_fingerprint": first.prefix_fingerprint if first else "",
    }


def external_turn_record(*, ended_at: float, model: str) -> dict[str, Any]:
    """The record of a turn an external agent ran: the pause is known, what it sent is not."""
    return {
        "ended_at": float(ended_at),
        "model": model,
        "provider": "",
        "system_fingerprint": "",
        "tools_fingerprint": "",
        "prefix_fingerprint": "",
    }


def facts_for_turn(
    *,
    previous: Mapping[str, Any] | None,
    model: str,
    steplog: StepLog,
    now: float,
    ttl_seconds: float | None,
    explicit_cache: bool,
    had_history: bool = False,
) -> tuple[CacheFacts, float | None]:
    """Facts for a turn that has just finished, against the turn before it. Returns them with the gap
    in seconds since that turn ended (None when there is no record of one).

    ``had_history`` says the conversation already held messages when this turn began. With no turn
    record and messages in it, the conversation is older than the record: it is not a first turn,
    and nothing is known about what came before, so no comparison is made and no rule can name one.
    """
    first = steplog.steps[0] if steplog.steps else None
    gap: float | None = None
    if previous is not None and isinstance(previous.get("ended_at"), (int, float)):
        gap = max(0.0, now - float(previous["ended_at"]))
    prev_model = str(previous.get("model") or "") if previous else ""
    prev_route = str(previous.get("provider") or "") if previous else ""
    prev_system = str(previous.get("system_fingerprint") or "") if previous else ""
    prev_prefix = str(previous.get("prefix_fingerprint") or "") if previous else ""
    prev_tools = str(previous.get("tools_fingerprint") or "") if previous else ""
    facts = CacheFacts(
        cache_read_tokens=steplog.cache_read_tokens,
        first_turn=previous is None and not had_history,
        explicit_cache=explicit_cache,
        ttl_seconds=ttl_seconds,
        gap_seconds=gap,
        model_changed=_changed(prev_model, model),
        route_changed=_changed(prev_route, steplog.provider or ""),
        system_changed=_changed(prev_system, first.system_fingerprint if first else ""),
        prefix_changed=_changed(prev_prefix, first.prefix_fingerprint if first else ""),
        tools_changed=_changed(prev_tools, first.tools_fingerprint if first else ""),
    )
    return facts, gap


#: Below this share of prompt tokens served from the cache, a conversation is paying noticeably more
#: than it needs to: a cached token costs about a tenth of a fresh one. The warning is about the
#: whole conversation, not a turn — one cold turn after a lunch break is the cache doing its job.
CACHE_WARN_THRESHOLD = 0.80

#: How many turns of a conversation the window keeps. A cap, not a policy: the sums below are over
#: what is kept, and a conversation long enough to drop its oldest turns is judged on its recent ones.
CACHE_WINDOW_TURNS = 200


@dataclass(frozen=True)
class CacheTurn:
    """What one turn contributes to the conversation's cache rate."""

    prompt_tokens: int
    cache_read_tokens: int | None
    """None when the route reported nothing about the cache: the turn is then outside the rate."""
    cause: str
    """The turn's :func:`classify_cache_miss` answer."""
    first_turn: bool = False

    def to_record(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cause": self.cause,
            "first_turn": self.first_turn,
        }

    @classmethod
    def from_record(cls, raw: object) -> CacheTurn | None:
        """The turn a stored record describes, or None when it is not one."""
        if not isinstance(raw, Mapping):
            return None
        prompt, read = raw.get("prompt_tokens"), raw.get("cache_read_tokens")
        if not isinstance(prompt, int) or isinstance(prompt, bool) or prompt < 0:
            return None
        if read is not None and (not isinstance(read, int) or isinstance(read, bool) or read < 0):
            return None
        return cls(
            prompt_tokens=prompt,
            cache_read_tokens=read,
            cause=str(raw.get("cause") or "unknown"),
            first_turn=bool(raw.get("first_turn")),
        )


@dataclass(frozen=True)
class CacheWarning:
    """The conversation's cache rate is under the threshold, and what cost the most tokens."""

    rate: float
    threshold: float
    cause: str
    turns: int
    """How many turns the rate was computed over (first turn and silent routes excluded)."""

    def to_record(self) -> dict[str, Any]:
        return {
            "rate": round(self.rate, 4),
            "threshold": self.threshold,
            "cause": self.cause,
            "turns": self.turns,
        }


def _counted(turns: Sequence[CacheTurn]) -> list[CacheTurn]:
    """The turns the rate is over: the route reported its cache, and it is not the opening turn.

    The first turn has nothing to read from, so its zero is not a miss of anything; and a silent route
    said nothing, so scoring it as zero would read as a broken cache that was never measured.
    """
    return [t for t in turns if t.cache_read_tokens is not None and not t.first_turn]


def _rate(turns: Sequence[CacheTurn]) -> tuple[float, int] | None:
    counted = _counted(turns)
    prompt = sum(t.prompt_tokens for t in counted)
    if prompt <= 0:
        return None
    read = sum(min(t.cache_read_tokens or 0, t.prompt_tokens) for t in counted)
    return read / prompt, len(counted)


def _heaviest_cause(turns: Sequence[CacheTurn]) -> str:
    """The cause of the most tokens that were sent fresh. A partial hit ("none" with tokens lost) has
    no rule that explains it, so its loss counts as ``unknown``; ties go to the cause listed first."""
    lost: dict[str, int] = dict.fromkeys(CAUSES, 0)
    for t in _counted(turns):
        gap = t.prompt_tokens - min(t.cache_read_tokens or 0, t.prompt_tokens)
        if gap <= 0:
            continue
        cause = t.cause if t.cause in lost and t.cause != "none" else "unknown"
        lost[cause] += gap
    return max(CAUSES, key=lambda c: (lost[c], -CAUSES.index(c)))


def session_cache_warning(
    turns: Sequence[CacheTurn], threshold: float = CACHE_WARN_THRESHOLD
) -> CacheWarning | None:
    """A warning when this turn took the conversation's cache rate from the threshold up to under it.

    ``rate = sum(min(cache_read, prompt)) / sum(prompt)`` over the turns the route reported a cache
    for, the first turn left out. It fires on the CROSSING: the rate over every turn but the last was
    at or over the threshold (or there was nothing to rate yet), and with the last it is under. A
    conversation that stays under does not repeat the warning each turn, and one that recovers and
    falls again warns again, because the next call is judged against the recovered rate. Exactly the
    threshold is not under it. A route that never reports a cache has no rate and never warns.

    Pure: the same turns always give the same answer.
    """
    if not turns:
        return None
    return cache_warning_between(turns[:-1], turns, threshold)


def cache_warning_between(
    before: Sequence[CacheTurn], after: Sequence[CacheTurn], threshold: float = CACHE_WARN_THRESHOLD
) -> CacheWarning | None:
    """The warning for a move from the ``before`` window to the ``after`` window.

    Each rate is computed over exactly the turns of its own window. A conversation that keeps only
    its last N turns passes the window it HELD and the window it HOLDS now, so the oldest turn that
    fell out is out of the "after" rate and in the "before" one, as it is for the person looking.
    """
    now = _rate(after)
    if now is None or now[0] >= threshold:
        return None
    prior = _rate(before)
    if prior is not None and prior[0] < threshold:
        return None
    return CacheWarning(rate=now[0], threshold=threshold, cause=_heaviest_cause(after), turns=now[1])
