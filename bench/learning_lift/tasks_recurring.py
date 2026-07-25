"""Recurring-family suite — the transfer-POSSIBLE learning-lift instrument.

Runs 1–5 measured a null and diagnosed the cause: the `hfix_*` suite is surface-DISJOINT (every task a
different function), so there is nothing to transfer between tasks — no matter how well retrieval finds
neighbours (run 5 proved semantic recall, which fixes retrieval quality, still nulled). This suite
removes that ceiling **by construction**: tasks come in FAMILIES that share one nameable, transferable
fix, so solving an earlier member genuinely teaches the pattern the later members need.

## The transfer hypothesis (fixed here, before any model call)

Five families, five members each (25 tasks), ordered family-by-family so the learning arm meets a
family's members consecutively. Each family shares ONE fix pattern a distilled card can capture:

- **guard_** — the input can be empty / have no match; guard it at the top and return the given default
  before the main logic runs (which assumes a non-empty result).
- **copy_**  — never mutate the caller's input; build and return a NEW object.
- **incl_**  — a range endpoint is INCLUSIVE; use `b+1` in `range()` / `<=` in comparisons.
- **case_**  — compare / group case-INSENSITIVELY; normalise (casefold) BOTH sides.
- **reset_** — a per-group accumulator must be RESET at each new group, inside the loop.

**Why this can show transfer where `hfix_*` could not:** the learning arm, after solving `guard`
member 1, should mint a card capturing "guard the empty case and return the default"; on `guard`
members 2–5 it retrieves that card and applies the same move. The cold arm (fresh home per task) never
has it. So — IF accumulated learning helps at all — the learning arm should beat cold specifically on
the **non-first members** of each family. The disjoint suite structurally denied this; here it is the
whole point.

## Discipline (same as runs 1–5)

Every task states the CONTRACT, never the failing symptom, so the model must diagnose — but within a
family the diagnosis recurs. Each task is one buggy module + one pytest file, validated mechanically
(the committed test MUST fail against the committed buggy source) with NO model involved and NO tuning
against any pass rate. The families/patterns were fixed before authoring; members were not adjusted to
hit any target.
"""

from __future__ import annotations


def _t(task_id: str, prompt: str, mod: str, src: str, test: str, test_src: str) -> dict:
    return {
        "id": task_id,
        "prompt": prompt,
        "files": {mod: src},
        "verify": None,  # the runner supplies the pytest gate
        "test": test,
        "test_src": test_src,
    }


RECURRING_TASKS: list[dict] = [
    # ============================ family guard_ : empty/no-match -> default ============================
    _t(
        "guard_mean",
        "The package `stats` has a bug: mean(nums) in stats.py must return the arithmetic mean of nums, "
        "or 0.0 when nums is empty. Fix it.",
        "stats.py",
        '''"""Averages."""


def mean(nums):
    """Arithmetic mean of nums; 0.0 for an empty list."""
    return sum(nums) / len(nums)
''',
        "test_stats.py",
        '''from stats import mean

def test_mean_of_values():
    assert mean([2, 4, 6]) == 4.0

def test_empty_is_zero_not_crash():
    assert mean([]) == 0.0
''',
    ),
    _t(
        "guard_first_word",
        "The package `text1` has a bug: first_word(s) in text1.py must return the first whitespace-"
        "separated word of s, or '' when s has no words. Fix it.",
        "text1.py",
        '''"""Words."""


def first_word(s):
    """First whitespace-separated word, or '' if there is none."""
    return s.split()[0]
''',
        "test_text1.py",
        '''from text1 import first_word

def test_first_word():
    assert first_word("hello there world") == "hello"

def test_blank_returns_empty():
    assert first_word("   ") == ""
    assert first_word("") == ""
''',
    ),
    _t(
        "guard_longest",
        "The package `strs` has a bug: longest(strings) in strs.py must return the longest string (first "
        "one on a tie), or '' when the list is empty. Fix it.",
        "strs.py",
        '''"""Longest string."""


def longest(strings):
    """The longest string (first on a tie), or '' when empty."""
    return max(strings, key=len)
''',
        "test_strs.py",
        '''from strs import longest

def test_longest():
    assert longest(["a", "ccc", "bb"]) == "ccc"

def test_empty_returns_empty():
    assert longest([]) == ""
''',
    ),
    _t(
        "guard_last",
        "The package `seqs` has a bug: last(lst, default=None) in seqs.py must return the final element "
        "of lst, or default when lst is empty. Fix it.",
        "seqs.py",
        '''"""Last element."""


def last(lst, default=None):
    """Final element of lst, or default if empty."""
    return lst[-1]
''',
        "test_seqs.py",
        '''from seqs import last

def test_last():
    assert last([1, 2, 3]) == 3

def test_empty_returns_default():
    assert last([], default=0) == 0
    assert last([]) is None
''',
    ),
    _t(
        "guard_first_positive",
        "The package `nums1` has a bug: first_positive(nums, default=-1) in nums1.py must return the "
        "first n > 0, or default when there is none. Fix it.",
        "nums1.py",
        '''"""First positive."""


def first_positive(nums, default=-1):
    """First n > 0, or default when there is none."""
    return [n for n in nums if n > 0][0]
''',
        "test_nums1.py",
        '''from nums1 import first_positive

def test_first_positive():
    assert first_positive([-2, 0, 5, 7]) == 5

def test_none_positive_returns_default():
    assert first_positive([-1, -3], default=0) == 0
    assert first_positive([]) == -1
''',
    ),
    # ============================== family copy_ : never mutate the input ==============================
    _t(
        "copy_sorted",
        "The package `order` has a bug: ascending(lst) in order.py must return a new sorted list and "
        "must NOT modify the caller's lst. Fix it.",
        "order.py",
        '''"""Sorting."""


def ascending(lst):
    """A new ascending-sorted list; the input is left unchanged."""
    lst.sort()
    return lst
''',
        "test_order.py",
        '''from order import ascending

def test_sorts():
    assert ascending([3, 1, 2]) == [1, 2, 3]

def test_does_not_mutate_input():
    data = [3, 1, 2]
    ascending(data)
    assert data == [3, 1, 2]
''',
    ),
    _t(
        "copy_reversed",
        "The package `rev` has a bug: reverse(lst) in rev.py must return a new reversed list without "
        "modifying the caller's lst. Fix it.",
        "rev.py",
        '''"""Reversing."""


def reverse(lst):
    """A new reversed list; the input is left unchanged."""
    lst.reverse()
    return lst
''',
        "test_rev.py",
        '''from rev import reverse

def test_reverses():
    assert reverse([1, 2, 3]) == [3, 2, 1]

def test_does_not_mutate_input():
    data = [1, 2, 3]
    reverse(data)
    assert data == [1, 2, 3]
''',
    ),
    _t(
        "copy_dedup",
        "The package `uniq` has a bug: dedup(lst) in uniq.py must return a new list with duplicates "
        "removed (keeping first occurrence, order preserved) and must NOT modify the caller's lst. "
        "Fix it.",
        "uniq.py",
        '''"""Dedup."""


def dedup(lst):
    """A new list, duplicates removed keeping first occurrence; input unchanged."""
    seen = set()
    i = 0
    while i < len(lst):
        if lst[i] in seen:
            lst.pop(i)
        else:
            seen.add(lst[i])
            i += 1
    return lst
''',
        "test_uniq.py",
        '''from uniq import dedup

def test_dedup():
    assert dedup([1, 1, 2, 3, 2]) == [1, 2, 3]

def test_does_not_mutate_input():
    data = [1, 1, 2, 3, 2]
    dedup(data)
    assert data == [1, 1, 2, 3, 2]
''',
    ),
    _t(
        "copy_merge",
        "The package `dm` has a bug: merge(a, b) in dm.py must return a new dict with b's items layered "
        "over a's, without modifying either input dict. Fix it.",
        "dm.py",
        '''"""Dict merge."""


def merge(a, b):
    """A new dict = a updated with b; neither input is modified."""
    a.update(b)
    return a
''',
        "test_dm.py",
        '''from dm import merge

def test_merges():
    assert merge({"x": 1}, {"y": 2, "x": 9}) == {"x": 9, "y": 2}

def test_does_not_mutate_inputs():
    a = {"x": 1}
    merge(a, {"y": 2})
    assert a == {"x": 1}
''',
    ),
    _t(
        "copy_scale",
        "The package `vec` has a bug: scale(lst, k) in vec.py must return a new list with each element "
        "multiplied by k, without modifying the caller's lst. Fix it.",
        "vec.py",
        '''"""Scaling."""


def scale(lst, k):
    """A new list with each element * k; the input is left unchanged."""
    for i in range(len(lst)):
        lst[i] *= k
    return lst
''',
        "test_vec.py",
        '''from vec import scale

def test_scales():
    assert scale([1, 2, 3], 2) == [2, 4, 6]

def test_does_not_mutate_input():
    data = [1, 2, 3]
    scale(data, 2)
    assert data == [1, 2, 3]
''',
    ),
    # ============================ family incl_ : the range end is inclusive ============================
    _t(
        "incl_range_sum",
        "The package `rs` has a bug: range_sum(a, b) in rs.py must return the sum of all integers from a "
        "to b, INCLUDING both a and b. Fix it.",
        "rs.py",
        '''"""Range sum."""


def range_sum(a, b):
    """Sum of every integer from a to b, inclusive of both ends."""
    return sum(range(a, b))
''',
        "test_rs.py",
        '''from rs import range_sum

def test_inclusive_sum():
    assert range_sum(1, 3) == 6      # 1 + 2 + 3
    assert range_sum(5, 5) == 5      # a single-element inclusive range
''',
    ),
    _t(
        "incl_countdown",
        "The package `cd` has a bug: countdown(n) in cd.py must return the list [n, n-1, ..., 1], "
        "including 1. Fix it.",
        "cd.py",
        '''"""Countdown."""


def countdown(n):
    """[n, n-1, ..., 1] — down to and INCLUDING 1."""
    return list(range(n, 1, -1))
''',
        "test_cd.py",
        '''from cd import countdown

def test_includes_one():
    assert countdown(4) == [4, 3, 2, 1]
    assert countdown(1) == [1]
''',
    ),
    _t(
        "incl_count_between",
        "The package `cb` has a bug: count_between(nums, lo, hi) in cb.py must count how many values in "
        "nums fall in the range lo to hi, INCLUSIVE of both bounds. Fix it.",
        "cb.py",
        '''"""Count in range."""


def count_between(nums, lo, hi):
    """How many of nums are >= lo and <= hi (both bounds inclusive)."""
    return sum(1 for n in nums if lo <= n < hi)
''',
        "test_cb.py",
        '''from cb import count_between

def test_inclusive_bounds():
    assert count_between([1, 2, 3, 4, 5], 2, 4) == 3   # 2, 3, 4
    assert count_between([5, 5, 6], 5, 5) == 2         # both 5s count
''',
    ),
    _t(
        "incl_char_span",
        "The package `cs` has a bug: span(s, i, j) in cs.py must return the substring of s from index i "
        "to index j, INCLUDING the character at j. Fix it.",
        "cs.py",
        '''"""Character span."""


def span(s, i, j):
    """s from index i to index j, inclusive of the char at j."""
    return s[i:j]
''',
        "test_cs.py",
        '''from cs import span

def test_inclusive_end():
    assert span("abcdef", 1, 3) == "bcd"   # indices 1,2,3
    assert span("abcdef", 0, 0) == "a"
''',
    ),
    _t(
        "incl_pages",
        "The package `pg` has a bug: pages(n) in pg.py must return the page numbers [1, 2, ..., n], "
        "including page n. Fix it.",
        "pg.py",
        '''"""Page numbers."""


def pages(n):
    """[1, 2, ..., n] — including the last page n."""
    return list(range(1, n))
''',
        "test_pg.py",
        '''from pg import pages

def test_includes_last_page():
    assert pages(3) == [1, 2, 3]
    assert pages(1) == [1]
''',
    ),
    # ===================== family case_ : compare / group case-insensitively ==========================
    _t(
        "case_contains",
        "The package `mem` has a bug: contains(words, target) in mem.py must return True if target "
        "appears in words ignoring case, else False. Fix it.",
        "mem.py",
        '''"""Case-insensitive membership."""


def contains(words, target):
    """True if target is in words, comparing case-insensitively."""
    return target in words
''',
        "test_mem.py",
        '''from mem import contains

def test_case_insensitive():
    assert contains(["Apple", "Pear"], "apple") is True
    assert contains(["Apple"], "APPLE") is True

def test_absent():
    assert contains(["Apple"], "peach") is False
''',
    ),
    _t(
        "case_dedup",
        "The package `cu` has a bug: dedup_ci(words) in cu.py must remove case-insensitive duplicates, "
        "keeping the FIRST spelling of each, order preserved. Fix it.",
        "cu.py",
        '''"""Case-insensitive dedup."""


def dedup_ci(words):
    """Drop case-insensitive duplicates, keep the first spelling, preserve order."""
    seen = set()
    out = []
    for w in words:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out
''',
        "test_cu.py",
        '''from cu import dedup_ci

def test_case_insensitive_dedup():
    assert dedup_ci(["Apple", "apple", "Pear", "PEAR", "fig"]) == ["Apple", "Pear", "fig"]
''',
    ),
    _t(
        "case_count",
        "The package `wc1` has a bug: count(words, target) in wc1.py must count how many times target "
        "occurs in words, ignoring case. Fix it.",
        "wc1.py",
        '''"""Case-insensitive count."""


def count(words, target):
    """How many of words equal target, compared case-insensitively."""
    return sum(1 for w in words if w == target)
''',
        "test_wc1.py",
        '''from wc1 import count

def test_case_insensitive_count():
    assert count(["Hi", "hi", "HI", "bye"], "hi") == 3
    assert count([], "hi") == 0
''',
    ),
    _t(
        "case_group",
        "The package `grpci` has a bug: group_ci(words) in grpci.py must group words by their lowercased "
        "form, mapping each lowercased key to the list of original spellings in input order. Fix it.",
        "grpci.py",
        '''"""Case-insensitive grouping."""


def group_ci(words):
    """{lowercased key: [original spellings in input order]}."""
    out = {}
    for w in words:
        out.setdefault(w, []).append(w)
    return out
''',
        "test_grpci.py",
        '''from grpci import group_ci

def test_groups_by_lowercased_key():
    assert group_ci(["Apple", "apple", "Pear"]) == {"apple": ["Apple", "apple"], "pear": ["Pear"]}
''',
    ),
    _t(
        "case_index",
        "The package `idx` has a bug: index_ci(words, target) in idx.py must return the index of the "
        "first case-insensitive match of target, or -1 if none. Fix it.",
        "idx.py",
        '''"""Case-insensitive index."""


def index_ci(words, target):
    """Index of the first case-insensitive match of target, or -1."""
    for i, w in enumerate(words):
        if w == target:
            return i
    return -1
''',
        "test_idx.py",
        '''from idx import index_ci

def test_case_insensitive_index():
    assert index_ci(["a", "Bee", "c"], "bee") == 1
    assert index_ci(["a", "b"], "Z") == -1
''',
    ),
    # ==================== family reset_ : reset the per-group accumulator in the loop ==================
    _t(
        "reset_run_lengths",
        "The package `rle` has a bug: run_lengths(items) in rle.py must return, for each run of equal "
        "consecutive items, a (item, length) pair — the count RESTARTING at each new run. Fix it.",
        "rle.py",
        '''"""Run-length encoding."""


def run_lengths(items):
    """(item, run-length) per maximal run of equal consecutive items."""
    out = []
    count = 0
    for i, x in enumerate(items):
        count += 1
        if i + 1 == len(items) or items[i + 1] != x:
            out.append((x, count))
    return out
''',
        "test_rle.py",
        '''from rle import run_lengths

def test_counts_reset_per_run():
    assert run_lengths(["a", "a", "b", "a", "a", "a"]) == [("a", 2), ("b", 1), ("a", 3)]

def test_empty():
    assert run_lengths([]) == []
''',
    ),
    _t(
        "reset_group_sums",
        "The package `gs` has a bug: group_sums(nums) in gs.py must return the sum of each maximal run of "
        "equal consecutive numbers — the running sum RESETTING at each new run. Fix it.",
        "gs.py",
        '''"""Consecutive-group sums."""


def group_sums(nums):
    """Sum of each maximal run of equal consecutive values, reset per run."""
    out = []
    total = 0
    for i, n in enumerate(nums):
        total += n
        if i + 1 == len(nums) or nums[i + 1] != n:
            out.append(total)
    return out
''',
        "test_gs.py",
        '''from gs import group_sums

def test_sums_reset_per_group():
    assert group_sums([2, 2, 3, 2, 2, 2]) == [4, 3, 6]

def test_single():
    assert group_sums([5]) == [5]
''',
    ),
    _t(
        "reset_chunks",
        "The package `ch` has a bug: chunks(items, n) in ch.py must split items into consecutive lists of "
        "at most n, with the buffer RESET after each emitted chunk. Fix it.",
        "ch.py",
        '''"""Chunking."""


def chunks(items, n):
    """Consecutive sub-lists of at most n items each."""
    out = []
    buf = []
    for x in items:
        buf.append(x)
        if len(buf) == n:
            out.append(buf)
    return out
''',
        "test_ch.py",
        '''from ch import chunks

def test_buffer_resets_after_each_chunk():
    assert chunks([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]

def test_remainder():
    assert chunks([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
''',
    ),
    _t(
        "reset_word_counts",
        "The package `sw` has a bug: words_per_sentence(sentences) in sw.py must return the word count of "
        "each sentence — the counter RESETTING for each sentence. Fix it.",
        "sw.py",
        '''"""Words per sentence."""


def words_per_sentence(sentences):
    """Word count of each sentence, counter reset per sentence."""
    out = []
    total = 0
    for s in sentences:
        total += len(s.split())
        out.append(total)
    return out
''',
        "test_sw.py",
        '''from sw import words_per_sentence

def test_counter_resets_per_sentence():
    assert words_per_sentence(["a b", "c d e", "f"]) == [2, 3, 1]

def test_empty_list():
    assert words_per_sentence([]) == []
''',
    ),
    _t(
        "reset_max_per_line",
        "The package `ml` has a bug: max_per_line(lines) in ml.py must return the maximum value of each "
        "line (a list of numbers) — the running max RESETTING for each line. Fix it.",
        "ml.py",
        '''"""Max per line."""


def max_per_line(lines):
    """The max of each line's numbers, reset per line."""
    out = []
    best = float("-inf")
    for line in lines:
        for n in line:
            if n > best:
                best = n
        out.append(best)
    return out
''',
        "test_ml.py",
        '''from ml import max_per_line

def test_max_resets_per_line():
    assert max_per_line([[3, 1], [2, 0], [9, 4]]) == [3, 2, 9]
''',
    ),
]

__all__ = ["RECURRING_TASKS"]
