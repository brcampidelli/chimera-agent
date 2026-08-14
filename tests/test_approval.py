"""The other side of the gate — and the silence it was hiding.

Both governance layers have taken an ``approve=`` callable since they were written and neither was
ever given one. The consequence is not an opinion: with no approver, `bench/injection` measures
**100%** of dangerous-class calls refused on any run that read something external. The gate was
never too strict; there was nothing behind it.

The subtler failure is what a refusal looks like from outside. It comes back as an ordinary
observation string, so the agent reads it like any tool result and carries on — the run ends in
prose and the receipt says success. Most of this file is about making that countable.
"""

from __future__ import annotations

import io
from typing import Any

from chimera.governance.approval import ApprovalLedger, allow, approver_for, ask, deny
from chimera.governance.ledger import Decision, SequenceAssessment, TaintLedger
from chimera.governance.ledger_tool import LedgeredTool
from chimera.tools.base import Tool


class _Writer(Tool):
    name = "write_file"
    description = "writes"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.calls = 0

    def run(self, **kwargs: Any) -> str:
        self.calls += 1
        return "wrote it"


def _assessment() -> SequenceAssessment:
    return SequenceAssessment(True, Decision.REVIEW, "writes after reading untrusted content")


# --- the policies -------------------------------------------------------------------------------


def test_deny_refuses_and_records() -> None:
    """Refusing is fine. Refusing invisibly is the bug — the caller has to be able to tell "the job
    did its work" from "the job was not allowed to"."""
    book = ApprovalLedger()

    assert deny(book)(_assessment()) is False
    assert book.blocked is True
    assert "1 action(s) refused" in book.summary()


def test_allow_grants_and_still_records() -> None:
    # A run that was allowed forty dangerous actions should be able to say so. An opt-in that
    # becomes invisible is how a deliberate trade turns into an accident nobody remembers making.
    book = ApprovalLedger()

    assert allow(book)(_assessment()) is True
    assert book.granted and book.blocked is False


def test_ask_treats_anything_but_yes_as_no(monkeypatch: Any) -> None:
    """A prompt that reads silence as consent is worse than no prompt: it produces a record of an
    approval nobody gave."""
    for answer in ("", "n", "no", "maybe", "Y E S", "sure"):
        monkeypatch.setattr("builtins.input", lambda a=answer: a)
        book = ApprovalLedger()

        assert ask(book, stream=io.StringIO())(_assessment()) is False, answer
        assert book.blocked


def test_ask_denies_when_the_input_is_closed(monkeypatch: Any) -> None:
    # The cron case, reached by accident: stdin exists but hands back EOF.
    def eof() -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    book = ApprovalLedger()

    assert ask(book, stream=io.StringIO())(_assessment()) is False
    assert book.blocked


def test_ask_accepts_an_explicit_yes(monkeypatch: Any) -> None:
    monkeypatch.setattr("builtins.input", lambda: "y")
    book = ApprovalLedger()

    assert ask(book, stream=io.StringIO())(_assessment()) is True


# --- choosing one ------------------------------------------------------------------------------


def test_ask_degrades_to_deny_without_a_terminal(monkeypatch: Any) -> None:
    """The direction of the fallback is the whole decision. Degrading the other way — allowing
    because nobody could be asked — would make an unattended deployment the most permissive
    configuration in the product."""
    monkeypatch.setattr("sys.stdin", io.StringIO())  # not a tty
    book = ApprovalLedger()

    assert approver_for("ask", book)(_assessment()) is False
    assert book.blocked


def test_allow_is_never_reached_by_accident(monkeypatch: Any) -> None:
    # It has to be spelled out. No terminal, no default, no inference gets you here.
    monkeypatch.setattr("sys.stdin", io.StringIO())

    assert approver_for("", ApprovalLedger())(_assessment()) is False
    assert approver_for("deny", ApprovalLedger())(_assessment()) is False
    assert approver_for("allow", ApprovalLedger())(_assessment()) is True


def test_both_call_shapes_reach_the_same_policy() -> None:
    """The kernel asks with ``(verdict, action)`` and the taint ledger with ``(assessment,)``. One
    adapter, or the same policy drifts into two implementations that disagree under pressure."""
    book = ApprovalLedger()
    approve = deny(book)

    approve(_assessment())
    approve(type("V", (), {"reason": "blocked by kernel"})(), "run_shell {'command': 'rm -rf /'}")

    assert len(book.refused) == 2
    assert any("rm -rf" in r for r in book.refused)


# --- wired into the real tool -------------------------------------------------------------------


def test_an_approved_call_actually_runs() -> None:
    """The measurement this whole module answers: with an approver that says yes, legitimate work
    after an external read completes instead of being refused."""
    tainted = TaintLedger()
    tainted.record_fetch("docs-site", content="the upgrade guide says set timeout to 30")
    inner = _Writer()
    tool = LedgeredTool(inner, tainted, narrow_on_taint=True, approve=allow())

    out = tool.run(path="config/app.yml", content="timeout_seconds: 30")

    assert inner.calls == 1
    assert "needs review" not in out


def test_without_an_approver_it_is_still_refused() -> None:
    # The baseline that made this necessary, asserted so the fix cannot be mistaken for the gate
    # having been loosened.
    tainted = TaintLedger()
    tainted.record_fetch("docs-site", content="the upgrade guide says set timeout to 30")
    inner = _Writer()
    tool = LedgeredTool(inner, tainted, narrow_on_taint=True)

    out = tool.run(path="config/app.yml", content="timeout_seconds: 30")

    assert inner.calls == 0
    assert "needs review" in out


def test_a_refusal_through_the_real_tool_is_counted() -> None:
    """The bridge between the string the agent sees and the fact the caller needs. Without this the
    refusal exists only inside an observation nobody parses."""
    tainted = TaintLedger()
    tainted.record_fetch("page", content="x")
    book = ApprovalLedger()
    tool = LedgeredTool(_Writer(), tainted, narrow_on_taint=True, approve=deny(book))

    tool.run(path="a.py", content="x")

    assert book.blocked, "the run was blocked and nothing outside the observation string knew"


# --- the measurement that closes the loop -------------------------------------------------------


def test_an_approver_recovers_the_honest_work_without_weakening_the_defense() -> None:
    """The whole argument, in one comparison.

    Without an approver the stack refuses 50% of legitimate work (100% of the runs that read
    anything external) and the posture gate fails. With one, that goes to zero — and the attack
    block rate does not move, because the approver is offered only to the benign arm.

    That asymmetry is deliberate and worth stating: handing the same yes to the attack corpus would
    model a user who approves whatever an injected page asks for, which measures nothing about the
    defense and everything about the fiction.
    """
    from chimera.eval.injection import run_posture

    without = run_posture(defended=True)
    with_approver = run_posture(defended=True, approve=allow(ApprovalLedger()))

    assert without.gate()[0] is False
    assert with_approver.gate()[0] is True
    assert without.benign.summary()["over_block_rate"] == 0.5
    assert with_approver.benign.summary()["over_block_rate"] == 0.0
    # Unchanged: the approver bought back the work, not the exposure.
    assert (
        with_approver.attacks.summary()["block_rate"] == without.attacks.summary()["block_rate"]
    )
