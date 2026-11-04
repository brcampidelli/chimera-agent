"""Every autonomous path that can mint a card gives its worker a trace to cite.

Minting needs evidence: ``evidence_rejection`` refuses a lesson with no run id, and a run gets a
run id only when its ``AgentConfig`` carries a ``trace_path``. The app, the CLI and the scheduler
pass one. The kanban lanes, the workflow solve step and the lifecycle crew did not, so on those
paths every card was refused as "missing evidence id" — at info level, where nobody reads it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.kanban.lanes import SolveLane
from chimera.kanban.models import KanbanCard
from chimera.orchestration.lifecycle import lifecycle_crew
from chimera.providers.gateway import CompletionResult
from chimera.workflow.executors import build_executors
from chimera.workflow.models import WorkflowStep


class _Captured(Exception):
    """Raised by the fake loop once it has the worker; stops the path before any model call."""


class _CaptureLoop:
    workers: list[Any] = []

    def __init__(self, worker: Any, **_kwargs: Any) -> None:
        _CaptureLoop.workers.append(worker)

    def run(self, *_args: Any, **_kwargs: Any) -> None:
        raise _Captured()


class _Gateway:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def complete(self, *_args: Any, **_kwargs: Any) -> CompletionResult:
        return CompletionResult(content="", model="fake")


@pytest.fixture
def capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_PREFIX_NONCE", "")
    get_settings.cache_clear()
    _CaptureLoop.workers = []
    monkeypatch.setattr("chimera.core.AutonomousAgent", _CaptureLoop)
    monkeypatch.setattr("chimera.providers.LLMGateway", _Gateway)
    return _CaptureLoop.workers


def _card(action: str) -> KanbanCard:
    return KanbanCard(id="c1", title="t", action=action)


def _assert_traced(workers: list[Any], home: Path) -> None:
    assert len(workers) == 1, "the path must reach the loop, or the premise is untested"
    assert workers[0].config.trace_path == home / "traces.jsonl"


def test_the_solve_lane_worker_names_its_trace(tmp_path: Path, capture: list[Any]) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(_Captured):
        SolveLane(workspace=ws).run(_card("fix the parser"))

    _assert_traced(capture, tmp_path / "home")


def test_the_workflow_solve_step_names_its_trace(tmp_path: Path, capture: list[Any]) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    step = WorkflowStep(name="s", uses="solve", **{"with": {"task": "fix the parser"}})
    with pytest.raises(_Captured):
        build_executors(workspace=ws)["solve"](step)

    _assert_traced(capture, tmp_path / "home")


def test_the_lifecycle_crew_worker_names_its_trace(tmp_path: Path, capture: list[Any]) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    crew = lifecycle_crew(_Gateway(), workspace=ws)

    assert crew.worker.config.trace_path == tmp_path / "home" / "traces.jsonl"
