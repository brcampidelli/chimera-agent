"""Tests for the workflow (loop) DSL and runner (no network)."""

from __future__ import annotations

from pathlib import Path

from chimera.workflow import Workflow, WorkflowStep, load_workflow, run_workflow
from chimera.workflow.runner import StepResult


def _step(name: str, uses: str, **kwargs: object) -> WorkflowStep:
    return WorkflowStep(name=name, uses=uses, **kwargs)  # type: ignore[arg-type]


def ok_exec(step: WorkflowStep) -> StepResult:
    return StepResult(True, "ok")


def fail_exec(step: WorkflowStep) -> StepResult:
    return StepResult(False, "no")


def test_load_workflow_parses_with_alias(tmp_path: Path) -> None:
    path = tmp_path / "wf.yaml"
    path.write_text(
        "name: demo\nsteps:\n  - name: a\n    uses: run\n    with:\n      prompt: hi\n",
        encoding="utf-8",
    )
    flow = load_workflow(path)
    assert flow.name == "demo"
    assert flow.steps[0].uses == "run"
    assert flow.steps[0].with_["prompt"] == "hi"


def test_runs_all_steps_in_order() -> None:
    flow = Workflow(name="w", steps=[_step("a", "run"), _step("b", "run")])
    result = run_workflow(flow, {"run": ok_exec})
    assert result.success is True
    assert [r.success for r in result.runs] == [True, True]


def test_prev_failed_step_is_skipped_when_prev_succeeds() -> None:
    flow = Workflow(name="w", steps=[_step("a", "run"), _step("b", "run", when="prev_failed")])
    result = run_workflow(flow, {"run": ok_exec})
    assert result.runs[1].skipped is True
    assert result.success is True


def test_recovery_step_runs_after_a_non_required_failure() -> None:
    flow = Workflow(
        name="w",
        steps=[
            _step("check", "shell", required=False),  # may fail; not required
            _step("fix", "run", when="prev_failed"),
        ],
    )
    result = run_workflow(flow, {"shell": fail_exec, "run": ok_exec})
    assert result.runs[0].success is False and result.runs[0].skipped is False
    assert result.runs[1].skipped is False and result.runs[1].success is True
    assert result.success is True  # check non-required + recovery succeeded


def test_repeat_until_success_loops() -> None:
    state = {"n": 0}

    def flaky(step: WorkflowStep) -> StepResult:
        state["n"] += 1
        return StepResult(state["n"] >= 2, "x")  # fails once, then succeeds

    flow = Workflow(name="w", steps=[_step("a", "solve", repeat=3, until="success")])
    result = run_workflow(flow, {"solve": flaky})
    assert result.runs[0].attempts == 2
    assert result.runs[0].success is True


def test_unknown_executor_marks_step_failed() -> None:
    flow = Workflow(name="w", steps=[_step("a", "crew")])
    result = run_workflow(flow, {})
    assert result.success is False
    assert result.runs[0].success is False


def test_non_required_failure_does_not_fail_the_workflow() -> None:
    flow = Workflow(name="w", steps=[_step("a", "run", required=False)])
    result = run_workflow(flow, {"run": fail_exec})
    assert result.runs[0].success is False
    assert result.success is True
