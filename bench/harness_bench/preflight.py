"""Before a factorial spends a cent: can every task's GRADER actually run?

    python bench/harness_bench/preflight.py [--tasks ~/harness-bench/tasks] [--python ~/hb-venv/bin/python]

Bee lesson §2c #4, which this project wrote down and then did not execute: *run every reference
before loading any model — if the reference does not run, the defect is the evaluator's.* The
factorial in #453 skipped it, and it cost a task.

**What it cost, precisely.** `087-cli-parser-bug-tests`'s grader runs
``subprocess.run([sys.executable, "-m", "pytest", "tests"], ...)`` and weights the result 0.25 of
the 0.95 available. `~/hb-venv` has no pytest, so that check returned non-zero on **24 of 24 runs**
and 0.25 of the score was scored as the agent's failure. The best score any arm reached was 0.675
against a 0.8 threshold: **the task was unpassable in our environment**, and it sat in the results
as one of the eleven tasks that "could not distinguish any arm" (`bench/irt/RESULTS.md`).

A missing module does not fail loudly here. It fails as a low score, which reads as a hard task.

**What this checks, and what it deliberately does not.** Every module a grader invokes through
``-m`` must import in the interpreter that will run the grader. That is a static scan plus one
import per module: seconds, offline, no task content read into memory beyond the grader source.
It does not run the graders themselves — that needs a workspace per task and is the next step, not
this one. It catches the class of defect that actually occurred.

**One ambiguity, declared rather than guessed at.** A module the grader invokes may be the thing the
AGENT is meant to build, not a dependency the environment owes it: 087's grader runs both `pytest`
(ours to install) and `csvtool.cli` (the deliverable, correctly absent before any work). Telling
those apart statically would need to know each task's intent, so this reports both and says which
question to ask. A third-party tool missing is a defect; the artefact under test missing is the
point of the task.

Harness-Bench carries no licence, so this reads the local checkout and prints; nothing is copied
into the repo.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

#: `[sys.executable, "-m", "<module>"` — the shape a grader uses to shell out to a tool.
_DASH_M = re.compile(
    r"""sys\.executable\s*,\s*["']-m["']\s*,\s*["']([A-Za-z_][A-Za-z0-9_.]*)["']"""
)
#: A bare `subprocess.run(["<tool>"` / `.run("<tool> ` — a binary rather than a module.
_BARE = re.compile(r"""subprocess\.(?:run|check_output|call)\(\s*\[\s*["']([A-Za-z][\w.-]*)["']""")

#: Interpreters are not tools; flagging them would bury the finding in noise.
_IGNORE = frozenset({"python", "python3", "sh", "bash"})


def graders(tasks: Path) -> list[tuple[str, Path]]:
    return sorted(
        (path.parent.name, path) for path in tasks.glob("*/oracle_grade.py") if path.is_file()
    )


def required(source: str) -> tuple[set[str], set[str]]:
    """(modules invoked with -m, bare executables invoked) named in a grader."""
    return (
        set(_DASH_M.findall(source)),
        {t for t in _BARE.findall(source) if t not in _IGNORE},
    )


def importable(python: Path, module: str) -> bool:
    try:
        done = subprocess.run(
            [str(python), "-c", f"import {module}"], capture_output=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def on_path(tool: str) -> bool:
    from shutil import which

    return which(tool) is not None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, default=Path(os.path.expanduser("~/harness-bench/tasks")))
    parser.add_argument(
        "--python", type=Path, default=Path(os.path.expanduser("~/hb-venv/bin/python"))
    )
    args = parser.parse_args()

    found = graders(args.tasks)
    if not found:
        raise SystemExit(f"no oracle_grade.py under {args.tasks} — is the checkout there?")

    modules: dict[str, set[str]] = {}
    tools: dict[str, set[str]] = {}
    for task, path in found:
        mods, bins = required(path.read_text(encoding="utf-8", errors="replace"))
        modules[task], tools[task] = mods, bins

    every_module = sorted({m for s in modules.values() for m in s})
    every_tool = sorted({t for s in tools.values() for t in s})
    ok_module = {m: importable(args.python, m) for m in every_module}
    ok_tool = {t: on_path(t) for t in every_tool}

    print(f"graders: {len(found)}   interpreter: {args.python}")
    print(f"\nmodules invoked with -m ({len(every_module)}):")
    for module, ok in sorted(ok_module.items()):
        print(f"   {'ok  ' if ok else 'MISSING'} {module}")
    if every_tool:
        print(f"\nexecutables invoked ({len(every_tool)}):")
        for tool, ok in sorted(ok_tool.items()):
            print(f"   {'ok  ' if ok else 'MISSING'} {tool}")

    broken = [
        (task, sorted({m for m in modules[task] if not ok_module[m]}
                      | {t for t in tools[task] if not ok_tool[t]}))
        for task, _ in found
    ]
    broken = [(task, missing) for task, missing in broken if missing]

    print()
    if not broken:
        print("every grader's tooling is present — nothing here would score the agent for our gaps")
        return
    print(f"!! {len(broken)} task(s) whose GRADER cannot run as written:")
    for task, missing in broken:
        print(f"   {task:42s} missing {', '.join(missing)}")
    print(
        "\nA grader that cannot run does not fail loudly — it returns a low score, which reads as a "
        "hard task. For each name above ask which it is: a tool this environment owes the grader "
        "(install it), or the artefact the agent is meant to produce (expected to be missing). "
        "Settle that BEFORE spending, not by reading the results afterwards."
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
