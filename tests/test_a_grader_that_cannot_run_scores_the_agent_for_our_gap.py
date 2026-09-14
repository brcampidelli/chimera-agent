"""The preflight §2c required, pinned — including the distinction that makes it work.

Bee §2c #4: *run every reference before loading any model; if the reference does not run, the defect
is the evaluator's.* `bench/harness_bench` did not, and it cost a task: `087-cli-parser-bug-tests`'s
grader runs `[sys.executable, "-m", "pytest", "tests"]` and `~/hb-venv` has no pytest, so 0.25 of
0.95 available weight was scored as the agent's failure on **24 of 24 runs**. Best score reached:
0.675 against a 0.8 threshold. The task was unpassable in our environment and read as a hard one
(`bench/harness_bench/DEAD-TASKS.md`).

The one that matters most below is `test_a_tool_on_PATH_is_not_a_tool_in_the_interpreter`: when the
preflight ran for real, `pytest` came back **ok as an executable and MISSING as a module** — present
on PATH from another environment, absent from the grading venv. A check that asked `which pytest`
would have reported everything fine.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_BENCH = Path(__file__).resolve().parent.parent / "bench" / "harness_bench"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _BENCH / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


preflight = _load("preflight")

GRADER = '''
import subprocess, sys
result = subprocess.run([sys.executable, "-m", "pytest", "tests"], cwd=project)
subprocess.run(["git", "status"], cwd=project)
subprocess.run([sys.executable, "-m", "csvtool.cli", "samples/orders.csv"], cwd=project)
'''


def test_it_finds_the_modules_a_grader_shells_out_to() -> None:
    modules, tools = preflight.required(GRADER)
    assert modules == {"pytest", "csvtool.cli"}
    assert tools == {"git"}


def test_an_interpreter_is_not_reported_as_a_missing_tool() -> None:
    """`subprocess.run(["python3", ...])` names the thing running the check, not a dependency;
    flagging it would bury the finding in noise."""
    _modules, tools = preflight.required('subprocess.run(["python3", "-c", "pass"])')
    assert tools == set()


def test_a_tool_on_PATH_is_not_a_tool_in_the_interpreter() -> None:
    """The distinction the whole preflight turns on.

    `pytest` was on PATH and absent from the grading venv, and the grader invokes it as
    `sys.executable -m pytest` — so the module is the one that decides. These are separate questions
    and the tool asks both.
    """
    assert preflight.importable(Path(sys.executable), "json") is True
    assert preflight.importable(Path(sys.executable), "a_module_that_is_not_installed") is False
    # And a python that does not exist answers False rather than raising: a preflight that crashes
    # on a bad --python argument tells you nothing about your graders.
    assert preflight.importable(Path("/nonexistent/python"), "json") is False


def test_a_grader_with_no_shelling_out_needs_nothing() -> None:
    modules, tools = preflight.required("def grade(ws):\n    return {'score': 1.0}\n")
    assert modules == set()
    assert tools == set()
