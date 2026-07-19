"""HumanEval — execution-graded, with the grading tests structurally out of the agent's reach.

The integrity rule from PREREGISTRATION.md, enforced here rather than promised:

* the agent solves in ``solve/``, which contains only the stub it was given;
* the produced ``solution.py`` is copied into ``grade/``, a directory the agent never saw;
* the canonical ``check(candidate)`` runs in ``grade/``.

If the hidden tests were reachable from the solve workspace, the loop would optimise against the
grader instead of the problem and every number here would be meaningless.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from arms import Spend, extract_code, run_baseline, run_chimera_solve

_GRADE_TIMEOUT = 30

# The agent's own gate: run the docstring examples the prompt already contained.
#
# Two bugs this command exists to avoid, both found by testing the gate itself against known-correct
# and known-wrong implementations before spending a cent:
#   * `python -m doctest solution.py` exits 0 even when examples FAIL — it is a no-op as a gate.
#     `doctest.testmod` + an explicit exit code is what actually reports failure.
#   * `-B` is load-bearing. Without it, an edit that keeps the file the same SIZE within the same
#     mtime second reuses the cached .pyc, so the gate silently grades the PREVIOUS version. That is
#     exactly the shape of an agent iterating on one function, which would have poisoned every run.
# `r.failed` only — a docstring with no examples yields 0 attempted and 0 failed, and absence of
# examples must not be scored as failure.
_DOCTEST_GATE = "import doctest,sys,solution; sys.exit(1 if doctest.testmod(solution).failed else 0)"

_BASELINE_PROMPT = """Complete the following Python function. Reply with the complete function \
(including the signature and any imports it needs) inside a single ```python code block. \
Do not write tests or explanations.

```python
{prompt}```"""

_SOLVE_TASK = """Implement the function in solution.py so it satisfies its docstring.

The file solution.py already contains the signature and the docstring specification. Replace the \
body. Keep the signature exactly as given. The docstring contains examples — check your work against \
them (you can run `python -m doctest solution.py -v`) before you finish. Do not change the docstring \
and do not add a `if __name__ == "__main__"` block."""


def grade(solution_src: str, problem: dict[str, Any]) -> bool:
    """Run the canonical hidden test against ``solution_src`` in an isolated directory."""
    if not solution_src.strip():
        return False
    with tempfile.TemporaryDirectory() as tmp:
        grade_dir = Path(tmp)
        runner = (
            f"{solution_src}\n\n"
            f"{problem['test']}\n\n"
            f"check({problem['entry_point']})\n"
        )
        script = grade_dir / "grade_it.py"
        script.write_text(runner, encoding="utf-8")
        try:
            proc = subprocess.run(  # noqa: S603 — fixed argv, isolated dir
                [sys.executable, "-B", str(script)],  # -B: never grade a stale .pyc
                cwd=str(grade_dir),
                capture_output=True,
                text=True,
                timeout=_GRADE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return False  # honest FAIL: an infinite loop is a wrong answer
        return proc.returncode == 0


def run_baseline_task(problem: dict[str, Any], *, model: str, spend: Spend) -> bool:
    """One completion, then grade it."""
    text = run_baseline(_BASELINE_PROMPT.format(prompt=problem["prompt"]), model=model, spend=spend)
    return grade(extract_code(text), problem)


def run_chimera_task(problem: dict[str, Any], *, model: str, spend: Spend, root: Path) -> bool:
    """The full loop in a clean workspace, then grade the artifact it produced.

    The workspace is rebuilt from scratch for this task, and the grading test never enters it.
    """
    workspace = root / problem["task_id"].replace("/", "_")
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    # The stub the agent starts from: exactly the prompt the baseline arm also received.
    (workspace / "solution.py").write_text(problem["prompt"], encoding="utf-8")

    run_chimera_solve(
        _SOLVE_TASK,
        workspace=workspace,
        model=model,
        # The agent's only verification signal is the docstring it was already given. This uses zero
        # information the baseline did not also receive.
        verify=f'"{sys.executable}" -B -c "{_DOCTEST_GATE}"',
        spend=spend,
    )

    produced = workspace / "solution.py"
    src = produced.read_text(encoding="utf-8") if produced.exists() else ""
    return grade(src, problem)
