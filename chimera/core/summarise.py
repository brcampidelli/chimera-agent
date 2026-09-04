"""A summariser for the span a compaction drops — written as rules, not as narration.

`compact()` takes a `summarise` callable and no production caller has ever passed one, so every
compaction in the product replaces the dropped span with `_structural_note`: *"21 earlier messages
were removed. Tools used in that span: write_file."* That note is a deliberate choice and its own
docstring defends it — *"the agent can re-read a file; it cannot un-believe a fabricated summary"* —
which is why this module exists beside it rather than instead of it, and why it is measured before
it is switched on.

**What is actually lost.** `RunState` restores the task, the plan and the open file, and the system
message survives verbatim. What compaction drops with nothing to restore it is everything a *person*
said after the first message: a convention established in turn one of a conversation, a correction,
a "not that directory". On the Code screen that is the common case — a fresh `Agent` is built per
request, so `run_state.task` holds THIS turn's message and turn one's convention lives only in the
history being compacted.

**Why rule-form.** The two papers `context_budget` cites split exactly here: compactors retain 17% of
injected session constraints (arXiv 2608.11242), and rule-form items survive a compaction far better
than facts (arXiv 2608.11392). So this does not ask for a summary of what happened — the structural
note already says what happened, cheaper and without a model. It asks for the standing instructions
and decisions, which are the part with no other route back into the prompt.

**What it must never do** is invent. A summary is believed in a way a note is not, so the prompt
forbids inference and the fallback on any failure is the structural note rather than silence: a
compaction that could not summarise must still compact.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("core.summarise")

#: Cap on what is sent to the summariser. The dropped span can be most of a context window, and
#: paying full price to compress it would make the compaction cost as much as the overflow it
#: prevents. Head and tail both, for the reason `events._clip_observation` gives: the beginning
#: carries what was established and the end carries where the work had got to.
MAX_INPUT_CHARS = 12_000

SYSTEM = (
    "You compress the earlier part of a conversation that is being dropped to free context. "
    "Write ONLY the standing instructions, constraints, decisions and corrections that a "
    "collaborator would need in order to keep working consistently — in imperative form, one per "
    "line, shortest wording that keeps the meaning. "
    "Do NOT narrate what happened, do not summarise file contents, and do not list tool calls: "
    "those are recoverable by re-reading and are reported separately. "
    "Write nothing that was not actually said. If the span contains no standing instruction at all, "
    "reply with the single word NONE."
)


def _flatten(messages: list[Any]) -> str:
    """The dropped span as plain text, clipped at both ends."""
    lines: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "?")
        content = str(message.get("content") or "").strip()
        # Tool results are the bulk of a span and the least worth summarising: they are the thing
        # the agent can get back by asking again. The name is kept so a rule that depends on one
        # ("we agreed to use the second file grep found") is still legible.
        if role == "tool":
            content = content[:200]
        if content:
            lines.append(f"{role}: {content}")
    text = "\n".join(lines)
    if len(text) <= MAX_INPUT_CHARS:
        return text
    head = MAX_INPUT_CHARS // 2
    return f"{text[:head]}\n…\n{text[-(MAX_INPUT_CHARS - head):]}"


def rule_summariser(
    backend: Any,
    model: str | None = None,
    *,
    fallback: Callable[[list[Any]], str] | None = None,
) -> Callable[[list[Any]], str]:
    """Build the `summarise` callable `compact()` accepts.

    ``fallback`` is what is used when the model returns nothing usable, and defaults to the
    structural note. That is not defensive decoration: the alternative is a compaction that silently
    replaces a span with an empty string, which is the one outcome strictly worse than either arm.
    """
    from chimera.core.context_budget import _structural_note

    to_note = fallback or _structural_note

    def summarise(older: list[Any]) -> str:
        body = _flatten(older)
        if not body.strip():
            return to_note(older)
        try:
            from chimera.providers.gateway import Message

            answer = backend.complete(
                [Message(role="system", content=SYSTEM), Message(role="user", content=body)],
                model=model,
                temperature=0.0,
            ).content
        except Exception as exc:  # noqa: BLE001 — a compaction must not fail on a summariser
            _log.warning("compaction summariser failed, using the structural note: %s", exc)
            return to_note(older)

        rules = (answer or "").strip()
        if not rules or rules.upper().startswith("NONE"):
            # Nothing standing was said, which is a real answer and a common one. The note is then
            # both cheaper and more informative than an empty summary.
            return to_note(older)
        # The note goes WITH it, never instead of it. They answer different questions — the note
        # says how much was dropped and which tools ran, the summary says what still binds — and an
        # agent that reads only the second has no idea how much it is missing.
        return f"{to_note(older)}\n\nStanding from that span:\n{rules}"

    return summarise
