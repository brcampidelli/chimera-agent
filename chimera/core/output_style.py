"""How a conversation's answers are written: the output style a person picks for it on the Code screen.

Study 29, P4.5. The reference app offers a per-conversation style; this is the local version, and
it is deliberately small. Each style is ONE fixed suffix to the system prompt, kept here and in
`tests/prompt_snapshots/`, so a change to the wording shows up as a reviewed diff rather than as a
line moved inside some screen's request.

Three rules the code below holds, each tested in `test_an_output_style_changes_the_words_not_the_reach.py`:

- **The default is today's prompt, byte for byte.** ``"default"`` appends nothing. A conversation
  nobody chose a style for is built from exactly the system message it was built from before this
  module existed, so its `system_sha`, its cache and its behaviour do not move.
- **A style carries no permission.** It is a sentence about wording. It does not reach the tool
  registry, the posture, the approver or the write region: those are assembled from the request's
  other fields, and nothing here is passed to them. Each suffix also says so to the model, because
  "be concise" read by an agent mid-task can otherwise sound like "skip the check".
- **Versioned and recorded.** The receipt under the answer names the style and
  :data:`OUTPUT_STYLE_VERSION`, beside the `system_sha` the suffix changes — so two turns that read
  differently can be told apart as "different style" or "same style, different luck". The version
  moves whenever a suffix's text does.

Unmeasured. Nothing here claims a style makes answers better; the person chooses one, and the
default is the one that was already shipping.
"""

from __future__ import annotations

from typing import Literal, get_args

OutputStyle = Literal["default", "concise", "explanatory"]

#: Bumped whenever the text of a suffix changes, so a stored receipt keeps naming the words it ran on.
OUTPUT_STYLE_VERSION = 1

CONCISE_NOTE = (
    "Output style for this conversation: concise. Lead with the answer or the result. Write only "
    "what the person needs in order to act on it: no preamble, no restating the question, no recap "
    "of the steps you took unless they ask for one. Prefer a short list or a code block to "
    "paragraphs. This changes how you write, never what you do: verify, ask before risky actions "
    "and report failures exactly as you otherwise would."
)
"""The ``concise`` suffix. The last sentence is there because terseness is easy to over-read as a
licence to skip work that is not prose — a check, a question, a failure worth reporting."""

EXPLANATORY_NOTE = (
    "Output style for this conversation: explanatory. Besides the answer, explain what a reader "
    "would need in order to learn from it: why this approach rather than the alternatives, what the "
    "important parts of the code do, and what to watch out for. Tie each explanation to this "
    "project's own code rather than to a general tutorial. This changes how you write, never what "
    "you do: verify, ask before risky actions and report failures exactly as you otherwise would."
)
"""The ``explanatory`` suffix. Same closing sentence as the concise one, for the same reason."""

_SUFFIXES: dict[str, str] = {"concise": CONCISE_NOTE, "explanatory": EXPLANATORY_NOTE}

#: Every style a request may name, in the order the screen offers them.
OUTPUT_STYLES: tuple[str, ...] = get_args(OutputStyle)


def style_suffix(style: str | None) -> str:
    """The text a style adds to the system prompt — empty for the default and for anything unknown.

    Unknown is empty rather than an error because the request model already refuses an unknown
    name; a caller reaching this with one is internal, and the safe reading of "no style I know" is
    the prompt that was shipping before styles existed.
    """
    return _SUFFIXES.get(style or "default", "")


def with_output_style(system_prompt: str, style: str | None) -> str:
    """``system_prompt`` with the style's suffix, or ``system_prompt`` itself — the same object — for
    the default."""
    suffix = style_suffix(style)
    return f"{system_prompt}\n\n{suffix}" if suffix else system_prompt
