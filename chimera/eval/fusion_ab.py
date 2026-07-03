"""A/B benchmark: full fusion vs selective fusion.

Selective fusion (:mod:`chimera.fusion.engine`) only pays off if it cuts tokens
*without* dropping accuracy. This harness runs the same task set through both modes
against the same backend, using the per-stage token telemetry to measure cost and a
task's deterministic ``check`` to measure correctness. It reports the token reduction,
the accuracy delta, and — crucially — the accuracy split between early-stopped and
escalated turns, so a regression that only hits the agreement-heavy bucket is visible.

The verdict rule to keep honest: only prefer selective as a default if
``selective_accuracy >= full_accuracy - 1pp`` on a suite of a meaningful size.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from chimera.eval.continuous import EvalTask
from chimera.fusion.engine import FusionConfig, FusionEngine
from chimera.providers.gateway import Message, SupportsComplete


@dataclass
class ABRow:
    """One task's outcome under both fusion modes."""

    task_id: str
    full_ok: bool
    full_tokens: int | None
    selective_ok: bool
    selective_tokens: int | None
    early_stopped: bool


@dataclass
class ABReport:
    """Full vs selective results across a task suite."""

    rows: list[ABRow] = field(default_factory=list)

    def summary(self) -> dict[str, float]:
        n = len(self.rows)
        if not n:
            return {"tasks": 0.0}
        full_acc = sum(r.full_ok for r in self.rows) / n
        sel_acc = sum(r.selective_ok for r in self.rows) / n
        full_toks = [r.full_tokens for r in self.rows if r.full_tokens is not None]
        sel_toks = [r.selective_tokens for r in self.rows if r.selective_tokens is not None]
        full_avg = sum(full_toks) / len(full_toks) if full_toks else 0.0
        sel_avg = sum(sel_toks) / len(sel_toks) if sel_toks else 0.0
        early = [r for r in self.rows if r.early_stopped]
        escalated = [r for r in self.rows if not r.early_stopped]
        return {
            "tasks": float(n),
            "full_accuracy": round(full_acc, 3),
            "selective_accuracy": round(sel_acc, 3),
            "accuracy_delta_pp": round((sel_acc - full_acc) * 100, 1),
            "full_avg_tokens": round(full_avg, 1),
            "selective_avg_tokens": round(sel_avg, 1),
            "token_reduction_pct": round((1 - sel_avg / full_avg) * 100, 1) if full_avg else 0.0,
            "pct_early_stopped": round(len(early) / n * 100, 1),
            "selective_acc_early_stopped": (
                round(sum(r.selective_ok for r in early) / len(early), 3) if early else 0.0
            ),
            "selective_acc_escalated": (
                round(sum(r.selective_ok for r in escalated) / len(escalated), 3)
                if escalated
                else 0.0
            ),
        }


def _run_one(engine: FusionEngine, task: EvalTask) -> tuple[bool, int | None, bool]:
    """Run one task; return (passed, total_tokens, early_stopped). A crash counts as a fail."""
    try:
        trace = engine.run([Message(role="user", content=task.prompt)])
        return bool(task.check(trace.final)), trace.total_tokens(), trace.early_stopped
    except Exception:  # a crashing task is a failure, never aborts the suite
        return False, None, False


def run_fusion_ab(
    backend: SupportsComplete,
    tasks: Iterable[EvalTask],
    *,
    full_config: FusionConfig | None = None,
    selective_config: FusionConfig | None = None,
) -> ABReport:
    """Run each task through full and selective fusion against ``backend``."""
    full_engine = FusionEngine(backend, replace(full_config or FusionConfig.from_settings(), mode="full"))
    sel_engine = FusionEngine(
        backend, replace(selective_config or FusionConfig.from_settings(), mode="selective")
    )
    report = ABReport()
    for task in tasks:
        full_ok, full_tokens, _ = _run_one(full_engine, task)
        sel_ok, sel_tokens, early = _run_one(sel_engine, task)
        report.rows.append(
            ABRow(
                task_id=task.id,
                full_ok=full_ok,
                full_tokens=full_tokens,
                selective_ok=sel_ok,
                selective_tokens=sel_tokens,
                early_stopped=early,
            )
        )
    return report
