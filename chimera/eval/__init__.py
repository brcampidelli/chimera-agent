"""Evaluation harness: benchmarks (incl. continuous-evolution), LLM-as-judge.

The continuous-evolution benchmark measures degradation over chained tasks — the
key proof that Chimera resists the EvoClaw problem.

**Why the re-exports are lazy.** Importing this package used to pull ``chained`` → ``continuous`` →
``chimera.evolution`` → ``chimera.governance`` → ``chimera.core`` eagerly — ~600ms — and Python runs a
package's ``__init__`` before *any* submodule, so even ``from chimera.eval.benchmark_snapshot import
snapshot_path`` (a module whose own imports are just ``json``/``pathlib``) paid the full bill. The
desktop sidecar paid it on every cold boot just to read a JSON file. Names below now resolve on first
attribute access (PEP 562) and are cached into the module globals, so ``from chimera.eval import
EvalTask`` still works and costs the same as before *when you actually use it*. The ``TYPE_CHECKING``
block keeps mypy seeing the real types rather than ``Any``.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.eval.bench_ab import ABResult, Arm, format_report
    from chimera.eval.bench_ab import compare as compare_ab
    from chimera.eval.chained import ChainStep, demo_chain, run_chain
    from chimera.eval.continuous import (
        CostingSolver,
        EvalTask,
        EvolutionReport,
        RoundedEvolutionReport,
        SingleModelSolver,
        Solver,
        TaskOutcome,
        demo_tasks,
        run_continuous,
        run_evolution,
    )
    from chimera.eval.evoclaw import (
        EvoComparison,
        EvoStep,
        compare,
        counter_chain,
        run_guarded,
        run_naive,
    )
    from chimera.eval.hard import (
        HARD_CHAIN_OPS,
        HARD_CHAIN_START,
        hard_chain,
        hard_tasks,
    )
    from chimera.eval.injection import (
        AttackOutcome,
        InjectionAttack,
        RedTeamReport,
        default_attacks,
        run_redteam,
    )
    from chimera.eval.memory_bench import (
        MemoryProbe,
        RecallReport,
        run_memory_bench,
        synthetic_facts_and_probes,
    )
    from chimera.eval.memory_bench import sweep as memory_sweep
    from chimera.eval.rubric import (
        Dimension,
        RubricResult,
        cascade_dimensions,
        evaluate_cascade,
        model_judge,
    )
    from chimera.eval.rubric_grade import (
        Criterion,
        GradedOutcome,
        Rubric,
        RubricGrader,
        grade_batch,
        model_grader,
    )
    from chimera.eval.scenarios import Scenario, daily_scenarios, run_scenarios
    from chimera.eval.spec_tuning import scenario_scorer
    from chimera.eval.swe_bench import (
        SWEInstance,
        compare_arms,
        load_instances,
        parse_report,
        report_to_trials,
    )
    from chimera.eval.swe_bench import (
        build_solve_command as swe_build_solve_command,
    )

# Exported name -> (submodule, attribute in that submodule). The attribute differs from the exported
# name only where the original re-export renamed it (`compare as compare_ab`, etc.).
_LAZY: dict[str, tuple[str, str]] = {
    "ABResult": ("bench_ab", "ABResult"),
    "Arm": ("bench_ab", "Arm"),
    "format_report": ("bench_ab", "format_report"),
    "compare_ab": ("bench_ab", "compare"),
    "ChainStep": ("chained", "ChainStep"),
    "demo_chain": ("chained", "demo_chain"),
    "run_chain": ("chained", "run_chain"),
    "CostingSolver": ("continuous", "CostingSolver"),
    "EvalTask": ("continuous", "EvalTask"),
    "EvolutionReport": ("continuous", "EvolutionReport"),
    "RoundedEvolutionReport": ("continuous", "RoundedEvolutionReport"),
    "SingleModelSolver": ("continuous", "SingleModelSolver"),
    "Solver": ("continuous", "Solver"),
    "TaskOutcome": ("continuous", "TaskOutcome"),
    "demo_tasks": ("continuous", "demo_tasks"),
    "run_continuous": ("continuous", "run_continuous"),
    "run_evolution": ("continuous", "run_evolution"),
    "EvoComparison": ("evoclaw", "EvoComparison"),
    "EvoStep": ("evoclaw", "EvoStep"),
    "compare": ("evoclaw", "compare"),
    "counter_chain": ("evoclaw", "counter_chain"),
    "run_guarded": ("evoclaw", "run_guarded"),
    "run_naive": ("evoclaw", "run_naive"),
    "HARD_CHAIN_OPS": ("hard", "HARD_CHAIN_OPS"),
    "HARD_CHAIN_START": ("hard", "HARD_CHAIN_START"),
    "hard_chain": ("hard", "hard_chain"),
    "hard_tasks": ("hard", "hard_tasks"),
    "AttackOutcome": ("injection", "AttackOutcome"),
    "InjectionAttack": ("injection", "InjectionAttack"),
    "RedTeamReport": ("injection", "RedTeamReport"),
    "default_attacks": ("injection", "default_attacks"),
    "run_redteam": ("injection", "run_redteam"),
    "MemoryProbe": ("memory_bench", "MemoryProbe"),
    "RecallReport": ("memory_bench", "RecallReport"),
    "run_memory_bench": ("memory_bench", "run_memory_bench"),
    "synthetic_facts_and_probes": ("memory_bench", "synthetic_facts_and_probes"),
    "memory_sweep": ("memory_bench", "sweep"),
    "Dimension": ("rubric", "Dimension"),
    "RubricResult": ("rubric", "RubricResult"),
    "cascade_dimensions": ("rubric", "cascade_dimensions"),
    "evaluate_cascade": ("rubric", "evaluate_cascade"),
    "model_judge": ("rubric", "model_judge"),
    "Criterion": ("rubric_grade", "Criterion"),
    "GradedOutcome": ("rubric_grade", "GradedOutcome"),
    "Rubric": ("rubric_grade", "Rubric"),
    "RubricGrader": ("rubric_grade", "RubricGrader"),
    "grade_batch": ("rubric_grade", "grade_batch"),
    "model_grader": ("rubric_grade", "model_grader"),
    "Scenario": ("scenarios", "Scenario"),
    "daily_scenarios": ("scenarios", "daily_scenarios"),
    "run_scenarios": ("scenarios", "run_scenarios"),
    "scenario_scorer": ("spec_tuning", "scenario_scorer"),
    "SWEInstance": ("swe_bench", "SWEInstance"),
    "compare_arms": ("swe_bench", "compare_arms"),
    "load_instances": ("swe_bench", "load_instances"),
    "parse_report": ("swe_bench", "parse_report"),
    "report_to_trials": ("swe_bench", "report_to_trials"),
    "swe_build_solve_command": ("swe_bench", "build_solve_command"),
}


def __getattr__(name: str) -> Any:
    """Resolve a re-exported name on first use, then cache it (PEP 562)."""
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    submodule, attribute = target
    value = getattr(import_module(f"{__name__}.{submodule}"), attribute)
    globals()[name] = value  # subsequent lookups skip __getattr__ entirely
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "EvalTask",
    "EvolutionReport",
    "TaskOutcome",
    "Solver",
    "CostingSolver",
    "SingleModelSolver",
    "RoundedEvolutionReport",
    "run_continuous",
    "run_evolution",
    "demo_tasks",
    "ChainStep",
    "run_chain",
    "demo_chain",
    "EvoStep",
    "EvoComparison",
    "run_naive",
    "run_guarded",
    "compare",
    "counter_chain",
    "Scenario",
    "run_scenarios",
    "daily_scenarios",
    "Dimension",
    "RubricResult",
    "evaluate_cascade",
    "cascade_dimensions",
    "model_judge",
    "scenario_scorer",
    "hard_tasks",
    "hard_chain",
    "HARD_CHAIN_START",
    "HARD_CHAIN_OPS",
    "InjectionAttack",
    "AttackOutcome",
    "RedTeamReport",
    "default_attacks",
    "run_redteam",
    "MemoryProbe",
    "RecallReport",
    "run_memory_bench",
    "synthetic_facts_and_probes",
    "memory_sweep",
    "Arm",
    "ABResult",
    "compare_ab",
    "format_report",
    "Criterion",
    "Rubric",
    "GradedOutcome",
    "RubricGrader",
    "model_grader",
    "grade_batch",
    "SWEInstance",
    "load_instances",
    "swe_build_solve_command",
    "parse_report",
    "report_to_trials",
    "compare_arms",
]
