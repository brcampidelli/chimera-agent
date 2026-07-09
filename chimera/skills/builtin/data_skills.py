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


class DataVisualizationSkill(LLMSkill):
    """Write a self-contained chart script (matplotlib/seaborn static, plotly interactive).

    The honest way an agent "makes any chart" is not to reimplement matplotlib/plotly/bokeh — those
    are frameworks (matplotlib's renderer is C++; bokeh is half TypeScript; plotly wraps plotly.js).
    It writes correct plotting code and runs it in the `execute_code` sandbox. This skill names that
    capability and bakes in the two disciplines a headless agent otherwise forgets: the non-interactive
    backend, and save-to-file-then-print-the-path. For declarative/inspectable charts, prefer the
    `render_chart` tool (a Vega-Lite spec is inert data, not code).
    """

    name = "data_visualization"
    description = (
        "Write runnable Python that renders a chart and saves it to a file: matplotlib/seaborn for "
        "static images (PNG/SVG), plotly for interactive HTML. Run the result with the code sandbox."
    )
    version = "0.1.0"

    def run(self, **kwargs: Any) -> SkillResult:
        task = _require_str(kwargs, "task", "question")
        if task is None:
            return SkillResult(ok=False, error="missing required string 'task'")
        dataset = _require_str(kwargs, "dataset", "data") or "the dataset the user described"
        out = _require_str(kwargs, "out", "output") or "chart.png"
        system = (
            "You are an expert data-visualization engineer. Write ONE self-contained Python script that "
            "produces the requested chart and SAVES it to a file. Rules: "
            "(1) For STATIC charts use matplotlib/seaborn and set the headless backend FIRST — "
            "`import matplotlib; matplotlib.use('Agg')` BEFORE `import matplotlib.pyplot as plt`; never "
            "call plt.show(). (2) For INTERACTIVE charts use plotly and save with fig.write_html(path). "
            "(3) Prefer seaborn for statistical / DataFrame charts. (4) Pick the chart type that fits the "
            "data (line=trend, bar=comparison, scatter=correlation, hist/box=distribution). (5) Label the "
            "axes and title; for matplotlib use bbox_inches='tight' and a sane dpi. (6) Save to the given "
            "output path and PRINT that path as the last line of output. Handle a missing dataset "
            "gracefully. Output ONLY the Python code — no prose, no markdown fences."
        )
        prompt = f"Task: {task}\nData: {dataset}\nSave the chart to: {out}"
        return SkillResult(ok=True, output=self.ask(system, prompt))
