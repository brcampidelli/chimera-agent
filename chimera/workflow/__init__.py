"""Loop Engineering: author autonomous workflows as data, then run them.

A workflow is a declarative list of steps (YAML/JSON) that dispatch to the agent
stack with conditions and loops — designed flows instead of ad-hoc prompts. Load one
with :func:`load_workflow` and run it with :func:`run_workflow` (real executors come
from :mod:`chimera.workflow.executors`).
"""

from __future__ import annotations

from pathlib import Path

from chimera.workflow.models import Workflow, WorkflowStep
from chimera.workflow.runner import (
    StepExecutor,
    StepResult,
    StepRun,
    WorkflowResult,
    run_workflow,
)


def load_workflow(path: str | Path) -> Workflow:
    """Load a workflow from a YAML (or JSON) file."""
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Workflow.model_validate(data)


__all__ = [
    "Workflow",
    "WorkflowStep",
    "StepResult",
    "StepRun",
    "StepExecutor",
    "WorkflowResult",
    "run_workflow",
    "load_workflow",
]
