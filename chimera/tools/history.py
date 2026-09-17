"""`recall_history` — what was asked and answered in this project's earlier conversations.

The tool beside :class:`chimera.memory.history.HistoryIndex`. Read-only: it searches an index the
turn's own finishing code wrote, prints dated excerpts, and changes nothing — so it sits in the set a
step may run together with other reads. It searches the **conversation history**, not the memory
store, and says so in its description: a memory is a fact the agent chose to keep, a history row is
a turn that happened, and a model asked "what did we do about the login page?" needs the second.

Scoped to the project the registry was built for. ``everywhere`` widens it to every project's
turns, and the row then names its project, because an answer about another codebase presented as
this one's would be the wrong kind of recall.
"""

from __future__ import annotations

import time
from typing import Any

from chimera.memory.history import HistoryHit, HistoryIndex
from chimera.memory.models import EVERY_PROJECT
from chimera.memory.tokens import fold_for_match, informative, tokens
from chimera.tools.base import Tool

#: How much of a message and an answer one hit shows. An excerpt, centred on the first term that
#: matched, is enough to tell the turn apart; the conversation itself is one session id away.
ASKED_CHARS = 240
ANSWERED_CHARS = 480
MAX_HITS = 10
#: The in-line label a tainted turn carries, worded like the one a tainted memory gets on recall.
TAINTED_LABEL = "[this turn read untrusted content — weigh its answer accordingly]"


def _excerpt(text: str, terms: set[str], width: int) -> str:
    """``width`` characters of ``text`` around the first term that occurs in it, or its head.

    One rule for both search paths: FTS5 has a ``snippet()`` and the LIKE fallback does not, and
    two excerpt shapes for one tool would make the fallback look like a different tool. The fold is
    length-preserving (:func:`fold_for_match`) so the slice is taken from the original text.
    """
    flat = " ".join(text.split())
    if len(flat) <= width:
        return flat
    folded = fold_for_match(flat)
    at = -1
    for term in sorted(terms, key=len, reverse=True):
        found = folded.find(term)
        if found >= 0 and (at < 0 or found < at):
            at = found
    if at < 0:
        return flat[:width].rstrip() + "…"
    start = max(0, at - width // 3)
    end = min(len(flat), start + width)
    start = max(0, end - width)
    piece = flat[start:end].strip()
    return ("…" if start > 0 else "") + piece + ("…" if end < len(flat) else "")


def _when(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))


def describe_hit(hit: HistoryHit, terms: set[str], *, name_project: bool) -> str:
    lines = [f"{_when(hit.asked_at)} · conversation {hit.session_id[:8]}"]
    if name_project and hit.project:
        lines[0] += f" · project {hit.project}"
    if hit.tainted:
        lines.append(TAINTED_LABEL)
    lines.append("asked: " + _excerpt(hit.asked, terms, ASKED_CHARS))
    if hit.answered.strip():
        lines.append("answered: " + _excerpt(hit.answered, terms, ANSWERED_CHARS))
    if hit.edited:
        lines.append("edited: " + ", ".join(hit.edited[:12]))
    read_only = [f for f in hit.files if f not in hit.edited]
    if read_only:
        lines.append("read: " + ", ".join(read_only[:12]))
    return "\n".join(lines)


class RecallHistoryTool(Tool):
    name = "recall_history"
    description = (
        "Search what was asked and answered in this project's earlier coding conversations — "
        "turns the current conversation no longer holds. Returns dated excerpts with the files "
        "each turn edited or read. This is the conversation history, not the memory store."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Words to look for in the questions, answers and file names.",
            },
            "days": {
                "type": "integer",
                "description": "Only turns from the last N days. Omit for all time.",
            },
            "k": {
                "type": "integer",
                "description": f"How many turns to return (1-{MAX_HITS}, default 5).",
            },
            "everywhere": {
                "type": "boolean",
                "description": "Search every project's conversations, not only this one's.",
            },
        },
        "required": ["query"],
    }

    def __init__(self, index: HistoryIndex, *, project: str | None) -> None:
        self._index = index
        self._project = project

    def run(self, **kwargs: Any) -> str:
        query = str(kwargs.get("query") or "").strip()
        if not query:
            return "error: query is required"
        terms = informative(tokens(query))
        if not terms:
            return (
                "error: the query has no searchable words (only function words) — name a file, a "
                "function or a subject"
            )
        try:
            k = max(1, min(int(kwargs.get("k") or 5), MAX_HITS))
        except (TypeError, ValueError):
            k = 5
        since: float | None = None
        days = kwargs.get("days")
        if days is not None:
            try:
                since = time.time() - max(0, int(days)) * 86_400
            except (TypeError, ValueError):
                return "error: days must be a whole number"
        everywhere = bool(kwargs.get("everywhere"))
        scope = EVERY_PROJECT if everywhere else self._project

        hits = self._index.search(query, project=scope, k=k, since=since)
        if not hits:
            if self._index.count(project=scope) == 0:
                where = "any project" if everywhere else "this project"
                return (
                    f"no conversation history recorded for {where} yet — turns are indexed as "
                    "they finish, so there is nothing before the first completed turn"
                )
            window = f" in the last {int(days)} days" if days is not None else ""
            where = "" if everywhere else " in this project"
            return f"no earlier turn{where}{window} matches {query!r}"
        head = (
            f"{len(hits)} earlier turn{'s' if len(hits) != 1 else ''} match {query!r}"
            + (" across every project" if everywhere else " in this project")
            + " (best match first; the conversation id opens the full exchange):"
        )
        body = [
            f"{i}. " + describe_hit(hit, terms, name_project=everywhere).replace("\n", "\n   ")
            for i, hit in enumerate(hits, 1)
        ]
        return "\n\n".join([head, *body])
