"""A deterministic check of whether the spoken part of an answer can be read aloud as it stands.

Study 25, arm H9 (`PREREGISTRATION.md`). The voice mode reads an answer up to the line the model
was told to put between the spoken part and the screen part, and the screen shows everything. This
module looks only at what the voice would read — the text above that line, or the whole answer when
there is no line — and counts the things in it that do not survive being read aloud.

The line is found exactly the way the desktop reader finds it (`screenPartStart` in
`apps/desktop/src/lib/voice/speech-text.ts`): a Markdown rule alone on its line, `---`, `***` or
`___`, three or more marks, up to three leading spaces. A checker that cut somewhere else would be
measuring an answer nobody hears.

What counts, one key per kind so a reader can disagree with any one of them and recompute the rest:

* ``heading``, ``list``, ``table``, ``code_fence``, ``inline_code``, ``emphasis``, ``quote``,
  ``link`` — Markdown (the ``markdown`` group). Counted as marks the model wrote, not as what the
  reader does with them: the reader drops some marks and keeps others, and the question here is
  whether the model followed the standard, not whether the reader rescued it.
* ``emoji`` — pictographs and dingbats (U+1F000–U+1FAFF, U+2600–U+27BF and a few scattered ones),
  counted per code point. Arrows, dashes, ellipses and accented letters are not emoji.
* ``sentences`` — 1 when the spoken part holds more than four sentences. Counted per line, the way
  the streaming reader cuts pieces: a line holds at least one sentence if it holds any letter or
  digit, and one per terminal ``.``, ``!``, ``?`` or ``…`` followed by space or its end. A list of
  five bare items is five sentences, which is what it sounds like.
* ``url`` — ``http(s)://…``, ``www.…``, or a domain on a known top-level domain followed by a path.
  A bare domain (``python.org``) is speakable and is not counted.
* ``digits`` — a run of seven or more digits; a token of three or more numeric groups joined by
  ``.``, ``-``, ``/`` or ``:`` (``2026-09-25``, ``0.61.1``, ``v0.61.1``, ``192.168.0.1``) unless
  it is a thousands-grouped number (``1.000.000``, ``4,294,967,296``) — an IPv4 address counts
  even when its groups happen to have three digits; a hexadecimal id of seven or more characters
  mixing digits and letters; a UUID. Years, ports and short numbers are not counted.
* ``path`` — a token that starts at a root (``/etc/hosts``, ``~/.bashrc``, ``./run.sh``,
  ``C:\\Users``), or holds an interior ``/`` or ``\\`` with two or more separators or a file
  extension on its last segment (``src/app/page.tsx``, ``app/page.tsx``). ``and/or``, ``km/h``,
  ``24/7`` and a bare file name (``package.json``) are not counted.
* ``empty`` — 1 when the spoken part holds no letter or digit at all. Without it an answer that
  starts with the line would pass with nothing spoken.

``speakable`` is the primary outcome: every count is zero. ``format_speakable`` is the same with
``url``, ``digits`` and ``path`` left out — the part of the standard both arms state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MARKDOWN_KEYS = ("heading", "list", "table", "code_fence", "inline_code", "emphasis", "quote", "link")
CONTENT_KEYS = ("url", "digits", "path")
ALL_KEYS = (*MARKDOWN_KEYS, "emoji", "sentences", *CONTENT_KEYS, "empty")
MAX_SENTENCES = 4

# The desktop reader's own rule (`screenPartStart`), ported as is.
_RULE = re.compile(r"^[ \t]{0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$", re.M)

_FENCE_BLOCK = re.compile(r"^[ \t]{0,3}(```|~~~).*?(?:^[ \t]{0,3}\1[^\n]*$|\Z)", re.M | re.S)
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+\S", re.M)
_LIST = re.compile(r"^[ \t]*(?:[-*+•]|\d{1,3}[.)])[ \t]+\S", re.M)
_TABLE_ROW = re.compile(r"^[ \t]*\|.*\|[ \t]*$", re.M)
_TABLE_SEP = re.compile(r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)+\|?[ \t]*$", re.M)
_QUOTE = re.compile(r"^[ \t]{0,3}>[ \t]?\S", re.M)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_LINK = re.compile(r"!?\[[^\]\n]+\]\([^)\n]*\)")
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITALIC_STAR = re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])")
_ITALIC_UNDER = re.compile(r"(?<![\w_])_(?=[^\s_])([^_\n]+?)(?<=[^\s_])_(?![\w_])")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")

_EMOJI = re.compile(
    "["
    "\U0001f000-\U0001faff"  # mahjong .. symbols & pictographs extended-A (all the faces, 🚀, 👍)
    "\u2600-\u27bf"  # miscellaneous symbols and dingbats (☀, ⚠, ✅, ❌, ✨, ✓)
    "\u231a\u231b\u23e9-\u23f3\u23f8-\u23fa"  # ⌚ ⏩ ⏰ ⏳ ⏸
    "\u2b05-\u2b07\u2b1b\u2b1c\u2b50\u2b55"  # ⬅ ⬛ ⭐ ⭕
    "\u203c\u2049\u3030\u303d\u3297\u3299"  # ‼ ⁉ 〰 〽 ㊗ ㊙
    "\u20e3"  # the keycap enclosure of 1\ufe0f\u20e3 (the presentation selector itself is not counted)
    "]"
)

_TLDS = (
    "com|org|net|io|dev|app|ai|br|co|me|sh|so|gg|tv|info|edu|gov|uk|de|fr|es|pt|xyz|cloud|page|"
    "site|tech|us|ly|to|js\\.org"
)
_URL_SCHEME = re.compile(r"\bhttps?://[^\s<>()\[\]]+", re.I)
_URL_WWW = re.compile(r"(?<![\w.@/-])www\.[\w-]+(?:\.[\w-]+)+[^\s<>()\[\]]*", re.I)
_URL_PATHED = re.compile(
    rf"(?<![\w.@/-])(?:[a-z0-9-]+\.)+(?:{_TLDS})(?::\d+)?/[^\s<>()\[\]]*", re.I
)

_LONG_RUN = re.compile(r"(?<!\d)\d{7,}(?!\d)")
_GROUPED = re.compile(r"(?<![\w.:/-])[vV]?\d+(?:[.\-/:]\d+){2,}")
_THOUSANDS = re.compile(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?")
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_IPV4 = re.compile(rf"{_OCTET}(?:\.{_OCTET}){{3}}(?:/\d{{1,2}})?")
_HEX_ID = re.compile(r"(?<![\w-])(?=[0-9a-fA-F]*\d)(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{7,}(?![\w-])")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)

_ROOTED_PATH = re.compile(r"^(?:~|\.{1,2})?[\\/][\w.~-]")
_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_EXTENSION = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,5}$")
_OPEN_PUNCT = "([{\"'“‘«`*_"
_CLOSE_PUNCT = ")]}\"'”’»,;:!?.`*_"

_ALNUM = re.compile(r"[^\W_]")
_SENTENCE_END = re.compile(r"[.!?…]+[)\"'”’»*_]*(?=\s|$)")


@dataclass(frozen=True)
class SpokenCheck:
    """What the voice would read of one answer, and what in it cannot be read aloud as written."""

    spoken: str
    has_rule: bool
    screen_nonempty: bool
    sentences: int
    words: int
    chars: int
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def violations(self) -> int:
        return sum(self.counts.values())

    @property
    def speakable(self) -> bool:
        return self.violations == 0

    @property
    def format_speakable(self) -> bool:
        return all(self.counts[k] == 0 for k in ALL_KEYS if k not in CONTENT_KEYS)

    @property
    def markdown(self) -> int:
        return sum(self.counts[k] for k in MARKDOWN_KEYS)


def split_spoken(raw: str) -> tuple[str, str, bool]:
    """(spoken part, screen part, whether the line was there) — cut where the desktop reader cuts."""
    text = raw.replace("\r\n", "\n")
    match = _RULE.search(text)
    if match is None:
        return text, "", False
    rest = text[match.end():]
    return text[: match.start()], rest.lstrip("\n"), True


def count_sentences(spoken: str) -> int:
    """Sentences the voice reads, counted per line (see the module docstring)."""
    text = _FENCE_BLOCK.sub("\n(code)\n", spoken.replace("\r\n", "\n"))
    text = _LINK.sub(lambda m: m.group(0)[m.group(0).index("[") + 1: m.group(0).index("]")], text)
    text = _INLINE_CODE.sub(lambda m: m.group(0)[1:-1], text)
    total = 0
    for line in text.split("\n"):
        if not _ALNUM.search(line):
            continue
        body = re.sub(r"^[ \t]*(?:#{1,6}|>|[-*+•]|\d{1,3}[.)])[ \t]+", "", line)
        total += max(1, len(_SENTENCE_END.findall(body)))
    return total


def _tokens(text: str) -> list[str]:
    out = []
    for raw in text.split():
        token = raw.lstrip(_OPEN_PUNCT).rstrip(_CLOSE_PUNCT)
        if token:
            out.append(token)
    return out


def _is_path(token: str) -> bool:
    if _ROOTED_PATH.match(token) or _DRIVE_PATH.match(token):
        return True
    seps = len(re.findall(r"[\\/]", token))
    if seps == 0:
        return False
    if re.fullmatch(r"[\d\\/.:,-]+", token):  # 24/7, 25/09/2026: numbers, counted as digits or not at all
        return False
    if not re.search(r"[^\\/][\\/][^\\/]", token):  # a separator between two non-separators
        return False
    last = re.split(r"[\\/]", token)[-1]
    return seps >= 2 or bool(_EXTENSION.search(last))


def _count_digits(text: str) -> int:
    hits = len(_LONG_RUN.findall(text))
    for match in _GROUPED.finditer(text):
        token = match.group(0)
        if _LONG_RUN.search(token):
            continue  # already counted as a long run
        if _THOUSANDS.fullmatch(token) and not _IPV4.fullmatch(token):
            continue  # 1.000.000 is a number a voice reads well; 192.168.255.255 is not
        hits += 1
    without_uuid = _UUID.sub(" ", text)
    hits += len(_UUID.findall(text))
    for match in _HEX_ID.finditer(without_uuid):
        if not match.group(0).isdigit():
            hits += 1
    return hits


def _count_emphasis(text: str) -> int:
    return (
        len(_BOLD.findall(text))
        + len(_ITALIC_STAR.findall(_BOLD.sub(" ", text)))
        + len(_ITALIC_UNDER.findall(_BOLD.sub(" ", text)))
        + len(_STRIKE.findall(text))
    )


def check(raw: str) -> SpokenCheck:
    """Check one answer. Deterministic: the same text always gives the same counts."""
    spoken, screen, has_rule = split_spoken(raw)
    counts = dict.fromkeys(ALL_KEYS, 0)

    counts["code_fence"] = len(_FENCE_BLOCK.findall(spoken))
    prose = _FENCE_BLOCK.sub(" ", spoken)  # nothing inside a fence is judged twice
    counts["heading"] = len(_HEADING.findall(prose))
    counts["list"] = len(_LIST.findall(prose))
    table_lines = set(m.start() for m in _TABLE_ROW.finditer(prose))
    table_lines |= set(m.start() for m in _TABLE_SEP.finditer(prose))
    counts["table"] = len(table_lines)
    counts["quote"] = len(_QUOTE.findall(prose))
    counts["link"] = len(_LINK.findall(prose))
    counts["inline_code"] = len(_INLINE_CODE.findall(prose))
    no_code = _INLINE_CODE.sub(" ", prose)
    counts["emphasis"] = _count_emphasis(no_code)
    counts["emoji"] = len(_EMOJI.findall(spoken))

    n_sentences = count_sentences(spoken)
    counts["sentences"] = int(n_sentences > MAX_SENTENCES)

    counts["url"] = len(_URL_SCHEME.findall(prose))
    rest = _URL_SCHEME.sub(" ", prose)
    counts["url"] += len(_URL_WWW.findall(rest))
    rest = _URL_WWW.sub(" ", rest)
    counts["url"] += len(_URL_PATHED.findall(rest))
    rest = _URL_PATHED.sub(" ", rest)

    counts["digits"] = _count_digits(rest)
    counts["path"] = sum(1 for token in _tokens(rest) if _is_path(token))
    counts["empty"] = int(not _ALNUM.search(spoken))

    return SpokenCheck(
        spoken=spoken,
        has_rule=has_rule,
        screen_nonempty=bool(screen.strip()),
        sentences=n_sentences,
        words=len(spoken.split()),
        chars=len(spoken.strip()),
        counts=counts,
    )
