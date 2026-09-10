"""Reading a child process's output on a machine whose console is not UTF-8.

``text=True`` with no ``encoding=`` decodes with :func:`locale.getpreferredencoding`, which on
Windows is the **ANSI** code page — while a console program writes the **OEM** one. Measured on the
machine this project is developed on: ANSI ``cp1252``, OEM ``cp850``. Two different pages, and
nothing in between them notices.

The damage has three shapes, all measured through the real ``LocalSandbox`` rather than argued:

===========================  ==============================  ==================
child wrote                  ``text=True`` gave              with this module
===========================  ==============================  ==================
``café ñ`` as cp850          ``'caf\\u201a \\xa4'``            ``'café ñ'``
``check ✓ fogo 🔥`` as UTF-8  ``'check \\xe2\\u0153\\u201c…'``    unchanged
bytes undefined in cp1252    **``None``**, with ``rc=0``      a string
===========================  ==============================  ==================

The third row is the one that matters and it is not hypothetical. ``UnicodeDecodeError`` is raised
on ``subprocess``'s *reader thread*, so the call returns a **successful** process whose ``stdout``
is ``None`` — the command ran, the output is gone, and the exit code says everything is fine. The
five bytes cp1252 leaves undefined (``0x81 0x8D 0x8F 0x90 0x9D``) are ordinary UTF-8 continuation
bytes: they appear in emoji, in most CJK and in plenty of prose. ``chimera/core/worktree.py`` hit
exactly this in ``GET /api/git/diff`` and named its encoding; this module is that lesson applied to
the sites that run *arbitrary* commands, where the child is not known to write UTF-8.

**Why a fallback and not simply UTF-8.** Because the child is not git. Twelve commands an agent
actually runs were measured on Windows: six are pure ASCII, and of the six whose output carries
non-ASCII bytes, **four are not UTF-8** — ``dir``, ``echo café ñ``, ``cmd /c ver``, ``findstr /?``,
that is, cmd.exe's own builtins. Naming ``encoding="utf-8"`` alone would have turned every ``dir``
listing on such a machine into replacement characters, which is a regression bought with the fix.

**What this costs, said out loud.** Deciding by "is this UTF-8 at all" means a stream that is UTF-8
*except* for a few bad bytes in the middle falls back to the console page, and whatever non-ASCII it
held comes out mangled. Truncation is the realistic way that happens here — every sandbox run has a
timeout that kills the child mid-stream — so an incomplete **final** character is handled separately
and never triggers the fallback. What remains is genuinely mixed or binary output, which has no
correct answer in any single codec: it is mangled rather than lost, and losing it is what happens
today.
"""

from __future__ import annotations

import codecs
import os
from functools import lru_cache

_POSIX = os.name == "posix"

#: The range `errors="surrogateescape"` maps an undecodable byte to. Written as code points rather
#: than as string literals on purpose: a lone surrogate in a source file cannot be written back out
#: as UTF-8, so a literal here turns every tool that rewrites this module into a truncated file.
_SURROGATE_LOW, _SURROGATE_HIGH = 0xDC80, 0xDCFF


@lru_cache(maxsize=1)
def console_encoding() -> str:
    """The code page a console program writes on this machine, or ``utf-8`` where that is all there is.

    Windows is the only place that has the question. ``GetOEMCP`` rather than
    :func:`locale.getpreferredencoding`, because the latter answers with the ANSI page — the wrong
    one, and the whole defect.
    """
    if _POSIX:
        return "utf-8"
    try:
        import ctypes

        page = int(ctypes.windll.kernel32.GetOEMCP())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — a machine that cannot answer gets the safe answer
        return "utf-8"
    if page in (65001, 0):  # the console was already set to UTF-8, or the call gave nothing
        return "utf-8"
    codec = f"cp{page}"
    try:
        "".encode(codec)
    except LookupError:  # a page Python has no codec for
        return "utf-8"
    return codec


def console_text(raw: bytes | None) -> str:
    """Decode one child process's output.

    UTF-8 first, because that is what every tool written this decade emits and what every non-Windows
    machine uses. Only when the bytes are *not* UTF-8 does the console's own page get a turn — and an
    incomplete final character does not count as "not UTF-8", because that is what a killed child
    leaves behind rather than evidence about the codec.

    ``None`` maps to ``""``: callers pass ``proc.stdout`` straight in, and a pipe that was never
    opened is empty output, not missing output.
    """
    if not raw:
        return ""
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        # `final=False` holds an incomplete trailing sequence instead of failing on it, and still
        # raises on a byte that could not start or continue a character anywhere. That distinction
        # is the whole decision, and asking for it directly is why there is no "how close to the end
        # is close enough" constant here — a first draft had one, and on six bytes of cp850 output
        # its window covered half the string and sent `café ñ` down the truncation path.
        head = decoder.decode(raw, final=False)
    except UnicodeDecodeError:
        return raw.decode(console_encoding(), errors="replace")
    try:
        return head + decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        # Everything decoded except a character the child was cut off mid-way through.
        return head + "\N{REPLACEMENT CHARACTER}"


def console_line(text: str) -> str:
    """The same decision, for a reader that had to stay in text mode.

    A streaming reader cannot hand over bytes: ``bufsize=1`` is line buffering and Python honours it
    only in text mode, so reading binary would deliver blocks instead of lines and there would be no
    streaming left to decode. Such a reader is opened ``encoding="utf-8", errors="surrogateescape"``
    instead, which is lossless — every byte UTF-8 could not take became a lone surrogate and encodes
    back to exactly itself. When none are present the line was UTF-8 and is returned untouched; when
    some are, the original bytes are recovered and get the same treatment as any other output.

    Judging per line is not a compromise here, it is better than the block case: one ``dir`` line in
    an otherwise UTF-8 log is decoded on its own bytes rather than deciding the whole stream by the
    first line that was not UTF-8.
    """
    if not any(_SURROGATE_LOW <= ord(ch) <= _SURROGATE_HIGH for ch in text):
        return text
    return console_text(text.encode("utf-8", "surrogateescape"))


__all__ = ["console_encoding", "console_line", "console_text"]
