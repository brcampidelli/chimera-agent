"""Declarative workflow (loop) model — the Loop-Engineering authoring surface.

A workflow is an ordered list of steps. Each step *uses* a capability of the agent
stack (run / shell / solve / crew / lifecycle), may be gated on the previous step
(``when``), and may loop (``repeat`` up to N times, optionally ``until`` it succeeds).
Authored as data (YAML/JSON) instead of a hand-written prompt — designed flows, not
ad-hoc prompting.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

StepKind = Literal["run", "shell", "solve", "crew", "lifecycle"]
When = Literal["always", "prev_succeeded", "prev_failed"]
Until = Literal["always", "success"]


class WorkflowStep(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    uses: StepKind
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    when: When = "always"
    repeat: int = 1
    until: Until = "always"
    required: bool = True


class Workflow(BaseModel):
    name: str
    steps: list[WorkflowStep] = Field(default_factory=list)
