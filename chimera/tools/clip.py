"""Clipping a command's output to what the model is shown, and always saying that it was clipped.

The marker begins ``[truncated,`` everywhere. That prefix is load-bearing: ``chimera.eval.
scenario_traps`` recognises a cut-off observation by it, and a model that is never told an output
was cut off reads 20 000 characters as the whole answer.
"""

from __future__ import annotations

import os

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
