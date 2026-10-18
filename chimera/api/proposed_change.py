"""The Editor's "Change this": propose a replacement for some lines, and write nothing.

What the person pointed at travels the way every mention does — one ``context`` item, a file with
a line range (:mod:`chimera.api.turn_context`), read from disk, inlined within that module's caps and
fenced as data. This module holds only what a proposal adds on top of pointing:

* **the ask** — one fenced block holding the replacement for exactly those lines, so the Editor can
  lift it out of the answer and show it as a diff without guessing where prose ends and code begins;
* **the guarantee** — the turn cannot write. The Editor applies a proposal only when the person
  accepts its diff; an agent that edited the file itself would make "never auto-apply" a promise the
  client made and the server did not keep. So the request's posture is narrowed to ``read_only``
  at validation — narrowed, never widened, the direction ``granted_posture`` holds to — and an
  external agent, whose file tools are its own and which a posture does not reach, is refused.
"""

from __future__ import annotations

from typing import Any

#: What a proposal turn is asked to produce, after the mention's own block.
CHANGE_INSTRUCTION = (
    "Propose a replacement for exactly lines {start}-{end} of `{path}` and nothing else. Do not edit "
    "any file: the person reviews your proposal as a diff in the editor and applies it there. Reply "
    "with the full replacement text for those lines in a single fenced code block, without line "
    "numbers and without the data markers."
)


def proposal_target(context: list[Any]) -> Any:
    """The one file-with-a-range item a proposal is about, or ValueError naming what is wrong."""
    if len(context) != 1:
        raise ValueError("a proposed change is about exactly one context item")
    item = context[0]
    if getattr(item, "kind", None) != "file" or item.start_line is None or item.end_line is None:
        raise ValueError("a proposed change is about a file with a line range")
    return item


def change_note(context: list[Any]) -> str:
    """The sentence appended to a proposal turn's message, naming the lines it may replace."""
    item = proposal_target(context)
    return CHANGE_INSTRUCTION.format(start=item.start_line, end=item.end_line, path=item.path)
