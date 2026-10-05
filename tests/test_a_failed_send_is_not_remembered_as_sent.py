"""A send that ended in an error is not remembered as a send that happened (study 30, S30-30).

The idempotency cache (M15-A5) keeps the result of each side-effecting call so a retry of the SAME
call is not fired twice. It kept every RETURNED result, and a tool reports most failures by
returning ``error: ...`` rather than raising — `send_email` returns ``error: send_email failed:
<exc>`` for a refused login, a timeout, a server error. So a retry after a failed send was answered
``[idempotent: send_email already executed with these args; not repeated]``: the model was told the
email had gone when the only thing it ever saw was the failure, and `is_refusal` reads that marker
as an action that HAPPENED. A raise had always been retried (``test_a_send_that_raised_is_not_
remembered_as_sent``); a returned error, the commoner case, was not.

What is still not decided here, and why: an ambiguous failure — a timeout, a 5xx — may have taken
effect before it failed, so a retry can duplicate it. The study measured that agents check the
state before retrying such a failure about half the time (2609.38469). Holding the next call to the
same tool until the agent has checked is new behaviour no measurement recommended, so it is a
setting, off (``CHIMERA_HOLD_AFTER_FAILED_SEND``); the tests below pin both states.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.governance.ledger import TaintLedger
from chimera.governance.ledger_tool import HOLD_AFTER_FAILED_SEND_ENV, LedgeredTool
from chimera.tools.base import Tool, is_refusal, refusal


class _Send(Tool):
    """A send whose outcomes are scripted: each call returns the next one, or raises it."""

    name = "send_email"
    description = "send an email"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self, *outcomes: str | Exception) -> None:
        self.outcomes = list(outcomes)
        self.fires = 0

    def run(self, **kwargs: Any) -> str:
        self.fires += 1
        outcome = self.outcomes.pop(0) if self.outcomes else "sent"
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def _hold_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(HOLD_AFTER_FAILED_SEND_ENV, raising=False)


def test_a_send_that_returned_an_error_is_tried_again_not_reported_as_sent() -> None:
    inner = _Send("error: send_email failed: 503 service unavailable", "sent")
    tool = LedgeredTool(inner, TaintLedger())

    first = tool.run(to="boss@example.com", body="hi")
    second = tool.run(to="boss@example.com", body="hi")

    assert first.startswith("error:"), "precondition: the first send failed"
    assert "idempotent" not in second, "a failed send was reported as already executed"
    assert inner.fires == 2 and second == "sent"


def test_a_send_that_was_refused_inside_the_tool_is_not_remembered_either() -> None:
    inner = _Send(refusal("the connector refused this recipient"), "sent")
    tool = LedgeredTool(inner, TaintLedger())

    tool.run(to="boss@example.com")
    second = tool.run(to="boss@example.com")

    assert inner.fires == 2 and second == "sent"


def test_a_send_that_succeeded_is_still_not_repeated() -> None:
    """The guard itself is unchanged: only a success is remembered, and a success is remembered."""
    inner = _Send("sent #1")
    tool = LedgeredTool(inner, TaintLedger())

    tool.run(to="boss@example.com")
    second = tool.run(to="boss@example.com")

    assert inner.fires == 1 and "idempotent" in second and not is_refusal(second)


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("smtp timeout after DATA"), "error: send_email failed: 500 internal error"],
    ids=["raised", "returned"],
)
def test_with_the_hold_on_the_next_send_after_a_failure_waits_for_a_check(
    monkeypatch: pytest.MonkeyPatch, failure: str | Exception
) -> None:
    """Any arguments: the retry that duplicates an email is usually a REWORDED one (S30-30)."""
    monkeypatch.setenv(HOLD_AFTER_FAILED_SEND_ENV, "1")
    inner = _Send(failure, "sent")
    tool = LedgeredTool(inner, TaintLedger())

    if isinstance(failure, Exception):
        # A send that is not a taint source raises on to the loop, which reports it.
        with pytest.raises(type(failure)):
            tool.run(to="boss@example.com", body="the report")
    else:
        tool.run(to="boss@example.com", body="the report")
    held = tool.run(to="boss@example.com", body="the report, again")
    after_check = tool.run(to="boss@example.com", body="the report, again")

    assert inner.fires == 2, "the held call must not have been sent"
    assert is_refusal(held), "nothing ran, so the record must say it did not"
    assert "may have taken effect" in held and "check" in held.lower()
    assert after_check == "sent", "held once, not forever: the next call goes through"


def test_with_the_hold_off_a_reworded_retry_goes_straight_through() -> None:
    inner = _Send("error: send_email failed: 500 internal error", "sent")
    tool = LedgeredTool(inner, TaintLedger())

    tool.run(to="boss@example.com", body="the report")
    second = tool.run(to="boss@example.com", body="the report, again")

    assert inner.fires == 2 and second == "sent"


# ---- the hold asks nobody, and holds only what may have gone out (review of a15cf9f6)


def test_with_the_hold_on_a_held_send_never_spends_a_persons_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hold refuses whatever the answer, so it comes before any card: asked after one, the
    person's approval was spent on a call that then answered "This call was NOT sent"."""
    monkeypatch.setenv(HOLD_AFTER_FAILED_SEND_ENV, "1")
    asked: list[Any] = []

    def approve(assessment: Any) -> bool:
        asked.append(assessment)
        return True

    inner = _Send("error: send_email failed: 500 internal error", "sent")
    tool = LedgeredTool(inner, TaintLedger(), approve=approve, ask_unseen_recipient=True)

    tool.run(to="stranger@example.com", body="the report")
    cards_before = len(asked)
    held = tool.run(to="stranger@example.com", body="the report, again")

    assert cards_before >= 1, "precondition: this send asks a person (an unseen recipient)"
    assert is_refusal(held) and "may have taken effect" in held
    assert len(asked) == cards_before, "a card was shown for a call the hold then refused"


class _Wrapper(Tool):
    """A governance wrapper as the registry builds it: the real tool sits on ``inner``."""

    name = "send_email"
    description = "send an email"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self, inner: Tool) -> None:
        self.inner = inner

    def run(self, **kwargs: Any) -> str:
        return self.inner.run(**kwargs)


@pytest.mark.parametrize("wrapped", [False, True], ids=["bare", "through-a-wrapper"])
def test_a_send_that_failed_before_any_delivery_is_not_held(
    monkeypatch: pytest.MonkeyPatch, wrapped: bool
) -> None:
    """`send_email` with no SMTP settings sent nothing; "it may have taken effect" would be false."""
    from chimera.config import get_settings
    from chimera.tools.email import SendEmailTool

    monkeypatch.setenv(HOLD_AFTER_FAILED_SEND_ENV, "1")
    for key in ("CHIMERA_SMTP_HOST", "CHIMERA_SMTP_USER", "CHIMERA_SMTP_PASSWORD"):
        monkeypatch.setenv(key, "")
    get_settings.cache_clear()
    try:
        real: Tool = SendEmailTool()
        tool = LedgeredTool(_Wrapper(real) if wrapped else real, TaintLedger())

        first = tool.run(to="boss@example.com", subject="s", body="b")
        second = tool.run(to="boss@example.com", subject="s", body="b, again")
    finally:
        get_settings.cache_clear()

    assert first.startswith("error: send_email needs"), "precondition: failed before connecting"
    assert not is_refusal(second) and "may have taken effect" not in second
    assert second.startswith("error: send_email needs"), "the second call ran, and said why it failed"
