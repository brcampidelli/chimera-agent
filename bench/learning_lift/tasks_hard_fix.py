"""Hard same-family bugfix tasks — the suite for learning-lift run 2.

WHY THIS EXISTS. Run 1 used the 30 `fix_*` tasks from `local_lift` because they were the only family
with enough shared structure for transfer to be possible. They turned out to be saturated for this
model: the control arm scored 100% on the first half, so the design could not detect an improvement
even if there was one. The other 70 tasks in that suite sit at the right difficulty but are one-offs
with nothing to transfer between them — a null there would be attributable to "nothing to learn",
which is the ambiguity run 1 was designed to avoid.

So: same family, harder. Same prompt shape ("package X has a bug in FUNC; fix it"), same single-module
form, same strict pytest — transfer opportunity preserved — but authored against the difficulty spec
below so the control arm has somewhere to go.

## A-PRIORI DIFFICULTY SPEC (fixed BEFORE any task was written, and not tuned afterwards)

The existing `fix_*` tasks are easy for a 24B model for four identifiable reasons. Each is inverted
here, and every task in this file satisfies at least three of the four:

1. **The symptom is not handed over.** The prompt states the *contract*, never a failing example. The
   agent has to find the failure itself. (Existing tasks say "returns [[1,2],[3,4]] — the 5 is lost".)
2. **The bug is not local to the function named.** It originates in a helper, a default argument, or
   an interaction between two functions, so patching the obvious site does not fix it.
3. **The naive fix breaks a second required case.** There is a trap: the first patch a model reaches
   for satisfies the obvious case and fails another one the test also checks.
4. **The contract has a quiet clause.** Ordering, stability, aliasing, empty/boundary handling or type
   preservation is specified in the docstring and easy to skim past.

Target: the control arm (full scaffold, no learning) lands at **40–60%**. That target was set before
authoring and is NOT enforced by tuning tasks after measuring — if the realised rate lands outside the
band, that is reported as-is, and the band is a design goal, not a result to be manufactured.

Nothing here was written by looking at run 1's per-task outcomes.
"""

from __future__ import annotations


def _t(tid: str, prompt: str, module: str, src: str, test: str, test_src: str) -> dict:
    """One task in the local_lift schema, so the existing runner consumes it unchanged."""
    return {
        "id": tid,
        "prompt": prompt,
        "files": {module: src},
        "verify": f"python -m pytest -q {test}",
        "test": test,
        "test_src": test_src,
    }


HARD_FIX_TASKS: list[dict] = [
    _t(
        "hfix_merge_ranges",
        "The package `ranges` has a bug: merge(intervals) in ranges.py must return the minimal set of "
        "non-overlapping half-open [start, end) intervals covering the input, sorted by start. "
        "Touching intervals merge. Fix it.",
        "ranges.py",
        '''"""Interval helpers."""


def _norm(intervals):
    return sorted(intervals)


def merge(intervals):
    """Merge overlapping/touching half-open [start, end) intervals; sorted by start."""
    if not intervals:
        return []
    out = []
    for start, end in _norm(intervals):
        if out and start < out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out
''',
        "test_ranges.py",
        '''from ranges import merge

def test_touching_intervals_merge():
    # half-open: [1,3) and [3,5) are contiguous and must become one
    assert merge([(1, 3), (3, 5)]) == [(1, 5)]

def test_overlap_and_containment():
    assert merge([(1, 6), (2, 4)]) == [(1, 6)]
    assert merge([(5, 7), (1, 3)]) == [(1, 3), (5, 7)]

def test_empty_and_single():
    assert merge([]) == []
    assert merge([(2, 2)]) == [(2, 2)]
''',
    ),
    _t(
        "hfix_lru_cache",
        "The package `cache` has a bug: LRU in cache.py must evict the least-recently-USED entry when "
        "over capacity. A get() counts as a use. Fix it.",
        "cache.py",
        '''"""A tiny LRU cache."""


class LRU:
    """Evicts the least-recently-USED entry. get() counts as a use."""

    def __init__(self, capacity):
        self.capacity = capacity
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def put(self, key, value):
        self._data[key] = value
        if len(self._data) > self.capacity:
            oldest = next(iter(self._data))
            del self._data[oldest]
''',
        "test_cache.py",
        '''from cache import LRU

def test_get_counts_as_use():
    c = LRU(2)
    c.put("a", 1)
    c.put("b", 2)
    c.get("a")            # 'a' is now the most recently used
    c.put("c", 3)         # must evict 'b', not 'a'
    assert c.get("a") == 1
    assert c.get("b") is None
    assert c.get("c") == 3

def test_reinsert_refreshes():
    c = LRU(2)
    c.put("a", 1); c.put("b", 2); c.put("a", 9)
    c.put("c", 3)
    assert c.get("b") is None
    assert c.get("a") == 9
''',
    ),
    _t(
        "hfix_deep_update",
        "The package `conf` has a bug: deep_update(base, override) in conf.py must return a NEW nested "
        "dict with override merged into base, recursively, without mutating either argument. Fix it.",
        "conf.py",
        '''"""Config merging."""


def deep_update(base, override):
    """Recursively merge `override` into `base`. Returns a new dict; inputs are not mutated."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_update(out[key], value)
        else:
            out[key] = value
    return out
''',
        "test_conf.py",
        '''from conf import deep_update

def test_does_not_mutate_or_alias_inputs():
    base = {"a": {"x": 1}, "keep": [1, 2]}
    over = {"a": {"y": 2}}
    got = deep_update(base, over)
    assert got == {"a": {"x": 1, "y": 2}, "keep": [1, 2]}
    assert base == {"a": {"x": 1}, "keep": [1, 2]}
    # the untouched nested value must be a COPY, not the same object
    got["keep"].append(3)
    assert base["keep"] == [1, 2]

def test_override_replaces_non_dict():
    assert deep_update({"a": {"x": 1}}, {"a": 5}) == {"a": 5}
''',
    ),
    _t(
        "hfix_stable_sort_by",
        "The package `sorting` has a bug: sort_by(rows, keys) in sorting.py must sort rows by each key "
        "in order of priority, preserving the original order of rows that tie on every key. Fix it.",
        "sorting.py",
        '''"""Multi-key sorting."""


def sort_by(rows, keys):
    """Sort by `keys` in priority order. Ties keep their original relative order."""
    out = list(rows)
    for key in keys:
        out.sort(key=lambda r: r[key])
    return out
''',
        "test_sorting.py",
        '''from sorting import sort_by

def test_priority_order():
    rows = [
        {"dept": "b", "age": 30, "name": "x"},
        {"dept": "a", "age": 40, "name": "y"},
        {"dept": "a", "age": 20, "name": "z"},
    ]
    got = sort_by(rows, ["dept", "age"])
    assert [r["name"] for r in got] == ["z", "y", "x"]

def test_stability_on_full_tie():
    rows = [{"k": 1, "n": "first"}, {"k": 1, "n": "second"}, {"k": 1, "n": "third"}]
    assert [r["n"] for r in sort_by(rows, ["k"])] == ["first", "second", "third"]

def test_input_not_mutated():
    rows = [{"k": 2}, {"k": 1}]
    sort_by(rows, ["k"])
    assert rows == [{"k": 2}, {"k": 1}]
''',
    ),
    _t(
        "hfix_paginate",
        "The package `paging` has a bug: page(items, per_page, number) in paging.py must return the "
        "1-indexed page of items. Out-of-range pages return an empty list. Fix it.",
        "paging.py",
        '''"""Pagination."""


def page_count(total, per_page):
    return total // per_page


def page(items, per_page, number):
    """Return 1-indexed page `number`. Out-of-range pages are empty."""
    if per_page <= 0:
        raise ValueError("per_page must be positive")
    if number < 1 or number > page_count(len(items), per_page):
        return []
    start = (number - 1) * per_page
    return items[start:start + per_page]
''',
        "test_paging.py",
        '''import pytest
from paging import page, page_count

def test_partial_last_page_is_reachable():
    items = [1, 2, 3, 4, 5]
    assert page(items, 2, 3) == [5]
    assert page_count(5, 2) == 3

def test_first_page_and_out_of_range():
    assert page([1, 2, 3], 2, 1) == [1, 2]
    assert page([1, 2, 3], 2, 9) == []
    assert page([1, 2, 3], 2, 0) == []

def test_empty_input():
    assert page([], 3, 1) == []
    assert page_count(0, 3) == 0

def test_bad_per_page():
    with pytest.raises(ValueError):
        page([1], 0, 1)
''',
    ),
    _t(
        "hfix_word_wrap",
        "The package `wrap` has a bug: wrap(text, width) in wrap.py must break text into lines of at "
        "most `width` characters, breaking only at spaces, never splitting a word, and dropping the "
        "spaces used as break points. A word longer than width goes on its own line. Fix it.",
        "wrap.py",
        '''"""Greedy word wrapping."""


def wrap(text, width):
    """Lines of at most `width` chars, breaking only at spaces."""
    words = text.split()
    if not words:
        return []
    lines = []
    current = words[0]
    for word in words[1:]:
        if len(current) + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines
''',
        "test_wrap.py",
        '''from wrap import wrap

def test_respects_width_including_the_space():
    # "ab cd" is 5 chars — it must NOT fit in width 4
    assert wrap("ab cd", 4) == ["ab", "cd"]
    assert wrap("ab cd", 5) == ["ab cd"]

def test_long_word_on_its_own_line():
    assert wrap("hi enormous", 4) == ["hi", "enormous"]

def test_empty():
    assert wrap("", 10) == []
    assert wrap("   ", 10) == []
''',
    ),
    _t(
        "hfix_retry_backoff",
        "The package `retry` has a bug: call(fn, attempts) in retry.py must call fn until it returns "
        "without raising, at most `attempts` times, and re-raise the LAST exception if all fail. "
        "It must not swallow a success on the final attempt. Fix it.",
        "retry.py",
        '''"""Retry helper."""


def call(fn, attempts=3):
    """Call `fn` until it succeeds, at most `attempts` times; re-raise the last error."""
    last = None
    for _ in range(attempts - 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise last
''',
        "test_retry.py",
        '''import pytest
from retry import call

def test_success_on_final_attempt():
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("nope")
        return "ok"
    assert call(fn, attempts=3) == "ok"
    assert calls["n"] == 3

def test_reraises_last_error():
    def always():
        raise KeyError("boom")
    with pytest.raises(KeyError):
        call(always, attempts=2)

def test_first_try_success_calls_once():
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        return 42
    assert call(fn, attempts=5) == 42
    assert calls["n"] == 1
''',
    ),
    _t(
        "hfix_group_consecutive",
        "The package `runs` has a bug: group(items) in runs.py must collapse CONSECUTIVE equal items "
        "into (item, count) pairs, in order. Non-adjacent repeats are separate groups. Fix it.",
        "runs.py",
        '''"""Run-length grouping."""

from collections import Counter


def group(items):
    """Collapse consecutive equal items into (item, count), in order."""
    counts = Counter(items)
    return [(item, counts[item]) for item in dict.fromkeys(items)]
''',
        "test_runs.py",
        '''from runs import group

def test_non_adjacent_repeats_are_separate():
    assert group(["a", "a", "b", "a"]) == [("a", 2), ("b", 1), ("a", 1)]

def test_simple_runs():
    assert group([1, 1, 1, 2]) == [(1, 3), (2, 1)]
    assert group([]) == []
    assert group([7]) == [(7, 1)]
''',
    ),
    _t(
        "hfix_parse_duration",
        "The package `duration` has a bug: parse(text) in duration.py must turn a compound duration "
        "like '1h30m' into total seconds, accepting h/m/s in any combination and any order, and "
        "raising ValueError on anything else. Fix it.",
        "duration.py",
        '''"""Duration parsing."""

import re

_UNIT = {"h": 3600, "m": 60, "s": 1}
_RE = re.compile(r"(\\d+)([hms])")


def parse(text):
    """'1h30m' -> 5400. Raises ValueError on malformed input."""
    total = 0
    for amount, unit in _RE.findall(text):
        total += int(amount) * _UNIT[unit]
    return total
''',
        "test_duration.py",
        '''import pytest
from duration import parse

def test_compound_and_single():
    assert parse("1h30m") == 5400
    assert parse("45s") == 45
    assert parse("2h") == 7200

def test_rejects_garbage_instead_of_returning_zero():
    for bad in ["", "abc", "10x", "1h!", "h"]:
        with pytest.raises(ValueError):
            parse(bad)

def test_rejects_trailing_junk():
    with pytest.raises(ValueError):
        parse("1h30")
''',
    ),
    _t(
        "hfix_flatten_depth",
        "The package `flat` has a bug: flatten(items, depth) in flat.py must flatten nested lists to "
        "exactly `depth` levels (depth=None means fully). Strings are never iterated. Fix it.",
        "flat.py",
        '''"""Depth-limited flattening."""


def flatten(items, depth=None):
    """Flatten to `depth` levels; None = fully. Strings are atoms."""
    out = []
    for item in items:
        if isinstance(item, list):
            out.extend(flatten(item, depth))
        else:
            out.append(item)
    return out
''',
        "test_flat.py",
        '''from flat import flatten

def test_depth_is_respected():
    nested = [1, [2, [3, [4]]]]
    assert flatten(nested, 1) == [1, 2, [3, [4]]]
    assert flatten(nested, 2) == [1, 2, 3, [4]]

def test_full_flatten():
    assert flatten([1, [2, [3, [4]]]], None) == [1, 2, 3, 4]

def test_strings_are_atoms():
    assert flatten(["ab", ["cd"]], None) == ["ab", "cd"]

def test_depth_zero_is_a_copy():
    src = [1, [2]]
    got = flatten(src, 0)
    assert got == [1, [2]]
    assert got is not src
''',
    ),
    _t(
        "hfix_top_n",
        "The package `top` has a bug: top_n(counts, n) in top.py must return the n highest-scoring "
        "keys as (key, score), sorted by score descending and by key ascending on ties. Fix it.",
        "top.py",
        '''"""Top-N selection."""


def top_n(counts, n):
    """n highest by score desc, then key asc on ties."""
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])
    return ordered[:n]
''',
        "test_top.py",
        '''from top import top_n

def test_ties_break_by_key_ascending():
    counts = {"pear": 3, "apple": 3, "fig": 5}
    assert top_n(counts, 3) == [("fig", 5), ("apple", 3), ("pear", 3)]

def test_n_larger_than_input():
    assert top_n({"a": 1}, 10) == [("a", 1)]

def test_zero_and_empty():
    assert top_n({"a": 1}, 0) == []
    assert top_n({}, 3) == []
''',
    ),
    _t(
        "hfix_csv_quotes",
        "The package `csvlite` has a bug: split_row(line) in csvlite.py must split a CSV line on "
        "commas, honouring double-quoted fields (which may contain commas), and stripping the quotes "
        "from the value. A doubled quote inside a quoted field is a literal quote. Fix it.",
        "csvlite.py",
        '''"""Minimal CSV row splitting."""


def split_row(line):
    """Split on commas, honouring quoted fields."""
    return [cell.strip('"') for cell in line.split(",")]
''',
        "test_csvlite.py",
        '''from csvlite import split_row

def test_comma_inside_quotes():
    assert split_row('a,"b,c",d') == ["a", "b,c", "d"]

def test_doubled_quote_is_literal():
    assert split_row('"he said ""hi""",x') == ['he said "hi"', "x"]

def test_plain_and_empty_fields():
    assert split_row("a,b,c") == ["a", "b", "c"]
    assert split_row("a,,c") == ["a", "", "c"]
''',
    ),
    _t(
        "hfix_normalize_path",
        "The package `paths` has a bug: normalize(path) in paths.py must collapse '.' and '..' "
        "segments and repeated separators in a POSIX-style path, keeping a leading '/' if present. "
        "'..' must not escape above the root of an absolute path. Fix it.",
        "paths.py",
        '''"""Path normalisation."""


def normalize(path):
    """Collapse '.', '..' and repeated '/'. '..' cannot escape an absolute root."""
    absolute = path.startswith("/")
    parts = []
    for seg in path.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
        else:
            parts.append(seg)
    out = "/".join(parts)
    return "/" + out if absolute else out
''',
        "test_paths.py",
        '''from paths import normalize

def test_relative_dotdot_is_kept_when_it_escapes():
    # relative paths CAN go above their start; only absolute ones are clamped
    assert normalize("../a") == "../a"
    assert normalize("a/../../b") == "../b"

def test_absolute_is_clamped_at_root():
    assert normalize("/../a") == "/a"
    assert normalize("/a/../../b") == "/b"

def test_collapsing():
    assert normalize("/a//b/./c") == "/a/b/c"
    assert normalize("a/b/../c") == "a/c"

def test_edges():
    assert normalize("/") == "/"
    assert normalize("") == ""
''',
    ),
    _t(
        "hfix_diff_keys",
        "The package `delta` has a bug: changed(before, after) in delta.py must return the set of keys "
        "whose value differs between the two dicts, including keys present in only one of them. Fix it.",
        "delta.py",
        '''"""Dict diffing."""


def changed(before, after):
    """Keys whose value differs, including keys present in only one dict."""
    return {key for key in before if before[key] != after.get(key)}
''',
        "test_delta.py",
        '''from delta import changed

def test_added_keys_count_as_changed():
    assert changed({"a": 1}, {"a": 1, "b": 2}) == {"b"}

def test_removed_and_modified():
    assert changed({"a": 1, "b": 2}, {"a": 9}) == {"a", "b"}

def test_none_value_is_not_absence():
    # a key explicitly set to None differs from a missing key
    assert changed({"a": None}, {}) == {"a"}
    assert changed({}, {"a": None}) == {"a"}

def test_identical():
    assert changed({"a": 1}, {"a": 1}) == set()
''',
    ),
    _t(
        "hfix_running_median",
        "The package `stats2` has a bug: median(values) in stats2.py must return the middle value for "
        "an odd count and the average of the two middle values for an even count, without mutating "
        "the caller's list. Fix it.",
        "stats2.py",
        '''"""Median."""


def median(values):
    """Middle value (odd) or mean of the two middle values (even)."""
    if not values:
        raise ValueError("median of empty sequence")
    values.sort()
    mid = len(values) // 2
    return values[mid]
''',
        "test_stats2.py",
        '''import pytest
from stats2 import median

def test_even_count_averages_the_middle_two():
    assert median([1, 2, 3, 4]) == 2.5

def test_odd_count():
    assert median([3, 1, 2]) == 2

def test_does_not_mutate_caller_list():
    data = [3, 1, 2]
    median(data)
    assert data == [3, 1, 2]

def test_empty_raises():
    with pytest.raises(ValueError):
        median([])
''',
    ),
    _t(
        "hfix_template_render",
        "The package `tmpl` has a bug: render(text, values) in tmpl.py must replace each {name} with "
        "the matching value, leave unknown placeholders untouched, and treat {{ and }} as literal "
        "braces. Fix it.",
        "tmpl.py",
        '''"""Tiny templating."""

import re

_RE = re.compile(r"\\{(\\w+)\\}")


def render(text, values):
    """Replace {name}; unknown names are left as-is. {{ and }} are literal braces."""
    return _RE.sub(lambda m: str(values.get(m.group(1), m.group(0))), text)
''',
        "test_tmpl.py",
        '''from tmpl import render

def test_escaped_braces_are_literal():
    assert render("{{x}}", {"x": "no"}) == "{x}"
    assert render("{{", {}) == "{"

def test_substitution_and_unknown():
    assert render("hi {name}", {"name": "ana"}) == "hi ana"
    assert render("hi {nope}", {}) == "hi {nope}"

def test_mixed():
    assert render("{{a}} {b}", {"b": "B"}) == "{a} B"
''',
    ),
    _t(
        "hfix_batch_by_size",
        "The package `batching` has a bug: batches(items, max_weight, weight) in batching.py must "
        "group items into consecutive batches whose total weight does not exceed max_weight. An item "
        "heavier than max_weight goes alone in its own batch. Fix it.",
        "batching.py",
        '''"""Weight-bounded batching."""


def batches(items, max_weight, weight):
    """Consecutive batches under max_weight. An oversized item gets its own batch."""
    out = []
    current = []
    total = 0
    for item in items:
        w = weight(item)
        if total + w > max_weight:
            out.append(current)
            current = [item]
            total = w
        else:
            current.append(item)
            total += w
    out.append(current)
    return out
''',
        "test_batching.py",
        '''from batching import batches

def test_oversized_item_alone_without_empty_batch():
    got = batches([5, 1], 3, lambda x: x)
    assert [] not in got
    assert got == [[5], [1]]

def test_normal_grouping():
    assert batches([1, 1, 2, 1], 2, lambda x: x) == [[1, 1], [2], [1]]

def test_empty_input_has_no_batches():
    assert batches([], 3, lambda x: x) == []
''',
    ),
    _t(
        "hfix_case_insensitive_dict",
        "The package `cidict` has a bug: CIDict in cidict.py must look up keys case-insensitively "
        "while REMEMBERING the casing of the key as first inserted, which is what keys() returns. "
        "Re-assigning an existing key keeps the original casing. Fix it.",
        "cidict.py",
        '''"""Case-insensitive mapping."""


class CIDict:
    """Case-insensitive lookup; keys() reports the ORIGINAL casing first seen."""

    def __init__(self):
        self._data = {}

    def __setitem__(self, key, value):
        self._data[key.lower()] = (key, value)

    def __getitem__(self, key):
        return self._data[key.lower()][1]

    def keys(self):
        return [original for original, _ in self._data.values()]
''',
        "test_cidict.py",
        '''from cidict import CIDict

def test_reassign_keeps_original_casing():
    d = CIDict()
    d["Content-Type"] = "a"
    d["content-type"] = "b"
    assert d["CONTENT-TYPE"] == "b"
    assert d.keys() == ["Content-Type"]

def test_basic_lookup():
    d = CIDict()
    d["Xy"] = 1
    assert d["xy"] == 1
    assert d.keys() == ["Xy"]
''',
    ),
    _t(
        "hfix_split_sentences",
        "The package `sent` has a bug: split(text) in sent.py must split prose into sentences ending "
        "at '.', '!' or '?', keeping the terminator on the sentence and NOT splitting on a period "
        "that is part of a common abbreviation (Mr., Dr., e.g., i.e.). Fix it.",
        "sent.py",
        '''"""Sentence splitting."""

import re

ABBREV = {"mr.", "dr.", "e.g.", "i.e.", "mrs.", "st."}
_RE = re.compile(r"(?<=[.!?])\\s+")


def split(text):
    """Split into sentences; abbreviations do not end a sentence."""
    if not text.strip():
        return []
    return [s.strip() for s in _RE.split(text.strip()) if s.strip()]
''',
        "test_sent.py",
        '''from sent import split

def test_abbreviation_does_not_split():
    assert split("Dr. Ana left. She waved.") == ["Dr. Ana left.", "She waved."]
    assert split("Use e.g. this one. Then stop.") == ["Use e.g. this one.", "Then stop."]

def test_terminators_are_kept():
    assert split("Hi! Ok? Yes.") == ["Hi!", "Ok?", "Yes."]

def test_empty():
    assert split("") == []
    assert split("   ") == []
''',
    ),
    _t(
        "hfix_env_expand",
        "The package `envx` has a bug: expand(text, env) in envx.py must replace $NAME and ${NAME} "
        "with the value from env, replace unknown names with an empty string, and treat $$ as a "
        "literal dollar sign. Fix it.",
        "envx.py",
        '''"""Environment expansion."""

import re

_RE = re.compile(r"\\$(\\w+)")


def expand(text, env):
    """$NAME and ${NAME}; unknown -> ''; $$ is a literal '$'."""
    return _RE.sub(lambda m: env.get(m.group(1), ""), text)
''',
        "test_envx.py",
        '''from envx import expand

def test_braced_form():
    assert expand("${A}b", {"A": "x"}) == "xb"

def test_double_dollar_is_literal():
    assert expand("$$A", {"A": "x"}) == "$A"
    assert expand("cost: $$", {}) == "cost: $"

def test_plain_and_unknown():
    assert expand("$A/$B", {"A": "1"}) == "1/"
''',
    ),
    _t(
        "hfix_url_join",
        "The package `urls` has a bug: join(base, ref) in urls.py must resolve a relative reference "
        "against a base URL path: a ref starting with '/' replaces the whole path, otherwise it is "
        "resolved against the base's DIRECTORY (everything up to the last '/'). Fix it.",
        "urls.py",
        '''"""URL joining."""


def join(base, ref):
    """Resolve `ref` against `base`. '/x' replaces the path; 'x' is relative to base's directory."""
    if ref.startswith("/"):
        return ref
    return base + "/" + ref
''',
        "test_urls.py",
        '''from urls import join

def test_relative_resolves_against_the_directory():
    assert join("/a/b/page.html", "other.html") == "/a/b/other.html"

def test_absolute_replaces_path():
    assert join("/a/b/page.html", "/root") == "/root"

def test_base_ending_in_slash():
    assert join("/a/b/", "c") == "/a/b/c"
''',
    ),
    _t(
        "hfix_moving_window",
        "The package `window` has a bug: windows(items, size) in window.py must yield every "
        "consecutive sub-list of exactly `size` items, in order. Fewer items than size yields nothing. "
        "Fix it.",
        "window.py",
        '''"""Sliding windows."""


def windows(items, size):
    """Every consecutive sub-list of exactly `size` items."""
    if size <= 0:
        raise ValueError("size must be positive")
    return [items[i:i + size] for i in range(len(items))]
''',
        "test_window.py",
        '''import pytest
from window import windows

def test_no_short_tail_windows():
    assert windows([1, 2, 3, 4], 2) == [[1, 2], [2, 3], [3, 4]]

def test_shorter_than_size_yields_nothing():
    assert windows([1, 2], 3) == []
    assert windows([], 1) == []

def test_size_equals_length():
    assert windows([1, 2], 2) == [[1, 2]]

def test_invalid_size():
    with pytest.raises(ValueError):
        windows([1], 0)
''',
    ),
    _t(
        "hfix_partition_by",
        "The package `parts` has a bug: partition(items, pred) in parts.py must return (matching, "
        "rest) preserving the original order within each group, and must call `pred` exactly once "
        "per item. Fix it.",
        "parts.py",
        '''"""Partitioning."""


def partition(items, pred):
    """(matching, rest), order preserved, pred called once per item."""
    return [i for i in items if pred(i)], [i for i in items if not pred(i)]
''',
        "test_parts.py",
        '''from parts import partition

def test_pred_called_once_per_item():
    seen = []
    def pred(x):
        seen.append(x)
        return x % 2 == 0
    evens, odds = partition([1, 2, 3, 4], pred)
    assert evens == [2, 4] and odds == [1, 3]
    assert seen == [1, 2, 3, 4]      # exactly once each, in order

def test_empty():
    assert partition([], lambda x: True) == ([], [])
''',
    ),
    _t(
        "hfix_dedupe_keep_last",
        "The package `dedupe` has a bug: unique(items, key) in dedupe.py must drop duplicates keeping "
        "the LAST occurrence of each key, while preserving the order those last occurrences appear in. "
        "Fix it.",
        "dedupe.py",
        '''"""Deduplication."""


def unique(items, key=lambda x: x):
    """Keep the LAST occurrence of each key, in the order those last occurrences appear."""
    seen = set()
    out = []
    for item in items:
        k = key(item)
        if k not in seen:
            seen.add(k)
            out.append(item)
    return out
''',
        "test_dedupe.py",
        '''from dedupe import unique

def test_keeps_last_occurrence_in_last_position():
    assert unique([1, 2, 1, 3]) == [2, 1, 3]

def test_with_key():
    rows = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}, {"id": 1, "v": "c"}]
    assert unique(rows, key=lambda r: r["id"]) == [{"id": 2, "v": "b"}, {"id": 1, "v": "c"}]

def test_empty_and_all_unique():
    assert unique([]) == []
    assert unique([3, 1, 2]) == [3, 1, 2]
''',
    ),
    _t(
        "hfix_clamp_range",
        "The package `clampit` has a bug: clamp(value, low, high) in clampit.py must clamp value into "
        "[low, high], raise ValueError if low > high, and preserve the type of the returned bound "
        "(clamping an int against float bounds returns the float bound). Fix it.",
        "clampit.py",
        '''"""Clamping."""


def clamp(value, low, high):
    """Clamp into [low, high]. Raises ValueError if low > high."""
    return max(low, min(value, high))
''',
        "test_clampit.py",
        '''import pytest
from clampit import clamp

def test_rejects_inverted_bounds():
    with pytest.raises(ValueError):
        clamp(5, 10, 1)

def test_clamps_both_ends():
    assert clamp(5, 1, 3) == 3
    assert clamp(0, 1, 3) == 1
    assert clamp(2, 1, 3) == 2

def test_returns_the_bound_object():
    got = clamp(5, 1, 3.5)
    assert got == 3.5 and isinstance(got, float)
''',
    ),
    _t(
        "hfix_count_leaves",
        "The package `tree2` has a bug: count_leaves(node) in tree2.py must count nodes with no "
        "children in a nested dict tree of the form {'children': [...]}, where a node without a "
        "'children' key is a leaf and an EMPTY children list also makes it a leaf. Fix it.",
        "tree2.py",
        '''"""Tree counting."""


def count_leaves(node):
    """Count nodes with no children. Missing key OR empty list = leaf."""
    children = node.get("children")
    if children is None:
        return 1
    return sum(count_leaves(child) for child in children)
''',
        "test_tree2.py",
        '''from tree2 import count_leaves

def test_empty_children_list_is_a_leaf():
    assert count_leaves({"children": []}) == 1

def test_nested():
    tree = {"children": [{"children": []}, {"x": 1}, {"children": [{"y": 2}]}]}
    assert count_leaves(tree) == 3

def test_single_leaf():
    assert count_leaves({"name": "solo"}) == 1
''',
    ),
    _t(
        "hfix_safe_divide",
        "The package `mathx` has a bug: ratios(pairs) in mathx.py must return a/b for each (a, b), "
        "using None where b is zero, and must not lose the position of skipped entries. Fix it.",
        "mathx.py",
        '''"""Ratios."""


def ratios(pairs):
    """a/b per pair; None where b == 0. Output length always equals input length."""
    out = []
    for a, b in pairs:
        if b == 0:
            continue
        out.append(a / b)
    return out
''',
        "test_mathx.py",
        '''from mathx import ratios

def test_zero_denominator_keeps_its_slot():
    assert ratios([(1, 2), (1, 0), (3, 3)]) == [0.5, None, 1.0]

def test_all_valid():
    assert ratios([(4, 2)]) == [2.0]

def test_empty():
    assert ratios([]) == []
''',
    ),
    _t(
        "hfix_strip_comments",
        "The package `stripc` has a bug: strip(line) in stripc.py must remove a trailing '#' comment "
        "from a config line, but a '#' inside a quoted string is NOT a comment. Trailing whitespace "
        "is removed. Fix it.",
        "stripc.py",
        '''"""Comment stripping."""


def strip(line):
    """Remove a trailing # comment. A # inside quotes is literal."""
    return line.split("#")[0].rstrip()
''',
        "test_stripc.py",
        '''from stripc import strip

def test_hash_inside_quotes_is_kept():
    assert strip('name = "a#b"') == 'name = "a#b"'
    assert strip("name = 'x#y'  # real comment") == "name = 'x#y'"

def test_plain_comment():
    assert strip("a = 1  # note") == "a = 1"
    assert strip("# whole line") == ""

def test_no_comment():
    assert strip("a = 1") == "a = 1"
''',
    ),
    _t(
        "hfix_merge_sorted",
        "The package `msort` has a bug: merge(a, b) in msort.py must merge two already-sorted lists "
        "into one sorted list, keeping duplicates, and taking from `a` first when values tie. Fix it.",
        "msort.py",
        '''"""Merging sorted lists."""


def merge(a, b):
    """Merge two sorted lists; duplicates kept; ties take from `a` first."""
    out = []
    i = j = 0
    while i < len(a) and j < len(b):
        if a[i] < b[j]:
            out.append(a[i]); i += 1
        else:
            out.append(b[j]); j += 1
    out.extend(a[i:])
    return out
''',
        "test_msort.py",
        '''from msort import merge

def test_tail_of_b_is_not_dropped():
    assert merge([1], [2, 3]) == [1, 2, 3]

def test_ties_take_from_a_first():
    a = [(1, "a")]
    b = [(1, "b")]
    assert merge(a, b) == [(1, "a"), (1, "b")]

def test_duplicates_kept():
    assert merge([1, 1], [1]) == [1, 1, 1]

def test_empties():
    assert merge([], [1]) == [1]
    assert merge([1], []) == [1]
''',
    ),
    _t(
        "hfix_indent_block",
        "The package `indent` has a bug: reindent(text, spaces) in indent.py must set the indentation "
        "of every non-empty line to exactly `spaces`, preserving relative nesting, and leaving blank "
        "lines empty (not filled with spaces). Fix it.",
        "indent.py",
        '''"""Re-indentation."""


def reindent(text, spaces):
    """Set base indent to `spaces`, keep relative nesting, blank lines stay empty."""
    lines = text.split("\\n")
    return "\\n".join(" " * spaces + line.lstrip() for line in lines)
''',
        "test_indent.py",
        '''from indent import reindent

def test_blank_lines_stay_empty():
    assert reindent("a\\n\\nb", 2) == "  a\\n\\n  b"

def test_relative_nesting_preserved():
    assert reindent("a\\n  b", 4) == "    a\\n      b"

def test_single_line():
    assert reindent("x", 0) == "x"
''',
    ),
    _t(
        "hfix_percent_change",
        "The package `pct` has a bug: change(old, new) in pct.py must return the percentage change "
        "from old to new, return None when old is zero (undefined), and handle negative values "
        "correctly. Fix it.",
        "pct.py",
        '''"""Percentage change."""


def change(old, new):
    """(new - old) / |old| * 100. None when old == 0."""
    if not old:
        return None
    return (new - old) / old * 100
''',
        "test_pct.py",
        '''from pct import change

def test_negative_base_uses_magnitude():
    # from -50 to -25 is an improvement of +50%, not -50%
    assert change(-50, -25) == 50.0

def test_positive_base():
    assert change(50, 75) == 50.0
    assert change(100, 50) == -50.0

def test_zero_base_is_undefined():
    assert change(0, 5) is None
''',
    ),
    _t(
        "hfix_title_words",
        "The package `titler` has a bug: title(text) in titler.py must capitalise the first letter of "
        "each word, leave the rest of each word untouched (so 'iPhone' stays 'iPhone'), and collapse "
        "runs of whitespace to a single space. Fix it.",
        "titler.py",
        '''"""Title casing."""


def title(text):
    """Capitalise first letter of each word; keep the rest as typed."""
    return " ".join(word.capitalize() for word in text.split())
''',
        "test_titler.py",
        '''from titler import title

def test_interior_capitals_are_preserved():
    # only the FIRST letter changes; the rest of the word is untouched
    assert title("the iPhone era") == "The IPhone Era"
    assert title("mcDonald wins") == "McDonald Wins"

def test_whitespace_collapsed():
    assert title("a   b") == "A B"
    assert title("") == ""
''',
    ),
    _t(
        "hfix_rate_limit",
        "The package `limiter` has a bug: allow(timestamps, now, limit, window) in limiter.py must "
        "return True when fewer than `limit` timestamps fall within the half-open window (now-window, "
        "now]. Fix it.",
        "limiter.py",
        '''"""Sliding-window rate limiting."""


def allow(timestamps, now, limit, window):
    """True if fewer than `limit` timestamps are inside (now-window, now]."""
    recent = [t for t in timestamps if t >= now - window]
    return len(recent) < limit
''',
        "test_limiter.py",
        '''from limiter import allow

def test_window_is_half_open_at_the_start():
    # a timestamp exactly `window` old has EXPIRED and must not count
    assert allow([0], now=10, limit=1, window=10) is True

def test_inside_window_counts():
    assert allow([1], now=10, limit=1, window=10) is False

def test_under_and_over_limit():
    assert allow([9, 9], now=10, limit=3, window=5) is True
    assert allow([9, 9, 9], now=10, limit=3, window=5) is False

def test_no_history():
    assert allow([], now=10, limit=1, window=5) is True
''',
    ),
    _t(
        "hfix_bool_env",
        "The package `boolenv` has a bug: as_bool(value) in boolenv.py must read common truthy/falsy "
        "strings case-insensitively ('1','true','yes','on' / '0','false','no','off'), accept real "
        "bools unchanged, and raise ValueError on anything else. Fix it.",
        "boolenv.py",
        '''"""Boolean coercion."""

TRUE = {"1", "true", "yes", "on"}


def as_bool(value):
    """Coerce common string forms; raise ValueError on anything unrecognised."""
    if isinstance(value, bool):
        return value
    return str(value).lower() in TRUE
''',
        "test_boolenv.py",
        '''import pytest
from boolenv import as_bool

def test_unrecognised_raises_instead_of_returning_false():
    for bad in ["maybe", "", "2", None]:
        with pytest.raises(ValueError):
            as_bool(bad)

def test_truthy_and_falsy():
    for v in ["1", "TRUE", "Yes", "on"]:
        assert as_bool(v) is True
    for v in ["0", "False", "NO", "off"]:
        assert as_bool(v) is False

def test_real_bools_pass_through():
    assert as_bool(True) is True
    assert as_bool(False) is False
''',
    ),
    _t(
        "hfix_columnize",
        "The package `cols` has a bug: columnize(rows) in cols.py must render rows of strings as a "
        "space-padded table where every column is as wide as its widest cell, with NO trailing spaces "
        "at the end of a line. Fix it.",
        "cols.py",
        '''"""Column alignment."""


def columnize(rows):
    """Pad columns to their widest cell; no trailing whitespace on any line."""
    if not rows:
        return []
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return [" ".join(cell.ljust(w) for cell, w in zip(row, widths)) for row in rows]
''',
        "test_cols.py",
        '''from cols import columnize

def test_no_trailing_spaces():
    got = columnize([["a", "long"], ["bb", "x"]])
    assert got == ["a  long", "bb x"]
    for line in got:
        assert line == line.rstrip()

def test_empty():
    assert columnize([]) == []
''',
    ),
    _t(
        "hfix_slug_unique",
        "The package `uslug` has a bug: unique_slugs(names) in uslug.py must lowercase each name and "
        "replace runs of non-alphanumerics with '-', then disambiguate collisions by appending '-2', "
        "'-3'… in input order, never reusing a slug. Fix it.",
        "uslug.py",
        '''"""Unique slugs."""

import re


def unique_slugs(names):
    """Slugify, then disambiguate collisions with -2, -3, ... in input order."""
    out = []
    for name in names:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        out.append(slug)
    return out
''',
        "test_uslug.py",
        '''from uslug import unique_slugs

def test_collisions_get_suffixes():
    assert unique_slugs(["My Post", "my post", "My  Post"]) == ["my-post", "my-post-2", "my-post-3"]

def test_no_collision():
    assert unique_slugs(["A", "B"]) == ["a", "b"]

def test_suffix_does_not_collide_with_a_real_name():
    assert unique_slugs(["Post", "Post", "Post 2"]) == ["post", "post-2", "post-2-2"]
''',
    ),
    _t(
        "hfix_nested_get",
        "The package `dig` has a bug: get(data, path, default=None) in dig.py must walk a dotted path "
        "through nested dicts AND lists (numeric segments index a list), returning `default` if any "
        "step is missing. A stored None is a real value, not a miss. Fix it.",
        "dig.py",
        '''"""Nested lookup."""


def get(data, path, default=None):
    """Walk a dotted path through dicts and lists. Missing -> default."""
    node = data
    for seg in path.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(seg)
        if node is None:
            return default
    return node
''',
        "test_dig.py",
        '''from dig import get

def test_list_indexing():
    assert get({"a": [{"b": 1}]}, "a.0.b") == 1

def test_stored_none_is_returned_not_defaulted():
    assert get({"a": None}, "a", default="MISS") is None

def test_missing_returns_default():
    assert get({"a": 1}, "a.b", default="MISS") == "MISS"
    assert get({}, "x", default=0) == 0
''',
    ),
    _t(
        "hfix_truncate_words",
        "The package `trunc` has a bug: truncate(text, limit) in trunc.py must shorten text to at most "
        "`limit` characters INCLUDING the ellipsis '…', cutting at a word boundary when possible, and "
        "returning the text unchanged when it already fits. Fix it.",
        "trunc.py",
        '''"""Truncation."""


def truncate(text, limit):
    """At most `limit` chars including the ellipsis; cut on a word boundary."""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"
''',
        "test_trunc.py",
        '''from trunc import truncate

def test_ellipsis_counts_towards_the_limit():
    got = truncate("hello world", 8)
    assert len(got) <= 8
    assert got.endswith("…")

def test_cuts_on_a_word_boundary():
    assert truncate("hello world", 8) == "hello…"

def test_fits_unchanged():
    assert truncate("short", 10) == "short"
''',
    ),
    _t(
        "hfix_sum_by",
        "The package `agg` has a bug: sum_by(rows, key, value) in agg.py must total the value field "
        "per key, returning a dict, and must include keys whose total is zero rather than dropping "
        "them. Fix it.",
        "agg.py",
        '''"""Aggregation."""


def sum_by(rows, key, value):
    """Total `value` per `key`. Keys totalling zero are still present."""
    out = {}
    for row in rows:
        total = out.get(row[key], 0) + row[value]
        if total:
            out[row[key]] = total
    return out
''',
        "test_agg.py",
        '''from agg import sum_by

def test_zero_totals_are_kept():
    rows = [{"k": "a", "v": 1}, {"k": "a", "v": -1}, {"k": "b", "v": 2}]
    assert sum_by(rows, "k", "v") == {"a": 0, "b": 2}

def test_simple():
    assert sum_by([{"k": "x", "v": 2}], "k", "v") == {"x": 2}
    assert sum_by([], "k", "v") == {}
''',
    ),
    _t(
        "hfix_next_weekday",
        "The package `cal` has a bug: next_weekday(day, target) in cal.py must return how many days "
        "forward from `day` (0=Mon..6=Sun) the next `target` weekday is. When day == target the answer "
        "is 7, not 0 — 'next' means strictly in the future. Fix it.",
        "cal.py",
        '''"""Weekday arithmetic."""


def next_weekday(day, target):
    """Days forward to the NEXT `target` weekday. Same day -> 7."""
    return (target - day) % 7
''',
        "test_cal.py",
        '''import pytest
from cal import next_weekday

def test_same_day_is_a_full_week():
    assert next_weekday(2, 2) == 7

def test_forward_and_wrapping():
    assert next_weekday(0, 3) == 3
    assert next_weekday(5, 1) == 3

def test_rejects_out_of_range():
    with pytest.raises(ValueError):
        next_weekday(0, 9)
''',
    ),
]

__all__ = ["HARD_FIX_TASKS"]
