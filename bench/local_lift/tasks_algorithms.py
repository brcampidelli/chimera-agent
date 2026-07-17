"""Weak-model-lift tasks — domain: data structures + algorithms.

One of the domain modules of the n=100 pre-registered suite (see ``PREREGISTRATION.md``); the
sibling modules cover the other registered domains. These 29 tasks were authored to the
pre-registration's **a-priori difficulty spec** and to nothing else: each one needs at least two
non-obvious steps, or has an edge case a naive implementation misses (empty input, boundaries,
duplicates, ties, cycles, in-place mutation, one-sided edges). No task was ever run through either
arm of the experiment — selection by *intrinsic complexity only*, never by outcome, because keeping
a task because the loop wins on it is how a benchmark fabricates its own result.

The naive implementation each test is built to reject is named in a comment above the task, so the
strictness claim is auditable rather than asserted. Every test here was validated offline against a
correct reference (passes) and against that naive shortcut (fails). A test a naive implementation
passes is vacuous and grades nothing; those were strengthened before landing.

Tests are deterministic and stdlib-only: no network, no randomness, no clock, no filesystem beyond
the task's own files, and each file runs in well under a second — they grade correctness, never
speed. Ground truth is the strict pytest, which the runner re-runs independently of anything the
agent claims.
"""

from __future__ import annotations

from typing import Any

TASKS_ALGORITHMS: list[dict[str, Any]] = [
    # Naive rejected: merges only strict overlaps (touching intervals left apart) and mutates input.
    {
        "id": "insert_interval",
        "prompt": (
            "Create ranges.py with insert(intervals: list, new: tuple) -> list. `intervals` is a "
            "list of (start, end) tuples with start <= end, already sorted by start and mutually "
            "non-overlapping. Insert `new` and merge it with every interval it overlaps OR merely "
            "touches (so (1,2) and (2,5) merge into (1,5)). Return the resulting list of tuples "
            "sorted by start. The input list and its tuples must NOT be modified. "
            "insert([(1,3),(6,9)], (2,5)) -> [(1,5),(6,9)]; insert([], (5,7)) -> [(5,7)]."
        ),
        "files": {},
        "verify": "ranges.py",
        "test": "test_ranges.py",
        "test_src": (
            "from ranges import insert\n\n"
            "def test_overlap_merge():\n"
            "    assert insert([(1,3),(6,9)], (2,5)) == [(1,5),(6,9)]\n"
            "    assert insert([(1,2),(3,5),(6,7),(8,10),(12,16)], (4,8)) == "
            "[(1,2),(3,10),(12,16)]\n"
            "def test_touching_merges():\n"
            "    assert insert([(1,2),(5,6)], (2,5)) == [(1,6)]\n"
            "def test_edges():\n"
            "    assert insert([], (5,7)) == [(5,7)]\n"
            "    assert insert([(1,2)], (3,4)) == [(1,2),(3,4)]\n"
            "    assert insert([(3,4)], (1,2)) == [(1,2),(3,4)]\n"
            "def test_input_not_mutated():\n"
            "    src = [(1,3),(6,9)]\n"
            "    insert(src, (2,5))\n"
            "    assert src == [(1,3),(6,9)]\n"
        ),
    },
    # Naive rejected: trims the ends only — never splits an interval the cut lands inside.
    {
        "id": "remove_interval",
        "prompt": (
            "Create cut.py with remove(intervals: list, cut: tuple) -> list. Intervals are "
            "HALF-OPEN [start, end) tuples, sorted by start and non-overlapping. Return what is "
            "left after removing every point covered by the half-open `cut` = [cs, ce), as a list "
            "of tuples sorted by start, dropping any piece that became empty. A cut strictly "
            "inside an interval splits it in two. Because the ranges are half-open, a cut that only "
            "touches an end removes nothing. remove([(0,2),(3,4),(5,7)], (1,6)) -> [(0,1),(6,7)]; "
            "remove([(0,5)], (2,3)) -> [(0,2),(3,5)]; remove([(0,5)], (5,9)) -> [(0,5)]."
        ),
        "files": {},
        "verify": "cut.py",
        "test": "test_cut.py",
        "test_src": (
            "from cut import remove\n\n"
            "def test_across_several():\n"
            "    assert remove([(0,2),(3,4),(5,7)], (1,6)) == [(0,1),(6,7)]\n"
            "def test_split_in_the_middle():\n"
            "    assert remove([(0,5)], (2,3)) == [(0,2),(3,5)]\n"
            "def test_touching_removes_nothing():\n"
            "    assert remove([(0,5)], (5,9)) == [(0,5)]\n"
            "    assert remove([(5,9)], (0,5)) == [(5,9)]\n"
            "def test_full_and_empty():\n"
            "    assert remove([(0,5)], (0,5)) == []\n"
            "    assert remove([], (1,2)) == []\n"
            "    assert remove([(0,5)], (2,2)) == [(0,5)]\n"
        ),
    },
    # Naive rejected: counts a meeting ending exactly when another starts as a conflict.
    {
        "id": "meeting_rooms_min",
        "prompt": (
            "Create rooms.py with min_rooms(meetings: list) -> int. Each meeting is a (start, end) "
            "tuple, HALF-OPEN: a meeting ending at t and another starting at t do NOT conflict and "
            "can share a room. Return the minimum number of rooms needed to hold all meetings. "
            "min_rooms([]) is 0. The input may be in any order and must not be modified. "
            "min_rooms([(0,30),(5,10),(15,20)]) -> 2; min_rooms([(1,5),(5,9)]) -> 1."
        ),
        "files": {},
        "verify": "rooms.py",
        "test": "test_rooms.py",
        "test_src": (
            "from rooms import min_rooms\n\n"
            "def test_basic():\n"
            "    assert min_rooms([(0,30),(5,10),(15,20)]) == 2\n"
            "    assert min_rooms([(7,10),(2,4)]) == 1\n"
            "    assert min_rooms([]) == 0\n"
            "def test_touching_shares_a_room():\n"
            "    assert min_rooms([(1,5),(5,9)]) == 1\n"
            "    assert min_rooms([(1,5),(5,9),(9,12)]) == 1\n"
            "def test_deep_overlap():\n"
            "    assert min_rooms([(1,10),(2,7),(3,19),(8,12),(10,20),(11,30)]) == 4\n"
        ),
    },
    # Naive rejected: no validation of k — silently returns [] instead of raising ValueError.
    {
        "id": "sliding_window_max",
        "prompt": (
            "Create window.py with max_window(nums: list, k: int) -> list. Return the maximum of "
            "every contiguous window of length k, left to right. If `nums` is empty return [] "
            "whatever k is. Otherwise raise ValueError if k <= 0 or if k > len(nums). "
            "max_window([1,3,-1,-3,5,3,6,7], 3) -> [3,3,5,5,6,7]."
        ),
        "files": {},
        "verify": "window.py",
        "test": "test_window.py",
        "test_src": (
            "import pytest\n"
            "from window import max_window\n\n"
            "def test_windows():\n"
            "    assert max_window([1,3,-1,-3,5,3,6,7], 3) == [3,3,5,5,6,7]\n"
            "    assert max_window([9], 1) == [9]\n"
            "    assert max_window([4,2,12,3], 4) == [12]\n"
            "def test_empty_is_empty():\n"
            "    assert max_window([], 3) == []\n"
            "def test_bad_k_raises():\n"
            "    for k in [0, -1, 4]:\n"
            "        with pytest.raises(ValueError):\n"
            "            max_window([1,2,3], k)\n"
        ),
    },
    # Naive rejected: matches on the set of characters, ignoring how many times each is needed.
    {
        "id": "min_window_substring",
        "prompt": (
            "Create minwin.py with min_window(s: str, t: str) -> str. Return the shortest substring "
            "of s that contains every character of t INCLUDING multiplicity (t='aa' needs two "
            "'a's). Return '' if there is no such substring, and '' when s or t is empty. If "
            "several shortest substrings exist, return the leftmost. "
            "min_window('ADOBECODEBANC', 'ABC') -> 'BANC'; min_window('a', 'aa') -> ''."
        ),
        "files": {},
        "verify": "minwin.py",
        "test": "test_minwin.py",
        "test_src": (
            "from minwin import min_window\n\n"
            "def test_classic():\n"
            "    assert min_window('ADOBECODEBANC', 'ABC') == 'BANC'\n"
            "def test_multiplicity():\n"
            "    assert min_window('a', 'aa') == ''\n"
            "    assert min_window('aab', 'aab') == 'aab'\n"
            "    assert min_window('bba', 'ab') == 'ba'\n"
            "def test_empty():\n"
            "    assert min_window('', 'a') == ''\n"
            "    assert min_window('a', '') == ''\n"
            "def test_leftmost_of_equal_length():\n"
            "    assert min_window('abxab', 'ab') == 'ab'\n"
        ),
    },
    # Naive rejected: splits into equal-LENGTH chunks instead of minimising the largest sum.
    {
        "id": "split_min_largest",
        "prompt": (
            "Create split.py with min_largest_sum(nums: list, m: int) -> int. Split `nums` (a list "
            "of non-negative ints) into exactly m non-empty CONTIGUOUS parts so that the largest "
            "part-sum is as small as possible, and return that largest sum. Raise ValueError if "
            "m < 1 or m > len(nums). min_largest_sum([7,2,5,10,8], 2) -> 18 (split [7,2,5] and "
            "[10,8]); min_largest_sum([1,1,1,100], 2) -> 100."
        ),
        "files": {},
        "verify": "split.py",
        "test": "test_split.py",
        "test_src": (
            "import pytest\n"
            "from split import min_largest_sum\n\n"
            "def test_basic():\n"
            "    assert min_largest_sum([7,2,5,10,8], 2) == 18\n"
            "    assert min_largest_sum([1,2,3,4,5], 2) == 9\n"
            "    assert min_largest_sum([1,4,4], 3) == 4\n"
            "def test_uneven_split_beats_equal_lengths():\n"
            "    assert min_largest_sum([1,1,1,100], 2) == 100\n"
            "    assert min_largest_sum([100,1,1,1], 2) == 100\n"
            "def test_single_part():\n"
            "    assert min_largest_sum([2,3,1], 1) == 6\n"
            "def test_bad_m_raises():\n"
            "    for m in [0, -1, 4]:\n"
            "        with pytest.raises(ValueError):\n"
            "            min_largest_sum([1,2,3], m)\n"
        ),
    },
    # Naive rejected: union() always reports True, and no bounds validation.
    {
        "id": "union_find_ds",
        "prompt": (
            "Create dsu.py with a class DSU (disjoint-set / union-find). DSU(n) starts with n "
            "singleton sets holding the ids 0..n-1. Methods: find(x) -> the representative of x's "
            "set (any stable id in that set); union(a, b) -> True if the two sets were different "
            "and are now merged, False if a and b were already in the same set; connected(a, b) -> "
            "bool; size(x) -> how many ids are in x's set; count() -> how many disjoint sets exist. "
            "Any id outside 0..n-1 (including negatives) passed to any method raises ValueError."
        ),
        "files": {},
        "verify": "dsu.py",
        "test": "test_dsu.py",
        "test_src": (
            "import pytest\n"
            "from dsu import DSU\n\n"
            "def test_union_reports_merge():\n"
            "    d = DSU(5)\n"
            "    assert d.count() == 5\n"
            "    assert d.union(0, 1) is True\n"
            "    assert d.union(1, 0) is False\n"
            "    assert d.union(0, 1) is False\n"
            "    assert d.connected(0, 1) is True\n"
            "    assert d.connected(0, 2) is False\n"
            "    assert d.count() == 4\n"
            "def test_sizes_and_roots():\n"
            "    d = DSU(5)\n"
            "    d.union(0, 1); d.union(2, 3); d.union(3, 4)\n"
            "    assert d.size(0) == 2\n"
            "    assert d.size(4) == 3\n"
            "    assert d.count() == 2\n"
            "    assert d.find(2) == d.find(4)\n"
            "    assert d.find(0) != d.find(2)\n"
            "    assert d.union(1, 4) is True\n"
            "    assert d.count() == 1\n"
            "    assert d.size(3) == 5\n"
            "def test_bad_id_raises():\n"
            "    d = DSU(3)\n"
            "    for bad in [3, -1, 99]:\n"
            "        with pytest.raises(ValueError):\n"
            "            d.find(bad)\n"
        ),
    },
    # Naive rejected: delete() drops the whole branch, taking longer words down with it.
    {
        "id": "trie_delete",
        "prompt": (
            "Create trie.py with a class Trie. Trie() starts empty. insert(word) -> None adds a "
            "word; search(word) -> bool is True only for a word that was inserted (not for a mere "
            "prefix of one); starts_with(prefix) -> bool is True if any stored word starts with "
            "prefix; delete(word) -> bool removes the word and returns True, or returns False if "
            "the word was not stored. Deleting a word must leave every other stored word intact, "
            "including longer words that extend it. The empty string is a valid word."
        ),
        "files": {},
        "verify": "trie.py",
        "test": "test_trie.py",
        "test_src": (
            "from trie import Trie\n\n"
            "def test_insert_search_prefix():\n"
            "    t = Trie()\n"
            "    t.insert('app'); t.insert('apple')\n"
            "    assert t.search('app') is True\n"
            "    assert t.search('apple') is True\n"
            "    assert t.search('appl') is False\n"
            "    assert t.starts_with('appl') is True\n"
            "    assert t.starts_with('b') is False\n"
            "def test_delete_keeps_longer_words():\n"
            "    t = Trie()\n"
            "    t.insert('app'); t.insert('apple')\n"
            "    assert t.delete('app') is True\n"
            "    assert t.search('app') is False\n"
            "    assert t.search('apple') is True\n"
            "    assert t.starts_with('app') is True\n"
            "    assert t.delete('app') is False\n"
            "    assert t.delete('nope') is False\n"
            "def test_delete_prunes_dead_branch():\n"
            "    t = Trie()\n"
            "    t.insert('apple'); t.insert('bat')\n"
            "    assert t.delete('apple') is True\n"
            "    assert t.starts_with('app') is False\n"
            "    assert t.search('bat') is True\n"
            "def test_empty_word():\n"
            "    t = Trie()\n"
            "    assert t.search('') is False\n"
            "    t.insert('')\n"
            "    assert t.search('') is True\n"
        ),
    },
    # Naive rejected: plain LRU — evicts by recency, never by frequency.
    {
        "id": "lfu_cache",
        "prompt": (
            "Create lfu.py with a class LFUCache. LFUCache(capacity: int) holds up to `capacity` "
            "key->value pairs. get(key) returns the value or -1 if absent; put(key, value) inserts "
            "or updates. Every successful get and every put counts as one USE of that key. When "
            "inserting a new key would exceed capacity, evict the key with the fewest uses; if "
            "several are tied on uses, evict the least recently used of them. A capacity of 0 "
            "stores nothing (get always returns -1)."
        ),
        "files": {},
        "verify": "lfu.py",
        "test": "test_lfu.py",
        "test_src": (
            "from lfu import LFUCache\n\n"
            "def test_frequency_beats_recency():\n"
            "    c = LFUCache(2)\n"
            "    c.put(1, 1); c.put(2, 2)\n"
            "    assert c.get(1) == 1\n"
            "    assert c.get(1) == 1\n"
            "    assert c.get(2) == 2\n"
            "    c.put(3, 3)\n"
            "    assert c.get(2) == -1\n"
            "    assert c.get(1) == 1\n"
            "    assert c.get(3) == 3\n"
            "def test_tie_broken_by_recency():\n"
            "    c = LFUCache(2)\n"
            "    c.put(1, 1); c.put(2, 2)\n"
            "    assert c.get(1) == 1\n"
            "    c.put(3, 3)\n"
            "    assert c.get(3) == 3\n"
            "    c.put(4, 4)\n"
            "    assert c.get(1) == -1\n"
            "    assert c.get(3) == 3\n"
            "    assert c.get(4) == 4\n"
            "def test_update_counts_as_use():\n"
            "    c = LFUCache(2)\n"
            "    c.put(1, 1); c.put(2, 2)\n"
            "    c.put(1, 10)\n"
            "    c.put(3, 3)\n"
            "    assert c.get(2) == -1\n"
            "    assert c.get(1) == 10\n"
            "def test_zero_capacity():\n"
            "    c = LFUCache(0)\n"
            "    c.put(0, 0)\n"
            "    assert c.get(0) == -1\n"
        ),
    },
    # Naive rejected: one sorted(..., reverse=True) — flips every key, not just the score.
    {
        "id": "multi_key_sort",
        "prompt": (
            "Create rank.py with rank(records: list) -> list. Each record is a dict with keys "
            "'name' (str), 'score' (int) and 'age' (int). Return a NEW list of the same records "
            "ordered by score DESCENDING, then by age ASCENDING, then by name ascending "
            "(case-sensitive, normal string order). The input list must not be reordered and the "
            "records must not be copied or altered — the returned list holds the same dict objects."
        ),
        "files": {},
        "verify": "rank.py",
        "test": "test_rank.py",
        "test_src": (
            "from rank import rank\n\n"
            "def R(n, s, a):\n"
            "    return {'name': n, 'score': s, 'age': a}\n\n"
            "def names(rs):\n"
            "    return [r['name'] for r in rs]\n\n"
            "def test_score_desc_age_asc():\n"
            "    rs = [R('ann', 10, 40), R('bob', 30, 25), R('cid', 10, 20)]\n"
            "    assert names(rank(rs)) == ['bob', 'cid', 'ann']\n"
            "def test_name_tiebreak_ascending():\n"
            "    rs = [R('zoe', 5, 30), R('amy', 5, 30), R('Moe', 5, 30)]\n"
            "    assert names(rank(rs)) == ['Moe', 'amy', 'zoe']\n"
            "def test_all_three_keys():\n"
            "    rs = [R('d', 7, 9), R('a', 7, 9), R('c', 7, 3), R('b', 9, 50)]\n"
            "    assert names(rank(rs)) == ['b', 'c', 'a', 'd']\n"
            "def test_empty_and_no_mutation():\n"
            "    assert rank([]) == []\n"
            "    rs = [R('ann', 10, 40), R('bob', 30, 25)]\n"
            "    out = rank(rs)\n"
            "    assert names(rs) == ['ann', 'bob']\n"
            "    assert out is not rs\n"
            "    assert out[0] is rs[1]\n"
        ),
    },
    # Naive rejected: no guard for a single remaining row/column — emits those cells twice.
    {
        "id": "spiral_matrix",
        "prompt": (
            "Create spiral.py with spiral(matrix: list) -> list. Return every element of the "
            "matrix (a list of equal-length rows) in clockwise spiral order starting at the "
            "top-left: across the top row, down the right column, back along the bottom, up the "
            "left, then inwards. The matrix need not be square, may be a single row or a single "
            "column, and [] or [[]] returns []. Each element appears exactly once."
        ),
        "files": {},
        "verify": "spiral.py",
        "test": "test_spiral.py",
        "test_src": (
            "from spiral import spiral\n\n"
            "def test_square():\n"
            "    assert spiral([[1,2,3],[4,5,6],[7,8,9]]) == [1,2,3,6,9,8,7,4,5]\n"
            "def test_wide():\n"
            "    m = [[1,2,3,4],[5,6,7,8],[9,10,11,12]]\n"
            "    assert spiral(m) == [1,2,3,4,8,12,11,10,9,5,6,7]\n"
            "def test_single_row_and_column():\n"
            "    assert spiral([[1,2,3]]) == [1,2,3]\n"
            "    assert spiral([[1],[2],[3]]) == [1,2,3]\n"
            "    assert spiral([[7]]) == [7]\n"
            "def test_tall():\n"
            "    assert spiral([[1,2],[3,4],[5,6]]) == [1,2,4,6,5,3]\n"
            "def test_empty():\n"
            "    assert spiral([]) == []\n"
            "    assert spiral([[]]) == []\n"
        ),
    },
    # Naive rejected: builds and returns a new matrix, leaving the caller's list untouched.
    {
        "id": "rotate_matrix_inplace",
        "prompt": (
            "Create rot.py with rotate(matrix: list) -> None. Rotate the n x n `matrix` 90 degrees "
            "CLOCKWISE in place: the function returns None and the list the caller passed in holds "
            "the rotated matrix afterwards. rotate([[1,2],[3,4]]) leaves [[3,1],[4,2]]. An empty "
            "matrix and a 1x1 matrix are valid inputs."
        ),
        "files": {},
        "verify": "rot.py",
        "test": "test_rot.py",
        "test_src": (
            "from rot import rotate\n\n"
            "def test_two_by_two_in_place():\n"
            "    m = [[1,2],[3,4]]\n"
            "    assert rotate(m) is None\n"
            "    assert m == [[3,1],[4,2]]\n"
            "def test_three_by_three():\n"
            "    m = [[1,2,3],[4,5,6],[7,8,9]]\n"
            "    rotate(m)\n"
            "    assert m == [[7,4,1],[8,5,2],[9,6,3]]\n"
            "def test_four_rotations_restore():\n"
            "    m = [[1,2,3],[4,5,6],[7,8,9]]\n"
            "    for _ in range(4):\n"
            "        rotate(m)\n"
            "    assert m == [[1,2,3],[4,5,6],[7,8,9]]\n"
            "def test_edges():\n"
            "    m = [[5]]\n"
            "    rotate(m)\n"
            "    assert m == [[5]]\n"
            "    e = []\n"
            "    rotate(e)\n"
            "    assert e == []\n"
        ),
    },
    # Naive rejected: counts cells instead of moves (off-by-one) and has no unreachable case.
    {
        "id": "grid_shortest_path",
        "prompt": (
            "Create maze.py with shortest(grid: list) -> int. `grid` is a list of equal-length "
            "strings where '.' is open and '#' is a wall. Starting at the top-left cell and moving "
            "up/down/left/right through open cells only, return the number of MOVES on a shortest "
            "path to the bottom-right cell. Return -1 if there is no such path, if the start or the "
            "end cell is a wall, or if the grid is empty. A 1x1 open grid needs 0 moves."
        ),
        "files": {},
        "verify": "maze.py",
        "test": "test_maze.py",
        "test_src": (
            "from maze import shortest\n\n"
            "def test_open_grid():\n"
            "    assert shortest(['..', '..']) == 2\n"
            "    assert shortest(['...', '...', '...']) == 4\n"
            "def test_around_a_wall():\n"
            "    assert shortest(['....', '.##.', '....']) == 5\n"
            "def test_takes_the_shortest_not_the_first():\n"
            "    assert shortest(['...#', '.#..', '....']) == 5\n"
            "def test_single_cell_is_zero_moves():\n"
            "    assert shortest(['.']) == 0\n"
            "def test_unreachable_and_blocked():\n"
            "    assert shortest(['.#', '#.']) == -1\n"
            "    assert shortest(['#']) == -1\n"
            "    assert shortest(['.', '#']) == -1\n"
            "    assert shortest([]) == -1\n"
        ),
    },
    # Naive rejected: nested i<j loop that emits the same value-pair once per duplicate.
    {
        "id": "two_sum_pairs",
        "prompt": (
            "Create pairs.py with find_pairs(nums: list, target: int) -> list. Return every "
            "DISTINCT value pair (a, b) with a <= b, a + b == target, where a and b can be taken "
            "from `nums` as two different elements — so a pair (a, a) counts only if that value "
            "occurs at least twice. Each distinct pair appears exactly once, and the result is "
            "sorted ascending by a then b. find_pairs([1,2,3,4,3], 6) -> [(2,4),(3,3)]; "
            "find_pairs([3,1,5], 6) -> [(1,5)]."
        ),
        "files": {},
        "verify": "pairs.py",
        "test": "test_pairs.py",
        "test_src": (
            "from pairs import find_pairs\n\n"
            "def test_pairs():\n"
            "    assert find_pairs([1,2,3,4,3], 6) == [(2,4),(3,3)]\n"
            "def test_self_pair_needs_two_copies():\n"
            "    assert find_pairs([3,1,5], 6) == [(1,5)]\n"
            "    assert find_pairs([0,0], 0) == [(0,0)]\n"
            "def test_duplicates_not_repeated():\n"
            "    assert find_pairs([1,1,1], 2) == [(1,1)]\n"
            "    assert find_pairs([1,1,2,2], 3) == [(1,2)]\n"
            "def test_empty_and_none():\n"
            "    assert find_pairs([], 5) == []\n"
            "    assert find_pairs([1,2], 99) == []\n"
            "def test_negatives_sorted():\n"
            "    assert find_pairs([-1,4,2,1,3,0], 3) == [(-1,4),(0,3),(1,2)]\n"
        ),
    },
    # Naive rejected: sorts and counts, resetting the run when it meets a duplicate.
    {
        "id": "longest_consecutive_run",
        "prompt": (
            "Create consec.py with longest(nums: list) -> int. Return the length of the longest run "
            "of consecutive integers that appear in `nums`; the order of the list is irrelevant and "
            "duplicates count once. longest([100,4,200,1,3,2]) -> 4; longest([1,2,2,3]) -> 3; "
            "longest([]) -> 0. Negative numbers are ordinary values."
        ),
        "files": {},
        "verify": "consec.py",
        "test": "test_consec.py",
        "test_src": (
            "from consec import longest\n\n"
            "def test_basic():\n"
            "    assert longest([100,4,200,1,3,2]) == 4\n"
            "    assert longest([5]) == 1\n"
            "    assert longest([1,3,5,7]) == 1\n"
            "def test_duplicates_do_not_break_the_run():\n"
            "    assert longest([1,2,2,3]) == 3\n"
            "    assert longest([1,1,1]) == 1\n"
            "    assert longest([9,8,8,7,7,6]) == 4\n"
            "def test_negatives():\n"
            "    assert longest([-2,-1,0,2]) == 3\n"
            "def test_empty():\n"
            "    assert longest([]) == 0\n"
        ),
    },
    # Naive rejected: returns the lower middle for an even count, and no error on empty.
    {
        "id": "running_median",
        "prompt": (
            "Create medstream.py with a class MedianStream. MedianStream() starts empty. "
            "add(x) -> None records a number; median() returns the median of everything added so "
            "far — the middle value for an odd count, and the AVERAGE of the two middle values for "
            "an even count (so [1,2] gives 1.5). Calling median() before anything was added raises "
            "ValueError. Values may repeat and arrive in any order."
        ),
        "files": {},
        "verify": "medstream.py",
        "test": "test_medstream.py",
        "test_src": (
            "import pytest\n"
            "from medstream import MedianStream\n\n"
            "def test_empty_raises():\n"
            "    m = MedianStream()\n"
            "    with pytest.raises(ValueError):\n"
            "        m.median()\n"
            "def test_running():\n"
            "    m = MedianStream()\n"
            "    m.add(1)\n"
            "    assert m.median() == 1\n"
            "    m.add(2)\n"
            "    assert m.median() == 1.5\n"
            "    m.add(3)\n"
            "    assert m.median() == 2\n"
            "    m.add(4)\n"
            "    assert m.median() == 2.5\n"
            "    m.add(-10)\n"
            "    assert m.median() == 2\n"
            "def test_unsorted_arrival_and_duplicates():\n"
            "    m = MedianStream()\n"
            "    for x in [5, 5, 1, 9]:\n"
            "        m.add(x)\n"
            "    assert m.median() == 5\n"
        ),
    },
    # Naive rejected: BFS on hop count, ignoring the edge weights entirely.
    {
        "id": "dijkstra_paths",
        "prompt": (
            "Create dij.py with shortest_path(graph: dict, start, end) -> tuple. `graph` maps a "
            "node to a dict of {neighbour: weight} with positive weights; the edges are directed "
            "and every node appears as a key. Return (cost, path) where cost is the total weight of "
            "a cheapest start->end path and path is the list of nodes along it, start and end "
            "included. Return (None, []) if end is unreachable, and (0, [start]) when start == end. "
            "If several paths tie on cost, return the lexicographically smallest path. Raise "
            "ValueError if start or end is not a node of the graph."
        ),
        "files": {},
        "verify": "dij.py",
        "test": "test_dij.py",
        "test_src": (
            "import pytest\n"
            "from dij import shortest_path\n\n"
            "G = {'a': {'b': 1, 'c': 4}, 'b': {'c': 2, 'd': 5}, 'c': {'d': 1}, 'd': {}}\n\n"
            "def test_weights_beat_hop_count():\n"
            "    assert shortest_path(G, 'a', 'd') == (4, ['a', 'b', 'c', 'd'])\n"
            "    assert shortest_path(G, 'a', 'c') == (3, ['a', 'b', 'c'])\n"
            "def test_tie_is_lexicographic():\n"
            "    g = {'a': {'b': 1, 'c': 1}, 'b': {'d': 1}, 'c': {'d': 1}, 'd': {}}\n"
            "    assert shortest_path(g, 'a', 'd') == (2, ['a', 'b', 'd'])\n"
            "def test_same_node_and_unreachable():\n"
            "    assert shortest_path(G, 'a', 'a') == (0, ['a'])\n"
            "    assert shortest_path({'a': {}, 'b': {}}, 'a', 'b') == (None, [])\n"
            "def test_unknown_node_raises():\n"
            "    with pytest.raises(ValueError):\n"
            "        shortest_path(G, 'a', 'zz')\n"
            "    with pytest.raises(ValueError):\n"
            "        shortest_path(G, 'zz', 'a')\n"
        ),
    },
    # Naive rejected: compares each node with its direct children only, missing subtree violations.
    {
        "id": "validate_bst",
        "prompt": (
            "Create bst.py with is_bst(node) -> bool. A tree node is either None or a 3-tuple "
            "(value, left, right) whose children are nodes. Return True only if the tree is a valid "
            "binary search tree: EVERY value in a node's left subtree is strictly less than the "
            "node's value and every value in its right subtree is strictly greater — duplicates are "
            "not allowed anywhere. is_bst(None) is True."
        ),
        "files": {},
        "verify": "bst.py",
        "test": "test_bst.py",
        "test_src": (
            "from bst import is_bst\n\n"
            "def test_valid():\n"
            "    assert is_bst(None) is True\n"
            "    assert is_bst((1, None, None)) is True\n"
            "    assert is_bst((2, (1, None, None), (3, None, None))) is True\n"
            "    deep = (8, (4, (2, None, None), (6, None, None)), (12, (10, None, None), None))\n"
            "    assert is_bst(deep) is True\n"
            "def test_subtree_violation_not_just_children():\n"
            "    bad = (5, (1, None, (6, None, None)), (7, None, None))\n"
            "    assert is_bst(bad) is False\n"
            "    bad2 = (5, (1, None, None), (7, (4, None, None), None))\n"
            "    assert is_bst(bad2) is False\n"
            "def test_duplicates_rejected():\n"
            "    assert is_bst((2, (2, None, None), None)) is False\n"
            "    assert is_bst((2, None, (2, None, None))) is False\n"
        ),
    },
    # Naive rejected: plain level-order — never reverses the alternate levels.
    {
        "id": "zigzag_levels",
        "prompt": (
            "Create levels.py with zigzag(node) -> list. A tree node is either None or a 3-tuple "
            "(value, left, right). Return the values level by level as a list of lists: level 0 "
            "left-to-right, level 1 right-to-left, level 2 left-to-right, and so on. zigzag(None) "
            "is []. Missing children simply do not contribute to the next level."
        ),
        "files": {},
        "verify": "levels.py",
        "test": "test_levels.py",
        "test_src": (
            "from levels import zigzag\n\n"
            "def test_classic():\n"
            "    t = (3, (9, None, None), (20, (15, None, None), (7, None, None)))\n"
            "    assert zigzag(t) == [[3], [20, 9], [15, 7]]\n"
            "def test_empty_and_single():\n"
            "    assert zigzag(None) == []\n"
            "    assert zigzag((1, None, None)) == [[1]]\n"
            "def test_gaps():\n"
            "    t = (1, (2, (4, None, None), None), (3, None, (5, None, None)))\n"
            "    assert zigzag(t) == [[1], [3, 2], [4, 5]]\n"
            "def test_four_levels():\n"
            "    t = (1, (2, (4, (8, None, None), None), None), (3, None, None))\n"
            "    assert zigzag(t) == [[1], [3, 2], [4], [8]]\n"
        ),
    },
    # Naive rejected: uses a set of seen keys — blows up (TypeError) on unhashable keys.
    {
        "id": "dedup_by_key",
        "prompt": (
            "Create dedup.py with dedup(items: list, key=None) -> list. Return a new list with "
            "duplicates removed, keeping the FIRST occurrence of each and the original order. `key` "
            "is an optional callable mapping an item to the value used for comparison; when it is "
            "None the item itself is the key. Keys are compared with == and may be UNHASHABLE "
            "(lists, dicts), so a set of seen keys is not an option. The input list is not modified."
        ),
        "files": {},
        "verify": "dedup.py",
        "test": "test_dedup.py",
        "test_src": (
            "from dedup import dedup\n\n"
            "def test_order_and_first_wins():\n"
            "    assert dedup([3,1,3,2,1]) == [3,1,2]\n"
            "    assert dedup([]) == []\n"
            "    assert dedup(['a','a','a']) == ['a']\n"
            "def test_unhashable_items():\n"
            "    assert dedup([[1],[2],[1]]) == [[1],[2]]\n"
            "    assert dedup([{'a':1},{'a':1},{'b':2}]) == [{'a':1},{'b':2}]\n"
            "def test_key_callable():\n"
            "    items = [{'i':1,'v':'x'}, {'i':2,'v':'y'}, {'i':1,'v':'z'}]\n"
            "    out = dedup(items, key=lambda d: d['i'])\n"
            "    assert [d['v'] for d in out] == ['x','y']\n"
            "def test_unhashable_key():\n"
            "    items = [('a',[1]), ('b',[1]), ('c',[2])]\n"
            "    out = dedup(items, key=lambda t: t[1])\n"
            "    assert [t[0] for t in out] == ['a','c']\n"
            "def test_input_not_mutated():\n"
            "    src = [1,1,2]\n"
            "    dedup(src)\n"
            "    assert src == [1,1,2]\n"
        ),
    },
    # Naive rejected: walks the keys as a directed graph — misses one-sided edges and non-key nodes.
    {
        "id": "connected_components",
        "prompt": (
            "Create comps.py with components(graph: dict) -> list. `graph` maps a node to a list of "
            "neighbours and describes an UNDIRECTED graph: an edge listed on only one side still "
            "connects both nodes, and a neighbour that never appears as a key is still a node of "
            "the graph. A node with no neighbours is its own component. Return the components as a "
            "list of sorted node lists, the outer list sorted by each component's first node. "
            "components({}) is []."
        ),
        "files": {},
        "verify": "comps.py",
        "test": "test_comps.py",
        "test_src": (
            "from comps import components\n\n"
            "def test_basic():\n"
            "    g = {'a': ['b'], 'b': ['a'], 'c': [], 'd': ['e'], 'e': ['d']}\n"
            "    assert components(g) == [['a','b'], ['c'], ['d','e']]\n"
            "def test_one_sided_edge():\n"
            "    assert components({'a': ['b'], 'b': []}) == [['a','b']]\n"
            "    assert components({'b': ['a'], 'a': [], 'z': ['a']}) == [['a','b','z']]\n"
            "def test_neighbour_that_is_not_a_key():\n"
            "    assert components({'a': ['z']}) == [['a','z']]\n"
            "    assert components({'x': [], 'a': ['z']}) == [['a','z'], ['x']]\n"
            "def test_sorted_output():\n"
            "    g = {'d': ['c'], 'c': [], 'b': ['a'], 'a': []}\n"
            "    assert components(g) == [['a','b'], ['c','d']]\n"
            "def test_empty():\n"
            "    assert components({}) == []\n"
        ),
    },
    # Naive rejected: checks rows and columns but not the two diagonals.
    {
        "id": "n_queens_count",
        "prompt": (
            "Create queens.py with count_solutions(n: int) -> int. Return how many distinct ways n "
            "queens can be placed on an n x n board so that no two share a row, a column, or a "
            "diagonal. count_solutions(0) is 1 (the empty placement), count_solutions(2) is 0 and "
            "count_solutions(4) is 2. Raise ValueError for n < 0."
        ),
        "files": {},
        "verify": "queens.py",
        "test": "test_queens.py",
        "test_src": (
            "import pytest\n"
            "from queens import count_solutions\n\n"
            "def test_small():\n"
            "    assert count_solutions(0) == 1\n"
            "    assert count_solutions(1) == 1\n"
            "    assert count_solutions(2) == 0\n"
            "    assert count_solutions(3) == 0\n"
            "def test_known_counts():\n"
            "    assert count_solutions(4) == 2\n"
            "    assert count_solutions(5) == 10\n"
            "    assert count_solutions(6) == 4\n"
            "    assert count_solutions(8) == 92\n"
            "def test_negative_raises():\n"
            "    with pytest.raises(ValueError):\n"
            "        count_solutions(-1)\n"
        ),
    },
    # Naive rejected: returns a new list instead of mutating, and no descending wrap-around.
    {
        "id": "next_permutation",
        "prompt": (
            "Create nextperm.py with next_perm(nums: list) -> None. Rearrange `nums` IN PLACE into "
            "the next lexicographically greater permutation of its elements and return None. If no "
            "greater permutation exists (the list is in descending order) rearrange it into the "
            "smallest permutation, i.e. ascending order. Lists of length 0 or 1 are left as they "
            "are. Duplicate values are allowed. [1,2,3] -> [1,3,2]; [3,2,1] -> [1,2,3]."
        ),
        "files": {},
        "verify": "nextperm.py",
        "test": "test_nextperm.py",
        "test_src": (
            "from nextperm import next_perm\n\n"
            "def test_in_place_returns_none():\n"
            "    a = [1,2,3]\n"
            "    assert next_perm(a) is None\n"
            "    assert a == [1,3,2]\n"
            "def test_wraps_when_descending():\n"
            "    a = [3,2,1]\n"
            "    next_perm(a)\n"
            "    assert a == [1,2,3]\n"
            "def test_steps():\n"
            "    a = [1,3,2]\n"
            "    next_perm(a)\n"
            "    assert a == [2,1,3]\n"
            "    a = [1,1,5]\n"
            "    next_perm(a)\n"
            "    assert a == [1,5,1]\n"
            "def test_duplicates():\n"
            "    a = [2,3,1,3,3]\n"
            "    next_perm(a)\n"
            "    assert a == [2,3,3,1,3]\n"
            "    b = [1,1,1]\n"
            "    next_perm(b)\n"
            "    assert b == [1,1,1]\n"
            "def test_tiny():\n"
            "    a = []\n"
            "    next_perm(a)\n"
            "    assert a == []\n"
            "    b = [7]\n"
            "    next_perm(b)\n"
            "    assert b == [7]\n"
        ),
    },
    # Naive rejected: keeps the LAST window of maximal length instead of the leftmost.
    {
        "id": "longest_k_distinct",
        "prompt": (
            "Create kdistinct.py with longest_substring(s: str, k: int) -> str. Return the longest "
            "contiguous substring of s containing at most k distinct characters. If several "
            "substrings share the maximum length, return the LEFTMOST one. Return '' when s is "
            "empty or when k <= 0. longest_substring('eceba', 2) -> 'ece'; "
            "longest_substring('aabbcc', 1) -> 'aa'."
        ),
        "files": {},
        "verify": "kdistinct.py",
        "test": "test_kdistinct.py",
        "test_src": (
            "from kdistinct import longest_substring\n\n"
            "def test_basic():\n"
            "    assert longest_substring('eceba', 2) == 'ece'\n"
            "    assert longest_substring('aa', 1) == 'aa'\n"
            "    assert longest_substring('abcabc', 3) == 'abcabc'\n"
            "def test_leftmost_wins_ties():\n"
            "    assert longest_substring('aabbcc', 1) == 'aa'\n"
            "    assert longest_substring('abcabc', 2) == 'ab'\n"
            "def test_k_at_least_distinct_count():\n"
            "    assert longest_substring('abc', 5) == 'abc'\n"
            "def test_empty_and_zero_k():\n"
            "    assert longest_substring('', 2) == ''\n"
            "    assert longest_substring('abc', 0) == ''\n"
            "    assert longest_substring('abc', -1) == ''\n"
        ),
    },
    # Naive rejected: sliding window that assumes non-negative values.
    {
        "id": "subarray_count_k",
        "prompt": (
            "Create subsum.py with count_subarrays(nums: list, k: int) -> int. Return how many "
            "contiguous, non-empty subarrays of `nums` sum to exactly k. Values may be negative or "
            "zero, and overlapping subarrays each count. count_subarrays([1,1,1], 2) -> 2; "
            "count_subarrays([1,-1,0], 0) -> 3; count_subarrays([], 0) -> 0."
        ),
        "files": {},
        "verify": "subsum.py",
        "test": "test_subsum.py",
        "test_src": (
            "from subsum import count_subarrays\n\n"
            "def test_positive():\n"
            "    assert count_subarrays([1,1,1], 2) == 2\n"
            "    assert count_subarrays([1,2,3], 3) == 2\n"
            "def test_negatives_and_zeros():\n"
            "    assert count_subarrays([1,-1,0], 0) == 3\n"
            "    assert count_subarrays([0,0,0], 0) == 6\n"
            "    assert count_subarrays([3,4,-7,1,3,3,1,-4], 7) == 4\n"
            "def test_none_and_empty():\n"
            "    assert count_subarrays([], 0) == 0\n"
            "    assert count_subarrays([1,2], 99) == 0\n"
        ),
    },
    # Naive rejected: a single `min` attribute — a stale minimum survives popping it.
    {
        "id": "min_stack",
        "prompt": (
            "Create minstack.py with a class MinStack. MinStack() starts empty. push(x) -> None; "
            "pop() removes and returns the top; top() returns the top without removing it; "
            "get_min() returns the smallest value currently in the stack. pop(), top() and "
            "get_min() on an empty stack raise IndexError. The minimum may be pushed several "
            "times — popping one copy must not lose the others."
        ),
        "files": {},
        "verify": "minstack.py",
        "test": "test_minstack.py",
        "test_src": (
            "import pytest\n"
            "from minstack import MinStack\n\n"
            "def test_empty_raises():\n"
            "    s = MinStack()\n"
            "    for call in [s.pop, s.top, s.get_min]:\n"
            "        with pytest.raises(IndexError):\n"
            "            call()\n"
            "def test_min_tracks_pops():\n"
            "    s = MinStack()\n"
            "    s.push(2); s.push(0); s.push(3); s.push(0)\n"
            "    assert s.get_min() == 0\n"
            "    assert s.pop() == 0\n"
            "    assert s.get_min() == 0\n"
            "    assert s.pop() == 3\n"
            "    assert s.get_min() == 0\n"
            "    assert s.pop() == 0\n"
            "    assert s.get_min() == 2\n"
            "    assert s.top() == 2\n"
            "    assert s.pop() == 2\n"
            "    with pytest.raises(IndexError):\n"
            "        s.get_min()\n"
            "def test_increasing():\n"
            "    s = MinStack()\n"
            "    for x in [5, 6, 7]:\n"
            "        s.push(x)\n"
            "    assert s.get_min() == 5\n"
            "    s.pop()\n"
            "    assert s.get_min() == 5\n"
        ),
    },
    # Naive rejected: Counter.most_common(k) — ties fall back to insertion order, not alphabetical.
    {
        "id": "top_k_words",
        "prompt": (
            "Create topk.py with top_k(words: list, k: int) -> list. Return the k most frequent "
            "words, most frequent first; words with the same frequency are ordered alphabetically "
            "(ascending, case-sensitive normal string order). If k exceeds the number of distinct "
            "words return them all; return [] when k <= 0 or when `words` is empty. "
            "top_k(['b','a'], 2) -> ['a','b']."
        ),
        "files": {},
        "verify": "topk.py",
        "test": "test_topk.py",
        "test_src": (
            "from topk import top_k\n\n"
            "def test_frequency_order():\n"
            "    ws = ['i','love','leetcode','i','love','coding']\n"
            "    assert top_k(ws, 2) == ['i','love']\n"
            "def test_ties_are_alphabetical():\n"
            "    assert top_k(['b','a'], 2) == ['a','b']\n"
            "    ws = ['the','day','is','sunny','the','the','the','sunny','is','is']\n"
            "    assert top_k(ws, 4) == ['the','is','sunny','day']\n"
            "def test_k_bigger_than_distinct():\n"
            "    assert top_k(['x','y','x'], 9) == ['x','y']\n"
            "def test_edges():\n"
            "    assert top_k([], 3) == []\n"
            "    assert top_k(['x'], 0) == []\n"
            "    assert top_k(['x'], -2) == []\n"
        ),
    },
    # Naive rejected: {**a, **b} — a shallow merge that drops a's nested keys.
    {
        "id": "deep_merge_dicts",
        "prompt": (
            "Create merge.py with deep_merge(a: dict, b: dict) -> dict. Return a NEW dict holding "
            "b merged into a: where both hold a dict at the same key, merge those recursively; "
            "otherwise b's value wins. Lists are values, not containers to merge — b's list simply "
            "replaces a's. Neither input, at any depth, may be modified, and no nested dict of "
            "either input may be shared with the result. deep_merge({'x':{'y':1,'z':2}}, "
            "{'x':{'z':3,'w':4}}) -> {'x': {'y':1,'z':3,'w':4}}."
        ),
        "files": {},
        "verify": "merge.py",
        "test": "test_merge.py",
        "test_src": (
            "from merge import deep_merge\n\n"
            "def test_recursive_merge():\n"
            "    out = deep_merge({'x': {'y': 1, 'z': 2}}, {'x': {'z': 3, 'w': 4}})\n"
            "    assert out == {'x': {'y': 1, 'z': 3, 'w': 4}}\n"
            "def test_deeper():\n"
            "    a = {'p': {'q': {'r': 1, 's': 2}}, 'k': 0}\n"
            "    b = {'p': {'q': {'s': 9}}, 'j': 5}\n"
            "    assert deep_merge(a, b) == {'p': {'q': {'r': 1, 's': 9}}, 'k': 0, 'j': 5}\n"
            "def test_type_clash_b_wins():\n"
            "    assert deep_merge({'x': {'y': 1}}, {'x': 5}) == {'x': 5}\n"
            "    assert deep_merge({'x': 1}, {'x': {'y': 2}}) == {'x': {'y': 2}}\n"
            "    assert deep_merge({'l': [1, 2]}, {'l': [3]}) == {'l': [3]}\n"
            "def test_inputs_untouched_and_unshared():\n"
            "    a = {'x': {'y': 1}}\n"
            "    b = {'x': {'z': 2}}\n"
            "    out = deep_merge(a, b)\n"
            "    assert a == {'x': {'y': 1}}\n"
            "    assert b == {'x': {'z': 2}}\n"
            "    assert out['x'] is not a['x']\n"
            "    assert out['x'] is not b['x']\n"
            "    out['x']['y'] = 99\n"
            "    assert a == {'x': {'y': 1}}\n"
            "def test_empty():\n"
            "    assert deep_merge({}, {}) == {}\n"
            "    assert deep_merge({'a': 1}, {}) == {'a': 1}\n"
        ),
    },
    # Naive rejected: the idle-slot formula without the max(len(tasks), ...) clamp.
    {
        "id": "task_cooldown",
        "prompt": (
            "Create taskrun.py with min_time(tasks: list, n: int) -> int. `tasks` is a list of "
            "single-letter task names. Each time unit runs one task or idles, and two runs of the "
            "SAME task must be at least n time units apart. Return the minimum number of time units "
            "needed to run every task. min_time(['A','A','A','B','B','B'], 2) -> 8; the same tasks "
            "with n=0 -> 6; min_time([], 2) -> 0. n is never negative."
        ),
        "files": {},
        "verify": "taskrun.py",
        "test": "test_taskrun.py",
        "test_src": (
            "from taskrun import min_time\n\n"
            "def test_classic():\n"
            "    assert min_time(['A','A','A','B','B','B'], 2) == 8\n"
            "    assert min_time(['A','A','A','B','B','B'], 0) == 6\n"
            "    assert min_time(['A','A','A','B','B','B'], 50) == 104\n"
            "def test_no_idle_needed():\n"
            "    assert min_time(['A','B','C','D','A','B'], 2) == 6\n"
            "    assert min_time(['A','B','C','D','E','A','B','C','D','E'], 4) == 10\n"
            "def test_small():\n"
            "    assert min_time([], 2) == 0\n"
            "    assert min_time(['A'], 5) == 1\n"
            "    assert min_time(['A','A'], 3) == 5\n"
        ),
    },
]
