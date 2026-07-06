"""Local weak-model-lift task set v2 — harder, Docker-free, pytest-verified coding tasks.

v1 was too easy: the cheap model one-shot every task (7/7 baseline), leaving no headroom for the
scaffolding to matter (a ceiling effect). v2 raises the difficulty — multi-rule specs a one-shot
tends to under-satisfy, a `^`-power/integer-division calculator that defeats a lazy ``eval()``
shortcut, and a subtle multi-file numeric bug — so there is real room between "raw model, one shot"
and "the full Chimera loop (plan + verify-or-revert + M14 scaffolding)".

Ground truth is a strict pytest file; the runner re-runs it independently. NOT the official
Terminal-Bench / SWE-bench — a local proxy for the same build/fix-graded-by-tests shape.
"""

from __future__ import annotations

from typing import Any

_METRICS_INIT = "from metrics.calc import percentile\n\n__all__ = ['percentile']\n"
_METRICS_BUGGY = '''\
"""Numeric helpers."""


def percentile(values, p):
    """Linear-interpolated percentile of `values` for p in [0, 100]."""
    ordered = sorted(values)
    n = len(ordered)
    # BUG: naive nearest-rank index, no interpolation between neighbours.
    idx = int(p / 100 * n)
    if idx >= n:
        idx = n - 1
    return ordered[idx]
'''


TASKS: list[dict[str, Any]] = [
    {
        "id": "roman_validate",
        "prompt": (
            "Create roman.py with roman_to_int(s: str) -> int. It converts a valid Roman numeral "
            "(case-insensitive) to an integer, supporting subtractive forms (IV, IX, XL, XC, CD, CM). "
            "It must RAISE ValueError for any invalid numeral — including the empty string, non-Roman "
            "characters, illegal repeats like 'IIII' or 'VV', and illegal subtractions like 'IL', "
            "'IC', 'VX'. Hint: a value is valid only if it round-trips (re-encoding the integer yields "
            "the same canonical numeral)."
        ),
        "files": {},
        "verify": "roman.py",
        "test": "test_roman.py",
        "test_src": (
            "import pytest\n"
            "from roman import roman_to_int\n\n"
            "def test_valid():\n"
            "    assert roman_to_int('III') == 3\n"
            "    assert roman_to_int('IV') == 4\n"
            "    assert roman_to_int('xlii') == 42\n"
            "    assert roman_to_int('MCMXCIV') == 1994\n"
            "def test_invalid_raises():\n"
            "    for bad in ['IIII', 'VV', 'IL', 'IC', 'VX', '', 'ABC', 'MM.']:\n"
            "        with pytest.raises(ValueError):\n"
            "            roman_to_int(bad)\n"
        ),
    },
    {
        "id": "config_parse",
        "prompt": (
            "Create config.py with parse_config(text: str) -> dict. It parses an INI-like config into "
            "a dict of sections, each a dict of typed values. Rules: '[name]' starts a section; "
            "'key = value' adds a key (trim whitespace around both); a line starting with '#' or ';' "
            "is a comment; blank lines are ignored; a key that appears before any section goes under "
            "the section 'default'. Coerce each value: an integer if it looks like one, else a float "
            "if it looks like one, else True/False for 'true'/'false' (case-insensitive), else the "
            "raw string."
        ),
        "files": {},
        "verify": "config.py",
        "test": "test_config.py",
        "test_src": (
            "from config import parse_config\n\n"
            "SAMPLE = '''\n"
            "# a comment\n"
            "[server]\n"
            "host = localhost\n"
            "port = 8080\n"
            "debug = true\n"
            "ratio = 0.5\n"
            "; trailing comment\n"
            "\n"
            "[db]\n"
            "name = mydb\n"
            "'''\n\n"
            "def test_sections_and_types():\n"
            "    cfg = parse_config(SAMPLE)\n"
            "    assert cfg['server']['host'] == 'localhost'\n"
            "    assert cfg['server']['port'] == 8080\n"
            "    assert cfg['server']['debug'] is True\n"
            "    assert cfg['server']['ratio'] == 0.5\n"
            "    assert cfg['db']['name'] == 'mydb'\n"
            "def test_default_section():\n"
            "    cfg = parse_config('x = 1\\n# c\\ny = FALSE')\n"
            "    assert cfg['default']['x'] == 1\n"
            "    assert cfg['default']['y'] is False\n"
        ),
    },
    {
        "id": "path_get",
        "prompt": (
            "Create pathq.py with get_path(data, path: str, default=None). It navigates nested dicts "
            "and lists by a path string like 'a.b[1].c': dots separate dict keys, and [n] indexes a "
            "list. Return the value at the path, or `default` if any key is missing or any list index "
            "is out of range. Support chained indices like 'a[0][1]' and mixed 'a.b[2].c'."
        ),
        "files": {},
        "verify": "pathq.py",
        "test": "test_pathq.py",
        "test_src": (
            "from pathq import get_path\n\n"
            "def test_nested():\n"
            "    data = {'a': {'b': [10, {'c': 42}]}}\n"
            "    assert get_path(data, 'a.b[1].c') == 42\n"
            "    assert get_path(data, 'a.b[0]') == 10\n"
            "def test_chained_index():\n"
            "    assert get_path({'a': [[1, 2], [3, 4]]}, 'a[1][0]') == 3\n"
            "def test_missing_returns_default():\n"
            "    data = {'a': {'b': [1]}}\n"
            "    assert get_path(data, 'a.x.y', default='none') == 'none'\n"
            "    assert get_path(data, 'a.b[5]', default=-1) == -1\n"
            "    assert get_path(data, 'a.b[0].c', default=0) == 0\n"
        ),
    },
    {
        "id": "eval_expr",
        "prompt": (
            "Create calc.py with eval_expr(s: str) -> int that evaluates an integer arithmetic "
            "expression. Operators: + - * with usual precedence, / as INTEGER floor division, and ^ "
            "as exponentiation that is RIGHT-associative and binds tighter than * and /. Support "
            "parentheses. Examples: '2+3*4'->14, '(2+3)*4'->20, '7/2'->3, '2^3^2'->512, '2^3*2'->16. "
            "Do not use Python's eval (its ^ is xor and its / is not integer division)."
        ),
        "files": {},
        "verify": "calc.py",
        "test": "test_calc.py",
        "test_src": (
            "from calc import eval_expr\n\n"
            "def test_precedence():\n"
            "    assert eval_expr('2+3*4') == 14\n"
            "    assert eval_expr('(2+3)*4') == 20\n"
            "    assert eval_expr('7/2') == 3\n"
            "def test_power_right_assoc():\n"
            "    assert eval_expr('2^3^2') == 512\n"
            "    assert eval_expr('2^3*2') == 16\n"
            "    assert eval_expr('10-2-3') == 5\n"
        ),
    },
    {
        "id": "word_wrap",
        "prompt": (
            "Create wrap.py with wrap(text: str, width: int) -> list[str]. Greedily wrap the "
            "whitespace-separated words of `text` into lines no longer than `width`, joining words "
            "with single spaces and with no leading/trailing spaces on a line. A word longer than "
            "`width` goes alone on its own line. Return the list of lines; an empty/whitespace-only "
            "text returns []."
        ),
        "files": {},
        "verify": "wrap.py",
        "test": "test_wrap.py",
        "test_src": (
            "from wrap import wrap\n\n"
            "def test_basic():\n"
            "    assert wrap('the quick brown fox', 10) == ['the quick', 'brown fox']\n"
            "def test_long_word_alone():\n"
            "    assert wrap('a bb ccccccc dd', 5) == ['a bb', 'ccccccc', 'dd']\n"
            "def test_empty():\n"
            "    assert wrap('   ', 10) == []\n"
        ),
    },
    {
        "id": "fix_percentile",
        "prompt": (
            "The package `metrics` has a bug: percentile in metrics/calc.py uses a naive nearest-rank "
            "index instead of linear interpolation between the two neighbouring order statistics. Fix "
            "it so it returns the linearly-interpolated percentile (the standard 'linear' method): for "
            "p in [0,100], rank = p/100*(n-1), then interpolate between the floor and ceil ranks. Do "
            "not change the signature or the tests."
        ),
        "files": {"metrics/__init__.py": _METRICS_INIT, "metrics/calc.py": _METRICS_BUGGY},
        "verify": "metrics/calc.py",
        "test": "test_percentile.py",
        "test_src": (
            "from metrics import percentile\n\n"
            "def test_even_and_odd():\n"
            "    assert percentile([1, 2, 3, 4], 50) == 2.5\n"
            "    assert percentile([1, 2, 3, 4, 5], 50) == 3\n"
            "def test_edges():\n"
            "    assert percentile([1, 2, 3, 4], 0) == 1\n"
            "    assert percentile([1, 2, 3, 4], 100) == 4\n"
            "def test_interpolation():\n"
            "    assert percentile([10, 20, 30], 25) == 15.0\n"
        ),
    },
]
