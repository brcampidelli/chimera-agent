"""Weak-model-lift task set — the *parsing + string/format handling* domain (28 tasks).

Sibling of :mod:`tasks` (the 15 pre-registered originals), authored for the n=100 paired re-run under
``PREREGISTRATION.md``. Same contract: each task is a dict with ``id``, ``prompt`` (all the agent is
told — it never sees the test), ``files`` (starter workspace, usually empty), ``verify`` (the file the
task must produce), and ``test``/``test_src`` (the strict pytest that is ground truth, re-run
independently by the runner).

Selection is by the pre-registered **a-priori difficulty spec only** — every task needs >=2
non-obvious steps or has edge cases a naive implementation misses (empty input, boundaries,
malformed input, ordering, unicode, escaping, nesting). No task here was piloted against any arm;
none was authored, tuned or kept because of how a model performed on it. Doing so would fabricate
the result the suite exists to measure.

The bite is deliberately in the *rules*, not in the algorithms: half-up rounding where an f-string
gives banker's, decimal-vs-binary size units, escaping that a chain of ``.replace()`` gets wrong in
the wrong order, quoting that survives a ``split()``, and error cases a happy-path regex sails past.
Each ships a test that a correct reference solution passes and a plausible naive one fails.

Tests are stdlib-only, deterministic (no network, no randomness, no clock/timezone, no filesystem
outside the task's own workspace) and run in well under a second each. ``test_src`` uses
triple-quoted literals rather than :mod:`tasks`-style implicit concatenation because these tests are
escape-dense, and a mis-escaped ground truth is the one bug this file cannot afford.
"""

from __future__ import annotations

from typing import Any

TASKS_PARSING: list[dict[str, Any]] = [
    {
        "id": "query_string_parse",
        "prompt": (
            "Create qs.py with parse_qs(query: str) -> dict. It parses a URL query string into a "
            "dict mapping each key to the LIST of its values (in order). Rules: pairs are separated "
            "by '&'; the key and value are separated by the FIRST '=' (a later '=' is part of the "
            "value); a pair with no '=' has the value ''; '+' decodes to a space; '%XX' is a "
            "percent-escape decoding to that byte, and the resulting bytes are decoded as UTF-8 "
            "(both the key and the value are decoded); empty segments — from '&&' or a leading or "
            "trailing '&' — are skipped; a repeated key appends to its existing list. An empty "
            "query returns {}."
        ),
        "files": {},
        "verify": "qs.py",
        "test": "test_qs_parse.py",
        "test_src": '''from qs import parse_qs


def test_basic_and_repeats():
    assert parse_qs('a=1&b=2') == {'a': ['1'], 'b': ['2']}
    assert parse_qs('a=1&a=2') == {'a': ['1', '2']}
    assert parse_qs('') == {}


def test_blank_values_kept():
    assert parse_qs('a') == {'a': ['']}
    assert parse_qs('a=&b') == {'a': [''], 'b': ['']}
    assert parse_qs('&a=1&&') == {'a': ['1']}


def test_decoding():
    assert parse_qs('q=hello+world%21') == {'q': ['hello world!']}
    assert parse_qs('k=a%3Db%26c') == {'k': ['a=b&c']}
    assert parse_qs('n=caf%C3%A9') == {'n': ['caf\\u00e9']}
    assert parse_qs('a%20b=1') == {'a b': ['1']}


def test_first_equals_splits():
    assert parse_qs('x=1=2') == {'x': ['1=2']}
''',
    },
    {
        "id": "query_string_build",
        "prompt": (
            "Create qsbuild.py with build_qs(params: dict) -> str, the serialiser for a query "
            "string. Emit keys in the mapping's iteration order, joined by '&'. A list or tuple "
            "value emits one 'key=value' pair per element, in order. A value of None emits the bare "
            "key with no '='. True and False emit 'true' and 'false'. Any other value uses str(). "
            "Percent-encode both keys and values: every character except the unreserved ASCII set "
            "[A-Za-z0-9-._~] becomes '%XX' per UTF-8 byte with UPPERCASE hex — in particular a "
            "space encodes as '%20', not '+'. build_qs({}) returns ''."
        ),
        "files": {},
        "verify": "qsbuild.py",
        "test": "test_qs_build.py",
        "test_src": '''from qsbuild import build_qs


def test_basic():
    assert build_qs({'a': '1', 'b': '2'}) == 'a=1&b=2'
    assert build_qs({}) == ''
    assert build_qs({'a': [1, 2]}) == 'a=1&a=2'


def test_encoding():
    assert build_qs({'q': 'hello world'}) == 'q=hello%20world'
    assert build_qs({'k': 'a=b&c'}) == 'k=a%3Db%26c'
    assert build_qs({'n': 'caf\\u00e9'}) == 'n=caf%C3%A9'
    assert build_qs({'a b': 'c'}) == 'a%20b=c'


def test_unreserved_untouched():
    assert build_qs({'s': '~a-b._c'}) == 's=~a-b._c'


def test_none_and_bools():
    assert build_qs({'f': None}) == 'f'
    assert build_qs({'ok': True, 'no': False}) == 'ok=true&no=false'
''',
    },
    {
        "id": "duration_parse",
        "prompt": (
            "Create durparse.py with parse_duration(s: str) -> int returning the total number of "
            "seconds. The format is one or more '<number><unit>' chunks with no separators between "
            "them, where the units are w=weeks, d=days, h=hours, m=minutes, s=seconds — for example "
            "'1h30m', '2d4h30m15s', '90s'. The number is a non-negative integer of one or more "
            "digits. Units must appear in strictly descending order of size and each at most once. "
            "Whitespace around the whole string is ignored. Raise ValueError for anything else, "
            "including: the empty or whitespace-only string, an unknown unit, a repeated or "
            "out-of-order unit, a unit with no number, a number with no unit, a non-integer number, "
            "a negative number, and any trailing garbage."
        ),
        "files": {},
        "verify": "durparse.py",
        "test": "test_duration_parse.py",
        "test_src": '''import pytest

from durparse import parse_duration


def test_valid():
    assert parse_duration('1h30m') == 5400
    assert parse_duration('90s') == 90
    assert parse_duration('2d4h30m15s') == 189015
    assert parse_duration('1w') == 604800
    assert parse_duration(' 5m ') == 300
    assert parse_duration('0s') == 0
    assert parse_duration('10h5s') == 36005


def test_invalid_raises():
    bad = ['', '   ', 'h', '90', '1x', '1.5h', '-5m', '1h30', 'abc', '30m1h', '1h1h', '5s5s']
    for s in bad:
        with pytest.raises(ValueError):
            parse_duration(s)
''',
    },
    {
        "id": "duration_format",
        "prompt": (
            "Create durfmt.py with format_duration(seconds: int) -> str, the human-readable "
            "rendering of a duration. Decompose the value into weeks, days, hours, minutes and "
            "seconds; emit ONLY the non-zero units, largest first; join them with ', ' except the "
            "last two, which are joined with ' and '. Each unit is written as '<n> <word>' with the "
            "word pluralised by an 's' when n != 1 ('1 hour', '2 hours'). Zero returns 'now'. A "
            "negative value raises ValueError."
        ),
        "files": {},
        "verify": "durfmt.py",
        "test": "test_duration_format.py",
        "test_src": '''import pytest

from durfmt import format_duration


def test_small():
    assert format_duration(0) == 'now'
    assert format_duration(1) == '1 second'
    assert format_duration(120) == '2 minutes'
    assert format_duration(3600) == '1 hour'


def test_joining():
    assert format_duration(62) == '1 minute and 2 seconds'
    assert format_duration(3662) == '1 hour, 1 minute and 2 seconds'
    assert format_duration(3601) == '1 hour and 1 second'
    assert format_duration(90000) == '1 day and 1 hour'
    assert format_duration(1000000) == '1 week, 4 days, 13 hours, 46 minutes and 40 seconds'


def test_negative_raises():
    with pytest.raises(ValueError):
        format_duration(-1)
''',
    },
    {
        "id": "version_compare",
        "prompt": (
            "Create semver.py with compare(a: str, b: str) -> int returning -1, 0 or 1 (a<b, a==b, "
            "a>b). A version is 'MAJOR.MINOR.PATCH' with an optional '-prerelease' and an optional "
            "'+build'. Compare the three core fields numerically, in order. Build metadata is "
            "ignored entirely. A version WITH a prerelease is lower than the same version without "
            "one. A prerelease is dot-separated identifiers compared left to right: an identifier "
            "of only digits compares numerically and ranks BELOW an alphanumeric one; alphanumeric "
            "identifiers compare by ASCII order; if all compared identifiers are equal, the "
            "prerelease with FEWER identifiers is lower. Raise ValueError if either argument is not "
            "a valid version: missing core fields, non-numeric core fields, leading zeros in a core "
            "field, or an empty prerelease/identifier."
        ),
        "files": {},
        "verify": "semver.py",
        "test": "test_semver_compare.py",
        "test_src": '''import pytest

from semver import compare


def test_core():
    assert compare('1.0.0', '1.0.1') == -1
    assert compare('1.2.3', '1.2.3') == 0
    assert compare('2.0.0', '1.9.9') == 1
    assert compare('1.10.0', '1.9.0') == 1


def test_prerelease():
    assert compare('1.0.0-alpha', '1.0.0') == -1
    assert compare('1.0.0', '1.0.0-alpha') == 1
    assert compare('1.0.0-alpha', '1.0.0-alpha.1') == -1
    assert compare('1.0.0-alpha.1', '1.0.0-alpha.beta') == -1
    assert compare('1.0.0-beta.2', '1.0.0-beta.11') == -1
    assert compare('1.0.0-rc.1', '1.0.0') == -1


def test_build_ignored():
    assert compare('1.0.0+build1', '1.0.0+build2') == 0
    assert compare('1.0.0+x', '1.0.1+x') == -1


def test_invalid_raises():
    for bad in ['1.0', 'a.b.c', '1.0.0-', '', '01.0.0', '1.0.0-alpha..1', '1.0.0.0']:
        with pytest.raises(ValueError):
            compare(bad, '1.0.0')
''',
    },
    {
        "id": "shell_split",
        "prompt": (
            "Create shsplit.py with split_command(s: str) -> list. Tokenise a POSIX-ish command "
            "line. Rules: tokens are separated by runs of spaces and tabs; a single-quoted section "
            "'...' is literal (no escapes inside it); inside a double-quoted section \"...\" a "
            "backslash escapes ONLY a following '\"' or '\\' (before any other character the "
            "backslash stays literal), and spaces are preserved; outside quotes a backslash escapes "
            "the next character literally; quoted and unquoted parts that touch form ONE token "
            "(a\"b\"c is the single token abc); a quoted empty string produces an empty token. "
            "Return [] for an empty or whitespace-only string. Raise ValueError on an unclosed "
            "quote or on a trailing lone backslash."
        ),
        "files": {},
        "verify": "shsplit.py",
        "test": "test_shell_split.py",
        "test_src": r'''import pytest

from shsplit import split_command


def test_plain():
    assert split_command('ls -la') == ['ls', '-la']
    assert split_command('') == []
    assert split_command('   ') == []
    assert split_command('  a   b  ') == ['a', 'b']


def test_quotes():
    assert split_command("echo 'hello world'") == ['echo', 'hello world']
    assert split_command('echo "a b"') == ['echo', 'a b']
    assert split_command('a"b"c') == ['abc']
    assert split_command("echo ''") == ['echo', '']


def test_escapes():
    assert split_command('echo a\\ b') == ['echo', 'a b']
    assert split_command('echo "say \\"hi\\""') == ['echo', 'say "hi"']
    assert split_command("echo '\\n'") == ['echo', '\\n']
    assert split_command('echo "a\\nb"') == ['echo', 'a\\nb']


def test_errors():
    for bad in ['echo "unclosed', "echo 'x", 'echo a\\']:
        with pytest.raises(ValueError):
            split_command(bad)
''',
    },
    {
        "id": "justify_text",
        "prompt": (
            "Create justify.py with justify(words: list, width: int) -> list. Greedily pack the "
            "words into lines: a line holds as many words as fit when joined by at least one space "
            "each. Every returned line is EXACTLY `width` characters. A full line is "
            "fully-justified — the extra spaces are spread as evenly as possible between the words, "
            "and when they do not divide evenly the LEFTMOST gaps each take one more. The LAST "
            "line, and any line holding a single word, is left-justified with single spaces and "
            "padded with trailing spaces to `width`. Assume every word is at most `width` long. "
            "justify([], width) returns []."
        ),
        "files": {},
        "verify": "justify.py",
        "test": "test_justify.py",
        "test_src": '''from justify import justify


def test_canonical():
    words = ['This', 'is', 'an', 'example', 'of', 'text', 'justification.']
    assert justify(words, 16) == ['This    is    an', 'example  of text', 'justification.  ']


def test_uneven_gaps_go_left():
    words = ['What', 'must', 'be', 'acknowledgment', 'shall', 'be']
    assert justify(words, 16) == ['What   must   be', 'acknowledgment  ', 'shall be        ']


def test_edges():
    assert justify([], 5) == []
    assert justify(['a'], 3) == ['a  ']
    assert justify(['a', 'b'], 3) == ['a b']
''',
    },
    {
        "id": "log_line_parse",
        "prompt": (
            "Create logline.py with parse_line(line: str) -> dict. A log line has the exact shape "
            "'YYYY-MM-DD HH:MM:SS [LEVEL] logger: message' — for example '2024-01-02 10:00:00 "
            "[ERROR] db.pool: connection failed'. Return a dict with the keys 'date', 'time', "
            "'level', 'logger' and 'message'. The message is everything after the first ': ' that "
            "follows the logger, and may itself contain ':', '[' and ']'; a single trailing newline "
            "is removed but the message is not otherwise altered. The date and time must have "
            "exactly those digit shapes (zero-padded), and the level must be one of DEBUG, INFO, "
            "WARNING, ERROR, CRITICAL. Raise ValueError if the line does not match."
        ),
        "files": {},
        "verify": "logline.py",
        "test": "test_log_line.py",
        "test_src": '''import pytest

from logline import parse_line


def test_message_keeps_punctuation():
    got = parse_line('2024-01-02 10:00:00 [ERROR] db.pool: connection failed: timeout after 30s\\n')
    assert got == {
        'date': '2024-01-02',
        'time': '10:00:00',
        'level': 'ERROR',
        'logger': 'db.pool',
        'message': 'connection failed: timeout after 30s',
    }


def test_brackets_in_message():
    got = parse_line('2024-01-02 10:00:00 [INFO] api: got [200] from: upstream')
    assert got['level'] == 'INFO'
    assert got['logger'] == 'api'
    assert got['message'] == 'got [200] from: upstream'


def test_invalid_raises():
    bad = [
        'garbage',
        '',
        '2024-01-02 10:00:00 [TRACE] a: b',
        '2024-1-2 10:00:00 [INFO] a: b',
        '2024-01-02 10:00:00 INFO a: b',
        '2024-01-02 10:00:00 [INFO] nocolonmessage',
    ]
    for line in bad:
        with pytest.raises(ValueError):
            parse_line(line)
''',
    },
    {
        "id": "table_align",
        "prompt": (
            "Create table.py with render(rows: list) -> str, an ASCII table renderer. `rows` is a "
            "list of lists of strings; the first row is the header. Each column is as wide as its "
            "longest cell. Every cell is padded to its column's width and the cells are joined with "
            "' | ' — the padding is kept, so a left-aligned final column can end a line with "
            "spaces. Directly under the header comes a separator line: each "
            "column contributes as many '-' as its width, and the columns are joined with '-+-'. A "
            "column is RIGHT-aligned (header included) if every one of its non-header cells is a "
            "valid integer, optionally with a leading '-'; every other column is left-aligned. A "
            "row shorter than the header is padded with empty cells. Lines are joined by '\\n' with "
            "no trailing newline; render([]) returns ''."
        ),
        "files": {},
        "verify": "table.py",
        "test": "test_table_align.py",
        "test_src": '''from table import render


def test_numeric_column_right_aligned():
    out = render([['name', 'qty'], ['apple', '10'], ['kiwi', '3']])
    assert out == 'name  | qty\\n------+----\\napple |  10\\nkiwi  |   3'


def test_non_numeric_column_left_aligned():
    out = render([['k', 'v'], ['a', '10a'], ['bb', '3']])
    assert out == 'k  | v  \\n---+----\\na  | 10a\\nbb | 3  '


def test_ragged_and_empty():
    assert render([]) == ''
    out = render([['a', 'b'], ['x']])
    assert out == 'a | b\\n--+--\\nx |  '
''',
    },
    {
        "id": "slugify",
        "prompt": (
            "Create slug.py with slugify(text: str) -> str producing a URL slug. Steps: replace the "
            "German sharp s ('\\u00df') with 'ss'; unicode-normalise with NFKD and drop the "
            "combining marks so that accented letters lose their accent ('\\u00e9' becomes 'e'); "
            "lowercase; drop every character that is still not [a-z0-9] by turning each RUN of such "
            "characters into a single '-'; strip any leading and trailing '-'. A text with nothing "
            "slug-worthy returns ''."
        ),
        "files": {},
        "verify": "slug.py",
        "test": "test_slugify.py",
        "test_src": '''from slug import slugify


def test_basic():
    assert slugify('Hello, World!') == 'hello-world'
    assert slugify('a--b') == 'a-b'
    assert slugify('') == ''
    assert slugify('!!!') == ''


def test_unicode_folding():
    assert slugify('  Caf\\u00e9  au  lait  ') == 'cafe-au-lait'
    assert slugify('\\u00dcn\\u00efc\\u00f6d\\u00e9 \\u2014  Test') == 'unicode-test'
    assert slugify('Stra\\u00dfe') == 'strasse'
    assert slugify('\\u00c5NGSTR\\u00d6M 42') == 'angstrom-42'
''',
    },
    {
        "id": "camel_to_snake",
        "prompt": (
            "Create casing.py with to_snake(name: str) -> str converting camelCase/PascalCase to "
            "snake_case while handling acronym runs. Insert a '_' before an uppercase letter that "
            "is preceded by a lowercase letter or a digit; also insert a '_' before an uppercase "
            "letter that is preceded by an uppercase letter AND followed by a lowercase letter (so "
            "the last capital of an acronym run starts the next word). Then lowercase everything, "
            "collapse runs of '_' into one, and strip leading/trailing '_'. Examples: "
            "'getHTTPResponseCode' -> 'get_http_response_code'; 'HTTPResponse' -> 'http_response'; "
            "'userID' -> 'user_id'; 'utf8Decoder' -> 'utf8_decoder'."
        ),
        "files": {},
        "verify": "casing.py",
        "test": "test_camel_to_snake.py",
        "test_src": '''from casing import to_snake


def test_simple():
    assert to_snake('camelCase') == 'camel_case'
    assert to_snake('PascalCase') == 'pascal_case'
    assert to_snake('already_snake') == 'already_snake'


def test_acronyms():
    assert to_snake('getHTTPResponseCode') == 'get_http_response_code'
    assert to_snake('HTTPResponse') == 'http_response'
    assert to_snake('userID') == 'user_id'
    assert to_snake('HTML') == 'html'


def test_digits_and_edges():
    assert to_snake('utf8Decoder') == 'utf8_decoder'
    assert to_snake('A') == 'a'
    assert to_snake('') == ''
    assert to_snake('__mixed__Case__') == 'mixed_case'
''',
    },
    {
        "id": "title_case",
        "prompt": (
            "Create titlecase.py with title_case(text: str) -> str. Normalise whitespace (strip the "
            "ends, collapse runs of spaces to one), then capitalise each word: the first letter "
            "upper, the rest lower. EXCEPT: a minor word — one of a, an, and, as, at, but, by, for, "
            "in, nor, of, on, or, the, to, up, with — is written entirely lowercase, unless it is "
            "the FIRST or the LAST word of the text, which are always capitalised. A hyphenated "
            "word applies the same rule to each hyphen-separated part, where its first and last "
            "parts always capitalise ('state-of-the-art' -> 'State-of-the-Art'). An empty or "
            "whitespace-only text returns ''."
        ),
        "files": {},
        "verify": "titlecase.py",
        "test": "test_title_case.py",
        "test_src": '''from titlecase import title_case


def test_minor_words():
    assert title_case('the quick brown fox') == 'The Quick Brown Fox'
    assert title_case('a tale of two cities') == 'A Tale of Two Cities'
    assert title_case('THE LORD OF THE RINGS') == 'The Lord of the Rings'


def test_first_and_last_always_capitalised():
    assert title_case('what are you waiting for') == 'What Are You Waiting For'
    assert title_case('of mice and men') == 'Of Mice and Men'


def test_hyphens_and_whitespace():
    assert title_case('state-of-the-art design') == 'State-of-the-Art Design'
    assert title_case('  hello   world  ') == 'Hello World'
    assert title_case('') == ''
''',
    },
    {
        "id": "c_escape",
        "prompt": (
            "Create cescape.py with escape(s: str) -> str and unescape(s: str) -> str, for the body "
            "of a C-style string literal (no surrounding quotes). escape maps: a backslash to '\\\\', "
            "'\"' to '\\\"', a newline to '\\n', a tab to '\\t', a carriage return to '\\r', and any "
            "OTHER character whose code point is below 32 or equal to 127 to '\\xHH' with exactly "
            "two lowercase hex digits (each of those replacements is a literal backslash followed "
            "by the shown characters). Every other character, including non-ASCII, passes through "
            "unchanged. unescape is the exact inverse and also accepts uppercase hex digits; it "
            "raises ValueError on a trailing lone backslash, an unknown escape letter, or a "
            "malformed '\\x' escape (fewer than two hex digits)."
        ),
        "files": {},
        "verify": "cescape.py",
        "test": "test_c_escape.py",
        "test_src": r'''import pytest

from cescape import escape, unescape


def test_escape():
    assert escape('say "hi"') == 'say \\"hi\\"'
    assert escape('a\nb') == 'a\\nb'
    assert escape('back\\slash') == 'back\\\\slash'
    assert escape('\x00\x1f\x7f') == '\\x00\\x1f\\x7f'
    assert escape('café') == 'café'
    assert escape('') == ''


def test_unescape():
    assert unescape('\\x41') == 'A'
    assert unescape('\\x4a') == 'J'
    assert unescape('\\x4A') == 'J'
    assert unescape('a\\tb') == 'a\tb'


def test_roundtrip():
    for s in ['', 'plain', 'a"b\\c', 'x\ny\tz\r', '\x00\x01\x7f', 'café 中']:
        assert unescape(escape(s)) == s


def test_unescape_errors():
    for bad in ['abc\\', '\\q', '\\xZZ', '\\x4']:
        with pytest.raises(ValueError):
            unescape(bad)
''',
    },
    {
        "id": "json_string_decode",
        "prompt": (
            "Create jstr.py with decode(s: str) -> str, where s is the CONTENT of a JSON string "
            "literal (the surrounding quotes are not included). Resolve the escapes \\\" \\\\ \\/ "
            "\\b \\f \\n \\r \\t and \\uXXXX (exactly four hex digits, either case), including "
            "SURROGATE PAIRS: a high surrogate (D800-DBFF) immediately followed by a \\u low "
            "surrogate (DC00-DFFF) decodes to the single astral character they encode. Any other "
            "character passes through unchanged. Raise ValueError for: an unknown escape letter, a "
            "truncated \\u escape, a high surrogate not followed by a low surrogate, a lone low "
            "surrogate, a trailing lone backslash, and a raw control character below 0x20 (which "
            "JSON forbids unescaped)."
        ),
        "files": {},
        "verify": "jstr.py",
        "test": "test_json_string_decode.py",
        "test_src": r'''import pytest

from jstr import decode


def test_plain_and_escapes():
    assert decode('hello') == 'hello'
    assert decode('') == ''
    assert decode('a\\nb') == 'a\nb'
    assert decode('\\"q\\"') == '"q"'
    assert decode('\\/') == '/'
    assert decode('\\\\') == '\\'
    assert decode('café') == 'café'


def test_unicode_escapes():
    assert decode('\\u0041') == 'A'
    assert decode('\\u00e9') == 'é'
    assert decode('\\uD83D\\uDE00') == '\U0001f600'
    assert decode('x\\ud83d\\ude00y') == 'x\U0001f600y'


def test_errors():
    for bad in ['\\ud83d', '\\ude00', '\\u00', '\\q', 'abc\\', 'a\x01b', '\\ud83dz']:
        with pytest.raises(ValueError):
            decode(bad)
''',
    },
    {
        "id": "tokenize_expr",
        "prompt": (
            "Create lexer.py with tokenize(s: str) -> list returning a list of (kind, text) tuples. "
            "Kinds: 'num' for an unsigned number matching digits with an optional '.' and more "
            "digits ('3', '3.5'); 'name' for an identifier — a letter or '_' followed by letters, "
            "digits or '_'; 'op' for an operator, where the two-character operators '<=', '>=', "
            "'==', '!=', '**' and '//' are matched GREEDILY before the single-character ones "
            "+ - * / % < > = ( ) , ; and 'str' for a single-quoted string, whose token text is the "
            "string's CONTENT with a doubled quote ('') meaning one literal quote. Whitespace "
            "separates tokens and produces none of its own. Return [] for an empty or "
            "whitespace-only input. Raise ValueError on an unexpected character or an unterminated "
            "string."
        ),
        "files": {},
        "verify": "lexer.py",
        "test": "test_tokenize_expr.py",
        "test_src": '''import pytest

from lexer import tokenize


def test_basic():
    assert tokenize('a+1') == [('name', 'a'), ('op', '+'), ('num', '1')]
    assert tokenize('') == []
    assert tokenize('   ') == []


def test_multichar_ops_are_greedy():
    assert tokenize('x <= 3.5') == [('name', 'x'), ('op', '<='), ('num', '3.5')]
    assert tokenize('2**3') == [('num', '2'), ('op', '**'), ('num', '3')]
    assert tokenize('7//2') == [('num', '7'), ('op', '//'), ('num', '2')]
    assert tokenize('a==b!=c') == [
        ('name', 'a'), ('op', '=='), ('name', 'b'), ('op', '!='), ('name', 'c'),
    ]


def test_strings_and_calls():
    assert tokenize("f(x, 'it''s')") == [
        ('name', 'f'), ('op', '('), ('name', 'x'), ('op', ','),
        ('str', "it's"), ('op', ')'),
    ]
    assert tokenize("''") == [('str', '')]


def test_errors():
    for bad in ['a $ b', "'unterminated", 'a & b']:
        with pytest.raises(ValueError):
            tokenize(bad)
''',
    },
    {
        "id": "glob_path_match",
        "prompt": (
            "Create pglob.py with match(pattern: str, path: str) -> bool for path globbing, where "
            "'/' separates segments. Rules: '*' matches any run of characters WITHIN one segment "
            "and never crosses a '/'; '?' matches exactly one character other than '/'; a '**' that "
            "is a WHOLE segment matches zero or more segments (so 'src/**' matches 'src', and "
            "'**/*.py' matches 'a.py'); a character class '[abc]' or '[a-z]' matches one character "
            "from the set, and '[!...]' negates it — a class never matches '/'. Everything else is "
            "literal, and the whole path must match. Do not use fnmatch: its '*' crosses '/'."
        ),
        "files": {},
        "verify": "pglob.py",
        "test": "test_glob_path.py",
        "test_src": '''from pglob import match


def test_star_does_not_cross_slash():
    assert match('*.py', 'a.py') is True
    assert match('*.py', 'src/a.py') is False
    assert match('src/*.py', 'src/a.py') is True
    assert match('src/*', 'src/a/b') is False


def test_globstar():
    assert match('**/*.py', 'src/deep/a.py') is True
    assert match('**/*.py', 'a.py') is True
    assert match('src/**', 'src/a/b') is True
    assert match('src/**', 'src') is True
    assert match('src/**', 'other/a') is False


def test_question_and_classes():
    assert match('a?c', 'abc') is True
    assert match('a?c', 'a/c') is False
    assert match('[abc]x', 'bx') is True
    assert match('[abc]x', 'dx') is False
    assert match('[!abc]x', 'dx') is True
    assert match('[!abc]x', 'ax') is False
    assert match('[a-c]1', 'b1') is True
    assert match('[a-c]1', 'd1') is False
''',
    },
    {
        "id": "number_format",
        "prompt": (
            "Create numfmt.py with format_number(value, decimals: int = 2, thousands: str = ',', "
            "decimal: str = '.') -> str. `value` is an int, a float or a numeric string. Round it "
            "to `decimals` places using HALF-UP — a half always rounds away from zero, NOT Python's "
            "default banker's rounding, and not the binary artefacts of an f-string (2.675 at 2 "
            "places is '2.68'; 0.5 at 0 places is '1'; 2.5 at 0 places is '3'). Pad with trailing "
            "zeros to exactly `decimals` places, group the integer part in threes using the "
            "`thousands` separator, and use `decimal` as the decimal separator. decimals=0 emits no "
            "decimal separator at all. A negative value keeps a leading '-', except when the "
            "rounded result is entirely zero (-0.001 at 2 places is '0.00'). Raise ValueError for "
            "negative `decimals` or a non-numeric `value`."
        ),
        "files": {},
        "verify": "numfmt.py",
        "test": "test_number_format.py",
        "test_src": '''import pytest

from numfmt import format_number


def test_grouping():
    assert format_number(1234567.891) == '1,234,567.89'
    assert format_number(0) == '0.00'
    assert format_number(999.999) == '1,000.00'
    assert format_number('1234.5', 2, ' ', ',') == '1 234,50'


def test_half_up_not_bankers():
    assert format_number(2.675) == '2.68'
    assert format_number(0.5, 0) == '1'
    assert format_number(1.5, 0) == '2'
    assert format_number(2.5, 0) == '3'


def test_negatives():
    assert format_number(-1234.5, 1) == '-1,234.5'
    assert format_number(-0.001) == '0.00'
    assert format_number(-2.5, 0) == '-3'


def test_errors():
    with pytest.raises(ValueError):
        format_number(1, -1)
    with pytest.raises(ValueError):
        format_number('abc')
''',
    },
    {
        "id": "parse_size",
        "prompt": (
            "Create sizeparse.py with parse_size(s: str) -> int returning a count of bytes. The "
            "input is a number followed by an optional unit, with optional whitespace between them "
            "and around the whole string. The number may be a decimal like '1.5'. Units are "
            "case-insensitive. '' and 'b' mean bytes. 'k','kb','m','mb','g','gb','t','tb' are "
            "DECIMAL — powers of 1000. 'ki','kib','mi','mib','gi','gib','ti','tib' are BINARY — "
            "powers of 1024. So '1KB' is 1000 and '1KiB' is 1024. The result is floored to an int. "
            "Raise ValueError on: empty input, a missing number, an unknown unit, a negative "
            "number, more than one number, or trailing garbage."
        ),
        "files": {},
        "verify": "sizeparse.py",
        "test": "test_parse_size.py",
        "test_src": '''import pytest

from sizeparse import parse_size


def test_decimal_vs_binary():
    assert parse_size('1KB') == 1000
    assert parse_size('1KiB') == 1024
    assert parse_size('2mb') == 2000000
    assert parse_size('1.5 MiB') == 1572864
    assert parse_size(' 3 GiB ') == 3221225472


def test_plain_and_floor():
    assert parse_size('1024') == 1024
    assert parse_size('0b') == 0
    assert parse_size('1.5kb') == 1500
    assert parse_size('1.7b') == 1


def test_errors():
    for bad in ['', '   ', 'abc', 'MB', '-1KB', '1XB', '1 2 KB', '1KB extra']:
        with pytest.raises(ValueError):
            parse_size(bad)
''',
    },
    {
        "id": "format_size",
        "prompt": (
            "Create sizefmt.py with format_size(n: int, binary: bool = False) -> str, the "
            "human-readable rendering of a byte count. Choose the largest unit whose value is at "
            "least 1, from B, kB, MB, GB, TB, PB (powers of 1000) or, when binary=True, from B, "
            "KiB, MiB, GiB, TiB, PiB (powers of 1024). Bytes print as an integer with no decimals "
            "('999 B'); every other unit prints with EXACTLY one decimal place, rounded half-up on "
            "the exact ratio (1150 bytes is '1.2 kB', not '1.1'). A single space separates the "
            "number from the unit. A negative value keeps its '-' and picks the unit from the "
            "magnitude. A value beyond the largest unit stays in it. format_size(0) is '0 B'."
        ),
        "files": {},
        "verify": "sizefmt.py",
        "test": "test_format_size.py",
        "test_src": '''from sizefmt import format_size


def test_decimal_units():
    assert format_size(0) == '0 B'
    assert format_size(999) == '999 B'
    assert format_size(1000) == '1.0 kB'
    assert format_size(1500) == '1.5 kB'
    assert format_size(1000000) == '1.0 MB'


def test_binary_units():
    assert format_size(1023, binary=True) == '1023 B'
    assert format_size(1024, binary=True) == '1.0 KiB'
    assert format_size(1536, binary=True) == '1.5 KiB'
    assert format_size(1048576, binary=True) == '1.0 MiB'


def test_half_up_and_signs_and_overflow():
    assert format_size(1150) == '1.2 kB'
    assert format_size(-1500) == '-1.5 kB'
    assert format_size(-999) == '-999 B'
    assert format_size(1250000000000000000) == '1250.0 PB'
''',
    },
    {
        "id": "csv_serialize",
        "prompt": (
            "Create csvout.py with format_row(fields: list) -> str producing one RFC4180 CSV line. "
            "Each field is rendered with str(), except None which becomes an empty field. A field "
            "is wrapped in double quotes if and only if it contains a comma, a double quote, a "
            "carriage return or a line feed, OR it has leading or trailing whitespace; inside a "
            "quoted field every '\"' is doubled. Fields are joined with ','. format_row([]) is ''."
        ),
        "files": {},
        "verify": "csvout.py",
        "test": "test_csv_serialize.py",
        "test_src": '''from csvout import format_row


def test_unquoted():
    assert format_row(['a', 'b']) == 'a,b'
    assert format_row(['plain']) == 'plain'
    assert format_row([]) == ''
    assert format_row(['']) == ''
    assert format_row([1, 2.5, None]) == '1,2.5,'


def test_quoting_rules():
    assert format_row(['a,b', 'c']) == '"a,b",c'
    assert format_row(['he said "hi"']) == '"he said ""hi"""'
    assert format_row([' pad ']) == '" pad "'
    assert format_row(['a\\nb']) == '"a\\nb"'
    assert format_row(['a\\rb']) == '"a\\rb"'
''',
    },
    {
        "id": "markdown_table_parse",
        "prompt": (
            "Create mdtable.py with parse_table(text: str) -> dict having the keys 'headers' (list "
            "of str), 'aligns' (list of 'left', 'right' or 'center') and 'rows' (list of lists of "
            "str). The input is a GitHub-style pipe table: a header row, then an alignment row "
            "whose cells each match one or more '-' with an optional leading and/or trailing ':' "
            "(':--' is left, '--:' is right, ':-:' is center, '--' is left), then data rows. A "
            "leading and trailing '|' on a line is optional. Cells are stripped of surrounding "
            "whitespace. An escaped pipe '\\|' inside a cell is a literal '|' and does not split "
            "the cell. Blank lines are ignored. A data row with fewer cells than there are headers "
            "is padded with '', and extra cells are dropped. Raise ValueError if there are fewer "
            "than two non-blank lines, if the alignment row is malformed, or if its cell count "
            "differs from the header's."
        ),
        "files": {},
        "verify": "mdtable.py",
        "test": "test_md_table.py",
        "test_src": r'''import pytest

from mdtable import parse_table


def test_basic():
    got = parse_table('| a | b |\n|---|--:|\n| 1 | 2 |\n')
    assert got == {'headers': ['a', 'b'], 'aligns': ['left', 'right'], 'rows': [['1', '2']]}


def test_alignments_and_no_outer_pipes():
    got = parse_table('a | b | c\n:-- | :-: | --:\n1 | 2 | 3')
    assert got['aligns'] == ['left', 'center', 'right']
    assert got['rows'] == [['1', '2', '3']]


def test_escaped_pipe_and_padding():
    got = parse_table('| x | y |\n| --- | --- |\n\n| a \\| b | c |\n| only |\n| 1 | 2 | 3 |\n')
    assert got['headers'] == ['x', 'y']
    assert got['rows'] == [['a | b', 'c'], ['only', ''], ['1', '2']]


def test_errors():
    for bad in ['| a |', '', '| a |\n| xx |\n', '| a | b |\n|---|\n| 1 | 2 |\n']:
        with pytest.raises(ValueError):
            parse_table(bad)
''',
    },
    {
        "id": "ansi_strip",
        "prompt": (
            "Create ansi.py with strip_ansi(s: str) -> str removing ANSI escape sequences and "
            "keeping everything else. Remove: CSI sequences — ESC '[' then any number of parameter "
            "bytes (0x30-0x3F), then any number of intermediate bytes (0x20-0x2F), then one final "
            "byte (0x40-0x7E); OSC sequences — ESC ']' up to a terminating BEL (0x07) or ST "
            "(ESC followed by a backslash); and two-character sequences — ESC followed by one byte in 0x40-0x5F "
            "other than '[' and ']'. Anything that is not a complete sequence, including a lone "
            "trailing ESC, is left as-is. Note this is more than the colour ('m') sequences."
        ),
        "files": {},
        "verify": "ansi.py",
        "test": "test_ansi_strip.py",
        "test_src": r'''from ansi import strip_ansi


def test_colours():
    assert strip_ansi('\x1b[31mred\x1b[0m') == 'red'
    assert strip_ansi('\x1b[1;32mok\x1b[0m done') == 'ok done'


def test_non_colour_csi():
    assert strip_ansi('\x1b[2J\x1b[Hclear') == 'clear'
    assert strip_ansi('a\x1b[10Cb') == 'ab'


def test_osc_and_plain():
    assert strip_ansi('\x1b]0;title\x07text') == 'text'
    assert strip_ansi('\x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\') == 'link'
    assert strip_ansi('plain') == 'plain'
    assert strip_ansi('') == ''
    assert strip_ansi('a\x1b') == 'a\x1b'
''',
    },
    {
        "id": "cli_args_parse",
        "prompt": (
            "Create cliargs.py with parse_args(argv: list) -> dict returning {'flags': dict, "
            "'positional': list}. Rules, applied left to right: '--name=value' sets name to value "
            "(a further '=' belongs to the value); '--name' sets name to True, UNLESS the next item "
            "exists and does not start with '-', in which case that item is consumed as the value; "
            "'-abc' is a group of one-letter flags, each set to True (short flags never take a "
            "value); a bare '--' ends option parsing, and every remaining item is positional even "
            "if it starts with '-'; a lone '-' is positional; anything else is positional. A "
            "repeated flag keeps the LAST value. Raise ValueError for a long option with an empty "
            "name ('--=x') or an empty short group."
        ),
        "files": {},
        "verify": "cliargs.py",
        "test": "test_cli_args.py",
        "test_src": '''import pytest

from cliargs import parse_args


def test_empty_and_positional():
    assert parse_args([]) == {'flags': {}, 'positional': []}
    assert parse_args(['a', 'b']) == {'flags': {}, 'positional': ['a', 'b']}
    assert parse_args(['-']) == {'flags': {}, 'positional': ['-']}


def test_long_flags():
    assert parse_args(['--verbose']) == {'flags': {'verbose': True}, 'positional': []}
    assert parse_args(['--out', 'file.txt']) == {'flags': {'out': 'file.txt'}, 'positional': []}
    assert parse_args(['--out=file.txt']) == {'flags': {'out': 'file.txt'}, 'positional': []}
    assert parse_args(['--out=a=b']) == {'flags': {'out': 'a=b'}, 'positional': []}
    assert parse_args(['--n', '1', '--n', '2']) == {'flags': {'n': '2'}, 'positional': []}


def test_short_groups_and_lookahead():
    assert parse_args(['-abc']) == {'flags': {'a': True, 'b': True, 'c': True}, 'positional': []}
    assert parse_args(['--verbose', '-x']) == {
        'flags': {'verbose': True, 'x': True}, 'positional': [],
    }
    assert parse_args(['--verbose', '--out', 'x', 'pos']) == {
        'flags': {'verbose': True, 'out': 'x'}, 'positional': ['pos'],
    }


def test_terminator_and_errors():
    assert parse_args(['a', '--', '-b', '--c']) == {'flags': {}, 'positional': ['a', '-b', '--c']}
    with pytest.raises(ValueError):
        parse_args(['--=x'])
''',
    },
    {
        "id": "range_spec_parse",
        "prompt": (
            "Create ranges.py with parse_ranges(spec: str, maximum: int | None = None) -> list. "
            "Parse a spec like '1-3,5,7-9' into a SORTED list of unique ints. Items are separated "
            "by ','; whitespace around an item or around its numbers is ignored. 'a-b' is an "
            "inclusive range and requires a <= b. A trailing open range 'a-' extends up to "
            "`maximum` — if `maximum` is None that is an error. All numbers are integers >= 1. "
            "Duplicates and overlaps collapse. An empty or whitespace-only spec returns []. Raise "
            "ValueError on: a malformed or empty item ('a', '1-2-3', '-', '1-x', or the empty item "
            "in '1,,2'), a reversed range ('5-1'), "
            "a number below 1, an open range with no `maximum`, or any value greater than `maximum` "
            "when one is given."
        ),
        "files": {},
        "verify": "ranges.py",
        "test": "test_range_spec.py",
        "test_src": '''import pytest

from ranges import parse_ranges


def test_basic():
    assert parse_ranges('1-3,5,7-9') == [1, 2, 3, 5, 7, 8, 9]
    assert parse_ranges('5') == [5]
    assert parse_ranges('') == []
    assert parse_ranges('   ') == []


def test_sorting_dedupe_whitespace():
    assert parse_ranges('3,1,2') == [1, 2, 3]
    assert parse_ranges('1-3,2-4') == [1, 2, 3, 4]
    assert parse_ranges('2,2,2') == [2]
    assert parse_ranges(' 1 - 3 , 5 ') == [1, 2, 3, 5]


def test_open_range():
    assert parse_ranges('7-', maximum=9) == [7, 8, 9]
    assert parse_ranges('1,3-', maximum=4) == [1, 3, 4]
    with pytest.raises(ValueError):
        parse_ranges('7-')


def test_errors():
    for bad in ['a', '1-2-3', '-', '1-x', '5-1', '0', '1,,2']:
        with pytest.raises(ValueError):
            parse_ranges(bad)
    with pytest.raises(ValueError):
        parse_ranges('11', maximum=10)
''',
    },
    {
        "id": "dotenv_parse",
        "prompt": (
            "Create dotenv.py with parse_env(text: str) -> dict mapping str to str, parsing a .env "
            "file line by line. Rules: 'KEY=value'; an optional leading 'export ' is ignored; "
            "whitespace around the key and around an UNQUOTED value is stripped; a line that is "
            "blank or whose first non-space character is '#' is skipped; in an unquoted value a "
            "'#' that is preceded by whitespace starts a trailing comment, while a '#' with no "
            "space before it is part of the value; a single-quoted value is fully literal (no "
            "escapes, no comment handling); a double-quoted value resolves the escapes '\\n', "
            "'\\t', '\\\\' and '\\\"' and keeps any '#'; the value may be empty. A later "
            "definition of the same "
            "key wins. Raise ValueError for a non-comment line with no '=' or with an empty key."
        ),
        "files": {},
        "verify": "dotenv.py",
        "test": "test_dotenv_parse.py",
        "test_src": r'''import pytest

from dotenv import parse_env


def test_basic():
    assert parse_env('A=1\nB=2') == {'A': '1', 'B': '2'}
    assert parse_env('export A=1') == {'A': '1'}
    assert parse_env('A=') == {'A': ''}
    assert parse_env('A=1\nA=2') == {'A': '2'}
    assert parse_env('') == {}


def test_comments_and_stripping():
    assert parse_env('# c\n\nA = hello world  # trailing') == {'A': 'hello world'}
    assert parse_env('A=a#b') == {'A': 'a#b'}
    assert parse_env('   # only a comment\n') == {}


def test_quoting():
    assert parse_env("A='raw #1 \\n'") == {'A': 'raw #1 \\n'}
    assert parse_env('A="line\\nbreak # keep"') == {'A': 'line\nbreak # keep'}
    assert parse_env('A="say \\"hi\\""') == {'A': 'say "hi"'}


def test_errors():
    for bad in ['NOEQUALS', '=1', 'A=1\nBROKEN']:
        with pytest.raises(ValueError):
            parse_env(bad)
''',
    },
    {
        "id": "html_escape",
        "prompt": (
            "Create htmlesc.py with escape(s: str) -> str and unescape(s: str) -> str. escape "
            "replaces & < > \" and ' with &amp; &lt; &gt; &quot; and &#39; — and must not "
            "double-escape, so an already-produced entity is never re-processed. unescape reverses "
            "that in a SINGLE pass (so '&amp;lt;' becomes '&lt;', not '<') and also understands the "
            "named entities &amp; &lt; &gt; &quot; &apos; &nbsp; (&nbsp; is U+00A0) and the numeric "
            "references &#NN; in decimal and &#xHH; in hex (the 'x' and the digits are "
            "case-insensitive). An unknown or unterminated '&...' sequence is left exactly as it "
            "is."
        ),
        "files": {},
        "verify": "htmlesc.py",
        "test": "test_html_escape.py",
        "test_src": '''from htmlesc import escape, unescape


def test_escape():
    assert escape('<a href="x">') == '&lt;a href=&quot;x&quot;&gt;'
    assert escape('a & b') == 'a &amp; b'
    assert escape("it's") == 'it&#39;s'
    assert escape('<&>') == '&lt;&amp;&gt;'
    assert escape('plain') == 'plain'


def test_unescape_single_pass():
    assert unescape('&lt;a&gt;') == '<a>'
    assert unescape('&amp;lt;') == '&lt;'
    assert unescape('&amp;amp;') == '&amp;'


def test_unescape_entities():
    assert unescape('&#65;&#x41;&#X41;') == 'AAA'
    assert unescape('&nbsp;') == '\\xa0'
    assert unescape('&apos;') == "'"
    assert unescape('&unknown; &amp') == '&unknown; &amp'


def test_roundtrip():
    for s in ['', 'plain', 'a & <b> "c" \\'d\\'', '&amp;', '<&>']:
        assert unescape(escape(s)) == s
''',
    },
    {
        "id": "header_params_parse",
        "prompt": (
            "Create hparams.py with parse_header(value: str) -> tuple returning (main_value, "
            "params). Parse an HTTP header value such as 'text/html; charset=utf-8'. The main value "
            "is the text before the first ';' that is not inside quotes, stripped and LOWERCASED. "
            "Each following ';'-separated segment is a parameter: its name is stripped and "
            "lowercased, its value is stripped with its case PRESERVED; a segment with no '=' maps "
            "to ''; an empty segment (from ';;' or a trailing ';') is ignored. A double-quoted "
            "value keeps its content with the backslash escapes '\\\"' and '\\\\' resolved, and a "
            "';' inside the quotes does NOT split the segment. A repeated parameter name keeps the "
            "LAST value. parse_header('') is ('', {}). Raise ValueError on an unterminated quoted "
            "value."
        ),
        "files": {},
        "verify": "hparams.py",
        "test": "test_header_params.py",
        "test_src": r'''import pytest

from hparams import parse_header


def test_basic():
    assert parse_header('text/html; charset=utf-8') == ('text/html', {'charset': 'utf-8'})
    assert parse_header('') == ('', {})
    assert parse_header('text/plain') == ('text/plain', {})


def test_case_rules():
    assert parse_header('TEXT/HTML; CharSet=UTF-8') == ('text/html', {'charset': 'UTF-8'})


def test_quoted_values():
    assert parse_header('multipart/form-data; boundary="a;b"') == (
        'multipart/form-data', {'boundary': 'a;b'},
    )
    assert parse_header('attachment; filename="q\\"x.txt"') == (
        'attachment', {'filename': 'q"x.txt'},
    )


def test_empty_segments_and_dupes():
    assert parse_header('form-data;; name=a;') == ('form-data', {'name': 'a'})
    assert parse_header('inline; flag') == ('inline', {'flag': ''})
    assert parse_header('a; x=1; x=2') == ('a', {'x': '2'})


def test_unterminated_quote_raises():
    with pytest.raises(ValueError):
        parse_header('a; b="unterminated')
''',
    },
    {
        "id": "strip_comments",
        "prompt": (
            "Create decomment.py with strip_comments(src: str) -> str removing C/Java-style "
            "comments from source text. '//' starts a comment that runs to the end of the line, and "
            "the terminating newline is KEPT. '/* ... */' is a block comment that may span lines "
            "and is removed entirely, leaving nothing behind. Comment markers inside a "
            "double-quoted or single-quoted string literal are NOT comments; inside a string a "
            "backslash escapes the next character. Everything else is passed through byte for byte. "
            "Raise ValueError on an unterminated block comment or an unterminated string literal."
        ),
        "files": {},
        "verify": "decomment.py",
        "test": "test_strip_comments.py",
        "test_src": r'''import pytest

from decomment import strip_comments


def test_line_and_block():
    assert strip_comments('int a; // hi\nint b;') == 'int a; \nint b;'
    assert strip_comments('a /* x */ b') == 'a  b'
    assert strip_comments('a /* x\ny */ b') == 'a  b'
    assert strip_comments('') == ''
    assert strip_comments('no comments here') == 'no comments here'


def test_strings_are_not_comments():
    assert strip_comments('s = "http://x";') == 's = "http://x";'
    assert strip_comments("c = '/*';") == "c = '/*';"
    assert strip_comments('s = "a\\"//b";') == 's = "a\\"//b";'
    assert strip_comments('s = "/* not a comment */"; // gone') == 's = "/* not a comment */"; '


def test_errors():
    for bad in ['a /* b', 's = "abc', "c = 'x"]:
        with pytest.raises(ValueError):
            strip_comments(bad)
''',
    },
]
