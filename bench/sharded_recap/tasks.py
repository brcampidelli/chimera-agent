"""H7 corpus: 32 small Python function tasks, each fully specified by five shards.

Frozen with PREREGISTRATION.md. Shard 0 is the goal and names the function; shards 1-4 each add one
requirement (an edge case, a parameter, a return format or an error behaviour). Every hidden test is
tagged with the shard whose requirement it checks, so a failing final answer can be read as "lost an
early requirement" or "missed the last one".

Two solutions per task exist only for the $0 grader preflight (`run.py --check`):
- ``reference`` must pass every test (the grader runs first, PROTOCOL "standing rules");
- ``naive`` follows shard 0 alone and must fail at least one later-shard test (the tests can see a
  requirement being dropped, which is the loss the recap is meant to prevent).

Every expected value follows from the text of the shards (Bee §2n: derivable from the request).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Task:
    id: str
    names: tuple[str, ...]
    shards: tuple[str, ...]
    tests: tuple[tuple[int, str], ...]
    reference: str
    naive: str
    setup: str = field(default="")


TASKS: tuple[Task, ...] = (
    Task(
        id="t01_parse_duration",
        names=("parse_duration",),
        shards=(
            'Write a Python function `parse_duration(text)` that converts a duration such as "1h30m" into a whole number of seconds, returned as an int.',
            'The units are d, h, m and s (days, hours, minutes, seconds). They may appear in any order, and there may be spaces between the parts, as in "2m 5s".',
            'A number on its own, with no unit at all, means minutes, not seconds: "90" is 5400.',
            'Units are case-insensitive, so "1H" is the same as "1h".',
            "If the text is empty, is only whitespace, or contains anything that cannot be parsed, raise ValueError.",
        ),
        tests=(
            (0, 'assert parse_duration("1h30m") == 5400'),
            (0, 'assert isinstance(parse_duration("1h30m"), int)'),
            (1, 'assert parse_duration("2m 5s") == 125'),
            (1, 'assert parse_duration("5s2m") == 125'),
            (1, 'assert parse_duration("1d") == 86400'),
            (2, 'assert parse_duration("90") == 5400'),
            (3, 'assert parse_duration("1H30M") == 5400'),
            (4, 'assert raises(ValueError, parse_duration, "")'),
            (4, 'assert raises(ValueError, parse_duration, "   ")'),
            (4, 'assert raises(ValueError, parse_duration, "5x")'),
            (4, 'assert raises(ValueError, parse_duration, "abc")'),
        ),
        reference='''
import re
def parse_duration(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty duration")
    t = text.strip()
    if t.isdigit():
        return int(t) * 60
    compact = re.sub(r"\\s+", "", t).lower()
    if not re.fullmatch(r"(?:\\d+[dhms])+", compact):
        raise ValueError("cannot parse")
    factor = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    return sum(int(n) * factor[u] for n, u in re.findall(r"(\\d+)([dhms])", compact))
''',
        naive='''
import re
def parse_duration(text):
    total = 0
    for n, u in re.findall(r"(\\d+)([dhms])", text):
        total += int(n) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[u]
    return total
''',
    ),
    Task(
        id="t02_slugify",
        names=("slugify",),
        shards=(
            "Write a Python function `slugify(title)` that turns a title into a URL slug: lower-case words joined by hyphens.",
            "Any character that is not a letter or a digit is a separator, a run of separators becomes a single hyphen, and the slug never starts or ends with a hyphen.",
            'Accented letters lose their accents, so "Café Olé" becomes "cafe-ole".',
            "Add a keyword argument `max_length` with default 50. If the slug is longer than that, cut it at a hyphen so that no word is split, keeping as many whole words as fit; if even the first word is longer than max_length, cut that word to max_length characters.",
            'If nothing is left, for example when the title is only punctuation, return "untitled".',
        ),
        tests=(
            (0, 'assert slugify("Hello World") == "hello-world"'),
            (1, 'assert slugify("Hello, World!") == "hello-world"'),
            (1, 'assert slugify("  --Foo__Bar--  ") == "foo-bar"'),
            (1, 'assert slugify("Top 10 Tips") == "top-10-tips"'),
            (2, 'assert slugify("Café Olé") == "cafe-ole"'),
            (3, 'assert slugify("one two three", max_length=7) == "one-two"'),
            (3, 'assert slugify("one two three", max_length=6) == "one"'),
            (3, 'assert slugify("abcdefghij", max_length=4) == "abcd"'),
            (3, 'assert slugify("word " * 30) == "-".join(["word"] * 10)'),
            (4, 'assert slugify("!!!") == "untitled"'),
            (4, 'assert slugify("") == "untitled"'),
        ),
        reference='''
import re, unicodedata
def slugify(title, max_length=50):
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if len(s) > max_length:
        words = s.split("-")
        out = ""
        for w in words:
            cand = w if not out else out + "-" + w
            if len(cand) <= max_length:
                out = cand
            else:
                break
        s = out if out else words[0][:max_length]
    return s or "untitled"
''',
        naive='''
def slugify(title):
    return "-".join(title.lower().split())
''',
    ),
    Task(
        id="t03_chunk",
        names=("chunk",),
        shards=(
            "Write a Python function `chunk(items, size)` that splits a sequence into consecutive chunks of length `size` and returns them as a list of lists; the last chunk may be shorter.",
            "Add a keyword argument `pad` (default False). When pad is True, the last chunk is filled up to `size` with the value of another keyword argument, `fill`, which defaults to None.",
            "size must be a positive int. For zero, a negative number, or a bool, raise ValueError.",
            "It must accept any iterable, including generators, not only lists, and still return a list of lists.",
            "An empty input gives an empty list, even when pad is True.",
        ),
        tests=(
            (0, "assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]"),
            (0, "assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]"),
            (1, "assert chunk([1, 2, 3], 2, pad=True) == [[1, 2], [3, None]]"),
            (1, "assert chunk([1, 2, 3], 2, pad=True, fill=0) == [[1, 2], [3, 0]]"),
            (1, "assert chunk([1, 2, 3], 2, fill=0) == [[1, 2], [3]]"),
            (2, "assert raises(ValueError, chunk, [1], 0)"),
            (2, "assert raises(ValueError, chunk, [1], -1)"),
            (2, "assert raises(ValueError, chunk, [1], True)"),
            (3, "assert chunk((x for x in range(5)), 3) == [[0, 1, 2], [3, 4]]"),
            (3, "assert chunk(range(4), 2) == [[0, 1], [2, 3]]"),
            (4, "assert chunk([], 3, pad=True) == []"),
        ),
        reference='''
def chunk(items, size, pad=False, fill=None):
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError("size must be a positive int")
    out, cur = [], []
    for x in items:
        cur.append(x)
        if len(cur) == size:
            out.append(cur)
            cur = []
    if cur:
        if pad:
            cur = cur + [fill] * (size - len(cur))
        out.append(cur)
    return out
''',
        naive='''
def chunk(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]
''',
    ),
    Task(
        id="t04_roman",
        names=("to_roman", "from_roman"),
        shards=(
            "Write a Python function `to_roman(n)` that converts an integer to a Roman numeral string, using the standard subtractive forms such as IV for 4 and CM for 900.",
            "Only 1 to 3999 are valid; any other integer raises ValueError.",
            "Add a keyword argument `lower` (default False) that returns the numeral in lower case.",
            'Anything that is not an int, such as the float 3.0, the string "5" or a bool, raises TypeError.',
            'Add a companion function `from_roman(s)` that converts back. It accepts upper or lower case and raises ValueError for anything that is not a valid numeral in the standard form, so "IIII" and "VX" are rejected.',
        ),
        tests=(
            (0, 'assert to_roman(1994) == "MCMXCIV"'),
            (0, 'assert to_roman(4) == "IV"'),
            (0, 'assert to_roman(3999) == "MMMCMXCIX"'),
            (1, "assert raises(ValueError, to_roman, 0)"),
            (1, "assert raises(ValueError, to_roman, 4000)"),
            (1, "assert raises(ValueError, to_roman, -5)"),
            (2, 'assert to_roman(14, lower=True) == "xiv"'),
            (3, "assert raises(TypeError, to_roman, 3.0)"),
            (3, 'assert raises(TypeError, to_roman, "5")'),
            (3, "assert raises(TypeError, to_roman, True)"),
            (4, 'assert from_roman("MCMXCIV") == 1994'),
            (4, 'assert from_roman("xiv") == 14'),
            (4, 'assert raises(ValueError, from_roman, "IIII")'),
            (4, 'assert raises(ValueError, from_roman, "VX")'),
            (4, 'assert raises(ValueError, from_roman, "")'),
        ),
        reference='''
_VALS = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
         (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
def to_roman(n, lower=False):
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError("n must be an int")
    if not 1 <= n <= 3999:
        raise ValueError("out of range")
    out = []
    for v, s in _VALS:
        while n >= v:
            out.append(s)
            n -= v
    r = "".join(out)
    return r.lower() if lower else r
def from_roman(s):
    if not isinstance(s, str) or not s:
        raise ValueError("empty")
    u = s.upper()
    table = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    if any(c not in table for c in u):
        raise ValueError("bad numeral")
    total = 0
    for i, c in enumerate(u):
        v = table[c]
        if i + 1 < len(u) and table[u[i + 1]] > v:
            total -= v
        else:
            total += v
    if not 1 <= total <= 3999 or to_roman(total) != u:
        raise ValueError("not a standard numeral")
    return total
''',
        naive='''
def to_roman(n):
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
            (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out
''',
    ),
    Task(
        id="t05_top_words",
        names=("top_words",),
        shards=(
            "Write a Python function `top_words(text, n)` that returns the n most common words in a text, as a list of (word, count) tuples, most common first.",
            "Words are case-insensitive and made only of letters and apostrophes; every other character separates words. Return the words in lower case.",
            "When counts tie, order the tied words alphabetically.",
            "Ignore these stop words: the, a, an, and, or, of, to, in.",
            "If n is larger than the number of distinct words, return all of them; if n is 0 or negative, return an empty list.",
        ),
        tests=(
            (0, 'assert top_words("dog cat dog", 1) == [("dog", 2)]'),
            (1, 'assert top_words("Dog, DOG! cat", 2) == [("dog", 2), ("cat", 1)]'),
            (1, 'assert top_words("don\'t stop, don\'t", 2) == [("don\'t", 2), ("stop", 1)]'),
            (1, 'assert top_words("ab12cd ab", 5) == [("ab", 2), ("cd", 1)]'),
            (2, 'assert top_words("b d b c c", 2) == [("b", 2), ("c", 2)]'),
            (2, 'assert top_words("zeta alpha mid", 3) == [("alpha", 1), ("mid", 1), ("zeta", 1)]'),
            (3, 'assert top_words("The cat and the hat. The CAT sat!", 2) == [("cat", 2), ("hat", 1)]'),
            (4, 'assert top_words("x y", 10) == [("x", 1), ("y", 1)]'),
            (4, 'assert top_words("x y", 0) == []'),
            (4, 'assert top_words("x y", -1) == []'),
        ),
        reference='''
import re
from collections import Counter
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in"}
def top_words(text, n):
    if n <= 0:
        return []
    words = [w for w in re.findall(r"(?:[^\\W\\d_]|')+", text.lower()) if w not in _STOP]
    ranked = sorted(Counter(words).items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:n]
''',
        naive='''
from collections import Counter
def top_words(text, n):
    return Counter(text.split()).most_common(n)
''',
    ),
    Task(
        id="t06_merge_intervals",
        names=("merge_intervals",),
        shards=(
            "Write a Python function `merge_intervals(intervals)` that merges overlapping intervals given as (start, end) pairs.",
            "Intervals that only touch, like (1, 3) and (3, 5), count as overlapping and are merged.",
            "The input can be in any order. Return the merged intervals sorted by start, as a list of tuples.",
            "A pair whose start is greater than its end is reversed, so (5, 2) means the interval from 2 to 5.",
            "Never modify the input list.",
        ),
        tests=(
            (0, "assert merge_intervals([(1, 3), (2, 6), (8, 10)]) == [(1, 6), (8, 10)]"),
            (1, "assert merge_intervals([(1, 3), (3, 5)]) == [(1, 5)]"),
            (2, "assert merge_intervals([(8, 10), (1, 2)]) == [(1, 2), (8, 10)]"),
            (2, "assert merge_intervals([[1, 2], [2, 3]]) == [(1, 3)]"),
            (2, "assert merge_intervals([]) == []"),
            (3, "assert merge_intervals([(5, 2), (1, 3)]) == [(1, 5)]"),
            (3, "assert merge_intervals([(10, 8)]) == [(8, 10)]"),
            (4, "x = [(3, 4), (1, 2)]\nmerge_intervals(x)\nassert x == [(3, 4), (1, 2)]"),
            (4, "x = [[1, 5], [2, 3]]\nmerge_intervals(x)\nassert x == [[1, 5], [2, 3]]"),
        ),
        reference='''
def merge_intervals(intervals):
    norm = sorted((min(a, b), max(a, b)) for a, b in intervals)
    out = []
    for s, e in norm:
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out
''',
        naive='''
def merge_intervals(intervals):
    intervals.sort()
    out = []
    for s, e in intervals:
        if out and s < out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out
''',
    ),
    Task(
        id="t07_flatten",
        names=("flatten",),
        shards=(
            "Write a Python function `flatten(obj)` that flattens a nested list into a flat list.",
            "Tuples count as nesting too, but strings and bytes are never split into characters.",
            "Add a keyword argument `depth` (default None, meaning no limit) that flattens only that many levels; depth=0 returns a shallow copy of the top level as a list.",
            "If the argument itself is not a list or a tuple, return it wrapped in a list, so flatten(5) == [5].",
            "Dictionaries and sets are kept whole, as single items.",
        ),
        tests=(
            (0, "assert flatten([1, [2, [3, [4]]]]) == [1, 2, 3, 4]"),
            (1, 'assert flatten([1, (2, 3), "ab", b"cd"]) == [1, 2, 3, "ab", b"cd"]'),
            (1, "assert flatten((1, (2,))) == [1, 2]"),
            (2, "assert flatten([1, [2, [3]]], depth=1) == [1, 2, [3]]"),
            (2, "assert flatten([1, [2]], depth=0) == [1, [2]]"),
            (2, "x = [1, [2]]\ny = flatten(x, depth=0)\nassert y == x and y is not x"),
            (3, "assert flatten(5) == [5]"),
            (3, 'assert flatten("ab") == ["ab"]'),
            (4, 'assert flatten([{"a": 1}, [{"b": 2}]]) == [{"a": 1}, {"b": 2}]'),
            (4, "assert flatten([{1, 2}, [3]]) == [{1, 2}, 3]"),
        ),
        reference='''
def flatten(obj, depth=None):
    if not isinstance(obj, (list, tuple)):
        return [obj]
    out = []
    for item in obj:
        if isinstance(item, (list, tuple)) and (depth is None or depth > 0):
            out.extend(flatten(item, None if depth is None else depth - 1))
        else:
            out.append(item)
    return out
''',
        naive='''
def flatten(obj):
    out = []
    for x in obj:
        if isinstance(x, list):
            out.extend(flatten(x))
        else:
            out.append(x)
    return out
''',
    ),
    Task(
        id="t08_check_password",
        names=("check_password",),
        shards=(
            "Write a Python function `check_password(pw)` that checks a password and returns the list of rules it breaks; an empty list means the password is fine.",
            'The rules, with the exact strings to return: "too short" if it has fewer than 10 characters, "no digit" if it contains no digit, and "no upper" if it contains no upper-case letter.',
            'Also return "no symbol" when it contains no character other than letters, digits and whitespace.',
            'Also return "has space" when it contains any whitespace character.',
            "Always list the broken rules in this order: too short, no digit, no upper, no symbol, has space.",
        ),
        tests=(
            (0, 'assert check_password("Abcdefgh1!") == []'),
            (1, 'assert check_password("Ab1!") == ["too short"]'),
            (1, 'assert check_password("Abcdefghij!") == ["no digit"]'),
            (1, 'assert check_password("abcdefgh1!") == ["no upper"]'),
            (2, 'assert check_password("ABCDEFGHIJ1") == ["no symbol"]'),
            (3, 'assert check_password("Abc def 12!") == ["has space"]'),
            (3, 'assert check_password("Abcdefgh1!\\t") == ["has space"]'),
            (4, 'assert check_password("abcdefghij") == ["no digit", "no upper", "no symbol"]'),
            (4, 'assert check_password("") == ["too short", "no digit", "no upper", "no symbol"]'),
            (4, 'assert check_password("ab c") == ["too short", "no digit", "no upper", "no symbol", "has space"]'),
        ),
        reference='''
def check_password(pw):
    broken = []
    if len(pw) < 10:
        broken.append("too short")
    if not any(c.isdigit() for c in pw):
        broken.append("no digit")
    if not any(c.isupper() for c in pw):
        broken.append("no upper")
    if not any(not (c.isalnum() or c.isspace()) for c in pw):
        broken.append("no symbol")
    if any(c.isspace() for c in pw):
        broken.append("has space")
    return broken
''',
        naive='''
def check_password(pw):
    return []
''',
    ),
    Task(
        id="t09_rle",
        names=("rle", "unrle"),
        shards=(
            'Write a Python function `rle(text)` that run-length encodes a string by writing each run as its length followed by the character, leaving the length out when it is 1: "aaabcc" becomes "3ab2c".',
            'Runs can be longer than 9, so twelve a\'s encode as "12a".',
            "Add the inverse, `unrle(code)`, in the same code, so that unrle(rle(s)) == s.",
            "Digits in the input text cannot be encoded unambiguously, so rle raises ValueError if the text contains a digit.",
            "unrle raises ValueError on malformed input: a count with no character after it, or a count of 0.",
        ),
        tests=(
            (0, 'assert rle("aaabcc") == "3ab2c"'),
            (0, 'assert rle("") == ""'),
            (0, 'assert rle("xyz") == "xyz"'),
            (1, 'assert rle("a" * 12) == "12a"'),
            (2, 'assert unrle("3ab2c") == "aaabcc"'),
            (2, 'assert unrle("12a") == "a" * 12'),
            (2, 'assert unrle(rle("hello  world")) == "hello  world"'),
            (3, 'assert raises(ValueError, rle, "a1")'),
            (4, 'assert raises(ValueError, unrle, "3")'),
            (4, 'assert raises(ValueError, unrle, "0a")'),
            (4, 'assert raises(ValueError, unrle, "a00b")'),
        ),
        reference='''
from itertools import groupby
def rle(text):
    if any(c.isdigit() for c in text):
        raise ValueError("digits cannot be encoded")
    out = []
    for ch, grp in groupby(text):
        n = len(list(grp))
        out.append(f"{n}{ch}" if n > 1 else ch)
    return "".join(out)
def unrle(code):
    out = []
    i = 0
    while i < len(code):
        j = i
        while j < len(code) and code[j].isdigit():
            j += 1
        if j == len(code):
            raise ValueError("count with no character")
        count = int(code[i:j]) if j > i else 1
        if j > i and count == 0:
            raise ValueError("zero count")
        out.append(code[j] * count)
        i = j + 1
    return "".join(out)
''',
        naive='''
from itertools import groupby
def rle(text):
    out = []
    for ch, grp in groupby(text):
        n = len(list(grp))
        out.append(f"{n}{ch}" if n > 1 else ch)
    return "".join(out)
''',
    ),
    Task(
        id="t10_format_table",
        names=("format_table",),
        shards=(
            'Write a Python function `format_table(rows)` that turns a list of rows, each a list of values, into a plain-text table: one line per row, cells converted with str() and separated by " | ".',
            "Pad every column to the width of its widest cell.",
            "Numbers (int or float, but not bool) are right-aligned in their column; everything else is left-aligned.",
            "The first row is a header: put a line of '-' characters under it, as long as the longest line of the table, and no other separator lines.",
            "No line may end with a space, and the result has no trailing newline. An empty list of rows gives an empty string.",
        ),
        tests=(
            (0, 'assert format_table([["a", "b"]]).splitlines()[0] == "a | b"'),
            (1, 'assert format_table([["x", "y"], ["long", "z"]]).splitlines()[0] == "x    | y"'),
            (1, 'assert format_table([["x", "y"], ["long", "z"]]).splitlines()[2] == "long | z"'),
            (2, 'assert format_table([["n"], [5], [123]]).splitlines()[2] == "  5"'),
            (2, 'assert format_table([["v"], [1.5], [10.25]]).splitlines()[2] == "  1.5"'),
            (2, 'assert format_table([["flag", "v"], [True, 1]]).splitlines()[2] == "True | 1"'),
            (3, 'assert format_table([["name", "qty"], ["apple", 3]]).splitlines()[1] == "-" * 11'),
            (3, 'assert len(format_table([["h"], ["a"], ["b"]]).splitlines()) == 4'),
            (4, 'assert format_table([["a", "b"], ["ccc", "dd"]]) == "a   | b\\n--------\\nccc | dd"'),
            (4, "assert format_table([]) == \"\""),
            (4, 'assert not format_table([["a"]]).endswith("\\n")'),
            (4, 'assert format_table([["name", "qty"], ["apple", 3], ["kiwi", 12]]) == "name  | qty\\n-----------\\napple |   3\\nkiwi  |  12"'),
        ),
        reference='''
def format_table(rows):
    if not rows:
        return ""
    cells = [[str(v) for v in row] for row in rows]
    ncol = max(len(r) for r in rows)
    widths = [max((len(r[i]) for r in cells if i < len(r)), default=0) for i in range(ncol)]
    def is_num(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    lines = []
    for row, crow in zip(rows, cells):
        parts = []
        for i, (v, s) in enumerate(zip(row, crow)):
            parts.append(s.rjust(widths[i]) if is_num(v) else s.ljust(widths[i]))
        lines.append(" | ".join(parts).rstrip())
    dash = "-" * max(len(line) for line in lines)
    return "\\n".join([lines[0], dash] + lines[1:])
''',
        naive='''
def format_table(rows):
    return "\\n".join(" | ".join(str(v) for v in row) for row in rows)
''',
    ),
    Task(
        id="t11_parse_date",
        names=("parse_date",),
        shards=(
            "Write a Python function `parse_date(s)` that reads a date written as text and returns a datetime.date.",
            "Accept the ISO form 2024-03-05, and also day/month/year with slashes, 05/03/2024, which is the 5th of March: the day comes first.",
            "In the slash form the year may have two digits: 05/03/24 means 2024. Two-digit years 00 to 69 are 2000 to 2069, and 70 to 99 are 1970 to 1999.",
            "Ignore leading and trailing whitespace, and allow a single-digit day or month in the slash form, as in 5/3/2024.",
            "Anything else, including impossible dates such as 31/02/2024, raises ValueError.",
        ),
        tests=(
            (0, 'assert parse_date("2024-03-05") == date(2024, 3, 5)'),
            (1, 'assert parse_date("05/03/2024") == date(2024, 3, 5)'),
            (1, 'assert parse_date("12/01/2024") == date(2024, 1, 12)'),
            (2, 'assert parse_date("05/03/24") == date(2024, 3, 5)'),
            (2, 'assert parse_date("01/01/70") == date(1970, 1, 1)'),
            (2, 'assert parse_date("01/01/69") == date(2069, 1, 1)'),
            (3, 'assert parse_date("  5/3/2024 ") == date(2024, 3, 5)'),
            (4, 'assert raises(ValueError, parse_date, "31/02/2024")'),
            (4, 'assert raises(ValueError, parse_date, "2024/03/05")'),
            (4, 'assert raises(ValueError, parse_date, "")'),
            (4, 'assert raises(ValueError, parse_date, "5-3-2024")'),
        ),
        reference='''
import re
from datetime import date
def parse_date(s):
    t = s.strip()
    m = re.fullmatch(r"(\\d{4})-(\\d{2})-(\\d{2})", t)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\\d{1,2})/(\\d{1,2})/(\\d{2}|\\d{4})", t)
    if m:
        d, mo, y = int(m[1]), int(m[2]), m[3]
        if len(y) == 4:
            year = int(y)
        else:
            year = 2000 + int(y) if int(y) <= 69 else 1900 + int(y)
        return date(year, mo, d)
    raise ValueError("unrecognised date")
''',
        naive='''
from datetime import date
def parse_date(s):
    return date.fromisoformat(s)
''',
    ),
    Task(
        id="t12_to_snake",
        names=("to_snake",),
        shards=(
            "Write a Python function `to_snake(name)` that converts a camelCase or PascalCase identifier to snake_case.",
            'A run of capitals counts as one word: "parseHTTPResponse" becomes "parse_http_response" and "HTTPServer" becomes "http_server".',
            'Digits stay attached to the word before them: "version2Update" becomes "version2_update".',
            'Hyphens and spaces become underscores, and repeated underscores collapse into one: "my-var name" becomes "my_var_name".',
            'One leading underscore is kept, so "_privateVar" becomes "_private_var", and the result never ends with an underscore.',
        ),
        tests=(
            (0, 'assert to_snake("camelCase") == "camel_case"'),
            (0, 'assert to_snake("PascalCase") == "pascal_case"'),
            (0, 'assert to_snake("already_snake") == "already_snake"'),
            (1, 'assert to_snake("parseHTTPResponse") == "parse_http_response"'),
            (1, 'assert to_snake("HTTPServer") == "http_server"'),
            (2, 'assert to_snake("version2Update") == "version2_update"'),
            (3, 'assert to_snake("my-var name") == "my_var_name"'),
            (3, 'assert to_snake("a__b") == "a_b"'),
            (4, 'assert to_snake("_privateVar") == "_private_var"'),
            (4, 'assert to_snake("trailing_") == "trailing"'),
        ),
        reference='''
import re
def to_snake(name):
    lead = "_" if name.startswith("_") else ""
    s = re.sub(r"[-\\s]+", "_", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\\1_\\2", s)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\\1_\\2", s)
    s = re.sub(r"_+", "_", s.lower()).strip("_")
    return lead + s
''',
        naive='''
import re
def to_snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
''',
    ),
    Task(
        id="t13_spiral",
        names=("spiral",),
        shards=(
            "Write a Python function `spiral(matrix)` that returns the elements of a 2-D list (a list of rows) in clockwise spiral order, starting at the top-left corner and going right first.",
            "The matrix can be rectangular, not only square.",
            "Add a keyword argument `counterclockwise` (default False). When it is True, start at the top-left corner and go down the first column first, turning counterclockwise.",
            "An empty matrix, or one whose rows are all empty, gives an empty list.",
            "If the rows have different lengths, raise ValueError.",
        ),
        tests=(
            (0, "assert spiral([[1, 2, 3], [4, 5, 6], [7, 8, 9]]) == [1, 2, 3, 6, 9, 8, 7, 4, 5]"),
            (1, "assert spiral([[1, 2, 3, 4], [5, 6, 7, 8]]) == [1, 2, 3, 4, 8, 7, 6, 5]"),
            (1, "assert spiral([[1], [2], [3]]) == [1, 2, 3]"),
            (1, "assert spiral([[1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]]) == [1, 2, 3, 6, 9, 12, 11, 10, 7, 4, 5, 8]"),
            (2, "assert spiral([[1, 2], [3, 4]], counterclockwise=True) == [1, 3, 4, 2]"),
            (2, "assert spiral([[1, 2, 3], [4, 5, 6], [7, 8, 9]], counterclockwise=True) == [1, 4, 7, 8, 9, 6, 3, 2, 5]"),
            (2, "assert spiral([[1, 2, 3], [4, 5, 6]], counterclockwise=True) == [1, 4, 5, 6, 3, 2]"),
            (3, "assert spiral([]) == []"),
            (3, "assert spiral([[], []]) == []"),
            (4, "assert raises(ValueError, spiral, [[1, 2], [3]])"),
        ),
        reference='''
def spiral(matrix, counterclockwise=False):
    if matrix and len({len(r) for r in matrix}) > 1:
        raise ValueError("ragged matrix")
    if not matrix or not matrix[0]:
        return []
    m = [list(r) for r in matrix]
    if counterclockwise:
        m = [list(col) for col in zip(*m)]
    out = []
    top, bottom, left, right = 0, len(m) - 1, 0, len(m[0]) - 1
    while top <= bottom and left <= right:
        for j in range(left, right + 1):
            out.append(m[top][j])
        for i in range(top + 1, bottom + 1):
            out.append(m[i][right])
        if top < bottom:
            for j in range(right - 1, left - 1, -1):
                out.append(m[bottom][j])
        if left < right:
            for i in range(bottom - 1, top, -1):
                out.append(m[i][left])
        top, bottom, left, right = top + 1, bottom - 1, left + 1, right - 1
    return out
''',
        naive='''
def spiral(matrix):
    n = len(matrix)
    out = []
    for layer in range((n + 1) // 2):
        lo, hi = layer, n - 1 - layer
        for j in range(lo, hi + 1):
            out.append(matrix[lo][j])
        for i in range(lo + 1, hi + 1):
            out.append(matrix[i][hi])
        if lo < hi:
            for j in range(hi - 1, lo - 1, -1):
                out.append(matrix[hi][j])
            for i in range(hi - 1, lo, -1):
                out.append(matrix[i][lo])
    return out
''',
    ),
    Task(
        id="t14_format_money",
        names=("format_money",),
        shards=(
            'Write a Python function `format_money(amount)` that formats a number with a comma as thousands separator and exactly two decimals: 1234.5 becomes "1,234.50".',
            'Negative amounts are shown in parentheses instead of with a minus sign: -5 becomes "(5.00)".',
            'Round half up, not the way Python\'s round() does it: 2.675 must give "2.68" and 0.125 must give "0.13".',
            'Add keyword arguments `thousands` (default ",") and `decimal` (default ".") so it can do the European style: format_money(1234.5, thousands=".", decimal=",") gives "1.234,50".',
            "Accept int and decimal.Decimal as well as float; anything else, including bool and str, raises TypeError.",
        ),
        tests=(
            (0, 'assert format_money(1234.5) == "1,234.50"'),
            (0, 'assert format_money(1000000) == "1,000,000.00"'),
            (0, 'assert format_money(0) == "0.00"'),
            (1, 'assert format_money(-5) == "(5.00)"'),
            (1, 'assert format_money(-1234.567) == "(1,234.57)"'),
            (2, 'assert format_money(2.675) == "2.68"'),
            (2, 'assert format_money(0.125) == "0.13"'),
            (3, 'assert format_money(1234.5, thousands=".", decimal=",") == "1.234,50"'),
            (3, 'assert format_money(1234567.891, thousands=" ") == "1 234 567.89"'),
            (4, 'assert format_money(Decimal("10.005")) == "10.01"'),
            (4, 'assert raises(TypeError, format_money, "5")'),
            (4, "assert raises(TypeError, format_money, True)"),
        ),
        reference='''
from decimal import Decimal, ROUND_HALF_UP
def format_money(amount, thousands=",", decimal="."):
    if isinstance(amount, bool) or not isinstance(amount, (int, float, Decimal)):
        raise TypeError("amount must be int, float or Decimal")
    d = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    q = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    neg = q < 0
    whole, frac = f"{abs(q):.2f}".split(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    s = thousands.join(groups) + decimal + frac
    return f"({s})" if neg else s
''',
        naive='''
def format_money(amount):
    return f"{amount:,.2f}"
''',
    ),
    Task(
        id="t15_dedupe",
        names=("dedupe",),
        shards=(
            "Write a Python function `dedupe(items)` that removes duplicates from a list, keeping the first occurrence of each item and the original order.",
            "Add a keyword argument `key` (default None): a function, and two items count as duplicates when key(item) gives equal results.",
            "Items can be unhashable, such as lists or dicts, and it must still work; the same goes for the values returned by key.",
            "Return a new list and never modify the input.",
            'Add a keyword argument `keep` that is either "first" (the default) or "last"; with "last", keep the last occurrence of each item instead, still in the order those kept items appear in the input. Any other value raises ValueError.',
        ),
        tests=(
            (0, "assert dedupe([3, 1, 3, 2, 1]) == [3, 1, 2]"),
            (1, 'assert dedupe(["a", "A", "b"], key=str.lower) == ["a", "b"]'),
            (2, "assert dedupe([[1], [2], [1]]) == [[1], [2]]"),
            (2, 'assert dedupe([{"x": 1}, {"x": 1}]) == [{"x": 1}]'),
            (2, "assert dedupe([[1, 2], [1, 3]], key=lambda v: v[:1]) == [[1, 2]]"),
            (3, "x = [1, 1]\ndedupe(x)\nassert x == [1, 1]"),
            (3, "x = [1]\nassert dedupe(x) is not x"),
            (4, 'assert dedupe([3, 1, 3, 2, 1], keep="last") == [3, 2, 1]'),
            (4, 'assert dedupe(["a", "A", "b"], key=str.lower, keep="last") == ["A", "b"]'),
            (4, 'assert raises(ValueError, dedupe, [1], keep="middle")'),
        ),
        reference='''
def dedupe(items, key=None, keep="first"):
    if keep not in ("first", "last"):
        raise ValueError("keep must be 'first' or 'last'")
    seq = list(items)
    order = seq if keep == "first" else list(reversed(seq))
    seen_h, seen_u, out = set(), [], []
    for it in order:
        k = key(it) if key else it
        try:
            if k in seen_h:
                continue
            seen_h.add(k)
        except TypeError:
            if k in seen_u:
                continue
            seen_u.append(k)
        out.append(it)
    return out if keep == "first" else list(reversed(out))
''',
        naive='''
def dedupe(items):
    return list(dict.fromkeys(items))
''',
    ),
    Task(
        id="t16_bucket_counts",
        names=("bucket_counts",),
        shards=(
            "Write a Python function `bucket_counts(values, width)` that counts how many numbers fall into each bucket of the given width and returns a dict mapping each bucket's lower bound to its count.",
            "Buckets are aligned to multiples of width, starting from 0 and extending to negative numbers too: with width 10, 15 goes in bucket 10 and -3 goes in bucket -10.",
            "Include only buckets that received at least one value, and return the dict with its keys in increasing order.",
            "width must be positive, otherwise raise ValueError.",
            "Values that are None are skipped.",
        ),
        tests=(
            (0, "assert bucket_counts([1, 5, 12], 10) == {0: 2, 10: 1}"),
            (1, "assert bucket_counts([15, -3], 10) == {-10: 1, 10: 1}"),
            (1, "assert bucket_counts([10, 20], 10) == {10: 1, 20: 1}"),
            (1, "assert bucket_counts([1.2, 0.1], 0.5) == {0.0: 1, 1.0: 1}"),
            (2, "assert list(bucket_counts([25, 1, 12], 10)) == [0, 10, 20]"),
            (2, "assert bucket_counts([], 5) == {}"),
            (3, "assert raises(ValueError, bucket_counts, [1], 0)"),
            (3, "assert raises(ValueError, bucket_counts, [1], -2)"),
            (4, "assert bucket_counts([None, 3, None], 5) == {0: 1}"),
        ),
        reference='''
import math
def bucket_counts(values, width):
    if not width > 0:
        raise ValueError("width must be positive")
    counts = {}
    for v in values:
        if v is None:
            continue
        b = math.floor(v / width) * width
        counts[b] = counts.get(b, 0) + 1
    return dict(sorted(counts.items()))
''',
        naive='''
def bucket_counts(values, width):
    counts = {}
    for v in values:
        b = int(v / width) * width
        counts[b] = counts.get(b, 0) + 1
    return counts
''',
    ),
    Task(
        id="t17_wrap",
        names=("wrap",),
        shards=(
            "Write a Python function `wrap(text, width)` that wraps text into lines no longer than width characters and returns the list of lines.",
            "Break lines only at spaces. A single word longer than width goes on a line of its own, unbroken.",
            "Newlines already in the text are hard line breaks and must be kept; an empty line in the input stays an empty line in the output.",
            "Runs of spaces collapse into a single space, and no line starts or ends with a space.",
            "width must be at least 1, otherwise raise ValueError.",
        ),
        tests=(
            (0, 'assert wrap("the quick brown fox", 10) == ["the quick", "brown fox"]'),
            (0, 'assert wrap("ab cd", 2) == ["ab", "cd"]'),
            (1, 'assert wrap("a verylongword b", 5) == ["a", "verylongword", "b"]'),
            (2, 'assert wrap("one\\n\\ntwo", 10) == ["one", "", "two"]'),
            (2, 'assert wrap("aa bb\\ncc", 5) == ["aa bb", "cc"]'),
            (3, 'assert wrap("a   b    c", 10) == ["a b c"]'),
            (3, 'assert wrap("  lead", 10) == ["lead"]'),
            (4, 'assert raises(ValueError, wrap, "x", 0)'),
        ),
        reference='''
def wrap(text, width):
    if width < 1:
        raise ValueError("width must be at least 1")
    out = []
    for para in text.split("\\n"):
        words = para.split()
        if not words:
            out.append("")
            continue
        line = words[0]
        for w in words[1:]:
            if len(line) + 1 + len(w) <= width:
                line += " " + w
            else:
                out.append(line)
                line = w
        out.append(line)
    return out
''',
        naive='''
import textwrap
def wrap(text, width):
    return textwrap.wrap(text, width)
''',
    ),
    Task(
        id="t18_parse_query",
        names=("parse_query",),
        shards=(
            'Write a Python function `parse_query(qs)` that parses a URL query string such as "a=1&b=2" into a dict of strings.',
            "A leading '?' is allowed and ignored.",
            "Keys and values are percent-decoded, and '+' means a space.",
            "When a key appears more than once, its value becomes a list of all its values in order; a key that appears once keeps a plain string.",
            'A piece with no \'=\' is a key whose value is the empty string, and empty pieces, as in "a=1&&b=2", are skipped.',
        ),
        tests=(
            (0, 'assert parse_query("a=1&b=2") == {"a": "1", "b": "2"}'),
            (1, 'assert parse_query("?x=y") == {"x": "y"}'),
            (2, 'assert parse_query("q=hello+world%21") == {"q": "hello world!"}'),
            (2, 'assert parse_query("na%20me=a%26b") == {"na me": "a&b"}'),
            (3, 'assert parse_query("t=1&t=2&t=3") == {"t": ["1", "2", "3"]}'),
            (3, 'assert parse_query("t=1&u=2&t=3") == {"t": ["1", "3"], "u": "2"}'),
            (4, 'assert parse_query("flag&x=1") == {"flag": "", "x": "1"}'),
            (4, 'assert parse_query("a=1&&b=2") == {"a": "1", "b": "2"}'),
            (4, 'assert parse_query("") == {}'),
        ),
        reference='''
from urllib.parse import unquote_plus
def parse_query(qs):
    if qs.startswith("?"):
        qs = qs[1:]
    out = {}
    for piece in qs.split("&"):
        if not piece:
            continue
        k, _sep, v = piece.partition("=")
        k, v = unquote_plus(k), unquote_plus(v)
        if k in out:
            if isinstance(out[k], list):
                out[k].append(v)
            else:
                out[k] = [out[k], v]
        else:
            out[k] = v
    return out
''',
        naive='''
def parse_query(qs):
    return dict(p.split("=") for p in qs.split("&"))
''',
    ),
    Task(
        id="t19_summary",
        names=("summary",),
        shards=(
            'Write a Python function `summary(values)` that returns a dict with the mean and the median of a list of numbers, under the keys "mean" and "median".',
            'Also include "mode": the most common value; when several values are equally common, the smallest of them.',
            "Skip None and NaN values wherever they appear in the list.",
            "Round every number in the result to 2 decimal places.",
            "If no numbers are left to summarise, raise ValueError.",
        ),
        tests=(
            (0, 'assert summary([1, 2, 3, 4])["mean"] == 2.5'),
            (0, 'assert summary([1, 2, 3, 4])["median"] == 2.5'),
            (0, 'assert summary([3, 1, 2])["median"] == 2'),
            (1, 'assert summary([1, 2, 3, 4])["mode"] == 1'),
            (1, 'assert summary([3, 1, 2, 2])["mode"] == 2'),
            (2, 'r = summary([1, None, float("nan"), 3])\nassert (r["mean"], r["median"], r["mode"]) == (2.0, 2.0, 1)'),
            (3, 'r = summary([1 / 3])\nassert (r["mean"], r["median"], r["mode"]) == (0.33, 0.33, 0.33)'),
            (3, 'assert summary([1, 2, 2])["mean"] == 1.67'),
            (4, "assert raises(ValueError, summary, [])"),
            (4, 'assert raises(ValueError, summary, [None, float("nan")])'),
        ),
        reference='''
import math
from collections import Counter
def summary(values):
    nums = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not nums:
        raise ValueError("no numbers")
    s = sorted(nums)
    n = len(s)
    mean = sum(s) / n
    median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    counts = Counter(s)
    top = max(counts.values())
    mode = min(v for v, c in counts.items() if c == top)
    return {"mean": round(mean, 2), "median": round(median, 2), "mode": round(mode, 2)}
''',
        naive='''
import statistics
def summary(values):
    return {"mean": statistics.mean(values), "median": statistics.median(values)}
''',
    ),
    Task(
        id="t20_group_anagrams",
        names=("group_anagrams",),
        shards=(
            "Write a Python function `group_anagrams(words)` that groups words that are anagrams of each other and returns a list of groups, each group a list of words.",
            'The comparison ignores case and every character that is not a letter, so "Dormitory" and "dirty room!" are anagrams.',
            "Inside a group, keep the original words in their input order, and order the groups by where their first word appears in the input.",
            "Leave out groups that contain only one word.",
            "If exactly the same string appears more than once, keep it only once in its group.",
        ),
        tests=(
            (0, 'assert sorted(map(sorted, group_anagrams(["ab", "ba", "cd", "dc"]))) == [["ab", "ba"], ["cd", "dc"]]'),
            (1, 'assert group_anagrams(["Dormitory", "dirty room!"]) == [["Dormitory", "dirty room!"]]'),
            (2, 'assert group_anagrams(["listen", "google", "silent", "gooegl", "enlist"]) == [["listen", "silent", "enlist"], ["google", "gooegl"]]'),
            (3, 'assert group_anagrams(["a", "b"]) == []'),
            (3, 'assert group_anagrams(["abc", "def", "cab"]) == [["abc", "cab"]]'),
            (4, 'assert group_anagrams(["ab", "ba", "ab"]) == [["ab", "ba"]]'),
            (4, 'assert group_anagrams(["ab", "ab"]) == []'),
        ),
        reference='''
def group_anagrams(words):
    groups = {}
    for w in words:
        key = "".join(sorted(c.lower() for c in w if c.isalpha()))
        g = groups.setdefault(key, [])
        if w not in g:
            g.append(w)
    return [g for g in groups.values() if len(g) > 1]
''',
        naive='''
def group_anagrams(words):
    groups = {}
    for w in words:
        groups.setdefault("".join(sorted(w)), []).append(w)
    return list(groups.values())
''',
    ),
    Task(
        id="t21_balanced",
        names=("balanced",),
        shards=(
            "Write a Python function `balanced(text)` that returns True when the brackets in a string are balanced and False otherwise.",
            'There are three kinds of brackets, (), [] and {}, and they must be properly nested: "([)]" is not balanced.',
            "Brackets inside single-quoted or double-quoted strings do not count.",
            "A backslash escapes the next character: an escaped quote neither opens nor closes a string, and an escaped bracket does not count.",
            "A quote that is never closed makes the text unbalanced.",
        ),
        tests=(
            (0, 'assert balanced("(a[b]{c})") is True'),
            (0, 'assert balanced("(]") is False'),
            (1, 'assert balanced("([)]") is False'),
            (1, 'assert balanced("") is True'),
            (2, "assert balanced(\"'(' \") is True"),
            (2, "assert balanced('\"[\" + (x)') is True"),
            (3, 'assert balanced(r"\\( ") is True'),
            (3, 'assert balanced(r"(\\))") is True'),
            (3, "assert balanced(r\"'it\\'s (' \") is True"),
            (4, "assert balanced(\"'abc\") is False"),
        ),
        reference='''
def balanced(text):
    pairs = {")": "(", "]": "[", "}": "{"}
    stack = []
    quote = None
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\\\":
            i += 2
            continue
        if quote:
            if c == quote:
                quote = None
        elif c in "'\\"":
            quote = c
        elif c in "([{":
            stack.append(c)
        elif c in ")]}":
            if not stack or stack.pop() != pairs[c]:
                return False
        i += 1
    return quote is None and not stack
''',
        naive='''
def balanced(text):
    pairs = {")": "(", "]": "[", "}": "{"}
    stack = []
    for c in text:
        if c in "([{":
            stack.append(c)
        elif c in ")]}":
            if not stack or stack.pop() != pairs[c]:
                return False
    return not stack
''',
    ),
    Task(
        id="t22_normalize_phone",
        names=("normalize_phone",),
        shards=(
            'Write a Python function `normalize_phone(s)` that formats a Brazilian mobile number as "+55 11 91234-5678": country code, two-digit area code, then the number with a hyphen before its last four digits.',
            'The input may contain any mix of spaces, dots, dashes and parentheses, as in "(11) 91234.5678".',
            "The country code 55 may or may not be present, with or without a '+'.",
            'Landlines have 8 digits after the area code instead of 9 and are written as "+55 11 3123-4567".',
            "If what is left is not 10 or 11 digits once the country code is removed, or if the input contains letters, raise ValueError.",
        ),
        tests=(
            (0, 'assert normalize_phone("11912345678") == "+55 11 91234-5678"'),
            (1, 'assert normalize_phone("(11) 91234.5678") == "+55 11 91234-5678"'),
            (1, 'assert normalize_phone("11 91234-5678") == "+55 11 91234-5678"'),
            (2, 'assert normalize_phone("+55 11 91234-5678") == "+55 11 91234-5678"'),
            (2, 'assert normalize_phone("5511912345678") == "+55 11 91234-5678"'),
            (3, 'assert normalize_phone("1131234567") == "+55 11 3123-4567"'),
            (3, 'assert normalize_phone("+55 (21) 3123-4567") == "+55 21 3123-4567"'),
            (4, 'assert raises(ValueError, normalize_phone, "123")'),
            (4, 'assert raises(ValueError, normalize_phone, "11a12345678")'),
        ),
        reference='''
import re
def normalize_phone(s):
    if re.search(r"[A-Za-z]", s):
        raise ValueError("letters in phone number")
    digits = re.sub(r"\\D", "", s)
    if len(digits) in (12, 13) and digits.startswith("55"):
        digits = digits[2:]
    if len(digits) not in (10, 11):
        raise ValueError("wrong number of digits")
    area, num = digits[:2], digits[2:]
    return f"+55 {area} {num[:-4]}-{num[-4:]}"
''',
        naive='''
def normalize_phone(s):
    return f"+55 {s[:2]} {s[2:7]}-{s[7:]}"
''',
    ),
    Task(
        id="t23_get_path",
        names=("get_path",),
        shards=(
            'Write a Python function `get_path(data, path)` that reads a value from nested dicts using a dotted path such as "a.b.c".',
            'A part that is a whole number indexes into a list: "users.0.name".',
            "Add a keyword argument `default` (default None), returned whenever any part of the path is missing, instead of raising an error.",
            'Negative numbers index from the end, as in Python: "items.-1" is the last item.',
            "An empty path returns the data itself.",
        ),
        setup='D = {"a": {"b": {"c": 1}}, "users": [{"name": "ana"}, {"name": "bo"}], "items": [1, 2, 3]}',
        tests=(
            (0, 'assert get_path(D, "a.b.c") == 1'),
            (1, 'assert get_path(D, "users.0.name") == "ana"'),
            (1, 'assert get_path(D, "users.1.name") == "bo"'),
            (2, 'assert get_path(D, "a.x") is None'),
            (2, 'assert get_path(D, "a.x", default=5) == 5'),
            (2, 'assert get_path(D, "users.5.name", default="?") == "?"'),
            (2, 'assert get_path(D, "a.b.c.d") is None'),
            (2, 'assert get_path(D, "users.x") is None'),
            (3, 'assert get_path(D, "items.-1") == 3'),
            (4, 'assert get_path(D, "") is D'),
        ),
        reference='''
def get_path(data, path, default=None):
    if path == "":
        return data
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            if part in cur:
                cur = cur[part]
            else:
                return default
        elif isinstance(cur, (list, tuple)):
            try:
                idx = int(part)
            except ValueError:
                return default
            if -len(cur) <= idx < len(cur):
                cur = cur[idx]
            else:
                return default
        else:
            return default
    return cur
''',
        naive='''
def get_path(data, path):
    for k in path.split("."):
        data = data[k]
    return data
''',
    ),
    Task(
        id="t24_time_ago",
        names=("time_ago",),
        shards=(
            'Write a Python function `time_ago(seconds)` that describes an elapsed number of seconds in words, such as "5 minutes ago".',
            "Use the largest whole unit that fits, rounding down: seconds below a minute, minutes below an hour, hours below a day, and days from there on.",
            'Use the singular for one: "1 minute ago", not "1 minutes ago".',
            'Anything under 10 seconds is "just now".',
            'Negative numbers are in the future and read like "in 2 minutes"; the "just now" rule applies in both directions, to anything strictly between -10 and 10.',
        ),
        tests=(
            (0, 'assert time_ago(300) == "5 minutes ago"'),
            (1, 'assert time_ago(59) == "59 seconds ago"'),
            (1, 'assert time_ago(3599) == "59 minutes ago"'),
            (1, 'assert time_ago(7200) == "2 hours ago"'),
            (1, 'assert time_ago(86400 * 3 + 5) == "3 days ago"'),
            (2, 'assert time_ago(60) == "1 minute ago"'),
            (2, 'assert time_ago(3600) == "1 hour ago"'),
            (2, 'assert time_ago(90.7) == "1 minute ago"'),
            (3, 'assert time_ago(0) == "just now"'),
            (3, 'assert time_ago(9) == "just now"'),
            (3, 'assert time_ago(10) == "10 seconds ago"'),
            (4, 'assert time_ago(-120) == "in 2 minutes"'),
            (4, 'assert time_ago(-5) == "just now"'),
            (4, 'assert time_ago(-3600) == "in 1 hour"'),
            (4, 'assert time_ago(-10) == "in 10 seconds"'),
        ),
        reference='''
def time_ago(seconds):
    if -10 < seconds < 10:
        return "just now"
    future = seconds < 0
    s = abs(seconds)
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60), ("second", 1)):
        if s >= size:
            n = int(s // size)
            break
    label = unit if n == 1 else unit + "s"
    return f"in {n} {label}" if future else f"{n} {label} ago"
''',
        naive='''
def time_ago(seconds):
    return f"{int(seconds // 60)} minutes ago"
''',
    ),
    Task(
        id="t25_calc",
        names=("calc",),
        shards=(
            "Write a Python function `calc(expr)` that evaluates an arithmetic expression given as a string, with +, -, *, / and parentheses, without using eval or exec.",
            "Use the usual precedence: * and / before + and -, and left to right within the same level.",
            'Unary minus is allowed, as in "-3 + 5" and "2 * -(1 + 1)".',
            "Numbers may have decimals, such as 2.5. Return an int when the result is a whole number and a float otherwise.",
            "Division by zero and malformed expressions raise ValueError.",
        ),
        tests=(
            (0, 'assert calc("1 + 2") == 3'),
            (0, 'assert calc("(1 + 2) * 3") == 9'),
            (1, 'assert calc("1 + 2 * 3") == 7'),
            (1, 'assert calc("10 - 4 - 3") == 3'),
            (1, 'assert calc("8 / 4 / 2") == 1'),
            (2, 'assert calc("-3 + 5") == 2'),
            (2, 'assert calc("2 * -(1 + 1)") == -4'),
            (3, 'assert calc("7 / 2") == 3.5'),
            (3, 'r = calc("4 / 2")\nassert r == 2 and type(r) is int'),
            (3, 'r = calc("2.5 * 2")\nassert r == 5 and type(r) is int'),
            (4, 'assert raises(ValueError, calc, "1 / 0")'),
            (4, 'assert raises(ValueError, calc, "1 +")'),
            (4, 'assert raises(ValueError, calc, "(1")'),
            (4, 'assert raises(ValueError, calc, "2 3")'),
        ),
        reference='''
import re
def calc(expr):
    tokens = re.findall(r"\\d+\\.\\d+|\\d+|[-+*/()]|\\S", expr)
    pos = 0
    def peek():
        return tokens[pos] if pos < len(tokens) else None
    def take():
        nonlocal pos
        t = peek()
        pos += 1
        return t
    def parse_expr():
        v = parse_term()
        while peek() in ("+", "-"):
            op = take()
            r = parse_term()
            v = v + r if op == "+" else v - r
        return v
    def parse_term():
        v = parse_factor()
        while peek() in ("*", "/"):
            op = take()
            r = parse_factor()
            if op == "*":
                v = v * r
            else:
                if r == 0:
                    raise ValueError("division by zero")
                v = v / r
        return v
    def parse_factor():
        t = take()
        if t == "-":
            return -parse_factor()
        if t == "(":
            v = parse_expr()
            if take() != ")":
                raise ValueError("missing )")
            return v
        if t is not None and re.fullmatch(r"\\d+(\\.\\d+)?", t):
            return float(t) if "." in t else int(t)
        raise ValueError("unexpected token")
    if not tokens:
        raise ValueError("empty")
    v = parse_expr()
    if pos != len(tokens):
        raise ValueError("trailing input")
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return v
''',
        naive='''
def calc(expr):
    return eval(expr)
''',
    ),
    Task(
        id="t26_ranges",
        names=("ranges",),
        shards=(
            "Write a Python function `ranges(nums)` that summarises a list of integers as a compact string of ranges.",
            'Write a range as start..end and separate the items with commas and no spaces: [1, 2, 3, 5] gives "1..3,5".',
            "The input may be unsorted and may contain duplicates.",
            'Only three or more consecutive numbers make a range; a run of exactly two is written as two separate items: [1, 2] gives "1,2".',
            "An empty list gives an empty string.",
        ),
        tests=(
            (1, 'assert ranges([1, 2, 3, 5, 7, 8, 9]) == "1..3,5,7..9"'),
            (1, 'assert ranges([-3, -2, -1, 1]) == "-3..-1,1"'),
            (1, 'assert ranges([7]) == "7"'),
            (2, 'assert ranges([5, 3, 4, 1]) == "1,3..5"'),
            (2, 'assert ranges([1, 1, 2, 3, 3]) == "1..3"'),
            (3, 'assert ranges([1, 2]) == "1,2"'),
            (3, 'assert ranges([4, 5, 7, 8, 9]) == "4,5,7..9"'),
            (4, 'assert ranges([]) == ""'),
        ),
        reference='''
def ranges(nums):
    s = sorted(set(nums))
    out = []
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[j] + 1:
            j += 1
        if j - i >= 2:
            out.append(f"{s[i]}..{s[j]}")
        else:
            out.extend(str(x) for x in s[i:j + 1])
        i = j + 1
    return ",".join(out)
''',
        naive='''
def ranges(nums):
    out = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ", ".join(out)
''',
    ),
    Task(
        id="t27_parse_config",
        names=("parse_config",),
        shards=(
            "Write a Python function `parse_config(text)` that reads lines of the form key = value into a dict.",
            "Lines starting with '#' or ';' are comments, blank lines are skipped, and spaces around keys and values are trimmed.",
            'A line such as [section] starts a section, and the keys after it are stored as "section.key"; keys that come before any section have no prefix.',
            "Values true and false, in any letter case, become booleans, and values that are integers such as 42 or -7 become ints; everything else stays a string.",
            "A line that is not blank, not a comment, not a section header and has no '=' raises ValueError, and the error message must include the line number, counting from 1.",
        ),
        tests=(
            (0, 'assert parse_config("a = x\\nb=hello") == {"a": "x", "b": "hello"}'),
            (1, 'assert parse_config("# c\\n; c2\\n\\n  k  =  v  ") == {"k": "v"}'),
            (2, 'assert parse_config("x=y\\n[db]\\nhost = h") == {"x": "y", "db.host": "h"}'),
            (3, 'assert parse_config("flag = TRUE\\noff=false") == {"flag": True, "off": False}'),
            (3, 'assert parse_config("v = -7\\nw = 1.5\\nn = 42") == {"v": -7, "w": "1.5", "n": 42}'),
            (4, 'assert raises_msg(ValueError, "3", parse_config, "a=1\\n\\nbad line")'),
            (4, 'assert raises(ValueError, parse_config, "just words")'),
        ),
        reference='''
import re
def parse_config(text):
    out = {}
    section = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        m = re.fullmatch(r"\\[(.+)\\]", line)
        if m:
            section = m.group(1).strip()
            continue
        if "=" not in line:
            raise ValueError(f"line {n}: expected key = value")
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if v.lower() in ("true", "false"):
            val = v.lower() == "true"
        elif re.fullmatch(r"-?\\d+", v):
            val = int(v)
        else:
            val = v
        out[f"{section}.{k}" if section else k] = val
    return out
''',
        naive='''
def parse_config(text):
    out = {}
    for line in text.splitlines():
        k, v = line.split("=")
        out[k.strip()] = v.strip()
    return out
''',
    ),
    Task(
        id="t28_rotate",
        names=("rotate",),
        shards=(
            "Write a Python function `rotate(items, k)` that rotates a list to the right by k positions: rotate([1, 2, 3, 4], 1) gives [4, 1, 2, 3].",
            "A negative k rotates to the left.",
            "k can be larger than the length of the list.",
            "It also works on strings and tuples and returns the same type it was given.",
            "An empty sequence comes back unchanged whatever k is, and the input is never modified.",
        ),
        tests=(
            (0, "assert rotate([1, 2, 3, 4], 1) == [4, 1, 2, 3]"),
            (1, "assert rotate([1, 2, 3, 4], -1) == [2, 3, 4, 1]"),
            (2, "assert rotate([1, 2, 3], 5) == [2, 3, 1]"),
            (2, "assert rotate([1, 2, 3], -4) == [2, 3, 1]"),
            (3, 'assert rotate("abcd", 1) == "dabc"'),
            (3, "assert rotate((1, 2, 3), 1) == (3, 1, 2)"),
            (4, "assert rotate([], 3) == []"),
            (4, 'assert rotate("", -2) == ""'),
            (4, "x = [1, 2]\nrotate(x, 1)\nassert x == [1, 2]"),
        ),
        reference='''
def rotate(items, k):
    n = len(items)
    if n == 0:
        return items[:]
    k %= n
    return items[-k:] + items[:-k] if k else items[:]
''',
        naive='''
def rotate(items, k):
    items = list(items)
    return items[-k:] + items[:-k]
''',
    ),
    Task(
        id="t29_split_csv_line",
        names=("split_csv_line",),
        shards=(
            "Write a Python function `split_csv_line(line)` that splits one line of CSV text into a list of field strings.",
            "A field may be wrapped in double quotes, and inside the quotes a comma is part of the field.",
            "Inside a quoted field, two double quotes in a row stand for one literal double quote.",
            'Add a keyword argument `sep`, default ",", so other separators such as ";" can be used.',
            "Strip spaces around unquoted fields but never inside quotes, and raise ValueError when a quoted field is never closed.",
        ),
        tests=(
            (0, 'assert split_csv_line("a,b,c") == ["a", "b", "c"]'),
            (0, 'assert split_csv_line("a,,b") == ["a", "", "b"]'),
            (1, "assert split_csv_line('a,\"b,c\",d') == [\"a\", \"b,c\", \"d\"]"),
            (2, "assert split_csv_line('\"say \"\"hi\"\"\",x') == ['say \"hi\"', \"x\"]"),
            (3, 'assert split_csv_line("a;b;c", sep=";") == ["a", "b", "c"]'),
            (3, 'assert split_csv_line("a,b;c", sep=";") == ["a,b", "c"]'),
            (4, 'assert split_csv_line(" a , b ") == ["a", "b"]'),
            (4, "assert split_csv_line('\" a \",b') == [\" a \", \"b\"]"),
            (4, "assert raises(ValueError, split_csv_line, '\"abc,d')"),
        ),
        reference='''
def split_csv_line(line, sep=","):
    fields = []
    i = 0
    n = len(line)
    while True:
        j = i
        while j < n and line[j] == " ":
            j += 1
        if j < n and line[j] == '"':
            j += 1
            buf = []
            while True:
                if j >= n:
                    raise ValueError("unterminated quoted field")
                if line[j] == '"':
                    if j + 1 < n and line[j + 1] == '"':
                        buf.append('"')
                        j += 2
                        continue
                    j += 1
                    break
                buf.append(line[j])
                j += 1
            while j < n and line[j] == " ":
                j += 1
            fields.append("".join(buf))
            if j >= n:
                break
            if line[j] != sep:
                raise ValueError("unexpected character after quoted field")
            i = j + 1
        else:
            k = line.find(sep, i)
            if k == -1:
                fields.append(line[i:].strip(" "))
                break
            fields.append(line[i:k].strip(" "))
            i = k + 1
    return fields
''',
        naive='''
def split_csv_line(line):
    return line.split(",")
''',
    ),
    Task(
        id="t30_compare_versions",
        names=("compare_versions",),
        shards=(
            'Write a Python function `compare_versions(a, b)` that compares two version strings such as "1.2.10" and "1.2.9" and returns -1 if a is lower, 0 if they are equal and 1 if a is higher.',
            "Compare part by part as numbers, so 1.10 is higher than 1.9.",
            'Missing parts count as zero: "1.2" equals "1.2.0".',
            'A leading "v" or "V" is allowed and ignored: "v1.2" equals "1.2".',
            'A pre-release suffix after a hyphen, as in "1.2.0-beta", makes a version lower than the same version without a suffix; two suffixed versions with the same numbers compare their suffixes as plain strings.',
        ),
        tests=(
            (0, 'assert compare_versions("1.2.10", "1.2.9") == 1'),
            (0, 'assert compare_versions("1.0", "1.0") == 0'),
            (1, 'assert compare_versions("1.9", "1.10") == -1'),
            (2, 'assert compare_versions("1.2", "1.2.0") == 0'),
            (2, 'assert compare_versions("1.2.1", "1.2") == 1'),
            (3, 'assert compare_versions("v1.2", "1.2") == 0'),
            (3, 'assert compare_versions("V2", "1.9.9") == 1'),
            (4, 'assert compare_versions("1.2.0-beta", "1.2.0") == -1'),
            (4, 'assert compare_versions("1.2.0", "1.2.0-rc1") == 1'),
            (4, 'assert compare_versions("1.2.0-alpha", "1.2.0-beta") == -1'),
            (4, 'assert compare_versions("1.2-beta", "1.2.0-beta") == 0'),
        ),
        reference='''
def compare_versions(a, b):
    def parse(v):
        v = v.strip()
        if v[:1] in ("v", "V"):
            v = v[1:]
        core, _sep, pre = v.partition("-")
        return [int(x) for x in core.split(".")], pre
    (na, pa), (nb, pb) = parse(a), parse(b)
    width = max(len(na), len(nb))
    na += [0] * (width - len(na))
    nb += [0] * (width - len(nb))
    if na != nb:
        return -1 if na < nb else 1
    if pa == pb:
        return 0
    if not pa:
        return 1
    if not pb:
        return -1
    return -1 if pa < pb else 1
''',
        naive='''
def compare_versions(a, b):
    return (a > b) - (a < b)
''',
    ),
    Task(
        id="t31_final_grade",
        names=("final_grade",),
        shards=(
            "Write a Python function `final_grade(scores)` that takes a dict mapping assignment names to scores from 0 to 100 and computes the average score.",
            "Add a keyword argument `weights`: a dict mapping names to weights, where names missing from it get weight 1. Use the weighted average.",
            "Drop the single lowest score before averaging, but only when there are at least 4 scores.",
            "Return a tuple (average rounded to 1 decimal place, letter), where the letter is A from 90, B from 80, C from 70, D from 60 and F below 60, judged on the rounded average.",
            "A score below 0 or above 100 raises ValueError, and so does an empty dict.",
        ),
        tests=(
            (0, 'assert final_grade({"a": 90, "b": 80})[0] == 85.0'),
            (1, 'assert final_grade({"a": 100, "b": 50}, weights={"a": 3})[0] == 87.5'),
            (2, 'assert final_grade({"a": 10, "b": 90, "c": 90, "d": 90})[0] == 90.0'),
            (2, 'assert final_grade({"a": 10, "b": 90, "c": 90})[0] == 63.3'),
            (2, 'assert final_grade({"a": 10, "b": 90, "c": 90, "d": 90}, weights={"a": 5}) == (90.0, "A")'),
            (3, 'assert final_grade({"a": 90, "b": 80}) == (85.0, "B")'),
            (3, 'assert final_grade({"a": 89.96, "b": 89.96}) == (90.0, "A")'),
            (3, 'assert final_grade({"a": 59.9}) == (59.9, "F")'),
            (4, 'assert raises(ValueError, final_grade, {"a": 101})'),
            (4, 'assert raises(ValueError, final_grade, {"a": -1})'),
            (4, "assert raises(ValueError, final_grade, {})"),
        ),
        reference='''
def final_grade(scores, weights=None):
    if not scores:
        raise ValueError("no scores")
    for v in scores.values():
        if v < 0 or v > 100:
            raise ValueError("score out of range")
    weights = weights or {}
    items = list(scores.items())
    if len(items) >= 4:
        items.remove(min(items, key=lambda kv: kv[1]))
    total_w = sum(weights.get(k, 1) for k, _v in items)
    avg = sum(v * weights.get(k, 1) for k, v in items) / total_w
    r = round(avg, 1)
    if r >= 90:
        letter = "A"
    elif r >= 80:
        letter = "B"
    elif r >= 70:
        letter = "C"
    elif r >= 60:
        letter = "D"
    else:
        letter = "F"
    return (r, letter)
''',
        naive='''
def final_grade(scores):
    return sum(scores.values()) / len(scores)
''',
    ),
    Task(
        id="t32_unique_name",
        names=("unique_name",),
        shards=(
            'Write a Python function `unique_name(name, existing)` that returns a file name that is not in the collection `existing`: the name itself if it is free, otherwise the name with a counter, such as "a (1).txt" for "a.txt".',
            'The counter goes before the extension, and only the last dot starts the extension: "archive.tar.gz" becomes "archive.tar (1).gz". A name with no extension gets the counter at the end: "notes (1)".',
            "Use the smallest free counter, starting from 1.",
            'If the name is taken and already ends in a counter, like "a (2).txt", count from its base name "a.txt" (trying "a (1).txt", "a (2).txt" and so on) instead of producing "a (2) (1).txt".',
            "Compare names with `existing` ignoring letter case, but keep the case of the name you were given.",
        ),
        tests=(
            (0, 'assert unique_name("a.txt", []) == "a.txt"'),
            (0, 'assert unique_name("a.txt", ["a.txt"]) == "a (1).txt"'),
            (1, 'assert unique_name("notes", {"notes"}) == "notes (1)"'),
            (1, 'assert unique_name("archive.tar.gz", ["archive.tar.gz"]) == "archive.tar (1).gz"'),
            (2, 'assert unique_name("a.txt", ["a.txt", "a (1).txt", "a (3).txt"]) == "a (2).txt"'),
            (3, 'assert unique_name("a (2).txt", ["a (2).txt"]) == "a (1).txt"'),
            (3, 'assert unique_name("a (2).txt", ["a (2).txt", "a (1).txt"]) == "a (3).txt"'),
            (4, 'assert unique_name("A.TXT", ["a.txt"]) == "A (1).TXT"'),
            (4, 'assert unique_name("a.txt", ["A.TXT", "a (1).TXT"]) == "a (2).txt"'),
        ),
        reference='''
import re
def unique_name(name, existing):
    taken = {e.lower() for e in existing}
    if name.lower() not in taken:
        return name
    dot = name.rfind(".")
    stem, ext = (name[:dot], name[dot:]) if dot > 0 else (name, "")
    m = re.fullmatch(r"(.*) \\((\\d+)\\)", stem)
    if m:
        stem = m.group(1)
    i = 1
    while f"{stem} ({i}){ext}".lower() in taken:
        i += 1
    return f"{stem} ({i}){ext}"
''',
        naive='''
import os
def unique_name(name, existing):
    if name not in existing:
        return name
    base, ext = os.path.splitext(name)
    i = 1
    while f"{base} ({i}){ext}" in existing:
        i += 1
    return f"{base} ({i}){ext}"
''',
    ),
)

#: The pilot runs every task; kept as a name so the pre-registration can point at it.
PILOT_TASKS = TASKS
