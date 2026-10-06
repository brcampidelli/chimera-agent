"""Answering a pending approval from the chat bot, with a one-time code (study 29, P3.3).

The approval webhook already puts the question in the owner's pocket (`governance/approval.py`,
`deliverer_for`), and the answer still needed `chimera approve` in a terminal — which is what a
phone does not have. With ``CHIMERA_APPROVE_VIA_CHAT`` on, every question the webhook carries also
carries a fresh six-digit code (`pending.ask_durably`), and the bot accepts::

    aprovar <id> <code>      recusar <id> <code>      (also: approve / deny)

What decides whether this is safe, each pinned by a test in
``tests/test_an_approval_answered_from_the_chat_needs_its_code.py``:

**It is intercepted before it can become a turn.** The bot routes every other message to a session,
and a session puts the text in front of the model, in the chat history and — with the owner's
"remember that…" on — in memory. A code that reached the model is a code a model under injection can
learn the shape of and try to reproduce. So :meth:`ChatApprovals.intercept` runs first in
`MessageGateway.on_message`, and an approval-shaped message never reaches a session, whatever the
outcome — including with the setting OFF, where it is simply refused. A longer message with such a
line in it (the whole notification, forwarded) is held back too, and not applied.

**Only an id the owner listed, and never the bot itself.** The sender must be in THAT platform's
allowlist; with the list empty the feature is refused outright (anyone who can message an open bot
may not approve anything), and `chimera serve` / the app warn at startup. A message from a bot —
the adapters drop their own messages, and :attr:`InboundMessage.from_bot` marks any other bot that a
``respond_to_bots`` adapter lets through — is never an answer, so a model that sends the exact format
through ``send_message`` is talking to itself.

**The code is the owner's, once.** Valid for one request, once, until that request expires; stored
hashed; consumed before the answer is written (`pending.answer_with_code`).

**One neutral line for every refusal.** Wrong code, reused code, another request's code, an expired
or unknown id, a sender outside the list, a bot, the setting off, a rate-limited sender: the same
sentence, so the reply never says whether an id exists or which check failed. Not silence, because
the person who typed it is almost always the owner with a typo, and a mistyped code met by silence
looks exactly like a bot that is down. The specific reason goes to the log, which only the owner
reads. A success replies without echoing the code.

**Failed attempts are rate limited per sender** (:data:`MAX_FAILURES` per :data:`FAILURE_WINDOW`).
While limited, every attempt is refused WITHOUT being checked — a limit that still accepted the right
code would only slow a brute force down, not stop it.

**Silence is still refusal.** Nothing here touches the timeout in `pending.py`.

Not covered: WhatsApp (served over the HTTP gateway, whose ``/chat`` route lets the caller write its
own ``platform`` and ``user``), and a question raised BY a Telegram or Signal turn itself — those two
adapters run each turn on their polling loop, so the answer cannot be read until the turn that is
waiting for it has timed out. Questions from cron, the board or another chat are answered fine there.
:data:`chat_answer_path` below is the executable form of this rule.
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
import unicodedata
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from chimera.telemetry import get_logger

if TYPE_CHECKING:
    from chimera.config import Settings
    from chimera.server.gateway import InboundMessage

_log = get_logger("server.chat_approval")

#: The platforms an answer may come from: the bots whose adapters filter on an allowlist and drop
#: their own messages. WhatsApp is left out on purpose (see the module docstring).
PLATFORMS: tuple[str, ...] = ("discord", "telegram", "slack", "signal")


def chat_answer_path(question_surface: str = "", *, via: str | None = None) -> bool:
    """Whether chat can answer a question raised on ``question_surface``.

    With ``via``, this checks whether that chat surface accepts answers at all. Without it, it
    applies the question-source rule documented above: WhatsApp questions and questions raised by
    Telegram or Signal turns have no chat answer path. The interceptor and the history measurement
    use this same decision so they cannot silently diverge.
    """
    if via is not None:
        return via in PLATFORMS
    # Bot governance facts retain the assembly path as a prefix and append the adapter which
    # received the turn (for example, `platform:telegram`). Historical rows that wrote only the
    # platform name remain readable too. Other namespaced surfaces (e.g. `cron:telegram`) are not
    # messaging bots and must not be classified by their final component.
    if question_surface.startswith(("platform:", "app-messaging:")):
        question_surface = question_surface.partition(":")[2]
    return question_surface not in ("whatsapp", "telegram", "signal")


#: Verb -> the decision it records. Portuguese first, because that is what the delivered text shows
#: the owner; the English pair is accepted because a phone keyboard in English autocorrects to it.
VERBS: dict[str, bool] = {"aprovar": True, "approve": True, "recusar": False, "deny": False}

#: An id as the owner might type it: exactly the twelve hex characters a request id has
#: (`uuid4().hex[:12]`), or a near miss of 6-16 hex characters that carries at least one digit. The
#: digit is what keeps "deny decade 2024" or "approve facade 1234" — an English word spelled in a-f —
#: an ordinary message, where a mistyped id keeps the digits it was copied with. A real id with no
#: digit at all (odds about 1 in 130 000) is still held back by the first branch, because letting it
#: through would put its code in front of the model.
_ID = r"(?:[0-9a-f]{12}|(?=[a-f]*[0-9])[0-9a-f]{6,16})"

#: ``re.ASCII`` is load-bearing, not tidiness. Without it ``IGNORECASE`` folds Unicode: "ſ" (U+017F,
#: long s) matches "s", so "recuſar <id> <code>" matched the shape and then raised ``KeyError`` on
#: the verb lookup — on the bot's polling thread, with the setting off, from any sender, and one
#: such message stopped `chimera serve --telegram`. Without it ``\d`` also takes every script's
#: digits, which no code is ever written in.
_FLAGS = re.IGNORECASE | re.ASCII

#: What counts as an attempt to answer. Deliberately WIDER than a valid answer: an id one character
#: short, or a five-digit code, is still somebody pasting a code, and letting it through as a turn
#: would put the code in front of the model — the one thing this module exists to prevent. Narrow
#: enough that "approve PR 123456" (not hex) stays an ordinary message to the agent.
_SHAPE = re.compile(
    rf"^(?P<verb>aprovar|recusar|approve|deny)\s+(?P<id>{_ID})\s+(?P<code>\d{{4,8}})$", _FLAGS
)

#: The same shape found anywhere in a longer message — see :func:`carries_an_answer`.
_INSIDE = re.compile(rf"\b(?:aprovar|recusar|approve|deny)\s+{_ID}\s+\d{{4,8}}\b", _FLAGS)

#: Failed attempts a sender may make inside :data:`FAILURE_WINDOW` before every attempt is refused
#: unchecked. Five covers a person fixing a typo twice; it leaves a guesser five tries in a
#: million per fifteen minutes.
MAX_FAILURES = 5
FAILURE_WINDOW = 900.0

#: The one reply to every refusal. Constant text: nothing in it depends on which check failed, so
#: it cannot say whether the id exists, the code was close, or the sender is listed.
NEUTRAL = (
    "Nothing was approved or refused. If this was an answer, check the id and the code in the "
    "request, or answer with `chimera approve`."
)


@dataclass(frozen=True)
class ChatAnswer:
    """A message shaped like an answer. Shape only: nothing about it has been checked."""

    approved: bool
    request_id: str
    code: str


def _normal(text: str) -> str:
    """The text as the patterns read it: NFKC-normalised, so the patterns can stay ASCII.

    NFKC folds the compatibility forms a phone or a paste can produce — "ſ" (long s) to "s",
    full-width digits to 0-9 — into what they read as. With the patterns ASCII-only, a message that
    LOOKS like an answer is still held back from the model, and nothing outside ASCII ever reaches
    the verb lookup.
    """
    return unicodedata.normalize("NFKC", str(text or ""))


def parse(text: str) -> ChatAnswer | None:
    """The answer this text is shaped like, or ``None`` for an ordinary message.

    Surrounding whitespace and backticks are ignored, because a line copied from a chat client
    often keeps the code formatting it was shown in.
    """
    match = _SHAPE.fullmatch(" ".join(_normal(text).strip().strip("`").split()))
    if match is None:
        return None
    approved = VERBS.get(match["verb"].casefold())
    if approved is None:
        # Unreachable while the pattern is ASCII-only. Should the pattern and the table drift apart
        # again, this raises into `ChatApprovals.intercept`, which holds the message back — the
        # subscript that stood here raised too, but nothing caught it and the bot stopped.
        raise ValueError("approval verb outside the table")
    return ChatAnswer(
        approved=approved,
        request_id=match["id"].lower(),
        code=match["code"],
    )


def carries_an_answer(text: str) -> bool:
    """Whether an answer appears anywhere inside a longer message.

    The case is a person forwarding the whole notification to the bot ("what is this?"), which
    carries the code. It is not applied — an answer is a message that says only that — but it is
    not a turn either, because the turn would put the code in front of the model.
    """
    return _INSIDE.search(_normal(text)) is not None


def _allowed_ids(settings: Settings, platform: str) -> list[str]:
    from chimera.server.allowlist import allowed_ids

    return allowed_ids(settings, platform)


def enabled_platforms(settings: Settings) -> set[str]:
    """The platforms an answer will be accepted from: the setting on AND that bot's list non-empty."""
    if not getattr(settings, "approve_via_chat", False):
        return set()
    return {p for p in PLATFORMS if _allowed_ids(settings, p)}


def offers_chat_code(settings: Settings) -> bool:
    """Whether a delivered question should carry a code: only if some bot would accept it."""
    return bool(enabled_platforms(settings))


def startup_warning(settings: Settings, platform: str) -> str | None:
    """The line a bot prints at startup when the owner turned this on and it cannot apply."""
    if not getattr(settings, "approve_via_chat", False):
        return None
    if platform not in PLATFORMS:
        return (
            f"CHIMERA_APPROVE_VIA_CHAT is on, but approvals are not accepted from {platform}: "
            "answer with `chimera approve`."
        )
    if _allowed_ids(settings, platform):
        return None
    from chimera.server.allowlist import ALLOWLIST_FIELDS

    _attr, env = ALLOWLIST_FIELDS[platform]
    return (
        f"CHIMERA_APPROVE_VIA_CHAT is on, but the {platform} bot has no allowlist ({env} is "
        "empty): approvals from this chat are REFUSED. An open bot lets anyone who can message it "
        "type; it must not let them approve."
    )


class ChatApprovals:
    """The gateway's interceptor for answers typed into a chat. See the module docstring."""

    def __init__(
        self,
        settings: Settings,
        home: Path,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_failures: int = MAX_FAILURES,
        window: float = FAILURE_WINDOW,
    ) -> None:
        # Read once, when the bot is built, like the allowlist the adapter was built with: a list
        # edited on the Settings screen applies at the next launch for both, never to one of them.
        self._on = bool(getattr(settings, "approve_via_chat", False))
        self._allowed = {p: set(_allowed_ids(settings, p)) for p in PLATFORMS}
        self._home = Path(home)
        self._clock = clock
        self._max_failures = max_failures
        self._window = window
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _limited(self, sender: str) -> bool:
        with self._lock:
            recent = self._failures.get(sender)
            if not recent:
                return False
            cutoff = self._clock() - self._window
            while recent and recent[0] <= cutoff:
                recent.popleft()
            return len(recent) >= self._max_failures

    def _failed(self, sender: str) -> None:
        with self._lock:
            self._failures.setdefault(sender, deque()).append(self._clock())

    def _refusal(self, message: InboundMessage) -> str | None:
        """Why this sender may not answer at all, or ``None`` when the code is worth checking."""
        if not self._on:
            return "setting_off"
        if message.from_bot:
            return "from_a_bot"
        if not chat_answer_path(via=message.platform):
            return "platform_not_accepted"
        listed = self._allowed.get(message.platform) or set()
        if not listed:
            return "allowlist_empty"
        if str(message.user) not in listed:
            return "sender_not_listed"
        return None

    def intercept(self, message: InboundMessage) -> str | None:
        """The reply to an approval-shaped message, or ``None`` to route it as an ordinary turn.

        Anything shaped like an answer is consumed here — answered or not — and never becomes a turn.

        Never raises. It runs before every message on every bot, with the setting off too, and the
        Telegram and Signal adapters call it on their polling loop, where an exception stops the bot
        for everyone. So a defect in here fails CLOSED: nothing is approved, the message is not a
        turn (it might carry a code), the sender gets the neutral line and one failed attempt, and
        the log gets the exception's type — never the text, which might be the code.
        """
        try:
            return self._intercept(message)
        except Exception as exc:  # noqa: BLE001 — see the docstring: fail closed, never stop the bot
            sender = f"{getattr(message, 'platform', '?')}:{getattr(message, 'user', '?')}"
            _log.error(
                "chat approval check failed (%s); message from %s held back",
                type(exc).__name__, sender,
            )
            # Counting the attempt is best effort; the refusal below is not.
            with contextlib.suppress(Exception):
                self._failed(sender)
            return NEUTRAL

    def _intercept(self, message: InboundMessage) -> str | None:
        parsed = parse(message.text)
        sender = f"{message.platform}:{message.user}"
        if parsed is None:
            if carries_an_answer(message.text):
                _log.warning("chat message from %s held back: it carries an approval code", sender)
                return NEUTRAL
            return None
        # Logged by request id and reason only. The code is never written anywhere in this module.
        refusal = self._refusal(message)
        if refusal is not None:
            _log.warning(
                "chat approval for %s from %s refused: %s", parsed.request_id, sender, refusal
            )
            return NEUTRAL
        if self._limited(sender):
            _log.warning(
                "chat approval for %s from %s refused: too many failed attempts",
                parsed.request_id, sender,
            )
            return NEUTRAL
        from chimera.governance.pending import answer_with_code

        outcome = answer_with_code(
            self._home, parsed.request_id, parsed.code, parsed.approved, via=message.key
        )
        if outcome == "forwarded":
            # The question was asked by another process, which alone holds the code (study 30,
            # S30-30): it checks it and refuses the question if it is wrong. Not a failed attempt
            # here — this process cannot tell — and not "Approved", which it cannot promise.
            _log.info("chat answer for %s from %s forwarded to the asker", parsed.request_id, sender)
            return f"Sent: request {parsed.request_id} (the run that asked checks the code)."
        if outcome != "applied":
            self._failed(sender)
            _log.warning(
                "chat approval for %s from %s refused: %s", parsed.request_id, sender, outcome
            )
            return NEUTRAL
        verdict = "Approved" if parsed.approved else "Refused"
        _log.info("chat approval for %s from %s: %s", parsed.request_id, sender, verdict.lower())
        return f"{verdict}: request {parsed.request_id}."
