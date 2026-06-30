"""Evaluation harness: benchmarks (incl. continuous-evolution), LLM-as-judge.

The continuous-evolution benchmark measures degradation over chained tasks — the
key proof that Chimera resists the EvoClaw problem.
"""

from chimera.eval.chained import ChainStep, demo_chain, run_chain
from chimera.eval.continuous import (
    EvalTask,
    EvolutionReport,
    SingleModelSolver,
    Solver,
    TaskOutcome,
    demo_tasks,
    run_continuous,
)

__all__ = [
    "EvalTask",
    "EvolutionReport",
    "TaskOutcome",
    "Solver",
    "SingleModelSolver",
    "run_continuous",
    "demo_tasks",
    "ChainStep",
    "run_chain",
    "demo_chain",
]
