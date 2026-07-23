"""Recurring-family suite, HARD edition — transfer-POSSIBLE *and* off the ceiling.

The easy recurring suite (`tasks_recurring.py`) proved the design point — families that share one
nameable, transferable fix so solving member 1 teaches members 2–5 — but it was too easy: the
no-learning baseline landed at 90.7%, which ceiling-caps the measurement (a learning arm cannot beat a
control that is already almost perfect). This suite keeps the family structure that makes transfer
POSSIBLE and imports the hard suite's four difficulty inversions so the baseline has somewhere to fall.

## The transfer hypothesis (fixed here, before any model call)

Five families, five members each (25 tasks), ordered family-by-family so the learning arm meets a
family's members consecutively. Each family shares ONE fix pattern a distilled card can capture — and,
crucially, the SAME hard trap RECURS within a family, so a card minted on member 1 genuinely applies to
members 2–5:

- **hguard_** — the emptiness that needs the contract's default arises AFTER a filter/selection step,
  not on the raw input; a naive guard on the raw input fixes the empty-input case and STILL crashes the
  filtered-to-empty case. Fix: guard the *result* and return the specified default.
- **hcopy_**  — the function copies the OUTER container but mutates a NESTED structure the caller shares;
  a shallow copy passes the "returns a new object" case and STILL leaks into the caller's nested data.
  Fix: copy the nested part that is actually touched.
- **hincl_**  — an endpoint is treated as exclusive but the contract is INCLUSIVE, and there is a SECOND
  bound (an inner loop, a lower bound, or a clamp) that is wrong the same way; fixing only the outer
  one breaks a second case. Fix: `+1` / `<=` on *every* bound, and clamp negatives.
- **hcase_**  — comparison/grouping normalises only ONE side (or none); fixing that one side is not
  enough because the OUTPUT must still preserve the original spelling. Fix: normalise BOTH sides for
  comparison while keeping the first/original spelling in the result.
- **hreset_** — an accumulator is flushed on a group boundary but never RESET, and the FINAL group is
  dropped; fixing only the reset still loses the trailing group (and vice versa). Fix: reset at each new
  group AND emit the trailing group, guarding the empty input.

**Why this can show transfer where the disjoint suite could not:** after solving `hguard` member 1 the
learning arm should mint a card capturing "guard the *filtered* result, not the raw input, and return
the default"; on members 2–5 it retrieves that card and applies the same move. The cold arm (fresh home
per task) never has it. So — IF accumulated learning helps at all — the learning arm should beat cold
specifically on the non-first members of each family.

## Discipline (same as the easy suite and the hard-fix suite)

Every task states the CONTRACT, never the failing symptom, so the model must diagnose — but within a
family the diagnosis recurs. Each task applies the four inversions: (1) the prompt gives the contract,
not a failing example; (2) the bug is not on the line the prompt names (a helper, a shallow copy, a
second bound, a one-sided compare); (3) the obvious patch fixes the visible case and BREAKS a second
case the test also checks; (4) one clause of the contract is QUIET — stated once in prose, enforced by
an assertion. Each task is one buggy module + one pytest file, validated mechanically (the committed
test MUST fail against the committed buggy source) with NO model involved.

Target: the no-learning baseline lands at **40–60%**. That band was fixed BEFORE authoring and is NOT
tuned against any measured pass rate — if the realised rate lands outside it, that is reported as-is.
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


RECURRING_HARD_TASKS: list[dict] = [
    # ============ family hguard_ : the default is needed AFTER a filter, not on raw input ============
    _t(
        "hguard_trimmed_mean",
        "The package `trimstats` has a bug: trimmed_mean(nums) in trimstats.py must return the mean of "
        "nums after dropping one smallest and one largest value, or 0.0 when nothing remains. Fix it.",
        "trimstats.py",
        '''"""Trimmed statistics."""


def _trim(nums):
    lo = min(nums)
    hi = max(nums)
    rest = list(nums)
    rest.remove(lo)
    rest.remove(hi)
    return rest


def trimmed_mean(nums):
    """Mean of nums with one smallest and one largest value removed; 0.0 when nothing is left."""
    rest = _trim(nums)
    return sum(rest) / len(rest)
''',
        "test_trimstats.py",
        '''from trimstats import trimmed_mean

def test_drops_one_min_and_one_max():
    assert trimmed_mean([1, 2, 3, 4, 10]) == 3.0   # drop 1 and 10 -> mean(2, 3, 4)

def test_two_values_leave_nothing():
    assert trimmed_mean([5, 3]) == 0.0             # both are removed -> default

def test_empty_is_zero():
    assert trimmed_mean([]) == 0.0
''',
    ),
    _t(
        "hguard_max_gap",
        "The package `gaps` has a bug: max_gap(nums) in gaps.py must return the largest difference "
        "between consecutive values of the sorted distinct entries of nums, or 0 when there are fewer "
        "than two distinct values. Fix it.",
        "gaps.py",
        '''"""Largest consecutive gap."""


def _sorted_unique(nums):
    return sorted(set(nums))


def max_gap(nums):
    """Biggest difference between neighbours in the sorted distinct values; 0 if fewer than two."""
    s = _sorted_unique(nums)
    return max(s[i + 1] - s[i] for i in range(len(s) - 1))
''',
        "test_gaps.py",
        '''from gaps import max_gap

def test_largest_consecutive_gap():
    assert max_gap([1, 3, 8, 9]) == 5     # 3 -> 8

def test_all_equal_has_no_gap():
    assert max_gap([4, 4, 4]) == 0        # one distinct value -> default

def test_empty_is_zero():
    assert max_gap([]) == 0
''',
    ),
    _t(
        "hguard_shortest_word",
        "The package `wordpick` has a bug: shortest_word(words) in wordpick.py must return the shortest "
        "word among those longer than one character (the first on a tie), or '' when none qualify. "
        "Fix it.",
        "wordpick.py",
        '''"""Shortest qualifying word."""


def _multichar(words):
    return [w for w in words if len(w) > 1]


def shortest_word(words):
    """Shortest word of length > 1 (first on a tie); '' when none qualify."""
    kept = _multichar(words)
    return min(kept, key=len)
''',
        "test_wordpick.py",
        '''from wordpick import shortest_word

def test_shortest_multichar():
    assert shortest_word(["hi", "world", "ab"]) == "hi"   # "hi" ties "ab" on length; first wins

def test_all_single_char_none_qualify():
    assert shortest_word(["a", "b", "c"]) == ""           # nothing has length > 1 -> default

def test_empty_is_blank():
    assert shortest_word([]) == ""
''',
    ),
    _t(
        "hguard_avg_positive",
        "The package `posavg` has a bug: avg_positive(nums) in posavg.py must return the average of the "
        "strictly-positive values of nums, or 0.0 when there are none. Fix it.",
        "posavg.py",
        '''"""Average of positive values."""


def _positives(nums):
    return [n for n in nums if n > 0]


def avg_positive(nums):
    """Mean of the strictly-positive values; 0.0 when there are none."""
    pos = _positives(nums)
    return sum(pos) / len(pos)
''',
        "test_posavg.py",
        '''from posavg import avg_positive

def test_average_of_positives():
    assert avg_positive([-1, 2, 4]) == 3.0

def test_no_positives_is_zero():
    assert avg_positive([-1, -2, -3]) == 0.0   # all filtered out -> default

def test_empty_is_zero():
    assert avg_positive([]) == 0.0
''',
    ),
    _t(
        "hguard_top_score",
        "The package `scoreboard` has a bug: top_score(scores) in scoreboard.py must return the highest "
        "score that is at least 50, or -1 when none reach 50. Fix it.",
        "scoreboard.py",
        '''"""Top passing score."""


def _passed(scores):
    return [s for s in scores if s >= 50]


def top_score(scores):
    """Highest score >= 50; -1 when nobody reaches 50."""
    passed = _passed(scores)
    return max(passed)
''',
        "test_scoreboard.py",
        '''from scoreboard import top_score

def test_highest_passing():
    assert top_score([30, 55, 80, 49]) == 80

def test_nobody_passed_is_minus_one():
    assert top_score([10, 20, 49]) == -1   # none reach 50 -> default

def test_empty_is_minus_one():
    assert top_score([]) == -1
''',
    ),
    # =============== family hcopy_ : copy the OUTER container, leak into a NESTED one ================
    _t(
        "hcopy_add_tag",
        "The package `tagging` has a bug: add_tag(records, i, tag) in tagging.py must return a new list "
        "of records with tag appended to record i's tag list, leaving the caller's records and their "
        "tag lists unchanged. Fix it.",
        "tagging.py",
        '''"""Tagging records."""


def add_tag(records, i, tag):
    """New list of records with `tag` appended to record i's tags; the caller's data is untouched."""
    out = list(records)
    out[i]["tags"].append(tag)
    return out
''',
        "test_tagging.py",
        '''from tagging import add_tag

def test_adds_tag_to_the_result():
    recs = [{"tags": ["a"]}, {"tags": []}]
    out = add_tag(recs, 0, "b")
    assert out[0]["tags"] == ["a", "b"]

def test_original_records_untouched():
    recs = [{"tags": ["a"]}, {"tags": []}]
    add_tag(recs, 0, "b")
    assert recs[0]["tags"] == ["a"]
''',
    ),
    _t(
        "hcopy_increment_cell",
        "The package `sheets` has a bug: increment(matrix, r, c) in sheets.py must return a new matrix "
        "with cell (r, c) increased by one, leaving the caller's matrix and its rows unchanged. Fix it.",
        "sheets.py",
        '''"""Spreadsheet cells."""


def increment(matrix, r, c):
    """New matrix with cell (r, c) + 1; the caller's matrix and its rows are unchanged."""
    out = matrix[:]
    out[r][c] += 1
    return out
''',
        "test_sheets.py",
        '''from sheets import increment

def test_increments_the_result():
    m = [[1, 2], [3, 4]]
    out = increment(m, 0, 1)
    assert out[0] == [1, 3]

def test_source_matrix_unchanged():
    m = [[1, 2], [3, 4]]
    increment(m, 0, 1)
    assert m == [[1, 2], [3, 4]]
''',
    ),
    _t(
        "hcopy_push",
        "The package `stacks` has a bug: push(stacks, name, item) in stacks.py must return a new mapping "
        "with item pushed onto stacks[name], leaving the caller's mapping and its lists unchanged. "
        "Fix it.",
        "stacks.py",
        '''"""Named stacks."""


def push(stacks, name, item):
    """New mapping with `item` pushed onto stacks[name]; the caller's mapping and lists are unchanged."""
    out = dict(stacks)
    out[name].append(item)
    return out
''',
        "test_stacks.py",
        '''from stacks import push

def test_pushes_onto_the_result():
    s = {"a": [1], "b": []}
    out = push(s, "a", 2)
    assert out["a"] == [1, 2]

def test_source_mapping_unchanged():
    s = {"a": [1], "b": []}
    push(s, "a", 2)
    assert s["a"] == [1]
''',
    ),
    _t(
        "hcopy_merge_lists",
        "The package `catalogs` has a bug: merge_lists(a, b) in catalogs.py must return a new mapping "
        "where each key maps to a's list followed by b's list (a key in only one input keeps that "
        "list), without modifying either input dict or its lists. Fix it.",
        "catalogs.py",
        '''"""Merging list-valued mappings."""


def merge_lists(a, b):
    """New mapping: a's list then b's list per key; neither input (nor its lists) is modified."""
    out = dict(a)
    for key, vals in b.items():
        if key in out:
            out[key] += vals
        else:
            out[key] = vals
    return out
''',
        "test_catalogs.py",
        '''from catalogs import merge_lists

def test_concatenates_shared_keys():
    a = {"x": [1]}
    b = {"x": [2], "y": [3]}
    assert merge_lists(a, b) == {"x": [1, 2], "y": [3]}

def test_inputs_untouched():
    a = {"x": [1]}
    b = {"x": [2], "y": [3]}
    merge_lists(a, b)
    assert a == {"x": [1]}
''',
    ),
    _t(
        "hcopy_set_flag",
        "The package `configs2` has a bug: set_flag(config, section, key, value) in configs2.py must "
        "return a new config with config[section][key] set to value, leaving the caller's config and "
        "its sections unchanged. Fix it.",
        "configs2.py",
        '''"""Sectioned config."""


def set_flag(config, section, key, value):
    """New config with config[section][key] = value; the caller's config and sections are unchanged."""
    out = dict(config)
    out[section][key] = value
    return out
''',
        "test_configs2.py",
        '''from configs2 import set_flag

def test_sets_on_the_result():
    c = {"ui": {"dark": False}, "net": {}}
    out = set_flag(c, "ui", "dark", True)
    assert out["ui"]["dark"] is True

def test_source_config_unchanged():
    c = {"ui": {"dark": False}, "net": {}}
    set_flag(c, "ui", "dark", True)
    assert c["ui"]["dark"] is False
''',
    ),
    # ============= family hincl_ : an endpoint is inclusive, and a SECOND bound is too ==============
    _t(
        "hincl_grid_points",
        "The package `lattice` has a bug: grid_points(x0, x1, y0, y1) in lattice.py must return every "
        "(x, y) with x0 <= x <= x1 and y0 <= y <= y1 (all bounds inclusive), ordered by x then y. "
        "Fix it.",
        "lattice.py",
        '''"""Integer lattice points."""


def grid_points(x0, x1, y0, y1):
    """All (x, y) with x0 <= x <= x1 and y0 <= y <= y1, both bounds inclusive, ordered by x then y."""
    out = []
    for x in range(x0, x1):
        for y in range(y0, y1):
            out.append((x, y))
    return out
''',
        "test_lattice.py",
        '''from lattice import grid_points

def test_includes_both_ends():
    assert grid_points(0, 1, 0, 1) == [(0, 0), (0, 1), (1, 0), (1, 1)]

def test_single_point():
    assert grid_points(2, 2, 3, 3) == [(2, 3)]
''',
    ),
    _t(
        "hincl_region_sum",
        "The package `regions` has a bug: region_sum(matrix, r0, r1, c0, c1) in regions.py must return "
        "the sum of the submatrix spanning rows r0..r1 and columns c0..c1, all bounds inclusive. "
        "Fix it.",
        "regions.py",
        '''"""Submatrix sums."""


def region_sum(matrix, r0, r1, c0, c1):
    """Sum of the submatrix over rows r0..r1 and columns c0..c1, all bounds inclusive."""
    total = 0
    for row in matrix[r0:r1]:
        for val in row[c0:c1]:
            total += val
    return total
''',
        "test_regions.py",
        '''from regions import region_sum

def test_inclusive_region():
    m = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
    assert region_sum(m, 0, 1, 0, 1) == 12   # 1 + 2 + 4 + 5

def test_single_cell():
    m = [[1, 2], [3, 4]]
    assert region_sum(m, 1, 1, 1, 1) == 4
''',
    ),
    _t(
        "hincl_count_in_band",
        "The package `tallies` has a bug: count_in_band(nums, lo, hi) in tallies.py must count the "
        "values n with lo <= n <= hi (both bounds inclusive). Fix it.",
        "tallies.py",
        '''"""Counting within a band."""


def count_in_band(nums, lo, hi):
    """How many of nums satisfy lo <= n <= hi, both bounds inclusive."""
    return sum(1 for n in nums if lo < n < hi)
''',
        "test_tallies.py",
        '''from tallies import count_in_band

def test_both_bounds_inclusive():
    assert count_in_band([1, 2, 3, 4, 5], 2, 4) == 3   # 2, 3, 4

def test_endpoints_at_a_single_value():
    assert count_in_band([5, 5, 6], 5, 5) == 2         # both 5s count
''',
    ),
    _t(
        "hincl_count_pairs",
        "The package `pairing` has a bug: count_pairs(a, b) in pairing.py must count the pairs (i, j) "
        "with a <= i <= j <= b (both ends inclusive). Fix it.",
        "pairing.py",
        '''"""Counting ordered pairs."""


def count_pairs(a, b):
    """Number of pairs (i, j) with a <= i <= j <= b, both ends inclusive."""
    total = 0
    for i in range(a, b):
        for j in range(i, b):
            total += 1
    return total
''',
        "test_pairing.py",
        '''from pairing import count_pairs

def test_inclusive_pairs():
    # a=1, b=3: (1,1)(1,2)(1,3)(2,2)(2,3)(3,3) = 6
    assert count_pairs(1, 3) == 6

def test_single():
    assert count_pairs(2, 2) == 1   # just (2, 2)
''',
    ),
    _t(
        "hincl_overlap",
        "The package `intervals2` has a bug: overlap(a1, a2, b1, b2) in intervals2.py must return the "
        "number of integer points shared by the inclusive intervals [a1, a2] and [b1, b2], or 0 when "
        "they do not meet. Fix it.",
        "intervals2.py",
        '''"""Inclusive-interval overlap."""


def overlap(a1, a2, b1, b2):
    """Count of integer points shared by inclusive [a1, a2] and [b1, b2]; 0 when disjoint."""
    lo = max(a1, b1)
    hi = min(a2, b2)
    return hi - lo
''',
        "test_intervals2.py",
        '''from intervals2 import overlap

def test_inclusive_overlap_length():
    # [1,5] and [3,8] share 3, 4, 5 -> 3 points
    assert overlap(1, 5, 3, 8) == 3

def test_disjoint_is_zero_not_negative():
    assert overlap(1, 2, 5, 6) == 0
''',
    ),
    # ========= family hcase_ : normalise BOTH sides, preserve the original spelling on output ========
    _t(
        "hcase_tally",
        "The package `census` has a bug: tally(words) in census.py must count words case-insensitively, "
        "returning a dict whose keys are the first spelling seen for each word. Fix it.",
        "census.py",
        '''"""Case-insensitive tally."""


def tally(words):
    """Case-insensitive counts; each key is the FIRST spelling seen for that word."""
    out = {}
    for w in words:
        key = w.lower()
        out[key] = out.get(key, 0) + 1
    return out
''',
        "test_census.py",
        '''from census import tally

def test_counts_case_insensitively():
    assert tally(["Apple", "apple", "APPLE"]) == {"Apple": 3}

def test_preserves_first_spelling():
    assert tally(["Pear", "PEAR", "fig"]) == {"Pear": 2, "fig": 1}
''',
    ),
    _t(
        "hcase_distinct",
        "The package `distinct` has a bug: distinct(words) in distinct.py must drop case-insensitive "
        "duplicates, keeping the first spelling of each, order preserved. Fix it.",
        "distinct.py",
        '''"""Case-insensitive de-duplication."""


def distinct(words):
    """Drop case-insensitive duplicates; keep the FIRST spelling of each, order preserved."""
    seen = set()
    out = []
    for w in words:
        if w not in seen:
            seen.add(w.lower())
            out.append(w)
    return out
''',
        "test_distinct.py",
        '''from distinct import distinct

def test_case_insensitive_first_spelling():
    assert distinct(["Apple", "apple", "APPLE", "Pear"]) == ["Apple", "Pear"]

def test_no_duplicates_unchanged():
    assert distinct(["a", "b", "c"]) == ["a", "b", "c"]
''',
    ),
    _t(
        "hcase_lookup",
        "The package `directory` has a bug: lookup(entries, name) in directory.py must return the value "
        "whose key matches name case-insensitively, or None when there is no match. entries is a list "
        "of (key, value) pairs. Fix it.",
        "directory.py",
        '''"""Case-insensitive lookup."""


def lookup(entries, name):
    """Value whose key equals `name` ignoring case; None when there is no match."""
    for key, value in entries:
        if key == name.lower():
            return value
    return None
''',
        "test_directory.py",
        '''from directory import lookup

def test_matches_ignoring_case():
    assert lookup([("Alice", 1), ("Bob", 2)], "alice") == 1
    assert lookup([("Alice", 1)], "ALICE") == 1

def test_absent_returns_none():
    assert lookup([("Alice", 1)], "carol") is None
''',
    ),
    _t(
        "hcase_group_words",
        "The package `bucketing` has a bug: group_words(words) in bucketing.py must group words "
        "case-insensitively, mapping each lowercased form to the list of original spellings in input "
        "order. Fix it.",
        "bucketing.py",
        '''"""Case-insensitive grouping."""


def group_words(words):
    """{lowercased form: [original spellings in input order]}."""
    out = {}
    for w in words:
        out.setdefault(w, []).append(w)
    return out
''',
        "test_bucketing.py",
        '''from bucketing import group_words

def test_groups_ignoring_case_keeping_originals():
    assert group_words(["Apple", "apple", "Pear"]) == {"apple": ["Apple", "apple"], "pear": ["Pear"]}
''',
    ),
    _t(
        "hcase_count_prefix",
        "The package `prefixes` has a bug: count_prefix(words, prefix) in prefixes.py must count how "
        "many words start with prefix, ignoring case. Fix it.",
        "prefixes.py",
        '''"""Case-insensitive prefix count."""


def count_prefix(words, prefix):
    """How many words start with `prefix`, comparing case-insensitively."""
    p = prefix.lower()
    return sum(1 for w in words if w.startswith(p))
''',
        "test_prefixes.py",
        '''from prefixes import count_prefix

def test_case_insensitive_prefix():
    assert count_prefix(["Apple", "apricot", "Banana"], "ap") == 2
    assert count_prefix(["Apple"], "AP") == 1

def test_no_match():
    assert count_prefix(["Banana"], "ap") == 0
''',
    ),
    # ========= family hreset_ : reset the accumulator per group AND emit the trailing group =========
    _t(
        "hreset_run_totals",
        "The package `runsum` has a bug: run_totals(nums) in runsum.py must return the sum of each "
        "maximal run of equal consecutive values, in order. Fix it.",
        "runsum.py",
        '''"""Per-run sums."""


def run_totals(nums):
    """Sum of each maximal run of equal consecutive values, in order."""
    out = []
    total = 0
    prev = None
    for n in nums:
        if prev is not None and n != prev:
            out.append(total)
        total += n
        prev = n
    return out
''',
        "test_runsum.py",
        '''from runsum import run_totals

def test_resets_and_emits_each_run():
    assert run_totals([2, 2, 3, 2, 2, 2]) == [4, 3, 6]

def test_trailing_group_emitted():
    assert run_totals([5]) == [5]

def test_empty():
    assert run_totals([]) == []
''',
    ),
    _t(
        "hreset_run_lengths",
        "The package `rle2` has a bug: run_lengths(items) in rle2.py must return an (item, length) pair "
        "for each maximal run of equal consecutive items, in order. Fix it.",
        "rle2.py",
        '''"""Run-length pairs."""


def run_lengths(items):
    """(item, run-length) for each maximal run of equal consecutive items, in order."""
    out = []
    count = 0
    prev = None
    started = False
    for x in items:
        if started and x != prev:
            out.append((prev, count))
        count += 1
        prev = x
        started = True
    return out
''',
        "test_rle2.py",
        '''from rle2 import run_lengths

def test_reset_and_emit():
    assert run_lengths(["a", "a", "b", "a", "a", "a"]) == [("a", 2), ("b", 1), ("a", 3)]

def test_single_trailing():
    assert run_lengths(["z"]) == [("z", 1)]

def test_empty():
    assert run_lengths([]) == []
''',
    ),
    _t(
        "hreset_group_sizes",
        "The package `letterruns` has a bug: group_sizes(words) in letterruns.py must return the size "
        "of each maximal run of consecutive words that share the same first letter, in order. Fix it.",
        "letterruns.py",
        '''"""Runs by shared first letter."""


def group_sizes(words):
    """Size of each maximal run of consecutive words sharing a first letter, in order."""
    out = []
    count = 0
    prev = None
    for w in words:
        first = w[0]
        if prev is not None and first != prev:
            out.append(count)
        count += 1
        prev = first
    return out
''',
        "test_letterruns.py",
        '''from letterruns import group_sizes

def test_reset_and_emit_runs():
    assert group_sizes(["apple", "ant", "bee", "cat", "car"]) == [2, 1, 2]

def test_single_run():
    assert group_sizes(["one"]) == [1]

def test_empty():
    assert group_sizes([]) == []
''',
    ),
    _t(
        "hreset_segment_sums",
        "The package `segments` has a bug: segment_sums(tokens) in segments.py must return the sum of "
        "the numbers in each segment, where segments are separated by None, in order. Fix it.",
        "segments.py",
        '''"""Segment sums split on None."""


def segment_sums(tokens):
    """Sum of the numbers in each segment; segments are separated by None. One sum per segment."""
    out = []
    total = 0
    for t in tokens:
        if t is None:
            out.append(total)
        else:
            total += t
    return out
''',
        "test_segments.py",
        '''from segments import segment_sums

def test_reset_each_segment_and_emit_last():
    assert segment_sums([1, 2, None, 3, None, 4]) == [3, 3, 4]

def test_trailing_segment_without_separator():
    assert segment_sums([5]) == [5]

def test_empty():
    assert segment_sums([]) == []
''',
    ),
    _t(
        "hreset_segment_averages",
        "The package `cohorts` has a bug: segment_averages(tokens) in cohorts.py must return the "
        "average of the numbers in each segment, where segments are separated by None, in order. "
        "Fix it.",
        "cohorts.py",
        '''"""Segment averages split on None."""


def segment_averages(tokens):
    """Average of the numbers in each segment; segments are separated by None. One value per segment."""
    out = []
    total = 0
    count = 0
    for t in tokens:
        if t is None:
            out.append(total / count)
        else:
            total += t
            count += 1
    return out
''',
        "test_cohorts.py",
        '''from cohorts import segment_averages

def test_reset_totals_and_emit_last():
    assert segment_averages([2, 4, None, 6, None, 10]) == [3.0, 6.0, 10.0]

def test_single_segment():
    assert segment_averages([8]) == [8.0]

def test_empty():
    assert segment_averages([]) == []
''',
    ),
]

__all__ = ["RECURRING_HARD_TASKS"]
