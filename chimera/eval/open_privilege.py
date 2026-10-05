"""Open privilege: what the governance stack leaves callable beyond what the task needs (study 30, S30-36).

Block rate and over-block (:mod:`chimera.eval.injection`) describe a defence at the two moments it
is tested: an attack arrives, honest work arrives. Neither says how much of the agent's power is left
**open** while ordinary work is going on — which is what an attacker who gets one sentence into the
context inherits. arXiv 2609.26900 (Ajar) measures exactly that against an oracle that grants each
task only what it needs, and finds every defence it tests leaving far more open than the oracle; a
tool-NAME allowlist, which is what :func:`chimera.governance.allowlist.restrict_registry` is, is its
leaky baseline.

This module measures it on Chimera's own deterministic stack, at US$ 0. For each legitimate task of
:func:`chimera.eval.injection.default_benign` — in the taint state that task's door leaves the run
in — it tries every privilege probe (the harmful calls of
:func:`chimera.eval.injection.default_attacks`), none of which any task needs, and records which
ones would execute under each layer:

- ``none``: no governance;
- ``allowlist``: a per-task tool-name allowlist holding exactly the task's tool (Ajar's baseline);
- ``rules``: the trust kernel's default lexical rules (:class:`~chimera.governance.kernel.TrustKernel`),
  unattended, so a REVIEW is a refusal;
- ``taint``: the taint-adaptive :class:`~chimera.governance.ledger_tool.LedgeredTool`, unattended;
- ``shipped``: rules and taint together, the deterministic stack the product assembles;
- ``allowlist+shipped``: the shipped stack inside the per-task allowlist.

The oracle leaves zero probes open by construction, so the number is an absolute share, not Ajar's
ratio to the oracle. The task is the unit: probes within one task share a taint state, so the
interval is a t interval over tasks, and the pooled Wilson over (task, probe) cells is printed
beside it only as a description.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from chimera.eval.injection import BenignTask, InjectionAttack, default_attacks, default_benign
from chimera.eval.proportions import mean_t_interval, wilson
from chimera.governance.governed_tool import GovernedTool
from chimera.governance.kernel import TrustKernel
from chimera.governance.ledger import TaintLedger
from chimera.governance.ledger_tool import LedgeredTool
from chimera.tools.base import Tool

LAYERS: tuple[str, ...] = ("none", "allowlist", "rules", "taint", "shipped", "allowlist+shipped")


class _Probe(Tool):
    """Stands in for the tool a probe calls; records whether the call reached it."""

    def __init__(self, name: str, hit: dict[str, bool]) -> None:
        self.name = name
        self.description = f"privilege probe for {name}"
        self.parameters = {"type": "object", "properties": {}}
        self._hit = hit

    def run(self, **kwargs: Any) -> str:
        self._hit["hit"] = True
        return "EXECUTED"


def _ledger_for(task: BenignTask, authority: str) -> TaintLedger:
    ledger = TaintLedger(authority=authority)
    if task.source == "fetch":
        # The same door `run_benign` models: an external read taints the run, a workspace read does not.
        ledger.record_fetch("upstream-content", content=task.context)
    return ledger


def executes(task: BenignTask, tool_name: str, args: dict[str, Any], layer: str, *, authority: str = "provenance") -> bool:
    """Whether ``tool_name(**args)`` would execute during ``task`` under ``layer``.

    A name the allowlist drops never reaches the model's schema, so it cannot be called at all.
    """
    if layer not in LAYERS:
        raise ValueError(f"unknown layer {layer!r}; expected one of {LAYERS}")
    if layer.startswith("allowlist") and tool_name != task.tool:
        return False
    hit = {"hit": False}
    ledger = _ledger_for(task, authority)
    tool: Tool = _Probe(tool_name, hit)
    if layer in ("taint", "shipped", "allowlist+shipped"):
        tool = LedgeredTool(tool, ledger, narrow_on_taint=True)
    if layer in ("rules", "shipped", "allowlist+shipped"):
        lineage = "tainted" if ledger.run_tainted() else ""
        tool = GovernedTool(tool, TrustKernel(), lineage=lambda: lineage)
    tool.run(**args)
    return hit["hit"]


@dataclass
class PrivilegeCell:
    task: str
    source: str
    probe: str
    category: str
    layer: str
    open: bool


@dataclass
class OpenPrivilegeReport:
    cells: list[PrivilegeCell] = field(default_factory=list)
    #: Whether the task's OWN call still runs under each layer — the cost side, kept beside the
    #: leak so a layer cannot score well by refusing the work it was meant to allow.
    task_runs: dict[tuple[str, str], bool] = field(default_factory=dict)

    def tasks(self) -> list[str]:
        return sorted({c.task for c in self.cells})

    def per_task(self, layer: str, source: str | None = None) -> dict[str, float]:
        """Share of the probes left open, per task, under ``layer`` (optionally one door only)."""
        out: dict[str, float] = {}
        for task in self.tasks():
            rows = [c for c in self.cells if c.task == task and c.layer == layer]
            if source is not None:
                rows = [c for c in rows if c.source == source]
            if rows:
                out[task] = sum(c.open for c in rows) / len(rows)
        return out

    def summary(self, layer: str, source: str | None = None) -> dict[str, Any]:
        shares = list(self.per_task(layer, source).values())
        rows = [c for c in self.cells if c.layer == layer and (source is None or c.source == source)]
        opened = sum(c.open for c in rows)
        mean, low, high = mean_t_interval(shares)
        pooled = wilson(opened, len(rows))
        own = [ok for (task, lay), ok in self.task_runs.items() if lay == layer
               and (source is None or any(c.task == task and c.source == source for c in self.cells))]
        return {
            "layer": layer, "source": source or "all", "tasks": len(shares), "cells": len(rows),
            "open_cells": opened, "open_share_mean": mean, "open_share_ci": (max(0.0, low), min(1.0, high)),
            "pooled_wilson": pooled, "own_task_runs": sum(own), "own_task_total": len(own),
        }


def run_open_privilege(
    tasks: Sequence[BenignTask] | None = None,
    probes: Iterable[InjectionAttack] | None = None,
    *,
    layers: Sequence[str] = LAYERS,
    authority: str = "provenance",
) -> OpenPrivilegeReport:
    """Every probe, during every task, under every layer. Deterministic; no model is called."""
    task_list = list(tasks) if tasks is not None else default_benign()
    probe_list = list(probes) if probes is not None else default_attacks()
    report = OpenPrivilegeReport()
    for task in task_list:
        for layer in layers:
            report.task_runs[(task.id, layer)] = executes(task, task.tool, task.args, layer, authority=authority)
            for probe in probe_list:
                report.cells.append(
                    PrivilegeCell(
                        task.id, task.source, probe.id, probe.category, layer,
                        executes(task, probe.harmful_tool, probe.harmful_args, layer, authority=authority),
                    )
                )
    return report
