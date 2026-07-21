"""Capture an EXPLICIT "remember this" instruction from a chat message.

The desktop chat did not build durable memory: talking to the agent never taught it anything. The
honest first step (before any automatic fact extraction, which risks polluting memory with trivia) is
to honour the one thing a user unambiguously means as a memory instruction — "remember that I'm
allergic to peanuts", "lembre que meu voo é dia 12". This module recognises exactly that, in English
and Portuguese, and nothing else: the trigger must START the message (after optional politeness), so
"I don't remember that meeting" is not mistaken for a command.
"""

from __future__ import annotations

import re

# Optional lead-in ("please remember…", "você pode lembrar…") that we skip before the trigger.
_LEAD = r"(?:please|kindly|hey|por\s+favor|voc[eê]\s+pode|pode|can\s+you|could\s+you|would\s+you)?[,:\s]*"

# The explicit memory triggers, EN + PT. Deliberately narrow — this is not general extraction.
_TRIGGER = (
    r"(?:remember(?:\s+that|\s+this)?"
    r"|note\s+that"
    r"|keep\s+in\s+mind\s+that"
    r"|don'?t\s+forget(?:\s+that)?"
    r"|lembre(?:-se)?(?:\s+de)?(?:\s+que)?"
    r"|guarde(?:\s+que)?"
    r"|anote(?:\s+que)?"
    r"|n[aã]o\s+esque[cç]a(?:\s+de|\s+que)?)"
)

_RE = re.compile(rf"^\s*{_LEAD}{_TRIGGER}[:\s]+(.+)", re.IGNORECASE | re.DOTALL)


def parse_remember_request(text: str) -> str | None:
    """Return the fact the user asked to remember, or None if the message isn't such a request.

    The trigger is anchored to the start of the message so an incidental "remember" mid-sentence
    (e.g. "I can't remember where I put my keys") is not captured. Trailing sentence punctuation is
    stripped; an empty capture returns None.
    """
    match = _RE.match(text or "")
    if match is None:
        return None
    fact = match.group(1).strip().strip(".!?").strip()
    return fact or None
