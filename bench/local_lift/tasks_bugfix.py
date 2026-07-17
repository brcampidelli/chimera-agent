"""Local weak-model-lift task set — the *bug-fix* domain (28 tasks).

Authored against the a-priori difficulty spec in ``bench/local_lift/PREREGISTRATION.md``: a task is
selected for *intrinsic* complexity only — never for how any arm performs on it. No task here was
ever piloted against a model.

The shape extends the two bug-fix tasks already in ``tasks.py`` (``fix_percentile``, ``fix_flatten``):
each task ships a small module that *looks* correct and handles the obvious happy path, with one
semantic bug that only surfaces on a boundary, an empty/duplicate input, an aliasing/mutation
contract, or a tie-break the naive reading misses. The prompt states the **symptom**, never the fix
and never the line.

Every test checks the *whole* contract, not just the reported symptom: a "fix" that special-cases the
input named in the prompt fails. That property was verified for all 28 tasks (buggy code fails, a
correct fix passes, a lazy special-case patch fails) before this file was committed.

Ground truth is the strict pytest file; the runner re-runs it independently. Tests are deterministic:
stdlib only, no network, no randomness, no clock, no filesystem outside the task's own workspace.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------------------------------
# Starter modules. Each is syntactically valid and importable — the defect is always semantic.
# --------------------------------------------------------------------------------------------------

_BUGGY_LOOKUP = '''\
"""Ordered-sequence lookup helpers."""


def index_of(items, target):
    """Index of the FIRST occurrence of `target` in ascending `items`, or -1 if absent."""
    lo, hi = 0, len(items) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if items[mid] == target:
            return mid
        if items[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1
'''

_BUGGY_MAVG = '''\
"""Rolling-window statistics."""


def moving_average(values, window):
    """Mean of every consecutive `window`-sized slice of `values`, left to right."""
    if window <= 0:
        raise ValueError("window must be positive")
    out = []
    for i in range(len(values) - window):
        chunk = values[i:i + window]
        out.append(sum(chunk) / window)
    return out
'''

_BUGGY_CHUNKER = '''\
"""Batching helpers."""


def chunk(items, size):
    """Split `items` into consecutive lists of at most `size` items."""
    if size <= 0:
        raise ValueError("size must be positive")
    out = []
    buf = []
    for it in items:
        buf.append(it)
        if len(buf) == size:
            out.append(buf)
            buf = []
    return out
'''

_BUGGY_SETTINGS = '''\
"""Settings helpers."""


def merge(base, override):
    """Return a NEW dict: `base` overlaid with `override`. Neither argument is modified."""
    result = base
    result.update(override)
    return result
'''

_BUGGY_CLONER = '''\
"""Config helpers."""


def clone(config):
    """Return an independent copy of `config`; mutating the copy never touches the original."""
    return dict(config)
'''

_BUGGY_COLLECTOR = '''\
"""Accumulation helpers."""


def collect(item, bucket=[]):
    """Append `item` to `bucket` and return it. Without a bucket, start from a fresh empty list."""
    bucket.append(item)
    return bucket
'''

_BUGGY_AMOUNT = '''\
"""Money parsing."""


def parse_amount(text):
    """Parse a money string such as '1,234.50' into a float."""
    try:
        return float(text.replace(",", ""))
    except Exception:
        return 0.0
'''

_BUGGY_TOTALS = '''\
"""Reconciliation helpers."""


def totals_match(values, expected):
    """True iff the sum of `values` equals `expected` within an absolute tolerance of 1e-9."""
    return sum(values) == expected
'''

_BUGGY_MONEY = '''\
"""Money rounding."""


def round_cents(value):
    """Round `value` to 2 decimal places, halves going away from zero. Returns a float."""
    return round(value, 2)
'''

_BUGGY_STATS = '''\
"""Descriptive statistics."""


def average(values):
    """Arithmetic mean of `values` as a float."""
    total = 0
    for v in values:
        total += v
    return total // len(values)
'''

_BUGGY_ROWCHECK = '''\
"""Row validation."""


def validate(rows):
    """Collect one message per problem found, over every row, in order."""
    errors = []
    for i, row in enumerate(rows):
        if not row.get("name"):
            errors.append("row %d: empty name" % i)
        if row.get("age", 0) < 0:
            errors.append("row %d: negative age" % i)
        return errors
    return errors
'''

_BUGGY_RANKING = '''\
"""Leaderboard helpers."""


def rank(players):
    """Order (name, score) pairs by score, highest first."""
    return sorted(players, key=lambda p: (p[1], p[0]), reverse=True)
'''

_BUGGY_DIVISORS = '''\
"""Number theory helpers."""


def divisors(n):
    """Every positive divisor of `n`, ascending."""
    if n < 1:
        raise ValueError("n must be >= 1")
    small = []
    large = []
    i = 1
    while i * i < n:
        if n % i == 0:
            small.append(i)
            large.append(n // i)
        i += 1
    return small + large[::-1]
'''

_BUGGY_FREQ = '''\
"""Frequency helpers."""


def most_common(items):
    """The most frequent item; ties go to whichever appeared first in `items`."""
    counts = {}
    for it in items:
        counts[it] = counts.get(it, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[0][0]
'''

_BUGGY_PAGING = '''\
"""Pagination helpers."""


def page_count(total, per_page):
    """How many pages `total` items need."""
    if per_page <= 0:
        raise ValueError("per_page must be positive")
    return total // per_page


def page_items(items, number, per_page):
    """The slice of `items` shown on page `number`. Pages are numbered from 1."""
    start = number * per_page
    return items[start:start + per_page]
'''

_BUGGY_SUFFIX = '''\
"""String trimming helpers."""


def remove_suffix(text, suffix):
    """`text` without a trailing `suffix`; unchanged when it does not end with one."""
    if text.endswith(suffix):
        return text[: -len(suffix)]
    return text
'''

_BUGGY_NORM = '''\
"""Whitespace helpers."""


def normalize(text):
    """Collapse every run of whitespace to a single space and strip the ends."""
    return text.replace("  ", " ").strip()
'''

_BUGGY_ROTATE = '''\
"""List rotation."""


def rotate(items, k):
    """Rotate `items` left by `k` places, always returning a new list."""
    return items[k:] + items[:k]
'''

_BUGGY_MATRIX = '''\
"""Matrix helpers."""


def transpose(rows):
    """Transpose a rectangular matrix given as a list of equal-length rows."""
    n = len(rows)
    out = []
    for j in range(n):
        out.append([rows[i][j] for i in range(n)])
    return out
'''

_BUGGY_GROUPING = '''\
"""Grouping helpers."""


def group_by(items, key):
    """Map key(item) -> the list of items with that key, each list in input order."""
    groups = dict.fromkeys([key(it) for it in items], [])
    for it in items:
        groups[key(it)].append(it)
    return groups
'''

_BUGGY_PAIRS = '''\
"""Combination helpers."""


def pairs(items):
    """Every pair of distinct positions as (items[i], items[j]) with i < j, in order."""
    out = []
    for i in range(len(items)):
        for j in range(len(items)):
            if i != j:
                out.append((items[i], items[j]))
    return out
'''

_BUGGY_EDITDIST = '''\
"""Edit distance."""


def distance(a, b):
    """Levenshtein distance between `a` and `b`."""
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1])
    return dp[m][n]
'''

_BUGGY_WORDCOUNT = '''\
"""Text statistics."""


def count_words(text):
    """Case-insensitive count of each word in `text`."""
    counts = {}
    for word in text.lower().split():
        counts[word] = counts.get(word, 0) + 1
    return counts
'''

_BUGGY_INSERTPOS = '''\
"""Sorted-insertion helpers."""


def insert_pos(items, value):
    """Index at which to insert `value` into ascending `items`, after any equal entries."""
    lo, hi = 0, len(items)
    while lo < hi:
        mid = (lo + hi) // 2
        if items[mid] < value:
            lo = mid + 1
        else:
            hi = mid
    return lo
'''

_BUGGY_QUERY = '''\
"""Query-string parsing."""


def parse_query(qs):
    """Parse a URL query string into a dict."""
    out = {}
    for part in qs.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        out[key] = value
    return out
'''

_BUGGY_RETRY = '''\
"""Retry helpers."""


def run_with_retries(func, retries=2):
    """Call `func()` until it succeeds and return its result; re-raise the last error otherwise."""
    last = None
    for _ in range(retries):
        try:
            return func()
        except Exception as exc:
            last = exc
    raise last
'''

_BUGGY_FIRSTVALID = '''\
"""Selection helpers."""


def first_value(values, default=None):
    """The first element of `values` that is not None, else `default`."""
    for v in values:
        if v:
            return v
    return default
'''

_BUGGY_TITLECASE = '''\
"""Text casing."""


def title_case(text):
    """Capitalise each whitespace-separated word of `text` and lowercase the rest of it."""
    return text.title()
'''


TASKS_BUGFIX: list[dict[str, Any]] = [
    {
        "id": "fix_index_of",
        "prompt": (
            "lookup.py has a bug. index_of(items, target) must return the index of the FIRST "
            "occurrence of target in the ascending-sorted list items, or -1 when it is absent. "
            "Symptom: index_of([1, 2, 2, 2, 3], 2) returns 2, expected 1. Lists without duplicates "
            "and absent targets already work and must keep working. Do not change the signature."
        ),
        "files": {"lookup.py": _BUGGY_LOOKUP},
        "verify": "lookup.py",
        "test": "test_index_of.py",
        "test_src": (
            "from lookup import index_of\n\n"
            "def test_first_occurrence():\n"
            "    assert index_of([1, 2, 2, 2, 3], 2) == 1\n"
            "    assert index_of([5, 5, 5], 5) == 0\n"
            "    assert index_of(['a', 'a', 'b'], 'a') == 0\n"
            "    assert index_of([1, 1, 1, 1, 1, 1, 1, 2], 1) == 0\n"
            "def test_unique_and_absent():\n"
            "    assert index_of([1, 2, 3, 4], 3) == 2\n"
            "    assert index_of([1, 2, 3, 4], 1) == 0\n"
            "    assert index_of([1, 2, 3, 4], 4) == 3\n"
            "    assert index_of([1, 2, 3, 4], 9) == -1\n"
            "    assert index_of([], 1) == -1\n"
            "    assert index_of([7], 7) == 0\n"
        ),
    },
    {
        "id": "fix_moving_average",
        "prompt": (
            "mavg.py has a bug. moving_average(values, window) must return the mean of every "
            "consecutive window-sized slice of values, left to right. Symptom: "
            "moving_average([1, 2, 3, 4], 2) returns [1.5, 2.5], expected [1.5, 2.5, 3.5]. When "
            "values is shorter than window the result is []; a window <= 0 raises ValueError. Do "
            "not change the signature."
        ),
        "files": {"mavg.py": _BUGGY_MAVG},
        "verify": "mavg.py",
        "test": "test_moving_average.py",
        "test_src": (
            "import pytest\n"
            "from mavg import moving_average\n\n"
            "def test_windows():\n"
            "    assert moving_average([1, 2, 3, 4], 2) == [1.5, 2.5, 3.5]\n"
            "    assert moving_average([1, 2, 3], 3) == [2.0]\n"
            "    assert moving_average([1, 2, 3], 1) == [1.0, 2.0, 3.0]\n"
            "    assert moving_average([2, 4, 6, 8, 10], 3) == [4.0, 6.0, 8.0]\n"
            "def test_short_and_invalid():\n"
            "    assert moving_average([1, 2], 3) == []\n"
            "    assert moving_average([], 2) == []\n"
            "    with pytest.raises(ValueError):\n"
            "        moving_average([1, 2], 0)\n"
        ),
    },
    {
        "id": "fix_chunk_list",
        "prompt": (
            "chunker.py has a bug. chunk(items, size) must split items into consecutive lists of at "
            "most size items, covering every item. Symptom: chunk([1, 2, 3, 4, 5], 2) returns "
            "[[1, 2], [3, 4]] — the 5 is lost; expected [[1, 2], [3, 4], [5]]. Exact multiples "
            "already work and must keep working; size <= 0 raises ValueError. Do not change the "
            "signature."
        ),
        "files": {"chunker.py": _BUGGY_CHUNKER},
        "verify": "chunker.py",
        "test": "test_chunker.py",
        "test_src": (
            "import pytest\n"
            "from chunker import chunk\n\n"
            "def test_partial_tail():\n"
            "    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]\n"
            "    assert chunk([1], 5) == [[1]]\n"
            "    assert chunk(['a', 'b', 'c'], 2) == [['a', 'b'], ['c']]\n"
            "def test_exact_and_empty():\n"
            "    assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]\n"
            "    assert chunk([], 3) == []\n"
            "    assert chunk([1, 2, 3], 1) == [[1], [2], [3]]\n"
            "def test_invalid_size():\n"
            "    with pytest.raises(ValueError):\n"
            "        chunk([1, 2], 0)\n"
        ),
    },
    {
        "id": "fix_merge_settings",
        "prompt": (
            "settings.py has a bug. merge(base, override) must return a NEW dict holding base "
            "overlaid with override, leaving BOTH arguments untouched. Symptom: after "
            "base = {'a': 1}; merge(base, {'b': 2}), base is {'a': 1, 'b': 2} — the caller's dict "
            "was modified; it should still be {'a': 1}. The returned mapping is already correct. Do "
            "not change the signature."
        ),
        "files": {"settings.py": _BUGGY_SETTINGS},
        "verify": "settings.py",
        "test": "test_merge_settings.py",
        "test_src": (
            "from settings import merge\n\n"
            "def test_result():\n"
            "    assert merge({'a': 1}, {'b': 2}) == {'a': 1, 'b': 2}\n"
            "    assert merge({'a': 1}, {'a': 9}) == {'a': 9}\n"
            "    assert merge({}, {}) == {}\n"
            "def test_arguments_untouched():\n"
            "    base = {'a': 1}\n"
            "    over = {'b': 2}\n"
            "    merge(base, over)\n"
            "    assert base == {'a': 1}\n"
            "    assert over == {'b': 2}\n"
            "    other = {'x': 0}\n"
            "    merge(other, {'y': 1, 'z': 2})\n"
            "    assert other == {'x': 0}\n"
            "def test_returns_new_object():\n"
            "    base = {'a': 1}\n"
            "    out = merge(base, {})\n"
            "    assert out is not base\n"
            "    out['c'] = 3\n"
            "    assert base == {'a': 1}\n"
        ),
    },
    {
        "id": "fix_clone_config",
        "prompt": (
            "cloner.py has a bug. clone(config) must return a fully independent copy: mutating "
            "anything inside the copy, at any depth, must never affect the original. Symptom: "
            "cfg = {'a': {'x': [1, 2]}}; c = clone(cfg); c['a']['x'].append(3) leaves cfg as "
            "{'a': {'x': [1, 2, 3]}} — the original changed. Equality (clone(cfg) == cfg) already "
            "holds and must keep holding. Do not change the signature."
        ),
        "files": {"cloner.py": _BUGGY_CLONER},
        "verify": "cloner.py",
        "test": "test_clone_config.py",
        "test_src": (
            "from cloner import clone\n\n"
            "def test_equal():\n"
            "    cfg = {'a': {'x': [1, 2]}, 'b': 1}\n"
            "    assert clone(cfg) == cfg\n"
            "    assert clone({}) == {}\n"
            "def test_nested_list_independent():\n"
            "    cfg = {'a': {'x': [1, 2]}, 'b': 1}\n"
            "    c = clone(cfg)\n"
            "    c['a']['x'].append(3)\n"
            "    assert cfg['a']['x'] == [1, 2]\n"
            "def test_nested_dict_independent():\n"
            "    cfg = {'a': {'x': 1}}\n"
            "    c = clone(cfg)\n"
            "    c['a']['y'] = 2\n"
            "    assert cfg['a'] == {'x': 1}\n"
            "def test_list_of_dicts_independent():\n"
            "    cfg = {'items': [{'n': 1}, {'n': 2}]}\n"
            "    c = clone(cfg)\n"
            "    c['items'][0]['n'] = 99\n"
            "    c['items'].append({'n': 3})\n"
            "    assert cfg == {'items': [{'n': 1}, {'n': 2}]}\n"
        ),
    },
    {
        "id": "fix_collect_items",
        "prompt": (
            "collector.py has a bug. collect(item, bucket=None-by-contract) appends item to bucket "
            "and returns it; called WITHOUT a bucket it must start from a fresh empty list every "
            "time, and called WITH one it must append to that very list in place. Symptom: "
            "collect(1) returns [1], then collect(2) returns [1, 2] instead of [2] — state leaks "
            "between calls. Do not change the parameter names or the call signature."
        ),
        "files": {"collector.py": _BUGGY_COLLECTOR},
        "verify": "collector.py",
        "test": "test_collect_items.py",
        "test_src": (
            "from collector import collect\n\n"
            "def test_fresh_each_call():\n"
            "    assert collect(1) == [1]\n"
            "    assert collect(2) == [2]\n"
            "    assert collect('x') == ['x']\n"
            "def test_given_bucket_used_in_place():\n"
            "    b = [0]\n"
            "    out = collect(1, b)\n"
            "    assert out == [0, 1]\n"
            "    assert b == [0, 1]\n"
            "    assert out is b\n"
            "def test_no_leak_after_explicit_bucket():\n"
            "    collect(1, [0])\n"
            "    assert collect(9) == [9]\n"
        ),
    },
    {
        "id": "fix_parse_amount",
        "prompt": (
            "amount.py has a bug. parse_amount(text) parses a money string like '1,234.50' into a "
            "float. An empty or whitespace-only string means 'no value' and returns 0.0; any other "
            "text that is not a number must raise ValueError. Symptom: parse_amount('abc') returns "
            "0.0 instead of raising ValueError — a typo is silently read as zero. Valid amounts "
            "already parse correctly. Do not change the signature."
        ),
        "files": {"amount.py": _BUGGY_AMOUNT},
        "verify": "amount.py",
        "test": "test_parse_amount.py",
        "test_src": (
            "import pytest\n"
            "from amount import parse_amount\n\n"
            "def test_valid():\n"
            "    assert parse_amount('1,234.50') == 1234.5\n"
            "    assert parse_amount('42') == 42.0\n"
            "    assert parse_amount('-3.5') == -3.5\n"
            "    assert parse_amount(' 7 ') == 7.0\n"
            "def test_blank_is_zero():\n"
            "    assert parse_amount('') == 0.0\n"
            "    assert parse_amount('   ') == 0.0\n"
            "def test_bad_raises():\n"
            "    for bad in ['abc', '1.2.3', '$5', '12x', '--1', 'nan!']:\n"
            "        with pytest.raises(ValueError):\n"
            "            parse_amount(bad)\n"
        ),
    },
    {
        "id": "fix_totals_match",
        "prompt": (
            "totals.py has a bug. totals_match(values, expected) must report whether the sum of "
            "values equals expected within an ABSOLUTE tolerance of 1e-9. Symptom: "
            "totals_match([0.1, 0.2], 0.3) returns False, expected True. A genuine mismatch must "
            "still be False — totals_match([0.1, 0.2], 0.30000001) is False, because 1e-8 exceeds "
            "the tolerance. Do not change the signature."
        ),
        "files": {"totals.py": _BUGGY_TOTALS},
        "verify": "totals.py",
        "test": "test_totals_match.py",
        "test_src": (
            "from totals import totals_match\n\n"
            "def test_float_noise_matches():\n"
            "    assert totals_match([0.1, 0.2], 0.3) is True\n"
            "    assert totals_match([0.1] * 10, 1.0) is True\n"
            "    assert totals_match([1.0, 2.0], 3.0) is True\n"
            "    assert totals_match([], 0.0) is True\n"
            "def test_real_mismatch_rejected():\n"
            "    assert totals_match([0.1, 0.2], 0.30000001) is False\n"
            "    assert totals_match([1.0], 2.0) is False\n"
            "    assert totals_match([0.1, 0.2], 0.4) is False\n"
            "    assert totals_match([], 0.001) is False\n"
        ),
    },
    {
        "id": "fix_round_cents",
        "prompt": (
            "money.py has a bug. round_cents(value) must round value to 2 decimal places with ties "
            "going AWAY FROM ZERO (the way an invoice rounds), returning a float. Symptom: "
            "round_cents(0.125) returns 0.12, expected 0.13. Non-tie values already round correctly "
            "and must keep doing so. Note 0.625 must give 0.63 and -0.125 must give -0.13. Do not "
            "change the signature."
        ),
        "files": {"money.py": _BUGGY_MONEY},
        "verify": "money.py",
        "test": "test_round_cents.py",
        "test_src": (
            "from money import round_cents\n\n"
            "def test_ties_away_from_zero():\n"
            "    assert round_cents(0.125) == 0.13\n"
            "    assert round_cents(0.625) == 0.63\n"
            "    assert round_cents(0.375) == 0.38\n"
            "    assert round_cents(-0.125) == -0.13\n"
            "    assert round_cents(-0.625) == -0.63\n"
            "def test_non_ties_unchanged():\n"
            "    assert round_cents(1.234) == 1.23\n"
            "    assert round_cents(1.239) == 1.24\n"
            "    assert round_cents(2.0) == 2.0\n"
            "    assert round_cents(0.0) == 0.0\n"
            "    assert round_cents(-1.234) == -1.23\n"
            "def test_returns_float():\n"
            "    assert isinstance(round_cents(0.125), float)\n"
        ),
    },
    {
        "id": "fix_average",
        "prompt": (
            "stats.py has a bug. average(values) must return the arithmetic mean of values as a "
            "float, and an empty sequence must average to 0.0. Symptoms: average([1, 2, 3, 4]) "
            "returns 2 instead of 2.5, and average([]) blows up with ZeroDivisionError instead of "
            "returning 0.0. Do not change the signature."
        ),
        "files": {"stats.py": _BUGGY_STATS},
        "verify": "stats.py",
        "test": "test_average.py",
        "test_src": (
            "from stats import average\n\n"
            "def test_mean():\n"
            "    assert average([1, 2, 3, 4]) == 2.5\n"
            "    assert average([2, 4]) == 3.0\n"
            "    assert average([5]) == 5.0\n"
            "    assert average([-1, -2]) == -1.5\n"
            "    assert average([1, 2]) == 1.5\n"
            "def test_empty():\n"
            "    assert average([]) == 0.0\n"
            "def test_float_type():\n"
            "    assert isinstance(average([1, 2, 3]), float)\n"
            "    assert isinstance(average([]), float)\n"
        ),
    },
    {
        "id": "fix_validate_rows",
        "prompt": (
            "rowcheck.py has a bug. validate(rows) must return the list of problem messages for "
            "EVERY row, in order: \"row {i}: empty name\" when the row's 'name' is missing/empty and "
            "\"row {i}: negative age\" when its 'age' is below zero (a row can produce both, name "
            "first). Symptom: with three rows where rows 0 and 2 are both bad, only row 0's message "
            "comes back. A single-row input already works. Do not change the signature or the "
            "message wording."
        ),
        "files": {"rowcheck.py": _BUGGY_ROWCHECK},
        "verify": "rowcheck.py",
        "test": "test_validate_rows.py",
        "test_src": (
            "from rowcheck import validate\n\n"
            "def test_every_row_reported():\n"
            "    rows = [{'name': '', 'age': 3}, {'name': 'ok', 'age': 1}, {'name': '', 'age': 2}]\n"
            "    assert validate(rows) == ['row 0: empty name', 'row 2: empty name']\n"
            "def test_late_row_reported():\n"
            "    rows = [{'name': 'a', 'age': 1}, {'name': 'b', 'age': -1}]\n"
            "    assert validate(rows) == ['row 1: negative age']\n"
            "def test_both_problems_one_row():\n"
            "    assert validate([{'name': '', 'age': -2}]) == [\n"
            "        'row 0: empty name', 'row 0: negative age',\n"
            "    ]\n"
            "def test_clean_and_empty():\n"
            "    assert validate([{'name': 'a', 'age': 0}, {'name': 'b', 'age': 5}]) == []\n"
            "    assert validate([]) == []\n"
        ),
    },
    {
        "id": "fix_rank_players",
        "prompt": (
            "ranking.py has a bug. rank(players) takes (name, score) pairs and must order them by "
            "score DESCENDING, with ties broken by name in normal ASCENDING alphabetical order. "
            "Symptom: rank([('bob', 10), ('amy', 10), ('cid', 20)]) returns "
            "[('cid', 20), ('bob', 10), ('amy', 10)]; expected [('cid', 20), ('amy', 10), "
            "('bob', 10)]. Scores already sort the right way. Do not change the signature."
        ),
        "files": {"ranking.py": _BUGGY_RANKING},
        "verify": "ranking.py",
        "test": "test_rank_players.py",
        "test_src": (
            "from ranking import rank\n\n"
            "def test_tie_names_ascending():\n"
            "    got = rank([('bob', 10), ('amy', 10), ('cid', 20)])\n"
            "    assert got == [('cid', 20), ('amy', 10), ('bob', 10)]\n"
            "def test_three_way_tie():\n"
            "    got = rank([('c', 5), ('a', 5), ('b', 5)])\n"
            "    assert got == [('a', 5), ('b', 5), ('c', 5)]\n"
            "def test_scores_descending():\n"
            "    got = rank([('a', 1), ('b', 3), ('c', 2)])\n"
            "    assert got == [('b', 3), ('c', 2), ('a', 1)]\n"
            "def test_edges():\n"
            "    assert rank([]) == []\n"
            "    assert rank([('solo', 7)]) == [('solo', 7)]\n"
        ),
    },
    {
        "id": "fix_divisors",
        "prompt": (
            "divisors.py has a bug. divisors(n) must return every positive divisor of n (n >= 1) in "
            "ascending order, each exactly once. Symptoms: divisors(9) returns [1, 9] — the 3 is "
            "missing — expected [1, 3, 9]; and divisors(1) returns [] instead of [1]. divisors(6) "
            "and divisors(12) are already right and must stay right; n < 1 raises ValueError. Do "
            "not change the signature."
        ),
        "files": {"divisors.py": _BUGGY_DIVISORS},
        "verify": "divisors.py",
        "test": "test_divisors.py",
        "test_src": (
            "import pytest\n"
            "from divisors import divisors\n\n"
            "def test_perfect_squares():\n"
            "    assert divisors(9) == [1, 3, 9]\n"
            "    assert divisors(4) == [1, 2, 4]\n"
            "    assert divisors(16) == [1, 2, 4, 8, 16]\n"
            "    assert divisors(36) == [1, 2, 3, 4, 6, 9, 12, 18, 36]\n"
            "def test_others():\n"
            "    assert divisors(1) == [1]\n"
            "    assert divisors(6) == [1, 2, 3, 6]\n"
            "    assert divisors(12) == [1, 2, 3, 4, 6, 12]\n"
            "    assert divisors(13) == [1, 13]\n"
            "def test_invalid():\n"
            "    with pytest.raises(ValueError):\n"
            "        divisors(0)\n"
        ),
    },
    {
        "id": "fix_most_common",
        "prompt": (
            "freq.py has a bug. most_common(items) must return the most frequent item, breaking a "
            "tie in favour of whichever tied item APPEARED FIRST in items. Symptom: "
            "most_common(['b', 'a', 'b', 'a']) returns 'a', expected 'b' (both appear twice, 'b' "
            "came first). An empty input must raise ValueError. A clear single winner already "
            "works. Do not change the signature."
        ),
        "files": {"freq.py": _BUGGY_FREQ},
        "verify": "freq.py",
        "test": "test_most_common.py",
        "test_src": (
            "import pytest\n"
            "from freq import most_common\n\n"
            "def test_tie_first_seen():\n"
            "    assert most_common(['b', 'a', 'b', 'a']) == 'b'\n"
            "    assert most_common(['c', 'a', 'a', 'c']) == 'c'\n"
            "    assert most_common(['z', 'y', 'z', 'y', 'x']) == 'z'\n"
            "def test_clear_winner():\n"
            "    assert most_common(['x', 'y', 'y']) == 'y'\n"
            "    assert most_common(['a', 'b', 'c', 'a']) == 'a'\n"
            "    assert most_common(['solo']) == 'solo'\n"
            "def test_empty_raises():\n"
            "    with pytest.raises(ValueError):\n"
            "        most_common([])\n"
        ),
    },
    {
        "id": "fix_pagination",
        "prompt": (
            "paging.py has two bugs. page_count(total, per_page) must give the number of pages "
            "needed to show every item (a partial last page still counts); symptom: "
            "page_count(10, 3) returns 3, expected 4, and page_count(1, 3) returns 0, expected 1. "
            "page_items(items, number, per_page) must return the slice shown on page `number`, "
            "where PAGES ARE NUMBERED FROM 1 and any out-of-range page gives []; symptom: "
            "page_items([1, 2, 3, 4, 5], 1, 2) returns [3, 4], expected [1, 2]. per_page <= 0 "
            "raises ValueError in page_count. Do not change the signatures."
        ),
        "files": {"paging.py": _BUGGY_PAGING},
        "verify": "paging.py",
        "test": "test_pagination.py",
        "test_src": (
            "import pytest\n"
            "from paging import page_count, page_items\n\n"
            "def test_page_count():\n"
            "    assert page_count(10, 3) == 4\n"
            "    assert page_count(9, 3) == 3\n"
            "    assert page_count(1, 3) == 1\n"
            "    assert page_count(0, 3) == 0\n"
            "    assert page_count(7, 1) == 7\n"
            "def test_page_count_invalid():\n"
            "    with pytest.raises(ValueError):\n"
            "        page_count(5, 0)\n"
            "def test_page_items():\n"
            "    items = [1, 2, 3, 4, 5]\n"
            "    assert page_items(items, 1, 2) == [1, 2]\n"
            "    assert page_items(items, 2, 2) == [3, 4]\n"
            "    assert page_items(items, 3, 2) == [5]\n"
            "def test_page_items_out_of_range():\n"
            "    items = [1, 2, 3, 4, 5]\n"
            "    assert page_items(items, 4, 2) == []\n"
            "    assert page_items(items, 0, 2) == []\n"
            "    assert page_items(items, -1, 2) == []\n"
            "    assert page_items([], 1, 2) == []\n"
        ),
    },
    {
        "id": "fix_remove_suffix",
        "prompt": (
            "suffix.py has a bug. remove_suffix(text, suffix) must return text without a trailing "
            "suffix, and text unchanged when it does not end with that suffix. Symptom: "
            "remove_suffix('report', '') returns '' — the whole string vanishes — expected "
            "'report'. Ordinary suffixes already work and must keep working. Do not change the "
            "signature."
        ),
        "files": {"suffix.py": _BUGGY_SUFFIX},
        "verify": "suffix.py",
        "test": "test_remove_suffix.py",
        "test_src": (
            "from suffix import remove_suffix\n\n"
            "def test_empty_suffix_is_noop():\n"
            "    assert remove_suffix('report', '') == 'report'\n"
            "    assert remove_suffix('data', '') == 'data'\n"
            "    assert remove_suffix('', '') == ''\n"
            "def test_normal_suffix():\n"
            "    assert remove_suffix('report.txt', '.txt') == 'report'\n"
            "    assert remove_suffix('aaa', 'a') == 'aa'\n"
            "    assert remove_suffix('abc', 'abc') == ''\n"
            "def test_no_match():\n"
            "    assert remove_suffix('report.txt', '.csv') == 'report.txt'\n"
            "    assert remove_suffix('', '.txt') == ''\n"
            "    assert remove_suffix('txt', '.txt') == 'txt'\n"
        ),
    },
    {
        "id": "fix_normalize_ws",
        "prompt": (
            "norm.py has a bug. normalize(text) must collapse every run of whitespace — spaces, "
            "tabs, newlines — into a single space and strip the ends. Symptoms: normalize('a   b') "
            "returns 'a  b' (still two spaces), expected 'a b'; and normalize('a\\tb') leaves the "
            "tab alone, expected 'a b'. normalize('a  b') already returns 'a b'. Do not change the "
            "signature."
        ),
        "files": {"norm.py": _BUGGY_NORM},
        "verify": "norm.py",
        "test": "test_normalize_ws.py",
        "test_src": (
            "from norm import normalize\n\n"
            "def test_long_runs():\n"
            "    assert normalize('a   b') == 'a b'\n"
            "    assert normalize('a     b') == 'a b'\n"
            "    assert normalize('a  b') == 'a b'\n"
            "def test_other_whitespace():\n"
            "    assert normalize('a\\tb') == 'a b'\n"
            "    assert normalize('a\\nb') == 'a b'\n"
            "    assert normalize('a \\t\\n b') == 'a b'\n"
            "def test_strip_and_empty():\n"
            "    assert normalize('  a \\n b  ') == 'a b'\n"
            "    assert normalize('') == ''\n"
            "    assert normalize('   ') == ''\n"
            "    assert normalize('solo') == 'solo'\n"
        ),
    },
    {
        "id": "fix_rotate_list",
        "prompt": (
            "rotate.py has a bug. rotate(items, k) must rotate items LEFT by k places and always "
            "return a NEW list. k is any integer: it wraps around when larger than the list, and a "
            "negative k rotates right. Symptoms: rotate([1, 2, 3, 4], 5) returns [1, 2, 3, 4], "
            "expected [2, 3, 4, 1]; rotate([1, 2, 3, 4], -5) returns [1, 2, 3, 4], expected "
            "[4, 1, 2, 3]. Small k already works, and rotate([], 3) must return []. Do not change "
            "the signature."
        ),
        "files": {"rotate.py": _BUGGY_ROTATE},
        "verify": "rotate.py",
        "test": "test_rotate_list.py",
        "test_src": (
            "from rotate import rotate\n\n"
            "def test_wraps():\n"
            "    assert rotate([1, 2, 3, 4], 5) == [2, 3, 4, 1]\n"
            "    assert rotate([1, 2, 3, 4], 9) == [2, 3, 4, 1]\n"
            "    assert rotate([1, 2, 3, 4], 4) == [1, 2, 3, 4]\n"
            "def test_negative():\n"
            "    assert rotate([1, 2, 3, 4], -1) == [4, 1, 2, 3]\n"
            "    assert rotate([1, 2, 3, 4], -5) == [4, 1, 2, 3]\n"
            "    assert rotate([1, 2, 3, 4], -4) == [1, 2, 3, 4]\n"
            "def test_small_k_and_empty():\n"
            "    assert rotate([1, 2, 3, 4], 1) == [2, 3, 4, 1]\n"
            "    assert rotate([1, 2, 3, 4], 0) == [1, 2, 3, 4]\n"
            "    assert rotate([], 3) == []\n"
            "    assert rotate([], -3) == []\n"
            "def test_new_list():\n"
            "    src = [1, 2, 3]\n"
            "    out = rotate(src, 0)\n"
            "    assert out is not src\n"
            "    out.append(9)\n"
            "    assert src == [1, 2, 3]\n"
        ),
    },
    {
        "id": "fix_transpose",
        "prompt": (
            "matrix.py has a bug. transpose(rows) must transpose a rectangular matrix given as a "
            "list of equal-length rows, and must raise ValueError when the rows are not all the "
            "same length. Symptom: transpose([[1, 2, 3], [4, 5, 6]]) returns [[1, 4], [2, 5]] — the "
            "column [3, 6] is missing — expected [[1, 4], [2, 5], [3, 6]]. Square matrices already "
            "work; transpose([]) is []. Do not change the signature."
        ),
        "files": {"matrix.py": _BUGGY_MATRIX},
        "verify": "matrix.py",
        "test": "test_transpose.py",
        "test_src": (
            "import pytest\n"
            "from matrix import transpose\n\n"
            "def test_wide():\n"
            "    assert transpose([[1, 2, 3], [4, 5, 6]]) == [[1, 4], [2, 5], [3, 6]]\n"
            "def test_tall():\n"
            "    assert transpose([[1], [2], [3]]) == [[1, 2, 3]]\n"
            "    assert transpose([[1, 2]]) == [[1], [2]]\n"
            "def test_square_and_empty():\n"
            "    assert transpose([[1, 2], [3, 4]]) == [[1, 3], [2, 4]]\n"
            "    assert transpose([]) == []\n"
            "def test_ragged_raises():\n"
            "    with pytest.raises(ValueError):\n"
            "        transpose([[1, 2], [3]])\n"
            "    with pytest.raises(ValueError):\n"
            "        transpose([[1], [2, 3]])\n"
        ),
    },
    {
        "id": "fix_group_by",
        "prompt": (
            "grouping.py has a bug. group_by(items, key) must map key(item) -> the list of the "
            "items with that key, each list in input order and independent of the others. Symptom: "
            "group_by([1, 2, 3, 4], lambda x: x % 2) returns {1: [1, 2, 3, 4], 0: [1, 2, 3, 4]} — "
            "every group holds everything — expected {1: [1, 3], 0: [2, 4]}. A single-group input "
            "already works. Do not change the signature."
        ),
        "files": {"grouping.py": _BUGGY_GROUPING},
        "verify": "grouping.py",
        "test": "test_group_by.py",
        "test_src": (
            "from grouping import group_by\n\n"
            "def test_split():\n"
            "    assert group_by([1, 2, 3, 4], lambda x: x % 2) == {1: [1, 3], 0: [2, 4]}\n"
            "    assert group_by(['aa', 'b', 'cc'], len) == {2: ['aa', 'cc'], 1: ['b']}\n"
            "def test_single_group_and_empty():\n"
            "    assert group_by([2, 4], lambda x: 'even') == {'even': [2, 4]}\n"
            "    assert group_by([], lambda x: x) == {}\n"
            "def test_groups_are_independent():\n"
            "    g = group_by([1, 2], lambda x: x)\n"
            "    g[1].append(99)\n"
            "    assert g[2] == [2]\n"
        ),
    },
    {
        "id": "fix_unique_pairs",
        "prompt": (
            "pairs.py has a bug. pairs(items) must return every pair of DISTINCT POSITIONS as "
            "(items[i], items[j]) with i < j, in that order. Symptom: pairs([1, 2, 3]) returns "
            "[(1, 2), (1, 3), (2, 1), (2, 3), (3, 1), (3, 2)] — each pair shows up twice, once "
            "reversed — expected [(1, 2), (1, 3), (2, 3)]. Note positions are what is distinct, not "
            "values: pairs(['a', 'a']) is [('a', 'a')]. Do not change the signature."
        ),
        "files": {"pairs.py": _BUGGY_PAIRS},
        "verify": "pairs.py",
        "test": "test_unique_pairs.py",
        "test_src": (
            "from pairs import pairs\n\n"
            "def test_ordered_pairs():\n"
            "    assert pairs([1, 2, 3]) == [(1, 2), (1, 3), (2, 3)]\n"
            "    assert pairs(['a', 'b', 'c', 'd']) == [\n"
            "        ('a', 'b'), ('a', 'c'), ('a', 'd'), ('b', 'c'), ('b', 'd'), ('c', 'd'),\n"
            "    ]\n"
            "def test_duplicate_values_kept():\n"
            "    assert pairs(['a', 'a']) == [('a', 'a')]\n"
            "    assert pairs([1, 1, 1]) == [(1, 1), (1, 1), (1, 1)]\n"
            "def test_small():\n"
            "    assert pairs([1]) == []\n"
            "    assert pairs([]) == []\n"
        ),
    },
    {
        "id": "fix_edit_distance",
        "prompt": (
            "editdist.py has a bug. distance(a, b) must return the Levenshtein distance — the "
            "minimum number of single-character INSERTIONS, DELETIONS or SUBSTITUTIONS turning a "
            "into b. Symptom: distance('cat', 'cut') returns 2, expected 1; distance('kitten', "
            "'sitting') returns 5, expected 3. Pure insertions and identical strings already give "
            "the right answer. Do not change the signature."
        ),
        "files": {"editdist.py": _BUGGY_EDITDIST},
        "verify": "editdist.py",
        "test": "test_edit_distance.py",
        "test_src": (
            "from editdist import distance\n\n"
            "def test_substitutions():\n"
            "    assert distance('cat', 'cut') == 1\n"
            "    assert distance('kitten', 'sitting') == 3\n"
            "    assert distance('flaw', 'lawn') == 2\n"
            "    assert distance('abc', 'xyz') == 3\n"
            "def test_insert_delete():\n"
            "    assert distance('', 'abc') == 3\n"
            "    assert distance('abc', '') == 3\n"
            "    assert distance('ab', 'abc') == 1\n"
            "def test_identical():\n"
            "    assert distance('abc', 'abc') == 0\n"
            "    assert distance('', '') == 0\n"
        ),
    },
    {
        "id": "fix_count_words",
        "prompt": (
            "wordcount.py has a bug. count_words(text) must count words case-insensitively, where a "
            "word is a run of letters and/or digits and every other character (punctuation, "
            "apostrophes, whitespace) merely separates words and is dropped. Symptom: "
            "count_words('Hi, hi! hi.') returns {'hi,': 1, 'hi!': 1, 'hi.': 1}, expected {'hi': 3}. "
            "By the same rule count_words(\"it's ok\") is {'it': 1, 's': 1, 'ok': 1}. Plain "
            "space-separated text already counts correctly. Do not change the signature."
        ),
        "files": {"wordcount.py": _BUGGY_WORDCOUNT},
        "verify": "wordcount.py",
        "test": "test_count_words.py",
        "test_src": (
            "from wordcount import count_words\n\n"
            "def test_punctuation_dropped():\n"
            "    assert count_words('Hi, hi! hi.') == {'hi': 3}\n"
            "    assert count_words('a-b a b') == {'a': 2, 'b': 2}\n"
            "    assert count_words('(x) [x]') == {'x': 2}\n"
            "def test_apostrophe_splits():\n"
            "    assert count_words(\"it's ok\") == {'it': 1, 's': 1, 'ok': 1}\n"
            "def test_case_and_digits():\n"
            "    assert count_words('The cat THE') == {'the': 2, 'cat': 1}\n"
            "    assert count_words('r2 r2 d2') == {'r2': 2, 'd2': 1}\n"
            "def test_empty():\n"
            "    assert count_words('') == {}\n"
            "    assert count_words('   ') == {}\n"
            "    assert count_words('!!! ...') == {}\n"
        ),
    },
    {
        "id": "fix_insert_pos",
        "prompt": (
            "insertpos.py has a bug. insert_pos(items, value) must return the index at which value "
            "should be inserted into the ascending list items to keep it sorted, placing it AFTER "
            "any entries already equal to it. Symptom: insert_pos([1, 2, 2, 2, 3], 2) returns 1, "
            "expected 4. Lists without an equal entry already give the right index. Do not change "
            "the signature."
        ),
        "files": {"insertpos.py": _BUGGY_INSERTPOS},
        "verify": "insertpos.py",
        "test": "test_insert_pos.py",
        "test_src": (
            "from insertpos import insert_pos\n\n"
            "def test_after_equals():\n"
            "    assert insert_pos([1, 2, 2, 2, 3], 2) == 4\n"
            "    assert insert_pos([2, 2], 2) == 2\n"
            "    assert insert_pos([1, 1, 1], 1) == 3\n"
            "    assert insert_pos([1, 2, 3], 3) == 3\n"
            "    assert insert_pos([1, 2, 3], 1) == 1\n"
            "def test_no_equals():\n"
            "    assert insert_pos([1, 3, 5], 4) == 2\n"
            "    assert insert_pos([1, 2, 3], 0) == 0\n"
            "    assert insert_pos([1, 2, 3], 9) == 3\n"
            "    assert insert_pos([], 1) == 0\n"
        ),
    },
    {
        "id": "fix_parse_query",
        "prompt": (
            "query.py has a bug. parse_query(qs) parses a URL query string into a dict. A key that "
            "appears ONCE maps to its string value; a key that appears MORE THAN ONCE maps to the "
            "list of its values in order. A part with no '=' maps to ''. An empty string parses to "
            "{}. Symptom: parse_query('a=1&a=2') returns {'a': '2'} — the first value is lost — "
            "expected {'a': ['1', '2']}. Single-occurrence keys already work. Do not change the "
            "signature."
        ),
        "files": {"query.py": _BUGGY_QUERY},
        "verify": "query.py",
        "test": "test_parse_query.py",
        "test_src": (
            "from query import parse_query\n\n"
            "def test_repeats():\n"
            "    assert parse_query('a=1&a=2') == {'a': ['1', '2']}\n"
            "    assert parse_query('a=1&a=2&a=3') == {'a': ['1', '2', '3']}\n"
            "    assert parse_query('a=1&b=2&a=3') == {'a': ['1', '3'], 'b': '2'}\n"
            "def test_single():\n"
            "    assert parse_query('a=1&b=2') == {'a': '1', 'b': '2'}\n"
            "    assert parse_query('a=') == {'a': ''}\n"
            "def test_bare_and_empty():\n"
            "    assert parse_query('flag') == {'flag': ''}\n"
            "    assert parse_query('flag&flag') == {'flag': ['', '']}\n"
            "    assert parse_query('') == {}\n"
        ),
    },
    {
        "id": "fix_retry_attempts",
        "prompt": (
            "retry.py has a bug. run_with_retries(func, retries) calls func() until it returns "
            "without raising, and re-raises the last exception when they all fail. `retries` counts "
            "the RETRIES AFTER the first attempt, so func is called at most 1 + retries times. "
            "Symptoms: with retries=2 and a func that fails twice then succeeds, the exception is "
            "re-raised after only 2 calls instead of succeeding on the 3rd; and retries=0 raises "
            "TypeError without calling func at all, when it should call it exactly once. Do not "
            "change the signature."
        ),
        "files": {"retry.py": _BUGGY_RETRY},
        "verify": "retry.py",
        "test": "test_retry_attempts.py",
        "test_src": (
            "import pytest\n"
            "from retry import run_with_retries\n\n"
            "def make(fail_times):\n"
            "    calls = []\n"
            "    def f():\n"
            "        calls.append(1)\n"
            "        if len(calls) <= fail_times:\n"
            "            raise ValueError('boom %d' % len(calls))\n"
            "        return 'ok'\n"
            "    return f, calls\n\n"
            "def test_succeeds_on_last_retry():\n"
            "    f, calls = make(2)\n"
            "    assert run_with_retries(f, retries=2) == 'ok'\n"
            "    assert len(calls) == 3\n"
            "def test_all_fail_reraises_last():\n"
            "    f, calls = make(99)\n"
            "    with pytest.raises(ValueError):\n"
            "        run_with_retries(f, retries=2)\n"
            "    assert len(calls) == 3\n"
            "def test_zero_retries_calls_once():\n"
            "    f, calls = make(0)\n"
            "    assert run_with_retries(f, retries=0) == 'ok'\n"
            "    assert len(calls) == 1\n"
            "def test_zero_retries_failure_raises():\n"
            "    f, calls = make(99)\n"
            "    with pytest.raises(ValueError):\n"
            "        run_with_retries(f, retries=0)\n"
            "    assert len(calls) == 1\n"
            "def test_first_attempt_succeeds():\n"
            "    f, calls = make(0)\n"
            "    assert run_with_retries(f, retries=5) == 'ok'\n"
            "    assert len(calls) == 1\n"
        ),
    },
    {
        "id": "fix_first_value",
        "prompt": (
            "firstvalid.py has a bug. first_value(values, default=None) must return the first "
            "element that is not None — every other value counts as present, INCLUDING 0, False, "
            "'' and [] — or `default` when there is none. Symptom: first_value([None, 0, 5]) "
            "returns 5, expected 0. Lists whose first non-None entry is an ordinary truthy value "
            "already work. Do not change the signature."
        ),
        "files": {"firstvalid.py": _BUGGY_FIRSTVALID},
        "verify": "firstvalid.py",
        "test": "test_first_value.py",
        "test_src": (
            "from firstvalid import first_value\n\n"
            "def test_falsy_values_count():\n"
            "    assert first_value([None, 0, 5]) == 0\n"
            "    assert first_value(['', 'a']) == ''\n"
            "    assert first_value([False, True]) is False\n"
            "    assert first_value([None, [], [1]]) == []\n"
            "def test_truthy():\n"
            "    assert first_value([None, None, 'x']) == 'x'\n"
            "    assert first_value([3, 4]) == 3\n"
            "def test_default():\n"
            "    assert first_value([]) is None\n"
            "    assert first_value([None, None]) is None\n"
            "    assert first_value([None], default=-1) == -1\n"
            "    assert first_value([], default='d') == 'd'\n"
        ),
    },
    {
        "id": "fix_title_case",
        "prompt": (
            "titlecase.py has a bug. title_case(text) must split text on whitespace, uppercase the "
            "first character of each word, lowercase the rest of that word, and join the words with "
            "single spaces. A character inside a word — an apostrophe or a hyphen — does NOT start "
            "a new word. Symptoms: title_case(\"don't stop\") returns \"Don'T Stop\", expected "
            "\"Don't Stop\"; title_case('mary-jane') returns 'Mary-Jane', expected 'Mary-jane'. "
            "Plain words already come out right. Do not change the signature."
        ),
        "files": {"titlecase.py": _BUGGY_TITLECASE},
        "verify": "titlecase.py",
        "test": "test_title_case.py",
        "test_src": (
            "from titlecase import title_case\n\n"
            "def test_inner_punctuation():\n"
            "    assert title_case(\"don't stop\") == \"Don't Stop\"\n"
            "    assert title_case('mary-jane') == 'Mary-jane'\n"
            "    assert title_case(\"o'brien and co-op\") == \"O'brien And Co-op\"\n"
            "def test_plain_words():\n"
            "    assert title_case('hello world') == 'Hello World'\n"
            "    assert title_case('hELLO') == 'Hello'\n"
            "    assert title_case('a') == 'A'\n"
            "def test_spacing_and_empty():\n"
            "    assert title_case('  a  b ') == 'A B'\n"
            "    assert title_case('') == ''\n"
            "    assert title_case('   ') == ''\n"
        ),
    },
]
