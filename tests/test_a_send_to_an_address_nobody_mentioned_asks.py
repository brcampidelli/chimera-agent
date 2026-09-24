"""A send to an email address the run was never shown asks, where somebody can answer (study 24, M2).

`bench/recipient_provenance` measured the check (7/7 fabrications caught, 0/9 false flags); the owner
chose where it shows (2026-09-24): a card on the surfaces with a person to answer it (the Code screen,
an attended right hand, `solve` at a terminal), and on every other surface the send goes ahead with a
`recipient_unseen` line in the audit. Never a block where nobody can say yes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.api.code_api import _message_texts
from chimera.governance.audit import AuditLog
from chimera.governance.ledger import SequenceAssessment, TaintLedger
from chimera.governance.ledger_tool import LedgeredTool
from chimera.tools.base import Tool, is_refusal

SEEN = "ana.souza@example.com"
MADE_UP = "ana.beatriz@example.com"  # the fabrication jev-eval-agent recorded, one name off


class _Send(Tool):
    """A send tool that only writes down what it would have sent."""

    def __init__(self, name: str = "send_email") -> None:
        self.name = name
        self.description = "send"
        self.sent: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> str:
        self.sent.append(kwargs)
        return f"sent to {kwargs.get('to') or kwargs.get('chat_id')}"


class _Lookup(Tool):
    name = "run_shell"
    description = "a contact lookup"

    def run(self, **kwargs: Any) -> str:
        return f"Ana Souza <{SEEN}>"


class _Cards:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.asked: list[SequenceAssessment] = []

    def __call__(self, assessment: SequenceAssessment) -> bool:
        self.asked.append(assessment)
        return self.answer


def _ledger(instruction: str = "email Ana the report") -> TaintLedger:
    ledger = TaintLedger()
    ledger.set_instruction(instruction)
    return ledger


def _entries(audit: AuditLog, kind: str) -> list[dict[str, Any]]:
    return [e for e in audit.entries() if e.get("type") == kind]


def test_an_address_nobody_mentioned_is_a_card_where_somebody_can_answer(tmp_path: Path) -> None:
    send, cards, audit = _Send(), _Cards(answer=False), AuditLog(tmp_path / "audit.jsonl")
    tool = LedgeredTool(send, _ledger(), approve=cards, audit=audit, ask_unseen_recipient=True)
    out = tool.run(to=MADE_UP, subject="report", body="attached")
    assert is_refusal(out) and send.sent == []
    assert len(cards.asked) == 1 and MADE_UP in cards.asked[0].reason
    assert "never appeared in the conversation" in cards.asked[0].reason
    assert _entries(audit, "recipient_unseen")[0]["recipients"] == [MADE_UP]


def test_a_yes_sends_it(tmp_path: Path) -> None:
    send, cards = _Send(), _Cards(answer=True)
    tool = LedgeredTool(send, _ledger(), approve=cards, ask_unseen_recipient=True)
    assert not is_refusal(tool.run(to=MADE_UP, subject="s", body="b"))
    assert send.sent and len(cards.asked) == 1


def test_where_nobody_can_answer_it_sends_and_records(tmp_path: Path) -> None:
    # An unattended surface HAS an approver, and it refuses everything; the note must not become a
    # block there, so the surface's flag decides, not the approver's presence.
    send, cards, audit = _Send(), _Cards(answer=False), AuditLog(tmp_path / "audit.jsonl")
    tool = LedgeredTool(send, _ledger(), approve=cards, audit=audit, ask_unseen_recipient=False)
    assert not is_refusal(tool.run(to=MADE_UP, subject="s", body="b"))
    assert send.sent and cards.asked == []
    (line,) = _entries(audit, "recipient_unseen")
    assert line["recipients"] == [MADE_UP] and line["card"] is False


def test_an_address_in_the_instruction_is_seen(tmp_path: Path) -> None:
    send, cards, audit = _Send(), _Cards(answer=False), AuditLog(tmp_path / "audit.jsonl")
    tool = LedgeredTool(send, _ledger(f"email {SEEN} the report"), approve=cards, audit=audit,
                        ask_unseen_recipient=True)
    assert not is_refusal(tool.run(to=f"Ana <{SEEN}>", subject="s", body="b"))
    assert cards.asked == [] and _entries(audit, "recipient_unseen") == []


def test_an_address_from_an_earlier_instruction_is_still_seen() -> None:
    ledger = _ledger(f"my colleague is {SEEN}")
    ledger.set_instruction("now send her the report")  # the next turn replaces the instruction
    assert ledger.unseen_addresses([SEEN]) == []


def test_an_address_a_tool_returned_is_seen() -> None:
    ledger, cards = _ledger(), _Cards(answer=False)
    LedgeredTool(_Lookup(), ledger).run(command="grep -i ana contacts.csv")
    send = _Send()
    tool = LedgeredTool(send, ledger, approve=cards, ask_unseen_recipient=True)
    assert not is_refusal(tool.run(to=SEEN, subject="s", body="b"))
    assert cards.asked == []


def test_a_near_miss_of_a_seen_address_is_still_flagged() -> None:
    ledger = _ledger(f"email {SEEN}")
    assert ledger.unseen_addresses([MADE_UP]) == [MADE_UP]


def test_a_recipient_that_is_not_an_address_is_not_checked() -> None:
    send, cards = _Send("send_message"), _Cards(answer=False)
    tool = LedgeredTool(send, _ledger(), approve=cards, ask_unseen_recipient=True)
    assert not is_refusal(tool.run(platform="discord", chat_id="1234567890", text="hi"))
    assert cards.asked == []


def test_a_connector_send_tool_is_checked_too() -> None:
    send, cards = _Send("gmail_send_email"), _Cards(answer=False)
    tool = LedgeredTool(send, _ledger(), approve=cards, ask_unseen_recipient=True)
    assert is_refusal(tool.run(to=MADE_UP, subject="s", body="b"))
    assert len(cards.asked) == 1


def test_a_taint_card_carries_the_note_and_no_second_card_is_shown() -> None:
    ledger = _ledger()
    ledger.record_fetch("https://pages.example/post", content="a page the agent read")
    send, cards = _Send(), _Cards(answer=True)
    tool = LedgeredTool(send, ledger, approve=cards, narrow_on_taint=True, ask_unseen_recipient=True)
    assert not is_refusal(tool.run(to=MADE_UP, subject="s", body="b"))
    assert len(cards.asked) == 1
    assert "untrusted content" in cards.asked[0].reason and MADE_UP in cards.asked[0].reason


def test_the_next_code_turn_knows_the_address_the_last_one_was_given(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Through the real `/api/code/turn`: the ledger is rebuilt per turn, so without the handover
    an address the person gave in turn 1 would read as made up in turn 2."""
    from fastapi.testclient import TestClient

    import chimera.core
    from chimera.api import build_api_app
    from chimera.config import Settings, get_settings
    from chimera.core.agent import AgentResult
    from chimera.interface import ChatSession

    verdicts: list[list[str]] = []

    class _Spy:
        def __init__(self, *a: Any, **kw: Any) -> None:
            self.registry = a[1] if len(a) > 1 else kw.get("registry")

        def run(self, task: str, **_: Any) -> Any:
            ledgers = [t.ledger for t in self.registry.tools() if isinstance(t, LedgeredTool)]
            verdicts.append(ledgers[0].unseen_addresses([SEEN]) if ledgers else ["no ledger"])
            return AgentResult(
                answer="ok", steps=1, stopped_reason="final", model="test/model",
                transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}],
            )

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", lambda *a, **kw: _Spy(*a, **kw), raising=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))  # type: ignore[call-arg]
    client = TestClient(build_api_app(lambda: ChatSession(_Spy()), workspace=ws, settings=settings))

    # The turn answers as a stream of `event:`/`data:` lines; the session id is in the `session` one.
    first = client.post("/api/code/turn", json={"message": f"my colleague is {SEEN}"}).text
    session = next(
        json.loads(line[len("data: "):]) for line, before in zip(first.splitlines()[1:], first.splitlines(), strict=False)
        if before == "event: session" and line.startswith("data: ")
    )
    client.post("/api/code/turn", json={"message": "send her the report", "session_id": session["session_id"]})
    assert verdicts == [[], []], verdicts


def test_the_code_screen_hands_earlier_turns_over_in_every_stored_shape() -> None:
    messages = [
        {"role": "user", "content": f"Ana is {SEEN}"},
        {"role": "assistant", "content": [{"type": "text", "text": "noted: bob@example.org"}]},
        {"role": "tool", "content": None},
    ]
    ledger = _ledger("send her the report")
    ledger.note_seen(*_message_texts(messages))
    assert ledger.unseen_addresses([SEEN, "bob@example.org"]) == []
