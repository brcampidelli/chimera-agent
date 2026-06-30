"""Workflow runner — executes a declarative workflow step by step.

Each step is gated on the previous step's result (``when``) and may loop (``repeat`` /
``until``). Step executors are injected, so the control flow is fully testable without
a model or a network. The real executors live in :mod:`chimera.workflow.executors`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from chimera.telemetry import get_logger
from chimera.workflow.models import When, Workflow, WorkflowStep

_log = get_logger("workflow.runner")


@dataclass
class StepResult:
    success: bool
    output: str = ""


class StepExecutor(Protocol):
    def __call__(self, step: WorkflowStep) -> StepResult: ...


@dataclass
class StepRun:
    name: str
    uses: str
    skipped: bool
    success: bool
    output: str
    attempts: int


@dataclass
class WorkflowResult:
    name: str
    success: bool
    runs: list[StepRun] = field(default_factory=list)


def _gate_open(when: When, prev_success: bool | None) -> bool:
    if when == "always":
        return True
    if prev_success is None:
        return False  # nothing ran yet; a prev-conditioned step can't fire first
    return prev_success if when == "prev_succeeded" else not prev_success


def run_workflow(
    workflow: Workflow, executors: dict[str, StepExecutor]
) -> WorkflowResult:
    """Run every step in order; returns the per-step runs and overall success."""
    runs: list[StepRun] = []
    prev_success: bool | None = None
    overall_ok = True

    for step in workflow.steps:
        if not _gate_open(step.when, prev_success):
            runs.append(StepRun(step.name, step.uses, True, False, "(skipped)", 0))
            continue  # a skipped step does not change prev_success

        executor = executors.get(step.uses)
        if executor is None:
            runs.append(
                StepRun(step.name, step.uses, False, False, f"no executor for '{step.uses}'", 0)
            )
            prev_success = False
            if step.required:
                overall_ok = False
            continue

        attempts = 0
        result = StepResult(False)
        for _ in range(max(1, step.repeat)):
            attempts += 1
            result = executor(step)
            if step.until == "success" and result.success:
                break  # loop satisfied

        runs.append(StepRun(step.name, step.uses, False, result.success, result.output, attempts))
        prev_success = result.success
        if step.required and not result.success:
            overall_ok = False

    _log.debug("workflow %s finished: success=%s", workflow.name, overall_ok)
    return WorkflowResult(workflow.name, overall_ok, runs)
