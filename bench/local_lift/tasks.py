"""Local weak-model-lift task set v3 — harder, Docker-free, pytest-verified coding tasks.

v1 was too easy: the cheap model one-shot every task (7/7 baseline), leaving no headroom for the
scaffolding to matter (a ceiling effect). v2 raises the difficulty — multi-rule specs a one-shot
tends to under-satisfy, a `^`-power/integer-division calculator that defeats a lazy ``eval()``
shortcut, and a subtle multi-file numeric bug — so there is real room between "raw model, one shot"
and "the full Chimera loop (plan + verify-or-revert + M14 scaffolding)".

v3 takes the suite to **n=100**, per ``PREREGISTRATION.md`` (written and committed before these
tasks were authored, and before any model call of the run). The 15 v2 tasks below are kept
verbatim — *including the 7 this model one-shots*. They cost us power and contribute nothing to
McNemar, and dropping them after seeing that they produced no signal would be post-hoc exclusion,
which inflates the effect. They stay.

The 85 new ones live in sibling modules by domain and are appended at the bottom of this file:
``tasks_parsing`` (28), ``tasks_algorithms`` (29), ``tasks_bugfix`` (28). Each was authored to the
a-priori difficulty spec and never run through either arm — selection by intrinsic complexity only,
never by outcome. Each ships a test proven to pass a correct reference and *fail* a plausible naive
shortcut: a test a naive implementation passes grades nothing.

Ground truth is a strict pytest file; the runner re-runs it independently. NOT the official
Terminal-Bench / SWE-bench — a local proxy for the same build/fix-graded-by-tests shape, on small
self-contained Python tasks. The result does not generalise beyond that population.
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
    # --- v3 expansion (2026-07-14): +9 neutral, standard coding tasks to raise n from 6 → 15 for
    # a pre-registered paired re-run. Standard problems (parsing/algorithms/data structures + one
    # multi-file bug), each graded by a strict pytest re-run independently. No bias toward the
    # scaffold: the loop's only edge is catching a failing test and retrying, a general advantage.
    {
        "id": "balanced_brackets",
        "prompt": (
            "Create brackets.py with is_balanced(s: str) -> bool. Consider only the bracket characters "
            "()[]{} (ignore every other character); return True iff they are correctly matched and "
            "nested. An empty string is balanced. Examples: '(a[b]{c})' -> True, '([)]' -> False, "
            "'(' -> False, ')(' -> False."
        ),
        "files": {},
        "verify": "brackets.py",
        "test": "test_brackets.py",
        "test_src": (
            "from brackets import is_balanced\n\n"
            "def test_ok():\n"
            "    assert is_balanced('(a[b]{c})') is True\n"
            "    assert is_balanced('') is True\n"
            "    assert is_balanced('x(y)z') is True\n"
            "def test_bad():\n"
            "    for bad in ['([)]', '(', ')(', '{[}]', '(((']:\n"
            "        assert is_balanced(bad) is False\n"
        ),
    },
    {
        "id": "run_length",
        "prompt": (
            "Create rle.py with encode(s: str) -> str and decode(s: str) -> str. encode does run-length "
            "encoding: each maximal run of a character becomes '<count><char>' (count always written, "
            "even 1): 'aaabbc' -> '3a2b1c'. decode is the exact inverse. Both map '' to ''. The input "
            "to encode contains no digits; decode input is always well-formed."
        ),
        "files": {},
        "verify": "rle.py",
        "test": "test_rle.py",
        "test_src": (
            "from rle import encode, decode\n\n"
            "def test_encode():\n"
            "    assert encode('aaabbc') == '3a2b1c'\n"
            "    assert encode('') == ''\n"
            "    assert encode('x') == '1x'\n"
            "def test_roundtrip():\n"
            "    for s in ['aaabbc', 'xxxxyz', 'abcabc', '']:\n"
            "        assert decode(encode(s)) == s\n"
        ),
    },
    {
        "id": "base_convert",
        "prompt": (
            "Create baseconv.py with convert(digits: str, from_base: int, to_base: int) -> str. Bases "
            "range 2..36; input digits are case-insensitive (a=10 .. z=35); the output uses lowercase "
            "digits and has no leading zeros, except the value zero which is '0'. Examples: "
            "convert('ff', 16, 2) -> '11111111', convert('101', 2, 10) -> '5', convert('z', 36, 10) -> "
            "'35', convert('0', 10, 2) -> '0'."
        ),
        "files": {},
        "verify": "baseconv.py",
        "test": "test_baseconv.py",
        "test_src": (
            "from baseconv import convert\n\n"
            "def test_convert():\n"
            "    assert convert('ff', 16, 2) == '11111111'\n"
            "    assert convert('101', 2, 10) == '5'\n"
            "    assert convert('z', 36, 10) == '35'\n"
            "    assert convert('0', 10, 2) == '0'\n"
            "    assert convert('255', 10, 16) == 'ff'\n"
        ),
    },
    {
        "id": "merge_intervals",
        "prompt": (
            "Create intervals.py with merge(intervals: list) -> list. Each interval is a (start, end) "
            "tuple with start <= end. Merge all overlapping OR touching intervals (so [1,2] and [2,3] "
            "merge into [1,3]) and return the result as a list of tuples sorted by start. merge([]) is "
            "[]. Example: merge([(1,3),(2,6),(8,10),(15,18)]) -> [(1,6),(8,10),(15,18)]."
        ),
        "files": {},
        "verify": "intervals.py",
        "test": "test_intervals.py",
        "test_src": (
            "from intervals import merge\n\n"
            "def test_merge():\n"
            "    assert merge([(1,3),(2,6),(8,10),(15,18)]) == [(1,6),(8,10),(15,18)]\n"
            "    assert merge([(1,4),(4,5)]) == [(1,5)]\n"
            "    assert merge([]) == []\n"
            "    assert merge([(5,6),(1,2)]) == [(1,2),(5,6)]\n"
        ),
    },
    {
        "id": "csv_parse",
        "prompt": (
            "Create csvline.py with parse_line(line: str) -> list. Split one CSV line on commas into "
            "fields. A field may be wrapped in double quotes, in which case commas inside it are "
            "literal; a doubled quote \"\"\"\" inside a quoted field is a literal quote character. Do not "
            "strip whitespace. Examples: parse_line('a,b,c') -> ['a','b','c']; parse_line('a,\"b,c\",d') "
            "-> ['a','b,c','d']; parse_line('') -> ['']; parse_line('a,,c') -> ['a','','c']."
        ),
        "files": {},
        "verify": "csvline.py",
        "test": "test_csvline.py",
        "test_src": (
            "from csvline import parse_line\n\n"
            "def test_plain():\n"
            "    assert parse_line('a,b,c') == ['a', 'b', 'c']\n"
            "    assert parse_line('') == ['']\n"
            "    assert parse_line('a,,c') == ['a', '', 'c']\n"
            "def test_quoted():\n"
            "    assert parse_line('a,\"b,c\",d') == ['a', 'b,c', 'd']\n"
            "    assert parse_line('\"he said \"\"hi\"\"\"') == ['he said \"hi\"']\n"
        ),
    },
    {
        "id": "template_render",
        "prompt": (
            "Create tmpl.py with render(template: str, values: dict) -> str. Replace each '{key}' with "
            "str(values[key]). A literal brace is written doubled: '{{' -> '{' and '}}' -> '}'. If a "
            "referenced key is missing from values, raise KeyError. Examples: render('Hi {name}!', "
            "{'name': 'Al'}) -> 'Hi Al!'; render('{{x}}', {}) -> '{x}'; render('{a}+{b}', {'a':1,'b':2}) "
            "-> '1+2'."
        ),
        "files": {},
        "verify": "tmpl.py",
        "test": "test_tmpl.py",
        "test_src": (
            "import pytest\n"
            "from tmpl import render\n\n"
            "def test_render():\n"
            "    assert render('Hi {name}!', {'name': 'Al'}) == 'Hi Al!'\n"
            "    assert render('{{x}}', {}) == '{x}'\n"
            "    assert render('{a}+{b}', {'a': 1, 'b': 2}) == '1+2'\n"
            "def test_missing_raises():\n"
            "    with pytest.raises(KeyError):\n"
            "        render('{nope}', {'a': 1})\n"
        ),
    },
    {
        "id": "lru_cache",
        "prompt": (
            "Create lru.py with a class LRUCache. LRUCache(capacity: int) holds up to `capacity` "
            "key->value pairs. get(key) returns the value or -1 if absent; put(key, value) inserts or "
            "updates. Both get and put mark the key most-recently-used. When inserting a NEW key would "
            "exceed capacity, evict the least-recently-used key first. (Classic LRU semantics.)"
        ),
        "files": {},
        "verify": "lru.py",
        "test": "test_lru.py",
        "test_src": (
            "from lru import LRUCache\n\n"
            "def test_lru():\n"
            "    c = LRUCache(2)\n"
            "    c.put(1, 1); c.put(2, 2)\n"
            "    assert c.get(1) == 1\n"
            "    c.put(3, 3)            # evicts key 2 (LRU)\n"
            "    assert c.get(2) == -1\n"
            "    c.put(4, 4)            # evicts key 1\n"
            "    assert c.get(1) == -1\n"
            "    assert c.get(3) == 3\n"
            "    assert c.get(4) == 4\n"
        ),
    },
    {
        "id": "topo_sort",
        "prompt": (
            "Create topo.py with topo_sort(graph: dict) -> list. `graph` maps a node to the list of "
            "nodes it points to (its dependencies-after edges: an edge a->b means a must come before "
            "b). Return a topological ordering of all nodes; when several nodes are ready, pick the "
            "smallest by normal sort order (deterministic). Raise ValueError if the graph has a cycle. "
            "Every node appears as a key. Example: {'a':['b'],'b':['c'],'c':[]} -> ['a','b','c']."
        ),
        "files": {},
        "verify": "topo.py",
        "test": "test_topo.py",
        "test_src": (
            "import pytest\n"
            "from topo import topo_sort\n\n"
            "def test_order():\n"
            "    assert topo_sort({'a': ['b'], 'b': ['c'], 'c': []}) == ['a', 'b', 'c']\n"
            "def test_tiebreak_deterministic():\n"
            "    assert topo_sort({'a': ['c'], 'b': ['c'], 'c': []}) == ['a', 'b', 'c']\n"
            "def test_cycle_raises():\n"
            "    with pytest.raises(ValueError):\n"
            "        topo_sort({'a': ['b'], 'b': ['a']})\n"
        ),
    },
    {
        "id": "fix_flatten",
        "prompt": (
            "The package `nested` has a bug: flatten in nested/core.py only flattens ONE level instead "
            "of fully flattening arbitrarily-deep nested lists. Fix it so flatten returns all "
            "non-list leaves in order, recursing to any depth. Strings must NOT be split into "
            "characters (treat a str as a leaf). Do not change the signature or the tests. Examples: "
            "flatten([1,[2,[3,[4]]]]) -> [1,2,3,4]; flatten([['a'],'bc']) -> ['a','bc']."
        ),
        "files": {
            "nested/__init__.py": "from nested.core import flatten\n\n__all__ = ['flatten']\n",
            "nested/core.py": (
                '"""List helpers."""\n\n\n'
                "def flatten(items):\n"
                '    """Flatten nested lists into a flat list of leaves."""\n'
                "    out = []\n"
                "    for it in items:\n"
                "        # BUG: only one level deep — a nested list inside `it` is left unflattened.\n"
                "        if isinstance(it, list):\n"
                "            out.extend(it)\n"
                "        else:\n"
                "            out.append(it)\n"
                "    return out\n"
            ),
        },
        "verify": "nested/core.py",
        "test": "test_flatten.py",
        "test_src": (
            "from nested import flatten\n\n"
            "def test_deep():\n"
            "    assert flatten([1, [2, [3, [4]]]]) == [1, 2, 3, 4]\n"
            "    assert flatten([]) == []\n"
            "def test_strings_are_leaves():\n"
            "    assert flatten([['a'], 'bc', [['d']]]) == ['a', 'bc', 'd']\n"
        ),
    },
]

# --- v3: the 85 new tasks (see PREREGISTRATION.md) -------------------------------------------
# Split by domain into sibling modules so the fixed domain mix is visible in the file layout rather
# than asserted in prose. Imported at the bottom because the runners all do `from tasks import TASKS`
# and must keep seeing one flat list.
#
# Duplicate `test`/`verify` filenames across domains are harmless and deliberate-by-omission: every
# task is restored into its OWN workspace keyed by its (unique) id, so two tasks named `titlecase.py`
# never share a directory. Verified, not assumed.
from tasks_algorithms import TASKS_ALGORITHMS  # noqa: E402
from tasks_bugfix import TASKS_BUGFIX  # noqa: E402
from tasks_parsing import TASKS_PARSING  # noqa: E402

TASKS.extend(TASKS_PARSING)
TASKS.extend(TASKS_ALGORITHMS)
TASKS.extend(TASKS_BUGFIX)

_ids = [t["id"] for t in TASKS]
if len(_ids) != len(set(_ids)):  # a silent id collision would overwrite a workspace mid-run
    raise AssertionError("duplicate task ids across the suite modules")
