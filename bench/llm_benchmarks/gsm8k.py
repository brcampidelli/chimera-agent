"""GSM8K — grade-school word problems, graded by exact match on the final number.

The interesting contrast with HumanEval: here the loop's lever is the code interpreter. A weak model
fails GSM8K mostly on arithmetic slips inside otherwise-correct reasoning, and running the arithmetic
converts that class of failure into a mechanical one. Registered prediction #3 says this should make
GSM8K lift >= HumanEval lift on the weak tier.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from arms import Spend, run_baseline, run_chimera_solve
from datasets import gsm8k_reference

_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")

_BASELINE_PROMPT = """Solve this problem. Think it through, then give the final numeric answer on \
the last line in exactly this format:

ANSWER: <number>

Problem: {question}"""

_SOLVE_TASK = """Solve the word problem in problem.txt.

Read problem.txt. Work out the answer — you may use Python to do the arithmetic rather than doing it \
in your head. When you are certain, write ONLY the final number (no units, no words, no commas) into \
a file called answer.txt."""


def normalise(value: str) -> str:
    """Canonical numeric form, so ``1,000``, ``1000`` and ``1000.0`` compare equal."""
    text = value.strip().replace(",", "").replace("$", "").rstrip(".")
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(number)) if number == int(number) else str(number)


def extract_answer(text: str) -> str:
    """The model's final number: the ``ANSWER:`` line if present, else the last number in the text."""
    for line in reversed((text or "").strip().splitlines()):
        if "ANSWER:" in line.upper():
            match = _NUMBER.search(line.split(":", 1)[-1])
            if match:
                return normalise(match.group())
    matches = _NUMBER.findall(text or "")
    return normalise(matches[-1]) if matches else ""


def grade(response: str, problem: dict[str, Any]) -> bool:
    return bool(response) and response == normalise(gsm8k_reference(problem["answer"]))


def run_baseline_task(problem: dict[str, Any], *, model: str, spend: Spend) -> bool:
    text = run_baseline(
        _BASELINE_PROMPT.format(question=problem["question"]), model=model, spend=spend, max_tokens=768
    )
    return grade(extract_answer(text), problem)


def run_chimera_task(problem: dict[str, Any], *, model: str, spend: Spend, root: Path, index: int) -> bool:
    workspace = root / f"gsm8k_{index:04d}"
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    (workspace / "problem.txt").write_text(problem["question"], encoding="utf-8")

    run_chimera_solve(
        _SOLVE_TASK,
        workspace=workspace,
        model=model,
        # Structural check only — that an answer was actually produced. It cannot check correctness
        # (that would require the gold answer, which the agent must never see).
        verify=f'"{Path(shutil.which("python") or "python")}" -c "assert open(\'answer.txt\').read().strip()"',
        spend=spend,
        max_attempts=2,
        timeout=240,
    )

    produced = workspace / "answer.txt"
    text = produced.read_text(encoding="utf-8") if produced.exists() else ""
    return grade(normalise(text.strip().splitlines()[0]) if text.strip() else "", problem)
