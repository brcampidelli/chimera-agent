"""Cascade benchmark (M16-A6): weak-only vs mid-only vs cascade vs fusion.

Four arms over the same deterministic task suite, reporting pass rate and
tokens-per-pass per arm. The published success criterion (stated before running,
FrugalGPT's 50-98% claim is the hypothesis, our number is whatever we measure):
**cascade >= mid-only pass rate at materially lower cost.**

Token accounting is honest: the cascade arm's cost is the SUM over every hop it
tried (read back from the route log), not just the accepted hop — escalations
are paid for, so they are counted. The summary reports the per-arm cost TAIL
(p50/p95/p99/max), not just the mean — a cascade can look cheap on average while
a few tasks escalate all the way to fusion, and a budget must plan for that tail.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from chimera.eval.continuous import EvalTask
from chimera.fusion.cascade import CascadeBackend, CascadeConfig
from chimera.fusion.route_log import load_routes
from chimera.providers.gateway import CompletionResult, SupportsComplete

ARMS = ("weak", "mid", "cascade", "fusion")


def _percentile(values: list[int], q: float) -> float | None:
    """The q-th percentile (0-100) of ``values`` by linear interpolation. None if empty.

    The mean hides the tail: a cascade arm can have a great average while a handful of tasks escalate
    all the way to fusion. p95/p99 surface that worst-case cost, which is what a budget must plan for.
    """
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    pos = q / 100 * (len(xs) - 1)
    lo = int(pos)
    frac = pos - lo
    hi = min(lo + 1, len(xs) - 1)
    return round(xs[lo] + frac * (xs[hi] - xs[lo]), 1)


@dataclass
class CascadeRow:
    """One task across the four arms."""

    task_id: str
    ok: dict[str, bool] = field(default_factory=dict)
    tokens: dict[str, int | None] = field(default_factory=dict)


@dataclass
class CascadeReport:
    rows: list[CascadeRow] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        n = len(self.rows)
        if n == 0:
            return {"n": 0}
        out: dict[str, object] = {"n": n}
        for arm in ARMS:
            passes = sum(1 for r in self.rows if r.ok.get(arm))
            spent = [t for t in (r.tokens.get(arm) for r in self.rows) if t is not None]
            total = sum(spent) if spent else None
            out[f"{arm}_pass_rate"] = round(passes / n, 4)
            out[f"{arm}_tokens"] = total
            out[f"{arm}_tokens_per_pass"] = (
                round(total / passes, 1) if total is not None and passes else None
            )
            # Tail cost, not just the mean: what a budget must actually plan for.
            out[f"{arm}_tokens_p50"] = _percentile(spent, 50)
            out[f"{arm}_tokens_p95"] = _percentile(spent, 95)
            out[f"{arm}_tokens_p99"] = _percentile(spent, 99)
            out[f"{arm}_tokens_max"] = max(spent) if spent else None
        return out


def _tokens_of(result: CompletionResult) -> int | None:
    if result.prompt_tokens is None and result.completion_tokens is None:
        return None
    return (result.prompt_tokens or 0) + (result.completion_tokens or 0)


def run_cascade_bench(
    gateway: SupportsComplete,
    fusion: SupportsComplete,
    tasks: list[EvalTask],
    *,
    weak: str,
    mid: str,
    entry: str = "weak",
    log_dir: Path | None = None,
) -> CascadeReport:
    """Run all four arms per task. ``fusion`` is the fusion backend (top rung + arm)."""
    report = CascadeReport()
    workdir = Path(log_dir) if log_dir is not None else Path(tempfile.mkdtemp(prefix="cascade-bench-"))
    for task in tasks:
        row = CascadeRow(task_id=task.id)
        messages = [{"role": "user", "content": task.prompt}]

        # Arm 1/2 — single-model at each tier.
        for arm, slug in (("weak", weak), ("mid", mid)):
            result = gateway.complete(list(messages), model=slug)
            row.ok[arm] = task.check(result.content)
            row.tokens[arm] = _tokens_of(result)

        # Arm 3 — the cascade; cost = SUM over hops, read back from its route log.
        log_path = workdir / f"{task.id}.jsonl"
        backend = CascadeBackend(
            gateway, fusion,
            CascadeConfig(weak=weak, mid=mid, entry=entry, log_path=log_path),
        )
        cascade_result = backend.complete(list(messages))
        row.ok["cascade"] = task.check(cascade_result.content)
        records = load_routes(log_path)
        row.tokens["cascade"] = (
            sum(sum(r.tokens_by_tier.values()) for r in records) if records else None
        )

        # Arm 4 — fusion straight.
        fused = fusion.complete(list(messages))
        row.ok["fusion"] = task.check(fused.content)
        row.tokens["fusion"] = _tokens_of(fused)

        report.rows.append(row)
    return report
