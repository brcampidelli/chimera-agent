"""Tier-1 data-science skill: turn a dataset + a question into runnable analysis code.

The honest way an agent "does machine learning" is not to reimplement scikit-learn — it's to write
correct pandas/sklearn code and run it in the code sandbox. This skill names that capability: given a
task and a dataset, it emits a self-contained Python script (load → explore → model → evaluate) the
agent then executes with `execute_code`. Orchestration, not reimplementation.
"""

from __future__ import annotations

from typing import Any

from chimera.skills.base import SkillResult
from chimera.skills.llm_skill import LLMSkill


def _require_str(kwargs: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = kwargs.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


class DataAnalysisSkill(LLMSkill):
    """Write a self-contained pandas + scikit-learn script for a data-analysis task."""

    name = "data_analysis"
    description = (
        "Write runnable Python (pandas + scikit-learn) for a data task: load a dataset, explore it, "
        "train/evaluate a model, or compute statistics. Run the result with the code sandbox."
    )
    version = "0.1.0"

    def run(self, **kwargs: Any) -> SkillResult:
        task = _require_str(kwargs, "task", "question")
        if task is None:
            return SkillResult(ok=False, error="missing required string 'task'")
        dataset = _require_str(kwargs, "dataset", "data") or "the dataset the user described"
        system = (
            "You are an expert data scientist. Write ONE self-contained Python script that accomplishes "
            "the task with pandas, numpy and scikit-learn (matplotlib only if a plot is asked for). "
            "Load the data, do the analysis, and PRINT clear results — metrics and the key numbers. When "
            "modelling, pick a simple correct model (LogisticRegression / RandomForest / KMeans / "
            "LinearRegression), use a train/test split, and report an honest score. Handle missing values. "
            "Output ONLY the Python code — no prose, no markdown fences."
        )
        return SkillResult(ok=True, output=self.ask(system, f"Task: {task}\nData: {dataset}"))
