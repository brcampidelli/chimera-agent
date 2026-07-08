"""Tests for the hierarchy paired A/B (M16-A8). Offline — scripted backends.

Covers: the synthetic suite is deterministically gradable; the two arms are
wired as registered (baseline = all-docs-inline, treatment = per-doc workers);
the report keeps significance to the quality axis and totals to the cost axis;
and the NEGATIVE CONTROL — the classifier falls back on all local_lift coding
tasks (prediction 3).
"""

from __future__ import annotations

from pathlib import Path

from chimera.eval.hierarchy_ab import (
    ArmOutcome,
    baseline_prompt,
    format_token_report,
    make_specs,
    run_hierarchy_ab,
    synthetic_tasks,
)
from chimera.orchestration.hierarchy import classify_task


def test_synthetic_suite_is_well_formed() -> None:
    tasks = synthetic_tasks()
    assert len(tasks) >= 10
    for task in tasks:
        assert len(task.docs) >= 2
        assert task.facts
        # A correct answer = every planted needle present; the empty string fails.
        assert task.check("") is False
        full = " ".join(f.needle for f in task.facts)
        assert task.check(full) is True


def test_baseline_prompt_carries_all_docs() -> None:
    task = synthetic_tasks()[0]
    prompt = baseline_prompt(task)
    for name in task.docs:
        assert name in prompt
    assert task.question in prompt


def test_specs_scope_one_doc_each() -> None:
    task = synthetic_tasks()[0]
    specs = make_specs(task)
    assert len(specs) == len(task.docs)
    # Each worker's context contains exactly ONE document (minimal-context scoping).
    for spec, name in zip(specs, task.docs, strict=True):
        assert name in spec.context
        others = [n for n in task.docs if n != name]
        assert all(o not in spec.context for o in others)


def test_report_paired_quality_and_totals_only_tokens() -> None:
    tasks = synthetic_tasks()[:6]

    # Scripted arms: hierarchy passes all, baseline passes every other one, and the
    # hierarchy is cheaper — enough to exercise the report math deterministically.
    def restore(_task: object) -> None:
        return None

    def baseline(task: object) -> ArmOutcome:
        idx = tasks.index(task)  # type: ignore[arg-type]
        return ArmOutcome(passed=idx % 2 == 0, tokens=1000)

    def treatment(_task: object) -> ArmOutcome:
        return ArmOutcome(passed=True, tokens=400)

    report = run_hierarchy_ab(tasks, restore=restore, baseline=baseline, treatment=treatment)
    summary = report.summary()
    assert summary["baseline_total_tokens"] == 6000
    assert summary["treatment_total_tokens"] == 2400
    assert summary["token_reduction"] == round(1 - 2400 / 6000, 4)
    assert "significant" in summary  # from the paired quality axis
    # The cost report must NOT claim significance.
    text = format_token_report(report)
    assert "no significance claimed on cost" in text


def test_negative_control_local_lift_all_fall_back() -> None:
    """Prediction 3: the classifier routes every local_lift coding task to
    single-agent (sequential_write) — the hierarchy must not engage there."""
    import sys

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "bench" / "local_lift"))
    from tasks import TASKS  # type: ignore[import-not-found]

    shapes = {t["id"]: classify_task(t["prompt"]) for t in TASKS}
    assert shapes, "local_lift task suite should be non-empty"
    assert all(shape == "sequential_write" for shape in shapes.values()), shapes
