"""Clipping a command's output to what the model is shown, and always saying that it was clipped.

The marker begins ``[truncated,`` everywhere. That prefix is load-bearing: ``chimera.eval.
scenario_traps`` recognises a cut-off observation by it, and a model that is never told an output
was cut off reads 20 000 characters as the whole answer.
"""

from __future__ import annotations

import os
from typing import Any

#: Opt-in: ``run_shell`` and ``execute_code`` keep the head AND the tail of a long output. OFF by
#: default — study 30 (S30-11) ties turning it on to a scenario_traps replay that has not been run.
#: That replay compares flag OFF against flag ON on THIS code, never against a baseline recorded
#: before it: ``code_interpreter``'s new marker also opens the truncation trap's mask (see
#: ``scenario_traps.TRUNCATION_MARK``), so the older baselines were measured with another ruler.
TAIL_ENV = "CHIMERA_EXEC_OUTPUT_TAIL"


def keep_tail_enabled() -> bool:
    """Read at call time, so a running app follows the setting without a restart."""
    return os.environ.get(TAIL_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def clip_output(text: str, limit: int, *, keep_tail: bool = False) -> str:
    """``text`` cut to about ``limit`` characters, with a marker saying so.

    Head-only is right where the start is the useful part (a document, a page). ``keep_tail`` keeps
    both ends, for a command's output, whose verdict — pytest's ``FAILED`` summary, a compiler's
    last error, a traceback's exception line — is at the end by construction. The head is kept too:
    it says which command ran and where. Same split as ``events._clip_observation`` (2/5 head).
    """
    if len(text) <= limit:
        return text
    if not keep_tail:
        return text[:limit] + f"\n... [truncated, {len(text)} chars total]"
    head = limit * 2 // 5
    tail = limit - head
    omitted = len(text) - limit
    return (
        f"{text[:head]}\n... [truncated, {len(text)} chars total; "
        f"{omitted} chars from the middle not shown] ...\n{text[-tail:]}"
    )


#: The most text any one tool result may put in the model's context, for the tools that do not clip
#: their own (``Tool.clips_own_output``). The same 20 000 characters every tool that clips its own
#: output already uses, so nothing that was already bounded changes: this is the floor under the
#: tools that were not.
CENTRAL_MAX_OUTPUT_CHARS = 20_000


def clip_central(out: str, limit: int = CENTRAL_MAX_OUTPUT_CHARS) -> str:
    """``out`` cut to ``limit`` characters (head only), never cutting through a data fence.

    By the time a result reaches :meth:`ToolRegistry.run` the taint layer may already have fenced
    it (``LedgeredTool`` wraps every tool of a registry), so the text can end with the CLOSING
    fence marker. A cut at the character count would eat that marker and leave the rest of the
    context reading as if it were inside the data region, or the data region open at the end:
    the defence against injection undone, with no error anywhere. So a fenced result is cut in its
    middle, the marker says so inside the fence, and the markers stay where they were.
    """
    from chimera.governance.sanitize import FENCE_CLOSE, FENCE_OPEN

    if out.endswith(FENCE_CLOSE):
        start = out.find(FENCE_OPEN)
        if start != -1:
            body_start = start + len(FENCE_OPEN) + 1  # the newline `fence` puts after the open marker
            body_end = len(out) - len(FENCE_CLOSE) - 1  # and the one before the close marker
            if body_start <= body_end:
                body = out[body_start:body_end]
                if len(body) <= limit:
                    return out
                return f"{out[:body_start]}{clip_output(body, limit)}{out[body_end:]}"
    return clip_output(out, limit)


#: The argument that pages through a long result by character position, for the three tools whose
#: output used to be cut with no way to read the rest (``http_get``, ``scrape``, ``read_document``).
OFFSET_PARAMETER: dict[str, object] = {
    "type": "integer",
    "minimum": 0,
    "description": "Character to start at (default 0). Re-fetches, so the source may have changed.",
}


def parse_offset(value: Any) -> int | str:
    """``value`` as a character offset >= 0, or an ``error:`` observation saying why it is not one."""
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return "error: offset must be a whole number of characters, 0 or more"
    try:
        offset = int(value)  # a model may send "20000" or 20000.0
    except (TypeError, ValueError, OverflowError):
        return "error: offset must be a whole number of characters, 0 or more"
    if offset < 0:
        return "error: offset must be a whole number of characters, 0 or more"
    return offset


def clip_page(text: str, limit: int, offset: int = 0) -> str:
    """One page of ``text``: ``limit`` characters from ``offset``, with a marker that says how to go on.

    The marker still begins ``[truncated,`` (what ``scenario_traps`` recognises) and now ends with
    ``continue with offset=N``, the same promise ``read_file`` makes with ``start_line``. The last
    page carries no marker, so reading until there is none reconstructs the text with no gap and no
    repeat. An ``offset`` past the end is an error, not an empty page: an empty page reads as "the
    document ends here" and that is a claim the tool cannot make about a document it did not finish.
    """
    total = len(text)
    if offset > total or (offset == total and total > 0):
        return f"error: offset {offset} is past the end ({total} chars)"
    end = offset + limit
    if end >= total:
        return text[offset:]
    return text[offset:end] + f"\n... [truncated, {total} chars total; continue with offset={end}]"
