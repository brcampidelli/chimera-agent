"""The interval arithmetic has one home, `chimera/eval/proportions.py`, and no copy may drift from it.

Study 30 (S30-34) counted the Wilson interval written out in 15 bench scripts, the exact McNemar test
in 15, and Newcombe's paired interval in 6 — and the copies had drifted. Three of the six Newcombe
copies (`sharded_recap`, which `chat_history` imports, `spoken_standard`, `unattended_claims`) left
out Newcombe's continuity correction to phi, which narrows the interval; on Newcombe's own worked
example they print 0.0182 to 0.2892 where the paper prints 0.0112 to 0.2954. Three Wilson copies
returned ``(0.0, 0.0)`` — certainty — for zero trials. None of it failed: each copy printed a
plausible interval under the right name.

So: a function in `chimera/` or `bench/` whose name says it computes one of these may only call into
the home module — no square roots, powers or binomial coefficients of its own — and every bench
Newcombe adapter must reproduce the published worked example in its own sign convention.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "chimera" / "eval" / "proportions.py"

#: Names that announce the arithmetic. `paired_ci` and `mcnemar_ci` are the two readers' names for
#: the superseded conditional interval, which now lives in the home module under its own name.
_STATS_NAME = re.compile(r"(wilson|mcnemar|newcombe|bonett|tost|clopper|agresti)|^(paired_ci)$", re.IGNORECASE)
_MATH = {"sqrt", "comb", "lgamma", "exp", "log", "log1p", "inv_cdf", "cdf", "factorial", "betainc"}


def _reimplementations(root: Path) -> list[str]:
    found = []
    for base in ("chimera", "bench", "scripts"):
        for path in sorted((root / base).glob("**/*.py")):
            if path.resolve() == (root / "chimera" / "eval" / "proportions.py").resolve():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not _STATS_NAME.search(node.name):
                    continue
                for inner in ast.walk(node):
                    power = isinstance(inner, ast.BinOp) and isinstance(inner.op, ast.Pow)
                    named = (isinstance(inner, ast.Name) and inner.id in _MATH) or (
                        isinstance(inner, ast.Attribute) and inner.attr in _MATH
                    )
                    if power or named:
                        found.append(f"{path.relative_to(root).as_posix()}:{node.lineno} {node.name}")
                        break
    return found


def test_no_function_outside_the_home_module_does_the_interval_arithmetic_itself() -> None:
    copies = _reimplementations(ROOT)
    assert copies == [], (
        "these re-implement interval arithmetic that lives in chimera/eval/proportions.py — call it "
        "instead, passing the z your registration fixed:\n  " + "\n  ".join(copies)
    )


def test_the_census_sees_a_copy_when_one_is_written(tmp_path: Path) -> None:
    # The scan above must be able to fail: a fresh copy of the Wilson formula is caught.
    (tmp_path / "chimera").mkdir()
    (tmp_path / "bench" / "x").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "bench" / "x" / "run.py").write_text(
        "import math\n\ndef wilson(k, n, z=1.96):\n    p = k / n\n    return p - z * math.sqrt(p * (1 - p) / n), p\n",
        encoding="utf-8",
    )
    assert _reimplementations(tmp_path) == ["bench/x/run.py:3 wilson"]


# (module, call) for each bench Newcombe adapter, oriented so the answer is Newcombe (1998)'s worked
# example (12, 9, 2, 21) read as "the arm with 21 passes minus the arm with 14": 0.0112 to 0.2954.
_ADAPTERS = {
    "bench/sharded_recap/run.py": "m.newcombe_paired(12, 2, 9, 21)",  # second − first
    "bench/spoken_standard/run.py": "m.newcombe_paired(12, 2, 9, 21)",  # B − A
    "bench/unattended_claims/run.py": "m.newcombe_paired(12, 2, 9, 21)",  # Y − X
    "bench/useful_context/run.py": "m.newcombe_paired(12, 9, 2, 21)",  # first − second
    "bench/prompt_overlays/report.py": "m.newcombe_paired(12, 9, 2, 21)",  # new − ref
    "bench/verified_cascade/stats.py": (
        "m.newcombe_paired([True] * 21 + [False] * 23, [True] * 12 + [False] * 9 + [True] * 2 + [False] * 21)"
    ),  # x − y
}

_LOADER = """
import importlib.util, json, sys
from pathlib import Path
path = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(path.parent))
spec = importlib.util.spec_from_file_location("adapter_under_test", path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print(json.dumps(list(eval(sys.argv[2]))))
"""


@pytest.mark.parametrize("module", sorted(_ADAPTERS))
def test_each_bench_newcombe_adapter_reproduces_the_published_worked_example(module: str) -> None:
    # A fresh interpreter per module: several bench scripts share the module name `run`.
    proc = subprocess.run(
        [sys.executable, "-c", _LOADER, str(ROOT / module), _ADAPTERS[module]],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    diff, low, high = json.loads(proc.stdout.strip().splitlines()[-1])
    assert diff == pytest.approx(7 / 44, abs=1e-12)
    assert (low, high) == pytest.approx((0.0112, 0.2954), abs=5e-5)
