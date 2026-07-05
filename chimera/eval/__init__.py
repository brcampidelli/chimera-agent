"""Evaluation harness: benchmarks (incl. continuous-evolution), LLM-as-judge.

The continuous-evolution benchmark measures degradation over chained tasks — the
key proof that Chimera resists the EvoClaw problem.
"""

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
from chimera.eval.rubric import (
    Dimension,
    RubricResult,
    cascade_dimensions,
    evaluate_cascade,
    model_judge,
)
from chimera.eval.scenarios import Scenario, daily_scenarios, run_scenarios
from chimera.eval.spec_tuning import scenario_scorer

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
]
