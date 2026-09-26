"""Reasoning tags, kept out of the answer without eating it.

A local model that reasons in `<think>` blocks writes them into `message.content` like any other
text. We read that field raw, so the reasoning lands in the terminal, in the desktop transcript, and
in whatever the agent's answer feeds next. Ollama is a first-class path in this project's config, so
this is not a hypothetical shape.

Three things make this harder than a `str.replace`, and each one is a way to get it wrong:

**A tag can be split across deltas.** `<thi` arrives in one chunk and `nk>` in the next, so a filter
that looks at one chunk at a time never sees a tag at all. Hence the carry buffer.

**A `<think>` inside a code fence is not a tag.** A task that says "write a parser for `<think>`"
would otherwise have its own diff corrupted by the thing meant to clean it up. This guard was not in
the design that suggested the feature; it is here because that failure is silent and lands in the
artifact rather than on the screen.

**A tag that never closes must not swallow the answer.** The obvious implementation drops everything
after an opening tag, so one unclosed `<think>` — a truncated stream, a false positive on prose —
produces an empty answer with no error anywhere. Instead the suppressed span is HELD: released and
discarded when the block closes normally, and released as ordinary text if the block runs past
:data:`MAX_HELD_CHARS` or the stream ends inside it. Showing reasoning is a blemish; losing the
answer is a bug.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection

#: `<think>`, `</thinking>`, and a fence. One pattern so the scanner makes a single pass.
_TOKEN = re.compile(r"```|</?think(?:ing)?\s*>", re.IGNORECASE)

#: Longest token plus slack, so a tag split across two deltas is still recognised.
_CARRY = 16

#: Ceiling on a single suppressed span. Past this the opening tag is treated as a false positive and
#: everything held is released as ordinary text — bounded memory AND no silent data loss, which the
#: naive "drop until close" version gives neither of.
MAX_HELD_CHARS = 32_000


class ThinkFilter:
    """Stateful filter over a text stream. Feed deltas in order; call :meth:`flush` at the end."""

    def __init__(self) -> None:
        self._depth = 0
        self._held: list[str] = []
        self._held_len = 0
        self._tail = ""
        self._in_code = False
        self._surrendered = False

    def feed(self, text: str) -> str:
        """Return the part of ``text`` that should be shown (possibly empty)."""
        if self._surrendered:
            return text
        buf = self._tail + text
        self._tail = ""
        out: list[str] = []
        pos = 0
        for match in _TOKEN.finditer(buf):
            self._push(buf[pos : match.start()], out)
            token = match.group(0)
            if token == "```":
                self._in_code = not self._in_code
                self._push(token, out)
            elif self._in_code:
                # Inside a fence a tag is literal text and stays. See the module docstring.
                self._push(token, out)
            elif token.startswith("</"):
                self._depth = max(0, self._depth - 1)
                if self._depth == 0:
                    # Closed normally: the held span really was reasoning, so it goes.
                    self._held.clear()
                    self._held_len = 0
                else:
                    self._push(token, out)
            else:
                self._depth += 1
                # The tag joins what is HELD, not what is shown. It matters only on release: when a
                # block turns out not to be one, the text has to come back byte for byte, and the
                # first version dropped the marker — `List<think> in prose` came out as
                # `List in prose`, which is a filter quietly editing ordinary text.
                self._push(token, out)
            pos = match.end()
            if self._surrendered:
                return "".join(out) + buf[pos:]

        rest = buf[pos:]
        # Hold back anything at the end that could be the start of a token in the next delta.
        cut = len(rest)
        for i in range(max(0, len(rest) - _CARRY), len(rest)):
            if rest[i] in "<`":
                cut = i
                break
        self._tail = rest[cut:]
        self._push(rest[:cut], out)
        return "".join(out)

    def flush(self) -> str:
        """Whatever is still held back. Safe to call once, at the end of the stream."""
        out = self._tail
        self._tail = ""
        if self._held:
            # The stream ended inside a `<think>` that never closed. Emit it: a truncated reasoning
            # block shown to the user is a blemish, an answer silently replaced by "" is a bug.
            out += "".join(self._held)
            self._held.clear()
            self._held_len = 0
        self._depth = 0
        return out

    def _push(self, text: str, out: list[str]) -> None:
        if not text:
            return
        if self._depth == 0:
            out.append(text)
            return
        self._held.append(text)
        self._held_len += len(text)
        if self._held_len > MAX_HELD_CHARS:
            # Past the ceiling this stops being a reasoning block and starts being a bug. Release
            # everything and stop filtering for the rest of the stream rather than keep guessing.
            out.append("".join(self._held))
            self._held.clear()
            self._held_len = 0
            self._depth = 0
            self._surrendered = True


def strip_think(text: str) -> str:
    """One-shot version for a non-streaming response.

    Built on the streaming filter rather than beside it: two implementations of the same rule is how
    one of them quietly stops handling code fences.
    """
    f = ThinkFilter()
    return f.feed(text) + f.flush()


#: How far back from the end of a reasoning field an answer object may start. A structured answer
#: is a line or two; the bound keeps the backward scan linear on a reasoning that ran to 40k tokens.
ANSWER_WINDOW_CHARS = 8_000


def answer_at_end_of_reasoning(reasoning: str, keys: Collection[str]) -> str:
    """The JSON object a reasoning field ENDS with, as written, if it carries every one of ``keys``;
    else "".

    For the route that files a whole reply as reasoning
    (:attr:`~chimera.providers.gateway.CompletionResult.answer_in_reasoning`), and only for a caller
    that asked for a structured answer and says on its receipt that it read it from here. The
    opposite of this module's rule for tags on purpose: there the reasoning sits inside ``content``
    and an unclosed tag may be prose, so the text is released; here the route itself labelled the
    text as reasoning, so nothing leaves it except an object of the caller's own schema.

    **The object must be the last thing written**, apart from whitespace and a closing code fence.
    An object followed by more reasoning is a draft the model went on thinking about, and is never
    taken. On H11's prompt that rule cost nothing: in the reasoning tails `bench/review_judge`
    stored for its 765 recovered answers (H11, 2026-09-25), 714 objects were followed by nothing and
    51 by a fence only, and this reader takes the same text that run took on all 765; on the three
    where the run found no valid object (a final with an invalid escape among them), neither does
    this one. A restatement of the requested format (``"approve" | "reject"``) is not valid JSON and
    is never taken either.

    **It is strict, and on another prompt it costs answers.** On the hosted decision backend's
    prompt (`bench/answer_in_reasoning/RESULTS.md`, 2026-09-26) it took 13 of 22 flagged replies,
    each the final object. The misses whose whole reasoning was kept (3 of 3) wrote the final object
    and then a prose justification of the same verdict; they fall back to the caller's re-ask.
    Taking an object followed by prose that names the same option is the candidate there, and it is
    unmeasured.

    Control characters inside strings are accepted (``strict=False``): a quoted line of code with a
    literal tab is still the model's answer (H11, amendment 2).
    """
    text = reasoning.rstrip().removesuffix("```").rstrip()
    if not text.endswith("}"):
        return ""
    decoder = json.JSONDecoder(strict=False)
    floor = max(0, len(text) - ANSWER_WINDOW_CHARS)
    pos = text.rfind("{", floor)
    while pos != -1:
        try:
            obj, stop = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            obj, stop = None, -1
        if stop == len(text) and isinstance(obj, dict) and all(k in obj for k in keys):
            return text[pos:]
        pos = text.rfind("{", floor, pos)
    return ""
