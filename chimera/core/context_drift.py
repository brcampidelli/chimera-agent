"""Context drift: is this trajectory still going somewhere?

Two different failures get called "context problems" and they need different instruments.

*Rot* happens inside a single step: attention thins out across a long prompt, so a model reading
180k tokens answers worse than the same model reading 20k. It is a property of one call.

*Drift* happens across a trajectory. Nothing is wrong with any individual step — each prompt is
answerable, each tool call is reasonable — but over dozens of turns the run stops accumulating and
starts circling. It re-reads what it read, re-derives what it concluded, and burns budget going
nowhere. Every step looks fine; only the shape of the whole run shows it.

:class:`~chimera.core.tool_loop.ToolLoopDetector` already catches the tight version of this — a
12-call sliding window that trips on identical repeats, ping-pong and back-to-back polling. By
construction it cannot see drift: a run that revisits the same three files every twenty turns passes
through a 12-call window without a single trip. That detector answers "is this spinning right now?".
This one answers "has this run stopped getting anywhere?".

**Every signal here is a comparison between the early and late parts of the run, never a total.**
A long, productive run repeats itself plenty; that is not drift. Drift is *degradation* — the rate
getting worse as the run goes on. An absolute threshold would flag exactly the long useful runs this
is supposed to protect.

**A known bias, stated rather than hidden.** Redundancy is measured against everything the run has
already seen, so the late half has strictly more history available to repeat. That asymmetry is
inherent to the question — "is it re-deriving what it already knew" only makes sense against full
history — and it does bias the late rate upward. Two things compensate: a signal must clear an
absolute floor *and* a ratio, and a run whose redundancy is high but flat is deliberately not
flagged (see the tests). Choosing an unbiased metric here would mean measuring something other than
the thing we care about.

**What this deliberately does not do:** decide what to do about it. Stopping, re-planning and
force-compacting are all plausible responses and we have no evidence about which one helps. Picking
one now would bake in an assumption nobody measured — which is the failure mode this whole line of
work exists to avoid. The detector reports; the caller decides.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.core.steplog import StepRecord

#: Below this many steps the halves are too small to compare — 4 steps a side is noise, not a trend.
MIN_STEPS = 10
#: Each side needs this many tool calls before a rate means anything. With 2 calls, one repeat is 50%.
MIN_CALLS_PER_SIDE = 4
#: A signal must clear this rate outright. Stops a 2%→6% tripling from reading as degradation.
DEFAULT_FLOOR = 0.4
#: ...and be at least this many times the early rate. Stops a high-but-flat run from being flagged.
DEFAULT_RATIO = 2.0


@dataclass(frozen=True)
class DriftSignal:
    """One measurement that crossed both thresholds, with the numbers that raised it."""

    name: str
    early: float
    late: float
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "early": round(self.early, 3),
            "late": round(self.late, 3),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class DriftReport:
    """What the trajectory looks like — or an honest admission that it is too short to tell."""

    #: False when the run was too short to compare. NOT the same as "no drift": a report that says
    #: "we could not tell" must never be read as a clean bill of health.
    assessed: bool
    steps: int
    signals: tuple[DriftSignal, ...] = ()

    @property
    def drifting(self) -> bool:
        """True only when something was actually measured AND crossed the thresholds."""
        return self.assessed and bool(self.signals)

    @property
    def summary(self) -> str:
        """One line naming what fired, for an event or a log. Empty when nothing did."""
        return "; ".join(s.detail for s in self.signals)

    def as_dict(self) -> dict[str, Any]:
        return {
            "assessed": self.assessed,
            "drifting": self.drifting,
            "signals": [s.as_dict() for s in self.signals],
        }


def _calls(steps: Sequence[StepRecord]) -> list[tuple[str, str, str, bool]]:
    """Flatten to (name, arguments, observation, ok), in order."""
    return [(t.name, t.arguments, t.observation, t.ok) for s in steps for t in s.tools]


def _redundancy(
    calls: Sequence[tuple[str, str, str, bool]], seen: set[tuple[str, str, str]]
) -> float:
    """Share of these calls that reproduced a (tool, args, observation) triple already in ``seen``.

    The observation is part of the key on purpose. Reading a file, editing it, then reading it again
    is the same tool with the same arguments and is *progress* — the answer changed. Keying on
    (tool, args) alone, which is right for a tight-loop detector, would call that a repeat. What
    counts here is asking a question you already had the answer to.

    ``seen`` is mutated as we go, so a call is judged against everything before it.
    """
    if not calls:
        return 0.0
    repeats = 0
    for name, args, obs, _ok in calls:
        key = (name, args, obs)
        if key in seen:
            repeats += 1
        else:
            seen.add(key)
    return repeats / len(calls)


def _failure_rate(calls: Sequence[tuple[str, str, str, bool]]) -> float:
    if not calls:
        return 0.0
    return sum(1 for *_rest, ok in calls if not ok) / len(calls)


def _crossed(early: float, late: float, floor: float, ratio: float) -> bool:
    """Both tests, always. The floor rejects noise; the ratio rejects a run that was always like
    this. Either alone produces the false positive the other exists to prevent."""
    if late < floor:
        return False
    if early <= 0.0:
        return True  # nothing early, a lot late — the sharpest form of the thing we are looking for
    return late >= early * ratio


def assess(
    steps: Sequence[StepRecord],
    *,
    min_steps: int = MIN_STEPS,
    floor: float = DEFAULT_FLOOR,
    ratio: float = DEFAULT_RATIO,
) -> DriftReport:
    """Compare the first half of a run against the second, and report what got worse."""
    if len(steps) < min_steps:
        return DriftReport(assessed=False, steps=len(steps))

    mid = len(steps) // 2
    early_calls, late_calls = _calls(steps[:mid]), _calls(steps[mid:])
    if min(len(early_calls), len(late_calls)) < MIN_CALLS_PER_SIDE:
        # A run that barely used tools leaves nothing to compare. Saying "no drift" here would be
        # inventing a finding out of an absence of evidence.
        return DriftReport(assessed=False, steps=len(steps))

    signals: list[DriftSignal] = []

    seen: set[tuple[str, str, str]] = set()
    early_redundancy = _redundancy(early_calls, seen)
    late_redundancy = _redundancy(late_calls, seen)  # judged against the WHOLE run so far
    if _crossed(early_redundancy, late_redundancy, floor, ratio):
        signals.append(
            DriftSignal(
                "redundancy",
                early_redundancy,
                late_redundancy,
                f"{late_redundancy:.0%} of late tool calls re-derived a result the run already had "
                f"(was {early_redundancy:.0%} early)",
            )
        )

    early_failures, late_failures = _failure_rate(early_calls), _failure_rate(late_calls)
    if _crossed(early_failures, late_failures, floor, ratio):
        signals.append(
            DriftSignal(
                "failure",
                early_failures,
                late_failures,
                f"{late_failures:.0%} of late tool calls failed (was {early_failures:.0%} early)",
            )
        )

    post = _post_compaction(steps, floor, ratio)
    if post is not None:
        signals.append(post)

    return DriftReport(assessed=True, steps=len(steps), signals=tuple(signals))


def _post_compaction(
    steps: Sequence[StepRecord], floor: float, ratio: float
) -> DriftSignal | None:
    """Did redundancy jump right after history was dropped?

    This is the specific failure compaction is supposed to prevent and can cause: the run compacts,
    loses the thread, and starts re-establishing what it already knew. It is worth its own signal
    because it names a mechanism — the fix is the restoration payload, not the task — and because it
    is invisible in the whole-run split when the compaction happens late.
    """
    cut = next((i for i in range(len(steps) - 1, -1, -1) if steps[i].compacted), None)
    if cut is None:
        return None
    before, after = _calls(steps[: cut + 1]), _calls(steps[cut + 1 :])
    if min(len(before), len(after)) < MIN_CALLS_PER_SIDE:
        return None

    seen: set[tuple[str, str, str]] = set()
    before_rate = _redundancy(before, seen)
    after_rate = _redundancy(after, seen)
    if not _crossed(before_rate, after_rate, floor, ratio):
        return None
    return DriftSignal(
        "post_compaction",
        before_rate,
        after_rate,
        f"after history was dropped, {after_rate:.0%} of tool calls re-derived something the run "
        f"already had (was {before_rate:.0%} before)",
    )
