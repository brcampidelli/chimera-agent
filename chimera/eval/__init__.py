"""Evaluation harness: benchmarks (incl. continuous-evolution), LLM-as-judge.

The continuous-evolution benchmark measures degradation over chained tasks — the
key proof that Chimera resists the EvoClaw problem.
"""

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
