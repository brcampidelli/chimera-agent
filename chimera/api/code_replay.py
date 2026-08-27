"""A stored conversation, turned back into the exchanges a person had.

A ``CodeSession`` keeps the model's own message list, which is the right thing to keep — it is what
the next turn is resumed from, tool calls and all. It is the wrong thing to *show*: a transcript is
``user`` / ``assistant`` / ``tool`` / ``assistant`` / ``tool`` … and a reader wants "I asked this,
it did these four things, it answered that".

So this folds the message list back into exchanges. The fold is here rather than in the UI for two
reasons: it is a rule about the model's wire format, which belongs next to the code that produced
it, and a rule with edge cases (an assistant that only called tools, a tool result whose call was
trimmed away, arguments that are not valid JSON) is worth testing in a language that has tests for
it rather than re-deriving in TypeScript.

**One thing does not come back: the diffs.** An edit's patch was streamed live and was never part of
the message list, so a resumed turn shows the tool call that wrote the file and not the coloured
diff. That is a real gap, and the UI says so rather than rendering an exchange that looks complete.
"""

from __future__ import annotations

import json
from typing import Any


def _text(content: Any) -> str:
    """Message content as text, whether it arrived as a string or as content parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Multimodal turns arrive as parts; the text ones are the only ones a transcript can show.
        return "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return "" if content is None else str(content)


def _arguments(raw: Any) -> dict[str, Any]:
    """Tool arguments as a dict. They travel as a JSON *string* on the wire.

    A string that does not parse is kept under a ``"raw"`` key rather than dropped: an argument we
    cannot read is still evidence of what the agent tried, and showing nothing there would make a
    malformed call look like a call with no arguments.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {"raw": raw}
        return parsed if isinstance(parsed, dict) else {"raw": raw}
    return {}


def exchanges_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold a stored message list into displayable exchanges, in order.

    An exchange opens on a ``user`` message and stays open until the next one, absorbing every
    assistant answer and tool call between. Messages that arrive before any ``user`` message — a
    trimmed conversation can start mid-turn — are attached to a leading exchange with an empty
    question, so the work is shown rather than silently dropped for having no header.
    """
    exchanges: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}  # tool_call_id -> the tool entry awaiting its result

    def current() -> dict[str, Any]:
        if not exchanges:
            exchanges.append({"you": "", "answer": "", "tools": [], "edits": []})
        return exchanges[-1]

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")

        if role == "user":
            exchanges.append({"you": _text(message.get("content")), "answer": "", "tools": [], "edits": []})
            pending = {}
            continue

        if role == "assistant":
            exchange = current()
            text = _text(message.get("content"))
            if text:
                # Concatenated, not replaced: a turn can answer, call a tool, then answer again, and
                # keeping only the last part would drop the reasoning that led to the call.
                exchange["answer"] = f"{exchange['answer']}\n{text}".strip() if exchange["answer"] else text
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                raw_fn = call.get("function")
                fn: dict[str, Any] = raw_fn if isinstance(raw_fn, dict) else {}
                entry = {
                    "name": str(fn.get("name") or call.get("name") or ""),
                    "arguments": _arguments(fn.get("arguments")),
                    # Unknown until the result arrives. A call whose result was trimmed away keeps
                    # ok=True with an empty observation — the alternative, defaulting to failure,
                    # would paint old conversations red for the crime of being long.
                    "ok": True,
                    "observation": "",
                }
                exchange["tools"].append(entry)
                call_id = call.get("id")
                if isinstance(call_id, str):
                    pending[call_id] = entry
            continue

        if role == "tool":
            observation = _text(message.get("content"))
            # Its own name: reusing `entry` from the assistant branch gives one variable two types
            # in one scope, which mypy reads as a bug and is one rename away from being one.
            waiting = pending.pop(str(message.get("tool_call_id") or ""), None)
            if waiting is None:
                # A result whose call is gone (trimmed at a boundary). Shown as its own row rather
                # than discarded, because "the agent ran something and this came back" is true.
                current()["tools"].append(
                    {"name": "", "arguments": {}, "ok": not observation.startswith("error:"),
                     "observation": observation}
                )
                continue
            waiting["observation"] = observation
            waiting["ok"] = not observation.startswith("error:")

    return exchanges


def attach_receipts(
    exchanges: list[dict[str, Any]], receipts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Give each replayed exchange the receipt of the turn it was.

    **Paired from the END.** Both lists are cut at the FRONT as a conversation grows — messages by
    ``trim_to_a_safe_boundary``, receipts by their own cap — and the last receipt always belongs to
    the last turn. Counting forward from index 0 would look right on every short conversation and
    then, the first time one grew past its limit, shift every receipt silently onto the wrong turn.
    A misattributed receipt is worse than an absent one: it reports somebody else's cost, taint and
    verdict as this turn's, with nothing on screen to suggest anything is wrong.

    Exchanges beyond what the receipts cover keep ``done: None``, which the screen already renders
    as "no accounting for this one" — the honest answer for a conversation older than the receipts.
    """
    out = [dict(e) for e in exchanges]
    for e in out:
        e.setdefault("done", None)
        e.setdefault("verified", None)
    for offset in range(1, min(len(out), len(receipts)) + 1):
        receipt = dict(receipts[-offset])
        # `verified` travels beside the receipt rather than inside it: the screen renders the two
        # in different places, and folding one into the other would make the verdict look like a
        # cost line.
        out[-offset]["verified"] = receipt.pop("verified", None)
        out[-offset]["done"] = receipt
    return out
