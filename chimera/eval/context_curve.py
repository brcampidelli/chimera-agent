"""Did runs carrying more context do worse? Measured on our own logs, or not claimed at all.

The literature calls it context rot, and it is the stated reason to build compaction. Building it on
someone else's benchmark would be building on a number measured with a different loop, a different
model and a different task mix. This computes it on ours: join what a run ACHIEVED (`runs.jsonl`,
where the verifier's verdict lives) to what it was CARRYING (`traces.jsonl`, where the per-step
prompt size lives), bucket by peak context, and report a success rate per bucket with a confidence
interval.

**It refuses to answer on thin data, and that is the feature.** With a handful of runs per bucket the
rate swings twenty points on one flipped outcome, and a curve drawn through that noise is an argument
for whatever the author already believed. The floor is pre-registered in
``bench/context_curve/PREREGISTRATION.md`` and enforced here, so the decision to act is made before
the number is seen rather than after.

The join key is ``run_id``, present on both sides since the trace stopped being keyed by a truncated
task. Rows that cannot be joined are counted and reported — a silently dropped half of the data is
how a real effect gets measured away.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Pre-registered. Below this many joined runs in a bucket, the bucket reports no rate at all: the
#: Wilson interval at n=5 spans most of the unit interval, and a point estimate drawn from it is
#: decoration.
MIN_PER_BUCKET = 20

#: And below this in total, the whole analysis declines. Three populated buckets are the minimum
#: shape in which "it declines with context" is distinguishable from "one bucket is unlucky".
MIN_TOTAL = 60

#: Peak-context buckets, in prompt tokens. Wide and few on purpose: narrow buckets look precise and
#: put five runs in each.
BUCKETS: tuple[tuple[int, int], ...] = ((0, 8_000), (8_000, 32_000), (32_000, 128_000), (128_000, 2**31))


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — the one that stays inside [0,1] at small n, unlike the normal
    approximation that hands back a negative lower bound and makes a thin bucket look decisive."""
    if total == 0:
        return (0.0, 1.0)
    phat = successes / total
    denom = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denom
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * total)) / total) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


@dataclass
class Bucket:
    low: int
    high: int
    runs: int = 0
    successes: int = 0

    @property
    def rate(self) -> float | None:
        """None below the floor. A rate computed from four runs is a number, not a measurement."""
        return (self.successes / self.runs) if self.runs >= MIN_PER_BUCKET else None

    def as_dict(self) -> dict[str, Any]:
        low, high = wilson(self.successes, self.runs) if self.runs else (0.0, 1.0)
        return {
            "context_tokens": f"{self.low}–{'∞' if self.high >= 2**31 else self.high}",
            "runs": self.runs,
            "successes": self.successes,
            "rate": self.rate,
            "ci95": [round(low, 4), round(high, 4)] if self.runs >= MIN_PER_BUCKET else None,
        }


@dataclass
class CurveResult:
    buckets: list[Bucket] = field(default_factory=list)
    joined: int = 0
    traces_seen: int = 0
    attempts_seen: int = 0
    unjoinable_traces: int = 0
    unjoinable_attempts: int = 0

    @property
    def enough(self) -> bool:
        return self.joined >= MIN_TOTAL and sum(1 for b in self.buckets if b.rate is not None) >= 3

    def verdict(self) -> str:
        """What the numbers license, in one sentence — never more than that.

        Three outcomes, and the first is the one this project keeps having to say out loud: there is
        not enough data to answer. That is not a null result. A null result means the effect was
        looked for and not found; this means nobody has looked yet, and reporting the two the same
        way is how an absence of evidence gets published as evidence of absence.
        """
        if not self.enough:
            return (
                f"not enough data: {self.joined} joined run(s), "
                f"{sum(1 for b in self.buckets if b.rate is not None)} bucket(s) above the floor "
                f"(need {MIN_TOTAL} and 3). No claim either way."
            )
        rated = [b for b in self.buckets if b.rate is not None]
        first, last = rated[0], rated[-1]
        low_last, high_last = wilson(last.successes, last.runs)
        low_first, high_first = wilson(first.successes, first.runs)
        if high_last < low_first:
            return (
                f"success falls with context: {first.rate:.1%} in the smallest bucket vs "
                f"{last.rate:.1%} in the largest, intervals disjoint."
            )
        if low_last > high_first:
            return (
                f"success RISES with context ({first.rate:.1%} -> {last.rate:.1%}), which is the "
                "opposite of the expected effect and worth understanding before acting on either."
            )
        return (
            f"no separation: {first.rate:.1%} vs {last.rate:.1%}, intervals overlap. On this data "
            "compaction is not the lever to pull."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict(),
            "enough_data": self.enough,
            "joined_runs": self.joined,
            "traces_seen": self.traces_seen,
            "attempts_seen": self.attempts_seen,
            "unjoinable_traces": self.unjoinable_traces,
            "unjoinable_attempts": self.unjoinable_attempts,
            "min_per_bucket": MIN_PER_BUCKET,
            "min_total": MIN_TOTAL,
            "buckets": [b.as_dict() for b in self.buckets],
        }


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # a torn last line after a crash must not lose the file
    return out


def context_curve(traces: Path, runs: Path) -> CurveResult:
    """Join the two logs on ``run_id`` and bucket the joined runs by peak context."""
    peak_by_id = {
        str(row.get("run_id") or ""): int(row.get("context_peak_tokens") or 0)
        for row in _rows(traces)
        if row.get("run_id")
    }
    result = CurveResult(buckets=[Bucket(low, high) for low, high in BUCKETS])
    result.traces_seen = len(peak_by_id)

    used: set[str] = set()
    for run in _rows(runs):
        for attempt in run.get("attempts") or []:
            result.attempts_seen += 1
            run_id = str(attempt.get("run_id") or "")
            if not run_id or run_id not in peak_by_id:
                result.unjoinable_attempts += 1
                continue
            used.add(run_id)
            peak = peak_by_id[run_id]
            # `success` on the ATTEMPT, not on the run: the run's flag is the last attempt's, and
            # attributing a late success to the context of an early attempt would smear the very
            # relationship being measured.
            succeeded = bool(attempt.get("success"))
            for bucket in result.buckets:
                if bucket.low <= peak < bucket.high:
                    bucket.runs += 1
                    bucket.successes += int(succeeded)
                    result.joined += 1
                    break
    result.unjoinable_traces = len(peak_by_id) - len(used)
    return result
