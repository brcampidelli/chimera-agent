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
_STATS_NAME = re.compile(
    r"(wilson|mcnemar|newcombe|bonett|tost|clopper|agresti|phi|fisher|hypergeom)|^(paired_ci)$", re.IGNORECASE
)
_MATH = {"sqrt", "comb", "lgamma", "exp", "log", "log1p", "inv_cdf", "cdf", "factorial", "betainc"}


def _is_comb(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Name) and func.id == "comb") or (
        isinstance(func, ast.Attribute) and func.attr == "comb"
    )


def _fair_coin_or_hypergeometric_tail(fn: ast.AST) -> bool:
    """A sum of binomial coefficients divided by ``2**n``, ``1 << n`` or another ``comb(...)``.

    That is the exact McNemar / sign test or the Fisher (hypergeometric) tail, whatever the function
    is called. A binomial pmf with a general ``p`` (``comb(n, i) * p**i * ...``) is divided by
    neither and is not caught: it is a different computation.
    """
    if not any(_is_comb(n) for n in ast.walk(fn)):
        return False
    for node in ast.walk(fn):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
            continue
        right = node.right
        if _is_comb(right):
            return True
        if isinstance(right, ast.BinOp) and isinstance(right.left, ast.Constant):
            if isinstance(right.op, ast.Pow) and right.left.value == 2:
                return True
            if isinstance(right.op, ast.LShift) and right.left.value == 1:
                return True
    return False


def _factors(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return _factors(node.left) + _factors(node.right)
    return [node]


def _wilson_shape(fn: ast.AST) -> bool:
    """``4 * n * n`` beside a square root: the Wilson half-width, whatever the function is called."""
    four_n_n = root = False
    for node in ast.walk(fn):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            parts = _factors(node)
            fours = [x for x in parts if isinstance(x, ast.Constant) and x.value == 4]
            names = [x.id for x in parts if isinstance(x, ast.Name)]
            four_n_n = four_n_n or (len(parts) == 3 and len(fours) == 1 and len(names) == 2 and names[0] == names[1])
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            root = root or (isinstance(node.right, ast.Constant) and node.right.value == 0.5)
        root = root or (isinstance(node, ast.Name) and node.id == "sqrt")
        root = root or (isinstance(node, ast.Attribute) and node.attr == "sqrt")
    return four_n_n and root


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
                where = f"{path.relative_to(root).as_posix()}:{node.lineno} {node.name}"
                # By shape, whatever the name: a reader's `_rate` or a tail written inline in
                # `axis_joint` is the same arithmetic as a function called `wilson` or `fisher`.
                if _fair_coin_or_hypergeometric_tail(node) or _wilson_shape(node):
                    found.append(where)
                    continue
                if not _STATS_NAME.search(node.name):
                    continue
                for inner in ast.walk(node):
                    power = isinstance(inner, ast.BinOp) and isinstance(inner.op, ast.Pow)
                    named = (isinstance(inner, ast.Name) and inner.id in _MATH) or (
                        isinstance(inner, ast.Attribute) and inner.attr in _MATH
                    )
                    if power or named:
                        found.append(where)
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


_SHAPED_COPIES = {
    # The three copies a review found that the name-only census missed (study 30, S30-34): manager_p's
    # `_rate`, spoken_standard's `exact_binomial_two_sided`, and the tail governance_axes wrote inline.
    "_rate": (
        "def _rate(k, n):\n"
        "    z = 1.96\n"
        "    p = k / n\n"
        "    centre = (p + z * z / (2 * n)) / (1 + z * z / n)\n"
        "    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)\n"
        "    return centre - half\n"
    ),
    "exact_binomial_two_sided": (
        "import math\n\n"
        "def exact_binomial_two_sided(k, n):\n"
        "    return 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2**n\n"
    ),
    "axis_joint": (
        "import math\n\n"
        "def axis_joint(a, r, c, n):\n"
        "    return sum(math.comb(c, k) * math.comb(n - c, r - k) for k in range(a, c + 1)) / math.comb(n, r)\n"
    ),
    "phi_of": "import math\n\ndef phi_of(a, b, c, d):\n    return (a * d - b * c) / math.sqrt((a + b) * (c + d))\n",
}


def _tree_with(tmp_path: Path, where: str, body: str) -> Path:
    for base in ("chimera", "bench", "scripts"):
        (tmp_path / base).mkdir(exist_ok=True)
    target = tmp_path / where
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("name", sorted(_SHAPED_COPIES))
def test_the_census_sees_a_copy_by_its_shape_not_only_its_name(tmp_path: Path, name: str) -> None:
    root = _tree_with(tmp_path, "bench/x/run.py", _SHAPED_COPIES[name])
    assert [hit.split(" ")[1] for hit in _reimplementations(root)] == [name]


def test_a_binomial_pmf_with_a_general_rate_is_not_mistaken_for_a_copy(tmp_path: Path) -> None:
    # chimera/eval/retries.py's binomial_tail is P(X <= k) for any p: a different computation.
    body = (
        "import math\n\n"
        "def binomial_tail(k, n, p):\n"
        "    return sum(math.comb(n, i) * p**i * (1.0 - p) ** (n - i) for i in range(k + 1))\n"
    )
    assert _reimplementations(_tree_with(tmp_path, "chimera/r.py", body)) == []


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
