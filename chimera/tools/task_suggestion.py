"""A task the agent noticed outside the one in hand, offered to the owner as a card.

The model finds code that is dead, a doc that is stale, a test that is missing, a risk seen in
passing. Instead of drifting off the task to fix it, it calls :class:`SuggestTaskTool`, and the
screen shows a card with the title, one line and the full prompt. The card is a proposal and nothing
more: these tools never start a conversation, never send a message and never touch a file. Starting
one is a click on the owner's own screen (``POST /api/code/task-suggestions/start``), and that route
refuses everything that is not that screen.

The tool is built only for an attended turn whose screen can show the card, the same condition as
``ask_user``. Headless, nobody could see the card, so a proposal would vanish; the tool is not
offered at all.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from chimera.tools.base import Tool

#: The title is a card heading, one line. The longer ones are refused rather than shortened, so the
#: model learns to name the task instead of pasting a paragraph into the heading.
TITLE_MAX = 60
TLDR_MAX = 280
#: The prompt starts a conversation with no memory of this one, so it must carry its own context.
PROMPT_MAX = 4000
#: More cards than this in one turn is noise the owner has to dismiss one by one.
PER_TURN_MAX = 5

#: The two events a tray sends to the screen. Named here so the screen and the tests share the words.
PROPOSED = "task_suggestion"
WITHDRAWN = "task_suggestion_dismissed"


class TaskSuggestionTray:
    """One turn's cards: what was proposed, what was withdrawn, and where the cards go.

    A proposal is kept in the card store under the conversation it was made in, together with the
    run's taint at that moment, so the start route can read both from the server. ``emit``,
    ``session_id`` and ``tainted`` are bound late, as the chart and frame sinks are: the tools are
    built with the registry before the turn's session and ledger exist. Until they are bound a
    proposal is refused, because a card nobody can start or see is worse than no card.
    """

    def __init__(self, store: Any = None) -> None:
        self.store = store
        self.emit: Callable[[str, dict[str, Any]], None] | None = None
        self.session_id: str | None = None
        self.tainted: Callable[[], bool] = lambda: False
        self._open: set[str] = set()
        self._titles: set[str] = set()
        self._proposed = 0

    def propose(self, title: str, tldr: str, prompt: str) -> str:
        """The card's id, or the reason it was refused. Nothing is started either way."""
        if self.emit is None or self.store is None or not self.session_id:
            return "error: this turn has no screen to show a task card on; say the task in your answer instead"
        if self._proposed >= PER_TURN_MAX:
            return f"error: at most {PER_TURN_MAX} task cards per turn; mention the rest in your answer"
        title, tldr, prompt = title.strip(), tldr.strip(), prompt.strip()
        if not title or len(title) > TITLE_MAX:
            return f"error: title must be 1–{TITLE_MAX} characters"
        if not tldr or len(tldr) > TLDR_MAX:
            return f"error: tldr must be 1–{TLDR_MAX} characters"
        if not prompt or len(prompt) > PROMPT_MAX:
            return f"error: prompt must be 1–{PROMPT_MAX} characters and stand on its own"
        if title.casefold() in self._titles:
            return "error: that task is already proposed in this turn"
        tainted = bool(self.tainted())
        try:
            card_id = self.store.propose(
                session_id=self.session_id, title=title, tldr=tldr, prompt=prompt, tainted=tainted,
            )
        except OSError as exc:
            return f"error: the card could not be kept ({exc}); say the task in your answer instead"
        # The store may know more than this turn does (the conversation read the web in an earlier one).
        saved = self.store.get(self.session_id, card_id)
        tainted = bool(saved.tainted) if saved is not None else tainted
        self.emit(PROPOSED, {
            "id": card_id, "title": title, "tldr": tldr, "prompt": prompt, "tainted": tainted,
        })
        self._open.add(card_id)
        self._titles.add(title.casefold())
        self._proposed += 1
        return json.dumps({"proposed": card_id, "started": False}, ensure_ascii=False)

    @property
    def proposed_ids(self) -> list[str]:
        """The cards of this turn still open, for the end-of-turn taint check."""
        return list(self._open)

    def withdraw(self, card_id: str, reason: str = "") -> str:
        """Take a card back from the screen and the store, when the task turned out stale or was done."""
        if self.emit is None or self.store is None or not self.session_id:
            return "error: this turn has no screen to show task cards on"
        if card_id not in self._open:
            return f"error: no open task card {card_id!r} in this turn"
        self._open.discard(card_id)
        self.store.discard(self.session_id, card_id)
        self.emit(WITHDRAWN, {"id": card_id, "reason": " ".join(reason.split())[:200]})
        return json.dumps({"withdrawn": card_id}, ensure_ascii=False)


class SuggestTaskTool(Tool):
    """Propose a task the owner may want, as a card. It starts nothing."""

    name = "suggest_task"
    description = (
        "Offer the owner a task you noticed outside the current one, as a card they can start. Use it "
        "instead of drifting off task: dead code, stale docs, a missing test, a security problem seen "
        "in passing. The card is only a proposal; nothing starts until the owner clicks Start, and "
        "your current task continues unchanged. title: a short heading, at most 60 characters. tldr: "
        "one line on why it matters now. prompt: the full instruction for a NEW conversation that has "
        "none of this one's context, so name the files, the symptom and the done condition. At most "
        "five per turn; do not propose what the current task already does."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": f"Card heading, at most {TITLE_MAX} characters."},
            "tldr": {"type": "string", "description": f"Why it matters, one line, at most {TLDR_MAX} characters."},
            "prompt": {
                "type": "string",
                "description": f"The complete instruction for a new conversation, at most {PROMPT_MAX} characters.",
            },
        },
        "required": ["title", "tldr", "prompt"],
        "additionalProperties": False,
    }

    def __init__(self, tray: TaskSuggestionTray) -> None:
        self.tray = tray

    def run(self, **kwargs: Any) -> str:
        title, tldr, prompt = kwargs.get("title"), kwargs.get("tldr"), kwargs.get("prompt")
        if not (isinstance(title, str) and isinstance(tldr, str) and isinstance(prompt, str)):
            return "error: title, tldr and prompt must all be strings"
        return self.tray.propose(title, tldr, prompt)


class DismissTaskTool(Tool):
    """Withdraw a card this turn proposed. The owner's own dismiss is a click on the card."""

    name = "dismiss_task"
    description = (
        "Withdraw a task card you proposed earlier in this turn, for example because you finished that "
        "work here or found it was not needed. Give the card id that suggest_task returned."
    )
    parameters = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The id suggest_task returned for the card."},
            "reason": {"type": "string", "description": "One short line on why it is withdrawn."},
        },
        "required": ["id"],
        "additionalProperties": False,
    }

    def __init__(self, tray: TaskSuggestionTray) -> None:
        self.tray = tray

    def run(self, **kwargs: Any) -> str:
        card_id = kwargs.get("id")
        reason = kwargs.get("reason", "")
        if not isinstance(card_id, str) or not card_id:
            return "error: id must be the card id suggest_task returned"
        if not isinstance(reason, str):
            return "error: reason must be a string"
        return self.tray.withdraw(card_id, reason)
