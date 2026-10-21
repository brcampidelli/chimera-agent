"""A typed question for the person at the Code screen.

Unlike an approval, the answer is ordinary user data: it cannot authorize an action or satisfy a
policy gate. The tool is constructed only for a turn whose screen can announce and answer it; a
headless caller must not park a worker for fifteen minutes asking a person who cannot see it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from chimera.governance.pending import ask_user_durably
from chimera.tools.base import Tool

#: The longest option label a card shows. A longer one is cut with an ellipsis and its full text
#: travels beside it as the option's description, so the person can still read it (hover) and the
#: agent still gets the full text back when that option is chosen.
OPTION_LABEL_MAX = 120


def _label_of(option: str) -> str:
    """``option`` as a label of at most :data:`OPTION_LABEL_MAX` characters, cut with an ellipsis."""
    text = " ".join(option.split())
    if len(text) <= OPTION_LABEL_MAX:
        return text
    return text[: OPTION_LABEL_MAX - 1].rstrip() + "…"


class AskUserTool(Tool):
    """Pause this attended turn for one bounded choice, or return an honest timeout."""

    name = "ask_user"
    description = (
        "Ask the user a question when a decision is needed rather than guessing. Optionally provide "
        "up to six short choices and say whether free-text is allowed. The turn pauses until the "
        "user answers or the question times out; timeout returns no answer."
    )
    parameters = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question to show the user."},
            "options": {
                "type": "array", "items": {"type": "string"}, "maxItems": 6,
                "description": "Optional list of up to six short answer labels (a label over 120 characters is shortened with an ellipsis on screen; you still get its full text back).",
            },
            "allow_free_text": {
                "type": "boolean", "default": True,
                "description": "Whether the user may enter an answer other than a listed option.",
            },
        },
        "required": ["question"],
        "additionalProperties": False,
    }

    def __init__(self, home: Path, announce: Any, *, wait_seconds: float) -> None:
        self.home = home
        self.announce = announce
        self.wait_seconds = wait_seconds

    def run(self, **kwargs: Any) -> str:
        question = str(kwargs.get("question", "")).strip()
        options = kwargs.get("options", [])
        free = kwargs.get("allow_free_text", True)
        if not question:
            return "error: question must not be empty"
        if not isinstance(options, list) or len(options) > 6:
            return "error: options must be a list of at most six labels"
        if any(not isinstance(item, str) or not item.strip() for item in options):
            return "error: each option must be a non-empty label"
        if len({item.strip().casefold() for item in options}) != len(options):
            return "error: options must be distinct"
        # A long option is shortened, not refused: refusing made the agent ask the whole question
        # again (study 34, item 12). Its full text rides along as the description.
        labels = [_label_of(item) for item in options]
        if len({label.casefold() for label in labels}) != len(labels):
            return "error: options must be distinct within their first 120 characters"
        full = {label: " ".join(item.split()) for label, item in zip(labels, options, strict=True)}
        details = {label: text for label, text in full.items() if label != text}
        if not isinstance(free, bool):
            return "error: allow_free_text must be a boolean"
        from chimera.tools.write_region import claim_repair_authorization_question

        repair_question = claim_repair_authorization_question()
        if repair_question is False:
            return "error: the repair already asked the user about protected test writes; do not ask again"
        answer = ask_user_durably(
            self.home, question, labels, free,
            announce=self.announce, wait_seconds=self.wait_seconds, option_details=details,
        )
        if answer is None:
            return "No response."
        selected = full.get(answer, answer)
        from chimera.tools.write_region import authorize_repair_test_writes

        authorize_repair_test_writes(selected)
        return json.dumps({"answer": selected}, ensure_ascii=False)


def is_question_id(request_id: str) -> bool:
    """Keep untrusted route ids from becoming paths in the pending directory."""
    return bool(re.fullmatch(r"[0-9a-f]{12}", request_id or ""))
